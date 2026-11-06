"""画面不在本机时，把「不归本机」的外接屏从 WindowServer 里停掉（macOS）。

## 为什么要做

显示器输入端切到另一台机器之后，**USB-C / DP 链路还在**：macOS 照样把这块屏当
active display，WindowServer 继续为它合成、继续扫描输出，GPU 与显示引擎白烧。
2026-11-06 在这台 M3 + Q2790R3 上实测（画面在 Windows、Mac 只见桌面，`outputs/measure_ab.py`）：

| 状态 | 系统 CPU（10 核，5s 均值 ×3） |
|---|---|
| 外接屏 active | 23.4% |
| 外接屏停用 | 19.0% |

差 ≈4.4 个百分点（≈0.44 个核）纯属白烧，GPU/显示引擎的功耗还没算进去 ——
用户侧的表现就是「Mac 没干活却很快发烫」。

## 怎么做的

调用 CoreGraphics 的 `CGSConfigureDisplayEnabled`（私有 API，等价于系统设置里
把这块屏「停用」），**只停用、不拔线**：

* 停用后 `CGGetActiveDisplayList` / `CGGetOnlineDisplayList` 里都没有它，
  WindowServer 不再为它建 framebuffer（实测 `CGSGetDisplayList` 仍能列出 1×1 的桩，
  所以自愈时还找得到 id）；
* DDC 读写**不受影响**（I2C 走的是接口，不是 WindowServer），所以「画面回来了没有」
  照样读得到 —— 这也是恢复的判据；
* 重新启用后 mode / origin 原样回来（本机实测 2560×1440@75、origin −2560,−1440）。

## 安全底线

这是私有 API，用错会留下「外接屏黑着但没人能叫醒它」的状态，所以：

1. **只停外接屏**：内建屏（`CGDisplayIsBuiltin`）和主屏（`CGDisplayIsMain`）永不动；
2. **只在自己确认「画面在对面」时停**，读不到（None / unknown）一律恢复；
3. **切回本机前先恢复**：`before_switch_to_local()` 在发 DDC Set 之前调用，
   保证画面落到一块活着的输出上；
4. **退出即恢复**：`stop()` 一定恢复；崩溃时把 id 记进 `display-power.json`，
   下次启动 `restore_pending()` 自愈；
5. 命令行兜底：`dualswitch display reset` 无条件恢复所有被停用的外接屏。
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

#: 被停用的显示器 id 记在这里，供崩溃后自愈
STATE_FILENAME = "display-power.json"

#: CoreGraphics 私有接口的配置作用域：kCGConfigureForSession。
#: 不用 Permanently —— 不能让「停用」写进显示设置活过这次登录。
_CONFIGURE_FOR_SESSION = 1


# --------------------------------------------------------------------------- #
# 平台层（darwin 才有内容）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DisplayInfo:
    id: int
    active: bool
    online: bool
    builtin: bool
    main: bool
    width: int
    height: int

    @property
    def disabled(self) -> bool:
        """被 `CGSConfigureDisplayEnabled(False)` 停用的屏：在线列表里没有它。"""
        return not self.active and not self.online

    def label(self) -> str:
        kind = "内建" if self.builtin else "外接"
        state = "在用" if self.active else ("停用" if self.disabled else "离线")
        return f"#{self.id} {kind} {self.width}×{self.height} {state}"


_cg: Any = None
_cg_tried = False


def _cg_lib() -> Any:
    """CoreGraphics（含私有 CGS* 符号）。拿不到就返回 None。"""
    global _cg, _cg_tried
    if _cg_tried:
        return _cg
    _cg_tried = True
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        lib.CGSGetDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.CGSGetDisplayList.restype = ctypes.c_int32
        lib.CGSBeginDisplayConfiguration.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.CGSBeginDisplayConfiguration.restype = ctypes.c_int32
        lib.CGSCompleteDisplayConfiguration.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        lib.CGSCompleteDisplayConfiguration.restype = ctypes.c_int32
        lib.CGSCancelDisplayConfiguration.argtypes = [ctypes.c_void_p]
        lib.CGSCancelDisplayConfiguration.restype = ctypes.c_int32
        lib.CGSConfigureDisplayEnabled.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_bool]
        lib.CGSConfigureDisplayEnabled.restype = ctypes.c_int32
        _cg = lib
    except Exception as exc:  # noqa: BLE001 - 缺符号就整体降级
        log.debug("CoreGraphics 私有接口不可用：%s", exc)
        _cg = None
    return _cg


def available() -> tuple[bool, str]:
    """能不能用。返回 (可用, 原因)。"""
    if sys.platform != "darwin":
        return False, "只有 macOS 需要（也才能）停用外接屏"
    if _cg_lib() is None:
        return False, "CoreGraphics 私有接口不可用"
    try:
        import Quartz  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"缺 pyobjc/Quartz：{exc}"
    return True, ""


def list_displays() -> list[DisplayInfo]:
    """列出**所有**显示器，含被停用的（`CGSGetDisplayList` 会给 1×1 的桩）。"""
    lib = _cg_lib()
    if lib is None:
        return []
    import Quartz

    buf = (ctypes.c_uint32 * 16)()
    count = ctypes.c_uint32(0)
    rc = lib.CGSGetDisplayList(16, buf, ctypes.byref(count))
    if rc != 0:
        raise RuntimeError(f"CGSGetDisplayList 失败：{rc}")
    out: list[DisplayInfo] = []
    for i in range(count.value):
        did = int(buf[i])
        try:
            out.append(
                DisplayInfo(
                    id=did,
                    active=bool(Quartz.CGDisplayIsActive(did)),
                    online=bool(Quartz.CGDisplayIsOnline(did)),
                    builtin=bool(Quartz.CGDisplayIsBuiltin(did)),
                    main=bool(Quartz.CGDisplayIsMain(did)),
                    width=int(Quartz.CGDisplayPixelsWide(did)),
                    height=int(Quartz.CGDisplayPixelsHigh(did)),
                )
            )
        except Exception:  # noqa: BLE001 - 单个 id 失效不影响其它
            continue
    return out


def set_enabled(display_id: int, enabled: bool) -> None:
    """停用 / 启用一块屏（等价于系统设置里勾掉「使用此显示器」）。

    没生效就抛 `RuntimeError` —— 调用方要知道「以为停了其实没停」。
    """
    lib = _cg_lib()
    if lib is None:
        raise RuntimeError("CoreGraphics 私有接口不可用")
    ref = ctypes.c_void_p()
    rc = lib.CGSBeginDisplayConfiguration(ctypes.byref(ref))
    if rc != 0 or not ref.value:
        raise RuntimeError(f"CGSBeginDisplayConfiguration 失败：{rc}")
    completed = False
    try:
        rc = lib.CGSConfigureDisplayEnabled(ref, int(display_id), bool(enabled))
        if rc != 0:
            raise RuntimeError(f"CGSConfigureDisplayEnabled(#{display_id}, {enabled}) 失败：{rc}")
        rc = lib.CGSCompleteDisplayConfiguration(ref, _CONFIGURE_FOR_SESSION)
        completed = True
        if rc != 0:
            raise RuntimeError(f"CGSCompleteDisplayConfiguration 失败：{rc}")
    finally:
        if not completed:
            lib.CGSCancelDisplayConfiguration(ref)


def disable_external_displays(record: bool = True) -> list[int]:
    """停用所有「在用」的外接屏，返回实际停掉的 id。

    内建屏与主屏永远不动：主屏被停掉会把整个界面挪走。
    `record=True` 会把 id 记进状态文件 —— 进程被 kill 时靠它自愈。
    """
    displays = list_displays()
    active = [d for d in displays if d.active]
    targets = [d for d in active if not d.builtin and not d.main]
    if targets and len(targets) >= len(active):
        # 只有这一块屏在输出（Mac mini + 单显示器）：停掉它等于把本机屏幕拔了，
        # 之后连「画面回来了」都没界面可看。宁可多烧一点电。
        log.info("跳过停用外接屏：停掉之后本机就没有任何可用输出了")
        return []
    done: list[int] = []
    for d in targets:
        try:
            set_enabled(d.id, False)
            done.append(d.id)
            log.info("外接屏已停用：%s（画面不在本机，不再为它合成/扫描）", d.label())
        except Exception as exc:  # noqa: BLE001 - 个别屏失败不影响其它
            log.warning("停用外接屏 %s 失败：%s", d.label(), exc)
    if done and record:
        _write_state(done)
    return done


def enable_displays(ids: list[int]) -> list[int]:
    """按 id 恢复显示。已经死掉的 id 会被跳过（`CGSGetDisplayList` 里找不到就不碰）。"""
    known = {d.id: d for d in list_displays()}
    done: list[int] = []
    for did in ids:
        info = known.get(int(did))
        if info is None:
            log.info("恢复显示跳过 #%s：这个 id 已经不存在了", did)
            continue
        if info.builtin:
            continue
        if info.active:
            # 已经在用：再 enable 一次会返回 1001，而且会让「恢复成功」判不出来
            done.append(info.id)
            continue
        try:
            set_enabled(info.id, True)
            done.append(info.id)
            # 注意别用 info.label()：那是启用**前**的快照，会打成「1×1 停用」
            log.info("外接屏已恢复输出：#%d（%s）", info.id, "内建" if info.builtin else "外接")
        except Exception as exc:  # noqa: BLE001
            log.warning("恢复外接屏 %s 失败：%s", info.label(), exc)
    return done


def _state_path() -> Path:
    from .config import config_dir

    return config_dir() / STATE_FILENAME


def _read_state(path: Path | None = None) -> dict[str, Any]:
    target = path or _state_path()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(ids: list[int], path: Path | None = None) -> None:
    target = path or _state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not ids:
            target.unlink(missing_ok=True)
            return
        payload = {"ids": [int(i) for i in ids], "at": time.time()}
        tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        log.warning("记录停用状态失败：%s", exc)


def restore_pending(path: Path | None = None) -> list[int]:
    """自愈：把上次进程留下的「停用」状态恢复掉（崩溃 / 被 kill 时只有这条路）。"""
    state = _read_state(path)
    ids = [int(i) for i in (state.get("ids") or [])]
    if not ids:
        return []
    log.warning("发现上次残留的停用显示器 %s，正在恢复", ids)
    done = enable_displays(ids)
    _write_state([i for i in ids if i not in done], path)
    return done


# --------------------------------------------------------------------------- #
# 编排：什么时候停、什么时候恢复
# --------------------------------------------------------------------------- #
class AwayDisplayPower:
    """跟着「画面归属」开关外接屏。**所有平台副作用都可注入**，方便单测。

    `read_host()` 返回 `"mac"/"windows"/None`，`local_host` 是本机身份。
    停 / 启的默认实现是上面那层 CGS 调用；测试时换成假函数即可。
    """

    IDLE = "idle"        # 什么都不做
    ARMED = "armed"      # 画面刚走，等着到点再停
    OFF = "off"          # 已经停了，盯着画面什么时候回来

    def __init__(
        self,
        *,
        enabled: bool,
        off_delay_ms: int,
        probe_interval_ms: int,
        read_host: Callable[[], str | None],
        local_host: str,
        disable: Callable[[], list[int]] | None = None,
        enable: Callable[[list[int]], list[int]] | None = None,
        state_path: Path | None = None,
        available_check: Callable[[], tuple[bool, str]] | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._off_delay = max(0.0, off_delay_ms / 1000.0)
        self._probe = max(0.2, probe_interval_ms / 1000.0)
        self._read_host = read_host
        self._local = local_host
        self._other = "windows" if local_host == "mac" else "mac"
        self._disable = disable or disable_external_displays
        self._enable = enable or enable_displays
        self._state_path = state_path
        self._available = available_check or available
        # 停/启之后通知调用方（用来让 DDC 丢掉失效的接口句柄）
        self._on_change = on_change

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mode = self.IDLE
        self._deadline = 0.0
        self._off_ids: list[int] = []
        # 读不到的次数：连续失败说明屏真的没了（拔线 / 断电），要恢复，别留个黑屏
        self._read_misses = 0

    # ---- 查询（界面 / CLI 用） -------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def off_ids(self) -> list[int]:
        with self._lock:
            return list(self._off_ids)

    # ---- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        if not self._enabled or self._thread is not None:
            return
        ok, why = self._available()
        if not ok:
            log.info("外接屏省电未启用：%s", why)
            return
        restored = restore_pending(self._state_path)
        if restored:
            log.info("启动自愈：已恢复 %s", restored)
            self._notify_change()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="display-power", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
            self._thread = None
        self.restore_now("程序退出")

    # ---- 外部事件 ---------------------------------------------------------
    def on_source(self, host: str | None) -> None:
        """画面归属变了（由 `SwitchController` 在读到真值时调）。"""
        if not self._enabled or self._thread is None:
            return
        if host == self._local:
            self.restore_now("画面回到本机")
            return
        if host == self._other:
            with self._lock:
                if self._mode == self.OFF:
                    return                     # 已经停了，等着就行
                self._mode = self.ARMED
                self._deadline = time.monotonic() + self._off_delay
            self._wake.set()
            return
        # None / unknown：证明不了画面在对面，保守起见恢复
        self.restore_now("归属不明")

    def before_switch_to_local(self) -> bool:
        """切回本机之前调用：先让屏活过来，画面才有地方落。

        返回 True 表示「这次真的恢复了某块屏」。调用方要据此忽略枚举结果 ——
        刚恢复的屏在 `active_monitors()` 里还要过一拍才出现，拿它判「画面不在本机」
        会把一次正常的本地切换误路由成「请对端代发」（2026-11-06 实测过这个滞后）。
        """
        if not self._enabled:
            return False
        return bool(self.restore_now("准备切回本机"))

    def restore_now(self, why: str = "") -> list[int]:
        """无条件恢复（幂等）。内存里没记 id 时，靠状态文件兜底。"""
        with self._lock:
            ids = list(self._off_ids)
            self._off_ids = []
            self._mode = self.IDLE
            self._deadline = 0.0
            self._read_misses = 0
        if not ids:
            # 不是这一代进程停的（上一个进程崩了 / CLI 手动 `display off` 过），
            # 内存里当然没有 id —— 这时候唯一的信息源是状态文件。
            pending = restore_pending(self._state_path)
            if pending:
                log.info("外接屏已恢复输出（%s）", why or "-")
                self._notify_change()
            return pending
        try:
            done = self._enable(ids)
        except Exception as exc:  # noqa: BLE001 - 恢复失败不能再抛
            log.warning("恢复外接屏失败（%s）：%s", why, exc)
            return []
        _write_state([i for i in ids if i not in done], self._state_path)
        log.info("外接屏已恢复输出（%s）", why or "-")
        self._notify_change()
        self._wake.set()
        return done

    def _notify_change(self) -> None:
        """停/启之后让调用方刷新依赖（DDC 句柄缓存必须丢掉）。"""
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 - 通知失败不影响省电本身
            log.exception("外接屏状态变化的回调失败（已忽略）")

    # ---- 工作线程 ---------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                mode = self._mode
                deadline = self._deadline
            if mode == self.ARMED:
                remain = deadline - time.monotonic()
                if remain > 0:
                    self._wake.wait(min(0.5, remain))
                    self._wake.clear()
                    continue
                self._arm_fire()
                continue
            if mode == self.OFF:
                self._wake.wait(self._probe)
                self._wake.clear()
                if not self._stop.is_set():
                    self._poll()
                continue
            self._wake.wait(0.5)
            self._wake.clear()

    def _arm_fire(self) -> None:
        """到点了：再读一次确认画面确实不在本机，然后停。"""
        host = self._safe_read()
        if host != self._other:
            with self._lock:
                self._mode = self.IDLE
                self._deadline = 0.0
            if host == self._local:
                log.debug("画面已回到本机，取消停用外接屏")
            return
        try:
            ids = self._disable()
        except Exception as exc:  # noqa: BLE001
            log.warning("停用外接屏失败：%s", exc)
            with self._lock:
                self._mode = self.IDLE
            return
        with self._lock:
            self._off_ids = list(ids)
            self._mode = self.OFF if ids else self.IDLE
            self._deadline = 0.0
            self._read_misses = 0
        if ids:
            _write_state(ids, self._state_path)
            log.info("画面不在本机 → 已停用 %s 块外接屏：画面回来会自动恢复", len(ids))
            self._notify_change()

    def _poll(self) -> None:
        """停用期间盯着 DDC：画面一回来立刻恢复。"""
        host = self._safe_read()
        if host == self._local:
            self.restore_now("画面回到本机")
            return
        if host == self._other:
            self._read_misses = 0
            return
        # 读不到：可能是拔线 / 断电。连着几次都不行就恢复，别把屏留在停用态
        self._read_misses += 1
        if self._read_misses >= 5:
            log.warning("连续 %d 次读不到显示器输入，按「屏不在了」恢复输出", self._read_misses)
            self.restore_now("读不到显示器")

    def _safe_read(self) -> str | None:
        try:
            return self._read_host()
        except Exception as exc:  # noqa: BLE001
            log.debug("读画面归属失败：%s", exc)
            return None
