"""注入端：收 `T_KEY` 帧 → 状态机 → 注入。

这一段在 Mac 上跑。对端（Windows）负责采集和吞键，本端只负责"把按键放回系统"。

三条不变式：

* **epoch 只由键盘所在端推进**（谁决定按键去向，谁定 epoch）。本端收到
  `T_FOCUS` 才换 epoch，本地切屏钩子只做"全量释放"这个安全动作，不动 epoch。
  这样两端永远不会各自数到不同的号 —— 各数各的话，一端数到 5、另一端发 3，
  所有帧都会被当成过期丢掉，键盘从此哑掉，而且现场看起来"连得上但不打字"。
* **非法 / 未映射的键只丢自己**，不中断整条链路。
* **注入失败只记数**，不抛异常、不断链 —— 键盘是附加功能，不能拖垮同步。
"""

from __future__ import annotations

import logging
import threading

from .inject_macos import MacInjector
from .keymap import MOD_MAP_POSITION, cg_flags, vk_for
from .state import KeyEvent, KeyState

log = logging.getLogger(__name__)

#: T_KEY 里 payload 恒为空、字段全在 header。少一个字段就整帧丢，不做"猜"。
_REQUIRED = ("u", "d")


class KeyboardReceiver:
    """把远端来的按键注进本机。线程安全（transport 的读线程会直接调）。"""

    def __init__(
        self,
        injector: MacInjector | None = None,
        *,
        mod_map: str = MOD_MAP_POSITION,
        dedupe_repeat: bool = False,
        enabled: bool = True,
        align_caps: bool = True,
    ) -> None:
        self._injector = injector if injector is not None else MacInjector()
        self.mod_map = mod_map
        self.state = KeyState(dedupe_repeat=dedupe_repeat)
        self._lock = threading.RLock()
        self._enabled = bool(enabled)
        self._align_caps = bool(align_caps)
        self._epoch = 0
        self._last_seq = -1
        #: 对端在 T_KEYSTATE 里报的链路往返（ms）。界面拿它显示「往返 N ms」。
        #: **这是链路 RTT，不是端到端按键延迟** —— 后者要等捕获端把 `t` 时间戳
        #: 填上、两端时钟校过之后才算得准。
        self.last_rtt_ms: float | None = None
        self.stats = {
            "keys": 0, "injected": 0, "dropped_stale": 0,
            "dropped_unmapped": 0, "dropped_disabled": 0,
            "released": 0, "resync_fixed": 0,
        }

    # ------------------------------------------------------------------ 开关
    @property
    def enabled(self) -> bool:
        return self._enabled

    def attach(self, engine) -> None:  # noqa: ARG002
        """注入端不持有引擎（按键发送在捕获端）。留着只为两端接口一致。"""

    def available(self) -> tuple[bool, str]:
        return self._injector.available()

    def ready_for_injection(self) -> bool:
        """本端现在**真的**能把按键注进系统吗（开关开着 + 注入器可用 + 有授权）。

        这是给**对端**看的一条证据（随 `T_STATE_ACK` 上报）：Windows 据此决定
        要不要吞键。2026-11-07 之前这条信息完全不存在 —— 对端关了开关、没给
        辅助功能授权、或者没装 pyobjc 时，Windows 照样吞键，用户的按键就静默
        掉进一个不存在的窗口里。现场表现是「键盘完全不好使，界面却显示转发中」，
        而且两端日志都干干净净（`dropped_disabled` 只是 Mac 端的一个计数器）。

        `trusted()` 返回 None（老 pyobjc 查不出来）**不**当作未授权：那种机器上
        注入本来可能是好的，把它判成不可用等于凭空禁掉键盘。
        """
        if not self._enabled:
            return False
        ok, _why = self._injector.available()
        if not ok:
            return False
        return self._injector.trusted() is not False

    @property
    def injector(self) -> MacInjector:
        """给工具脚本用（探针要读授权 / CapsLock 状态）。"""
        return self._injector

    def set_enabled(self, enabled: bool, *, release: bool = True) -> None:
        """关掉时默认先把手上的键全抬了 —— 否则关的那一刻按着的键会永远卡住。"""
        with self._lock:
            if self._enabled and not enabled and release:
                self._release_locked()
            self._enabled = bool(enabled)

    @property
    def epoch(self) -> int:
        return self._epoch

    # ------------------------------------------------------------------ 焦点
    def on_focus(self, target_host: str = "", *, is_self: bool = False) -> int:
        """本地切屏钩子。**不动 epoch**，只做全量释放。

        为什么不动：epoch 属于"决定按键去向"的那一端。本端若也自增，
        就会和发送端数出两套号，之后所有帧都被判过期（见模块注释）。
        """
        with self._lock:
            released = self._release_locked()
        if released:
            log.debug("切屏（→%s）释放 %d 个按下的键", target_host or "?", released)
        return released

    def handle_focus(self, header: dict) -> bool:
        """收到对端的 `T_FOCUS`：换 epoch + 释放残留。"""
        try:
            epoch = int(header.get("e", -1))
        except (TypeError, ValueError):
            return False
        with self._lock:
            if epoch <= self._epoch:
                return False
            self._epoch = epoch
            self._last_seq = -1
            self._release_locked()
        if self._align_caps:
            self._sync_caps(header)
        log.info("键盘焦点切到 %s（epoch=%d）", header.get("target") or "?", epoch)
        return True

    def _sync_caps(self, header: dict) -> None:
        """把本机 CapsLock 对齐到对端。对端没报就跳过。"""
        want = header.get("caps")
        if want is None:
            return
        try:
            self._injector.set_caps_lock(bool(int(want)))
        except Exception as exc:  # noqa: BLE001 - 对齐失败无伤大雅
            log.debug("CapsLock 对齐失败：%s", exc)

    # ------------------------------------------------------------------ 收键
    def handle_key(self, header: dict) -> bool:
        """收一个 `T_KEY`。返回是否真的注入了。"""
        for field in _REQUIRED:
            if field not in header:
                log.debug("T_KEY 缺字段 %s：%r", field, header)
                return False
        try:
            usage = int(header["u"])
            down = bool(int(header["d"]))
            mods = int(header.get("m", 0))
            epoch = int(header.get("e", self._epoch))
            seq = int(header.get("s", -1))
        except (TypeError, ValueError):
            log.debug("T_KEY 字段非法：%r", header)
            return False

        with self._lock:
            if not self._enabled:
                self.stats["dropped_disabled"] += 1
                return False
            if epoch < self._epoch:
                # 切屏前发出的按键在切屏后才到 —— 这一刻它已经不对应任何画面
                self.stats["dropped_stale"] += 1
                return False
            if epoch > self._epoch:
                # 对端先换了 epoch 才发键（正常顺序）：跟上，并把残留抬掉
                self._epoch = epoch
                self._last_seq = -1
                self._release_locked()
            if seq >= 0:
                if seq <= self._last_seq:
                    self.stats["dropped_stale"] += 1
                    return False
                self._last_seq = seq

            vk = vk_for(usage, self.mod_map)
            if vk is None:
                self.stats["dropped_unmapped"] += 1
                log.debug("没有 macOS 键码：usage=0x%02X", usage)
                return False

            event = KeyEvent(usage=usage, down=down, mods=mods, epoch=epoch, seq=seq)
            for out in self.state.apply(event):
                out_vk = vk_for(out.usage, self.mod_map)
                if out_vk is None:
                    continue
                flags = cg_flags(out.mods, self.mod_map)
                if self._injector.inject(out_vk, out.down, flags):
                    self.stats["injected"] += 1
            self.stats["keys"] += 1
            return True

    # ------------------------------------------------------------------ 对账
    def handle_state(self, header: dict) -> int:
        """收到对端的 `T_KEYSTATE`：纠正"它已经松了、我还按着"的残留。

        只做**释放**方向。反向（对端说按着、本端没按）绝不补按下 ——
        补错了就是误输入，而少按一个键最多是"这次没打出来"。
        """
        rtt = header.get("rtt")
        if isinstance(rtt, (int, float)) and not isinstance(rtt, bool):
            self.last_rtt_ms = float(rtt)
        peer_down = header.get("down")
        if not isinstance(peer_down, list):
            return 0
        peer: set[int] = set()
        for item in peer_down:
            try:
                peer.add(int(item))
            except (TypeError, ValueError):
                continue
        fixed = 0
        with self._lock:
            for usage in list(self.state.down_usages):
                if usage in peer:
                    continue
                pending = self.state.apply(
                    KeyEvent(usage=usage, down=False, epoch=self._epoch,
                             mods=self.state.mods_bitmap())
                )
                for out in pending:
                    vk = vk_for(out.usage, self.mod_map)
                    if vk is not None and self._injector.inject(
                        vk, False, cg_flags(out.mods, self.mod_map)
                    ):
                        fixed += 1
            if fixed:
                self.stats["resync_fixed"] += fixed
                log.info("按键状态对账：补了 %d 个 keyup", fixed)
        return fixed

    # ------------------------------------------------------------------ 内部
    def _release_locked(self) -> int:
        released = 0
        for out in self.state.release_all():
            vk = vk_for(out.usage, self.mod_map)
            if vk is None:
                continue
            if self._injector.inject(vk, False, cg_flags(out.mods, self.mod_map)):
                released += 1
        self.stats["released"] += released
        return released

    def describe(self) -> str:
        ok, why = self._injector.available()
        if not ok:
            return f"键盘注入不可用：{why}"
        trusted = self._injector.trusted()
        if trusted is False:
            return "键盘注入未授权（辅助功能）"
        down = len(self.state)
        return f"键盘注入就绪（epoch={self._epoch}，按着 {down} 键）"


__all__ = ["KeyboardReceiver"]
