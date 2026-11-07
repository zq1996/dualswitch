"""捕获端（Windows）：把本机键盘转给 Mac，并在闸门全绿时吞掉按键。

图纸：`docs/KEYBOARD-BRIDGE.md` §5/§8。这一端是**所有"吞键安全"逻辑所在**，
所以下面每一条都不是风格问题：

1. **钩子里绝不做 I/O。** `WH_KEYBOARD_LL` 的回调在系统输入链上同步执行，
   一次阻塞的 `sendall` 就能让整台机器打字卡顿甚至假死。按键只入队，
   真正的发送在 worker 线程里做。
2. **吞键闸门四条全绿才吞**（§8.5）：转发中 + 链路活着（300ms 内有成功发送）
   + 不在白名单 + 不是注入事件。任何一条不满足就 `CallNextHookEx` 放行 ——
   "吞了但没送出去"的按键会掉进一个用户看不见的窗口里乱敲。
3. **关闭 = 立刻停止吞键**（不只是停止发送）。Windows 是吞键端，只停发不停吞，
   按键既不去 Mac 也不去本机，直接消失且完全静默。
4. **关闭 / 切走时把按着的键全抬了**，否则对面那个 Ctrl 会一直按着。
5. **热键与紧急停止组合永远不转发也不吞**（§8.7）—— 转发期间连切屏热键都吞掉，
   用户就再也切不回来了。

键位按 **HID usage** 传（`keymap.usage_for_vk`），不传字符：对端输入法在对面
原生合成，候选窗出现在用户正在看的那块屏上，和真 KVM 一样。
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from typing import Any, Callable

from .hook_windows import KBDLLHOOKSTRUCT, caps_lock_on, key_down, shared_hook
from .keymap import (
    HID_LALT,
    HID_LCTRL,
    HID_LGUI,
    HID_LSHIFT,
    HID_RALT,
    HID_RCTRL,
    HID_RGUI,
    HID_RSHIFT,
    MODIFIER_USAGES,
    USAGE_TO_MOD_BIT,
    usage_for_vk,
)
from .state import KeyEvent, KeyState

if sys.platform != "win32":  # pragma: no cover - 平台守卫
    raise ImportError("dualswitch.keyboard.capture_windows 只能在 Windows 上导入")

log = logging.getLogger(__name__)

#: 闸门条件之一：最近这么久之内有过**成功的发送**，才认为链路活着。
#: 取 300ms 是 §8.5 定的：一个心跳周期（默认 100ms）的 3 倍，
#: 既能容忍一次抖动，又能在对端崩掉后 0.3 秒内把键盘还给用户。
LINK_FRESH_S = 0.3

#: 发送队列上限。满了就不吞（宁可漏几个键，也不能把钩子线程堵住）。
QUEUE_MAX = 256

VK_CAPITAL = 0x14

#: 白名单里的修饰键 → 判断"现在按着没有"的 VK
_MOD_VKS = {
    HID_LCTRL: (0xA2,), HID_RCTRL: (0xA3,),
    HID_LSHIFT: (0xA0,), HID_RSHIFT: (0xA1,),
    HID_LALT: (0xA4,), HID_RALT: (0xA5,),
    HID_LGUI: (0x5B,), HID_RGUI: (0x5C,),
}

#: 修饰键名（配置里的写法）→ 它的 HID usage
_MOD_USAGE_BY_NAME = {
    "ctrl": HID_LCTRL, "shift": HID_LSHIFT, "alt": HID_LALT, "ralt": HID_RALT,
    "win": HID_LGUI,
}


class KeyboardCapture:
    """把本机（Windows）键盘转给对端，并在闸门全绿时吞键。

    `engine` 只需要三个方法：`send_key / send_key_state / send_focus`（返回 bool）
    与 `switch_ready`；`hook` 只需要 `acquire(handler) / release(handler)`。
    两个都可以注入假实现 —— 闸门逻辑不该只能靠真键盘验。
    """

    def __init__(
        self,
        engine: Any = None,
        *,
        local_host: str = "windows",
        peer_host: str = "mac",
        hotkey_vk: int | None = None,
        hotkey_mods: tuple[str, ...] = (),
        release_vk: int | None = None,
        release_mods: tuple[str, ...] = (),
        key_state_hz: int = 10,
        enabled: bool = False,
        hook: Any = None,
        clock: Callable[[], float] = time.time,
        caps_reader: Callable[[], bool] = caps_lock_on,
        mods_reader: Callable[[int], bool] = key_down,
        on_release_request: Callable[[], None] | None = None,
        on_hotkey: Callable[[], None] | None = None,
        rtt_probe: Callable[[], bool | None] | None = None,
        rtt_interval_s: float = 5.0,
    ) -> None:
        self._engine = engine
        self.local_host = local_host
        self.peer_host = peer_host
        self._hook = hook if hook is not None else shared_hook()
        self._clock = clock
        self._caps_reader = caps_reader
        self._mods_reader = mods_reader
        self._on_release_request = on_release_request
        self._on_hotkey = on_hotkey
        #: 刚刚被我们吃掉的"白名单键"的 VK：它的抬起也要吃掉，否则会漏一个孤立的
        #: keyup 给 Windows（对大多数键无害，但用户会看到"我按了一下，系统收到半下"）。
        self._swallow_up_vk: int | None = None
        self._rtt_probe = rtt_probe
        self._rtt_interval = max(1.0, float(rtt_interval_s))
        self._hz = max(1, min(50, int(key_state_hz)))
        self._interval = 1.0 / self._hz

        self.hotkey_vk = hotkey_vk
        self.hotkey_mods = tuple(hotkey_mods)
        self.release_vk = release_vk
        self.release_mods = tuple(release_mods)

        self._enabled = bool(enabled)
        #: 按键应该去哪一台。只有 `peer_host` 时才会转发/吞键。
        self._target = local_host
        self._epoch = 0
        self._seq = 0
        #: **转发出去、对端还按着**的键（`_release_all` 就抬这些）。
        self._down = KeyState()
        #: **本机物理按着**的修饰键。与 `_down` 是两回事：白名单判断、以及每个
        #: T_KEY 的 `m` 位图都要用它，而它跟"有没有转发"无关（画面在本机时也按着）。
        self._local_mods: set[int] = set()
        self._lock = threading.RLock()
        self._queue: "queue.Queue[tuple[str, dict]]" = queue.Queue(maxsize=QUEUE_MAX)
        self._worker: threading.Thread | None = None
        self._rtt_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_ok_at = 0.0
        self._last_fail_log_at = 0.0
        #: 闸门当前为什么不开（空串 = 全绿）。只用来做「变化时记一条日志」。
        self._gate_reason = ""
        self._gate_reason_at = 0.0
        #: 本 epoch 里发出去的第一个按键记过一次没有（一次 INFO 就能证明链路通了）
        self._first_key_logged_epoch = -1
        self._caps = False
        self._hook_ok = False
        self._error: str | None = None
        #: 对端往返（ms）。界面拿它显示「往返 N ms」；None = 还没测出来。
        self.last_rtt_ms: float | None = None
        self.stats = {
            "keys": 0, "swallowed": 0, "passed": 0,
            "dropped_unmapped": 0, "dropped_queue": 0, "send_failures": 0,
            "released": 0, "heartbeats": 0, "rtt_samples": 0,
        }

    # ------------------------------------------------------------------ 状态
    @property
    def enabled(self) -> bool:
        return self._enabled

    def attach(self, engine: Any) -> None:
        """挂上引擎（发送通道）。`cli` 在引擎建好之后调。

        先建端点再建引擎是刻意的：引擎要拿端点的回调，端点要拿引擎的发送方法，
        谁先谁后都会缺一半。先建端点（engine=None）→ 建引擎 → attach 最直白。

        往返探测也在这里接上：借引擎现成的 `T_STATE` 请求/回执（不新增协议帧），
        捕获端因此不需要知道协议长什么样。
        """
        self._engine = engine
        if self._rtt_probe is None and hasattr(engine, "request_peer_state"):
            self._rtt_probe = lambda: engine.request_peer_state(timeout=1.0)

    # ------------------------------------------------------------------ 远端帧（本端不处理）
    # 键盘物理挂在本端，对端不会把按键发过来。这三个方法与注入端同名同签名，
    # 只为让 `cli.py` 用同一份接线代码（不需要按平台分支）。
    def handle_key(self, header: dict) -> bool:  # noqa: ARG002
        return False

    def handle_state(self, header: dict) -> int:  # noqa: ARG002
        return 0

    def handle_focus(self, header: dict) -> bool:  # noqa: ARG002
        return False

    @property
    def target(self) -> str:
        return self._target

    @property
    def forwarding(self) -> bool:
        """现在是否"按键该去对端"（= 该吞键的语义前提）。"""
        return self._enabled and self._target != self.local_host

    @property
    def epoch(self) -> int:
        return self._epoch

    def available(self) -> tuple[bool, str]:
        """能不能用。**没启动过也算可用**（没试过不等于不可用）。

        界面在打开开关前会问一次；如果这里对"还没装钩子"答 False，用户永远打不开。
        真正装不上的情况由 `start()` 抛错 → 界面弹回开关并说明原因。
        """
        if self._error:
            return False, self._error
        return True, ""

    def describe(self) -> str:
        if not self._enabled:
            return "键盘转发已关闭（按键留在本机）"
        if not self.forwarding:
            return "键盘转发已就绪 · 画面在本机，按键不转发"
        rtt = f"，往返 {self.last_rtt_ms:.0f}ms" if self.last_rtt_ms else ""
        return f"键盘转发中 → {self.peer_host}{rtt}"

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """装钩子 + 起 worker。幂等。"""
        if self._worker is not None:
            return
        self._stop.clear()
        self._error = None
        try:
            self._hook.acquire(self.on_hook_event, name="keyboard-capture")
            self._hook_ok = True
        except OSError as exc:
            self._hook_ok = False
            self._error = str(exc)
            log.error("键盘捕获启动失败（键盘跟随不可用）：%s", exc)
            return
        self._caps = bool(self._caps_reader())
        self._worker = threading.Thread(target=self._run, name="keyboard-send", daemon=True)
        self._worker.start()
        if self._rtt_probe is not None:
            self._rtt_thread = threading.Thread(
                target=self._rtt_loop, name="keyboard-rtt", daemon=True
            )
            self._rtt_thread.start()
        if self._enabled:
            self._push_focus()

    def stop(self) -> None:
        self._stop.set()
        self._error = None
        if self._enabled:
            self._enabled = False
            self._release_all()
        try:
            self._hook.release(self.on_hook_event)
        except Exception:  # noqa: BLE001 - 摘钩子失败不能连累退出
            log.debug("摘键盘钩子失败", exc_info=True)
        self._hook_ok = False
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.join(timeout=1.5)

    # ------------------------------------------------------------------ 开关
    def set_enabled(self, enabled: bool, *, release: bool = True) -> None:
        """开关转发。**关掉时先抬键再停吞**（§8.5 的"关闭 = 立刻停止吞键"）。

        打开时如果还没装钩子会顺手装上 —— 否则"已启用但 worker 没跑"的状态下
        按键会被吞进队列、一帧都发不出去（这是最坏的一种失败：静默丢键）。
        """
        enabled = bool(enabled)
        if enabled and self._worker is None:
            self.start()
            if not self._hook_ok:
                log.error("键盘转发没有打开：%s", self._error or "钩子不可用")
                with self._lock:
                    self._enabled = False
                return
        with self._lock:
            if enabled == self._enabled:
                return
            if not enabled and release:
                self._release_all()
            self._enabled = enabled
            self._target = self.local_host if not enabled else self._target
        self._bump_epoch()
        self._push_focus()
        log.info("键盘转发 → %s", "开" if enabled else "关（按键留在本机）")

    # ------------------------------------------------------------------ 焦点
    def on_focus(self, target_host: str = "", *, is_self: bool = False) -> int:
        """画面切走了（`app._after_screen_switch` 调）。**epoch 由本端推进**。

        `target_host` 是"画面现在在哪台"。等于本机 ⇒ 停止转发；否则开始转发。
        切换时把按着的键全抬掉 —— 专治"按住 Shift 切屏，Mac 上 Shift 永远卡着"。
        """
        del is_self
        host = (target_host or "").strip()
        if not host:
            return 0
        with self._lock:
            released = self._release_all()
            before = self._target
            self._target = self.local_host if host == self.local_host else self.peer_host
            changed = self._target != before
        epoch = self._bump_epoch()
        self._push_focus()
        if changed:
            # 这条日志以前没有，2026-11-07 排查"切过去键盘没反应"时吃了大亏：
            # 日志里只有「显示器直报 → mac」，看不出键盘端点到底有没有跟上。
            log.info(
                "键盘焦点 → %s（画面在 %s，epoch=%d，%s）",
                self._target, host, epoch,
                "开始转发按键" if self.forwarding else "按键留在本机",
            )
        if released:
            log.debug("切屏（→%s）释放 %d 个按下的键", host, released)
        return released

    def _bump_epoch(self) -> int:
        """推进 epoch。**只有捕获端能推进**（谁决定按键去向，谁定 epoch）。"""
        with self._lock:
            self._epoch += 1
            return self._epoch

    def _push_focus(self) -> None:
        """广播一次焦点（带 epoch + CapsLock），对端据此换号并抬掉残留。"""
        engine = self._engine
        if engine is None:
            return
        target = self.peer_host if self.forwarding else self.local_host
        header = {
            "e": int(self._epoch),
            "target": target,
            "caps": 1 if self._caps else 0,
        }
        try:
            engine.send_focus(header)
        except Exception:  # noqa: BLE001 - 广播失败不能影响切屏
            log.debug("焦点广播失败", exc_info=True)

    # ------------------------------------------------------------------ 钩子回调
    def on_hook_event(self, vk: int, down: bool, kb: KBDLLHOOKSTRUCT) -> bool:
        """钩子回调。返回 True = 吞掉。**这里只做判断和入队，绝不做 I/O。**"""
        if kb is not None and getattr(kb, "injected", False):
            return False          # 自己注入的事件不路由（防回环）
        # 上一次被我们吃掉的键（热键 / 紧急组合）：抬起也吃掉，别漏半下给 Windows
        if not down and self._swallow_up_vk is not None and self._swallow_up_vk == vk:
            self._swallow_up_vk = None
            return True

        usage = usage_for_vk(vk)
        if usage is None:
            if down:
                self.stats["dropped_unmapped"] += 1
            return False

        # ---- 紧急停止组合 ----
        # **必须吃掉这个键**：它的修饰键（Ctrl/Alt/Shift）在转发期间已经被吞给 Mac 了，
        # Windows 根本不知道它们按着 —— 放行的话就是一个光秃秃的 `k` 打进了前台窗口。
        if down and self._matches(vk, usage, self.release_vk, self.release_mods):
            log.warning("紧急停止组合命中，键盘转发放手")
            # **先算"吞不吞"再停转发**：`_trigger_release()` 会把 forwarding 变成 False，
            # 之后再问就永远得到 False —— 那个 `k` 就会漏给 Windows。
            swallow = self._should_swallow_whitelist()
            self._swallow_up_vk = vk if swallow else None
            self._trigger_release()
            return swallow

        if usage in MODIFIER_USAGES or usage == 0x39:
            self._track_local_state(usage, down)

        # ---- 切屏热键 ----
        # 这里**不能**简单"放行给 Windows"：热键是 Alt+Space，而 Alt 在转发期间已经被
        # 我们吞给 Mac 了，Windows 看不到 Alt 按下 —— RegisterHotKey / 钩子后端都不会
        # 触发，用户按热键毫无反应，等于**再也切不回来**（§8.7 最怕的那件事）。
        # 所以转发期间由捕获端自己认这个组合并直接回调，按键一个都不外泄。
        if down and self._matches(vk, usage, self.hotkey_vk, self.hotkey_mods):
            if not self.forwarding:
                return False              # 没在转发：交给系统热键后端，行为不变
            self._swallow_up_vk = vk
            self._fire_hotkey()
            return self._should_swallow_whitelist()

        if not self._gate_open():
            self.stats["passed"] += 1
            return False

        mods = self._local_mods_bitmap()
        if usage in MODIFIER_USAGES and down:
            mods |= USAGE_TO_MOD_BIT.get(usage, 0)
        if not self._enqueue_key(usage, down, mods):
            self.stats["passed"] += 1
            return False
        with self._lock:
            self._down.apply(KeyEvent(usage=usage, down=down, mods=mods))
        self.stats["keys"] += 1
        self.stats["swallowed"] += 1
        return True

    def _should_swallow_whitelist(self) -> bool:
        """白名单键该不该吞。**只要在转发中就必须吞**（见上面两段的理由），
        不在转发中一律放行，交回系统热键后端，行为与没装键盘桥时一致。"""
        return bool(self.forwarding)

    def _fire_hotkey(self) -> None:
        """触发切屏。**另起线程**：钩子回调里做 DDC Set 会卡住整条输入链。"""
        callback = self._on_hotkey
        if callback is None:
            return
        threading.Thread(target=self._run_callback, args=(callback, "切屏热键"),
                         name="keyboard-hotkey", daemon=True).start()

    @staticmethod
    def _run_callback(callback: Callable[[], None], what: str) -> None:
        try:
            callback()
        except Exception:  # noqa: BLE001 - 回调炸了不能把键盘留在吞键态
            log.exception("%s回调失败", what)

    def _matches(self, vk: int, usage: int, want_vk: int | None, want_mods: tuple[str, ...]) -> bool:
        if want_vk is None:
            return False
        if vk != want_vk and usage_for_vk(want_vk) != usage:
            return False
        for mod in want_mods:
            vks = _MOD_VKS.get(_MOD_USAGE_BY_NAME.get(mod, -1))
            if not vks:
                # 不认识的修饰键名：按"满足"处理 —— 白名单宁可多放行，也不能漏
                # （漏了就是热键被转发/被吞，用户切不回来）
                continue
            if not any(self._mods_reader(v) for v in vks):
                return False
        return True

    def _track_local_state(self, usage: int, down: bool) -> None:
        """维护"本机现在按着哪些修饰键"+ CapsLock 锁定态（白名单判断要用）。"""
        if usage == 0x39:            # CapsLock
            if down:
                self._caps = not self._caps
            return
        with self._lock:
            if down:
                self._local_mods.add(usage)
            else:
                self._local_mods.discard(usage)

    def _local_mods_bitmap(self) -> int:
        bits = 0
        with self._lock:
            for usage in self._local_mods:
                bits |= USAGE_TO_MOD_BIT.get(usage, 0)
        return bits

    # ------------------------------------------------------------------ 闸门
    def _gate_open(self) -> bool:
        """闸门全绿才允许吞（§8.5）。任何一条不满足就放行。"""
        reason = self._gate_block_reason()
        if reason:
            self._note_gate_block(reason)
            return False
        self._note_gate_block("")
        return True

    def _gate_block_reason(self) -> str:
        """第一条不满足的闸门条件（空串 = 全绿）。**只读内存，不做 I/O。**

        拆成"返回原因"而不是一串 `return False`，是为了让现场日志能一句话说清
        「为什么这个键没被转发」—— 以前闸门失败是完全静默的，两端日志都干净，
        只能靠猜（2026-11-07 就是这么卡住的）。
        """
        if not self.forwarding:
            return "不在转发中"
        if not self._hook_ok:
            return "键盘钩子未就绪"
        engine = self._engine
        if engine is None:
            return "同步引擎未挂载"
        try:
            if not bool(getattr(engine, "switch_ready", False)):
                return "链路未就绪（未连接 / 未配对）"
        except Exception:  # noqa: BLE001
            return "链路未就绪（读取失败）"
        # 第五条（2026-11-07）：**对端接不住键就绝不吞**。对端关着开关、没给
        # 辅助功能授权、没装 pyobjc 时，吞下去等于把按键丢进黑洞。
        # None = 问不出来（对端版本旧 / 不是注入端），此时保持老行为不额外拦。
        probe = getattr(engine, "peer_keyboard_ready", None)
        if callable(probe):
            try:
                ready = probe()
            except Exception:  # noqa: BLE001
                ready = None
            if ready is False:
                return "对端键盘注入未就绪（未开开关 / 未授权）"
        if self._clock() - self._last_ok_at > LINK_FRESH_S:
            return "链路不活（300ms 内没有成功发送）"
        return ""

    def _note_gate_block(self, reason: str) -> None:
        """闸门原因变化时记一条 INFO（同一个原因 10 秒内不重复刷）。

        钩子线程里调用，所以只做一次比较 + 最多一次 log。
        """
        if reason == self._gate_reason:
            return
        now = self._clock()
        if reason and now - self._gate_reason_at < 10.0:
            return
        self._gate_reason = reason
        self._gate_reason_at = now
        if reason:
            log.info("键盘不转发：%s（按键已放行给本机）", reason)
        else:
            log.info("键盘闸门全绿：开始转发按键 → %s", self.peer_host)

    # ------------------------------------------------------------------ 发送
    def _next_seq(self) -> int:
        """推进序号。**必须串行**：钩子线程与界面线程（`on_focus`）都会推进，
        交错会让对端看到 seq 回退 —— 那帧会被当成过期直接丢掉。"""
        with self._lock:
            self._seq += 1
            return self._seq

    def _enqueue_key(self, usage: int, down: bool, mods: int) -> bool:
        seq = self._next_seq()
        header = {
            "e": int(self._epoch), "s": int(seq), "u": int(usage),
            "d": 1 if down else 0, "m": int(mods) & 0xFF,
            "t": int(time.monotonic() * 1000) & 0x7FFFFFFF,
        }
        try:
            self._queue.put_nowait(("key", header))
        except queue.Full:
            self.stats["dropped_queue"] += 1
            return False
        return True

    def _enqueue_state(self) -> None:
        seq = self._next_seq()
        header = {
            "e": int(self._epoch), "s": int(seq),
            "down": self._down.bitmap(),
            "caps": 1 if self._caps else 0,
        }
        if self.last_rtt_ms is not None:
            header["rtt"] = round(float(self.last_rtt_ms), 2)
        try:
            self._queue.put_nowait(("state", header))
        except queue.Full:
            pass                     # 心跳丢了不影响安全，下一拍再发

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                kind, header = self._queue.get(timeout=self._interval)
            except queue.Empty:
                self._enqueue_state()
                continue
            self._send_item(kind, header)

    def _send_item(self, kind: str, header: dict) -> bool:
        """真正把一帧发出去。**只在 worker 线程（或测试）里调。**"""
        engine = self._engine
        if engine is None:
            return False
        try:
            if kind == "key":
                ok = bool(engine.send_key(header))
            else:
                ok = bool(engine.send_key_state(header))
                self.stats["heartbeats"] += 1
        except Exception:  # noqa: BLE001 - 发送异常按失败处理
            log.debug("键盘帧发送异常", exc_info=True)
            ok = False
        if ok:
            self._last_ok_at = self._clock()
            if kind == "key":
                self._note_first_key()
            return True
        self.stats["send_failures"] += 1
        # 发送失败以前完全静默（只进 stats），现场就是"键被吞了但对面没反应"。
        # 5 秒一条，够定位又不刷屏。
        now = self._clock()
        if now - self._last_fail_log_at >= 5.0:
            self._last_fail_log_at = now
            log.warning(
                "键盘帧发送失败（累计 %d 次）：链路可能已断，按键已改回放行本机",
                self.stats["send_failures"],
            )
        # 送不出去就别再囤：这些键已经吞掉了，继续排队只会让状态更旧
        self._drain()
        return False

    def _note_first_key(self) -> None:
        """本 epoch 第一个成功发出的按键记一条 INFO。

        这是「转发真的通了」的唯一正面证据：以前成功路径一个日志都没有，
        出问题时无法区分"根本没转发"和"转发了但对面没注入"。
        """
        epoch = int(self._epoch)
        if self._first_key_logged_epoch == epoch:
            return
        self._first_key_logged_epoch = epoch
        log.info("键盘按键已开始转发 → %s（epoch=%d）", self.peer_host, epoch)

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # ------------------------------------------------------------------ 抬键
    def _release_all(self) -> int:
        """把"转发出去、还按着"的键逐个抬起。返回抬起个数。"""
        with self._lock:
            events = self._down.release_all()
        if not events:
            return 0
        sent = 0
        for ev in events:
            seq = self._next_seq()
            header = {
                "e": int(self._epoch), "s": int(seq), "u": int(ev.usage),
                "d": 0, "m": int(ev.mods) & 0xFF,
            }
            try:
                self._queue.put_nowait(("key", header))
                sent += 1
            except queue.Full:
                break
        self.stats["released"] += sent
        return sent

    def _trigger_release(self) -> None:
        """紧急停止：关掉转发并把决定权交回调用方（落盘 + 刷界面）。"""
        self.set_enabled(False)
        callback = self._on_release_request
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - 回调炸了也不能把键盘留在吞键态
            log.exception("紧急停止回调失败")

    # ------------------------------------------------------------------ RTT
    def _rtt_loop(self) -> None:
        """链路往返：借现成的 `T_STATE` 请求/回执量一次（不新增协议帧）。

        慢速独立线程，间隔 5s。**不要放进心跳线程** —— 它最多阻塞 1s，
        会把 100ms 的心跳拖出 300ms 的闸门窗口，表现为"每 5 秒吞键断一下"。
        """
        probe = self._rtt_probe
        if probe is None:
            return
        while not self._stop.wait(self._rtt_interval):
            if not self._gate_open():
                continue
            started = time.monotonic()
            try:
                answered = probe()
            except Exception:  # noqa: BLE001
                continue
            if answered is None:
                continue
            elapsed = (time.monotonic() - started) * 1000.0
            self.last_rtt_ms = elapsed
            self.stats["rtt_samples"] += 1


__all__ = ["LINK_FRESH_S", "QUEUE_MAX", "KeyboardCapture"]
