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
2. **只在有依据说「画面在对面」时停**：自己发出的切换成功（`trusted=True`），或
   DDC 直读报「在对面」。两者都没有就恢复；
3. **切回本机前先恢复**：`before_switch_to_local()` 在发 DDC Set 之前调用，
   保证画面落到一块活着的输出上；
4. **退出即恢复**：`stop()` 一定恢复；崩溃时把 id 记进 `display-power.json`，
   下次启动 `restore_pending()` 自愈；
5. 命令行兜底：`dualswitch display reset` 无条件恢复所有被停用的外接屏。

## 停用期间怎么等画面回来（2026-11-14 改正）

**不能拿「读不到」当「屏没了」**：这块屏在画面跑到对面时，DDC 读通道本来就
可能不通（`ddc/avservice.py` 的句柄会随休眠/显示重配变哑），所以以前
「连续 5 次读不到就恢复」等于省电刚生效 7.5 秒就自己撤销（2026-11-13 21:58
实测，还撞上 enable 返回 1001 把屏留在停用态过夜）。

现在的判据，按可信度排：

* 读到本机 → 立刻恢复（唯一真值）；
* 被停用的屏**从 `CGSGetDisplayList` 里消失** → 拔线/断电 → 恢复；
* 以上都拿不到（读通道全哑）→ **保持停用**，只留一个安全阀：连续
  `blind_restore_s`（默认 15 分钟）都读不到就恢复一次，别把屏永久留在黑屏。
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
#: kCGConfigurePermanently：会话作用域被系统拒绝（1001）时的退路。
_CONFIGURE_FOR_PERMANENT = 0


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


_sl: Any = None
_sl_tried = False


def _sl_lib() -> Any:
    """SkyLight 里的同名私有接口。**启用显示器必须优先走它**（2026-11-14 踩坑）。

    用 `CGSConfigureDisplayEnabled` 重新启用一块被停用的屏时，
    `CGSCompleteDisplayConfiguration` 会稳定返回 1001（kCGErrorIllegalArgument），
    屏就再也回不来（用户当晚就是这么丢的扩展屏，最后靠拔插线才有一点救）；
    换成 SkyLight 的 `SLSConfigureDisplayEnabled` 同样的调用序列就能成功，
    屏和原来的排列（origin −1973,−1440）一起回来。
    """
    global _sl, _sl_tried
    if _sl_tried:
        return _sl
    _sl_tried = True
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.CDLL(
            "/System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/SkyLight"
        )
        lib.SLSConfigureDisplayEnabled.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_bool]
        lib.SLSConfigureDisplayEnabled.restype = ctypes.c_int32
        _sl = lib
    except Exception as exc:  # noqa: BLE001
        log.debug("SkyLight 私有接口不可用：%s", exc)
        _sl = None
    return _sl


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

    **启用优先走 SkyLight**：CoreGraphics 那条路在「重新启用」时会稳定返回
    1001，屏就醒不过来了（2026-11-14 用户因此丢了扩展屏）。停用两边都行，
    统一按「SLS → CGS」顺序试，谁成功用谁。
    """
    cg = _cg_lib()
    if cg is None:
        raise RuntimeError("CoreGraphics 私有接口不可用")
    sl = _sl_lib()

    errors: list[str] = []
    attempts = []
    if sl is not None:
        attempts.append(("SLS", sl.SLSConfigureDisplayEnabled))
    attempts.append(("CGS", cg.CGSConfigureDisplayEnabled))
    # 停用只用会话作用域：写成「永久」会活过这次登录，用户下次开机就少一块屏
    scopes = (_CONFIGURE_FOR_SESSION, _CONFIGURE_FOR_PERMANENT) if enabled else (
        _CONFIGURE_FOR_SESSION,
    )
    for name, configure in attempts:
        for scope in scopes:
            ref = ctypes.c_void_p()
            rc = cg.CGSBeginDisplayConfiguration(ctypes.byref(ref))
            if rc != 0 or not ref.value:
                errors.append(f"{name}/begin 失败：{rc}")
                break
            completed = False
            try:
                rc = configure(ref, int(display_id), bool(enabled))
                if rc != 0:
                    errors.append(f"{name}/configure({display_id}, {enabled}) = {rc}")
                    continue
                rc = cg.CGSCompleteDisplayConfiguration(ref, scope)
                completed = True
                if rc == 0:
                    return
                errors.append(f"{name}/complete(scope={scope}) = {rc}")
            finally:
                if not completed:
                    cg.CGSCancelDisplayConfiguration(ref)
    raise RuntimeError(
        f"CGSConfigureDisplayEnabled(#{display_id}, {enabled}) 失败："
        + ("; ".join(errors) or "没有可用的私有接口")
    )


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
            # 1001 = kCGSDisplayConfigInvalid：刚停用过/系统还在收尾时很常见，
            # 隔一拍再来一次基本就好（2026-11-13 21:58 就是因为没重试，屏留在停用态过夜）
            if "1001" in str(exc):
                time.sleep(0.3)
                try:
                    set_enabled(info.id, True)
                except Exception as exc2:  # noqa: BLE001
                    log.warning("恢复外接屏 %s 失败：%s", info.label(), exc2)
                    continue
                done.append(info.id)
                log.info("外接屏已恢复输出：#%d（重试成功）", info.id)
                continue
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


def restore_pending(
    path: Path | None = None,
    enable: Callable[[list[int]], list[int]] | None = None,
) -> list[int]:
    """自愈：把上次进程留下的「停用」状态恢复掉（崩溃 / 被 kill 时只有这条路）。

    `enable` 可注入（单测必须传，否则会用真实的私有 API 去动用户显示器）。
    """
    state = _read_state(path)
    ids = [int(i) for i in (state.get("ids") or [])]
    if not ids:
        return []
    log.warning("发现上次残留的停用显示器 %s，正在恢复", ids)
    done = (enable or enable_displays)(ids)
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

    #: 停用期间「读不到」多少次才去查一次 CGS 列表（0.4s/次 × 5 ≈ 2s）
    _MISS_PROBE_EVERY = 5
    #: 停用期间读不到的第几条才写一次日志（否则每 0.4s 一条，把日志刷满）
    _MISS_LOG_EVERY = 30

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
        list_ids: Callable[[], list[int]] | None = None,
        blind_restore_s: float = 900.0,
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
        self._list_ids = list_ids or (lambda: [d.id for d in list_displays()])
        self._blind_restore_s = max(0.0, float(blind_restore_s))
        # 停/启之后通知调用方（用来让 DDC 丢掉失效的接口句柄）
        self._on_change = on_change

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mode = self.IDLE
        self._deadline = 0.0
        self._off_ids: list[int] = []
        # 这次 ARMED 是「自己发出的切换」触发的（有依据），还是一句「听说画面在对面」
        self._trusted_arm = False
        # 读不到的次数 / 从什么时候开始读不到
        self._read_misses = 0
        self._blind_since = 0.0

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
        # 自愈跟「省电开关」无关：上一个进程（哪怕是开着省电的旧版本）可能把屏
        # 停在停用态。这一轮就算不省电，也得先把它救回来 —— 否则用户开机就少一块屏。
        restored = restore_pending(self._state_path, self._enable)
        if restored:
            log.info("启动自愈：已恢复 %s", restored)
            self._notify_change()
        if not self._enabled or self._thread is not None:
            return
        ok, why = self._available()
        if not ok:
            log.info("外接屏省电未启用：%s", why)
            return
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
    def on_source(self, host: str | None, trusted: bool = False) -> None:
        """画面归属变了（由 `SwitchController` 在读到真值时调）。

        `trusted=True` 表示这条归属有「硬依据」——本机刚发出一条成功切到对面的
        Set（或对端代发回了 ACK）。这种情况下即使 2.5 秒后 DDC 读不出来，也照停：
        这台显示器在画面跑到对面之后读通道经常不通（见模块注释），
        拿「读不到」去否决「我刚亲手发出去的命令」是本末倒置。
        """
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
                self._trusted_arm = self._trusted_arm or bool(trusted)
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
            self._trusted_arm = False
            self._read_misses = 0
            self._blind_since = 0.0
        if not ids:
            # 不是这一代进程停的（上一个进程崩了 / CLI 手动 `display off` 过），
            # 内存里当然没有 id —— 这时候唯一的信息源是状态文件。
            pending = restore_pending(self._state_path, self._enable)
            if pending:
                log.info("外接屏已恢复输出（%s）", why or "-")
                self._notify_change()
            return pending
        try:
            done = self._enable(ids)
        except Exception as exc:  # noqa: BLE001 - 恢复失败不能再抛
            log.warning("恢复外接屏失败（%s）：%s", why, exc)
            return []
        left = [i for i in ids if i not in done]
        _write_state(left, self._state_path)
        if done:
            log.info("外接屏已恢复输出（%s）：%s", why or "-", done)
        if left:
            # 这时候这块屏既不在系统里、也发不出 DDC：再点切换也是白点。
            # 必须给出人能照做的下一步，否则用户只能看着黑屏。
            log.error(
                "外接屏恢复失败 #%s（%s）：系统里已经没有这块屏的输出对象。"
                "请拔插一次 USB-C 线（或把显示器输入切走再切回），再运行 `dualswitch display reset`",
                left,
                why or "-",
            )
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
                self._wake.wait(self._probe_delay())
                self._wake.clear()
                if not self._stop.is_set():
                    self._poll()
                continue
            self._wake.wait(0.5)
            self._wake.clear()

    def _probe_delay(self) -> float:
        """停用期间的探测间隔：读不到就指数退避，别每 1.5 秒戳一次 I2C/IORegistry。

        停用之后这块屏的 DDC 基本就断了（`m1ddc` 连屏都枚举不到），硬按
        `probe_interval_ms` 轮询只会刷日志、漏句柄；退避到 30 秒既能及时发现
        「画面回来了」，又不会把 CPU 和日志吃掉。
        """
        misses = self._read_misses
        if misses <= 0:
            return self._probe
        factor = 2 ** min(5, misses // self._MISS_PROBE_EVERY)
        return min(self._probe * factor, 30.0)

    def _arm_fire(self) -> None:
        """到点了：读得到就按读到的校准，读不到就按「自己发出的那条切换」执行。"""
        host = self._safe_read()
        with self._lock:
            trusted = self._trusted_arm
        if host != self._other and not (host is None and trusted):
            with self._lock:
                self._mode = self.IDLE
                self._deadline = 0.0
                self._trusted_arm = False
            if host == self._local:
                log.debug("画面已回到本机，取消停用外接屏")
            else:
                # 读不到、也没有自己的切换作依据：宁可不省电，也别把用户在看的屏停掉
                log.info("跳过停用外接屏：画面归属读不到，本次也没有本机发出的切换作依据")
            return
        try:
            ids = self._disable()
        except Exception as exc:  # noqa: BLE001
            log.warning("停用外接屏失败：%s", exc)
            with self._lock:
                self._mode = self.IDLE
                self._trusted_arm = False
            return
        with self._lock:
            self._off_ids = list(ids)
            self._mode = self.OFF if ids else self.IDLE
            self._deadline = 0.0
            self._trusted_arm = False
            self._read_misses = 0
            self._blind_since = 0.0
        if ids:
            _write_state(ids, self._state_path)
            log.info(
                "画面不在本机 → 已停用 %s 块外接屏：画面回来会自动恢复（依据：%s）",
                len(ids),
                "DDC 直读" if host == self._other else "本机发出的切换",
            )
            self._notify_change()

    def _poll(self) -> None:
        """停用期间盯着画面什么时候回来。

        **读不到不等于屏没了**：这块屏在画面跑到对面时读通道常常不通。
        以前的「连续 5 次读不到就恢复」会让省电刚生效几秒就自我撤销
        （2026-11-13 21:58 实测）。现在的判据见模块注释：读不到就保持停用，
        只有「被停用的屏真从 CGS 列表消失」或安全阀超时才恢复。
        """
        host = self._safe_read()
        if host == self._local:
            self.restore_now("画面回到本机")
            return
        if host == self._other:
            self._read_misses = 0
            self._blind_since = 0.0
            return
        # 读不到：先只记账
        self._read_misses += 1
        if not self._blind_since:
            self._blind_since = time.monotonic()
        if self._read_misses % self._MISS_LOG_EVERY == 1:
            log.debug(
                "停用期间读不到画面归属（第 %d 次），保持停用：读通道不可用不是「屏没了」的证据",
                self._read_misses,
            )
        # 可信的「屏真没了」证据：被停用的屏从 CGS 列表里消失（拔线 / 断电）
        if self._read_misses >= self._MISS_PROBE_EVERY and not self._any_off_present():
            log.warning("被停用的外接屏已从显示器列表消失（拔线 / 断电），恢复输出")
            self.restore_now("被停用的屏已不在")
            return
        # 安全阀：读通道彻底哑了又一直挂着，给个上限，别把屏永久留在黑屏
        if self._blind_restore_s > 0 and time.monotonic() - self._blind_since >= self._blind_restore_s:
            log.warning(
                "连续 %.0f 秒读不到显示器输入，按「读通道不可用」恢复输出（安全阀）",
                time.monotonic() - self._blind_since,
            )
            self.restore_now("读通道长时间不可用")

    def _any_off_present(self) -> bool:
        """被我们停用的那几块屏，还在 CGS 的显示器列表里吗？

        停用后的屏会以 1×1 桩的形式留在列表里（见模块注释），所以「还在」
        说明只是我们把它关了，不是被拔了；枚举失败时也当成还在 —— 宁可继续
        省电，也别因为一次枚举异常把屏恢复成 active 白烧电。
        """
        ids = list(self._off_ids)
        if not ids:
            return True
        try:
            known = set(self._list_ids())
        except Exception:  # noqa: BLE001
            return True
        return any(i in known for i in ids)

    def _safe_read(self) -> str | None:
        try:
            return self._read_host()
        except Exception as exc:  # noqa: BLE001
            log.debug("读画面归属失败：%s", exc)
            return None
