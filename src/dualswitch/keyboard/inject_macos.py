"""macOS 按键注入：`CGEventPost(kCGHIDEventTap, …)`。

**全项目唯一碰 Quartz 的地方**，而且刻意做成懒加载：

* import 期不碰系统 API —— 否则 Windows 端跑全量测试会在收集阶段就炸
  （同 `clipboard/macos.py` 的处理方式）。
* 允许注入假实现（`MacInjector(quartz=FakeQuartz())`），纯逻辑用例因此
  能在两端都真跑，不需要 `_macos` 后缀的平台门禁。

三个已知边界（§10.1，都是"只能规避"那一档）：

1. **锁屏 / 登录窗**：注入会被系统丢弃，密码打不进去 → 用 MacBook 自带键盘。
2. **安全输入（密码框、终端"安全键盘输入"）**：它拦的是**读取**（event tap），
   不拦写入，所以注入理论可用 —— 待 `--probe` 实测确认。
3. **辅助功能授权**：没有 `AXIsProcessTrusted()` 就一句话都注入不进去，
   必须显式报错，不能静默失败。
"""

from __future__ import annotations

import logging
import sys
import threading

from .keymap import FLAG_ALPHA_SHIFT

log = logging.getLogger(__name__)


class MacInjector:
    """把 (virtual keycode, 按下/抬起, flags) 变成真实的系统按键。"""

    def __init__(self, quartz=None) -> None:
        self._q = quartz            # None = 首次使用时再 import（见 _module）
        self._source = None
        self._lock = threading.Lock()
        self._trusted: bool | None = None
        self.injected = 0
        self.failed = 0
        self.last_error = ""

    # ------------------------------------------------------------------ 就绪
    def _module(self):
        """拿到 Quartz 模块。返回 None 表示这条路走不通。"""
        if self._q is not None:
            return self._q or None      # False = 上次已经失败过，别再反复 import
        if sys.platform != "darwin":
            self.last_error = "非 macOS"
            return None
        try:
            import Quartz  # noqa: PLC0415 - 故意延迟：import 期不许碰系统 API

            self._q = Quartz
            return Quartz
        except Exception as exc:  # noqa: BLE001 - 缺 pyobjc 是正常情况
            self.last_error = f"pyobjc 的 Quartz 不可用：{type(exc).__name__}: {exc}"
            log.info("按键注入不可用：%s", self.last_error)
            self._q = False         # 记住失败，别每次都重试 import
            return None

    def available(self) -> tuple[bool, str]:
        """能不能注入。不可用是正常状态（Windows 端、没装 pyobjc），调用方静默跳过。"""
        q = self._module()
        if q is None:
            return False, self.last_error or "非 macOS 或 Quartz 不可用"
        return True, ""

    def trusted(self) -> bool | None:
        """能不能往系统里投递事件。None = 查不出来（**不许**当成"已授权"）。

        ⚠️ 2026-11-06 实测：这台机器的 pyobjc 里**没有** `AXIsProcessTrusted`
        （那是 HIServices 的符号，Quartz 模块未必导出），查它会 AttributeError，
        被兜底吞掉后表现成"一切正常、就是打不出字"。正确的是
        `CGPreflightPostEventAccess()` —— macOS 10.15+ 专门用来问
        "我能不能投递输入事件"，不弹窗、不阻塞。
        """
        if self._trusted is not None:
            return self._trusted
        q = self._module()
        if q is None:
            return None
        ok: bool | None = None
        try:
            preflight = getattr(q, "CGPreflightPostEventAccess", None)
            if callable(preflight):
                ok = bool(preflight())
            else:
                legacy = getattr(q, "AXIsProcessTrusted", None)
                if callable(legacy):
                    ok = bool(legacy())
        except Exception as exc:  # noqa: BLE001
            log.debug("查输入投递权限失败：%s", exc)
            return None
        if ok is None:
            log.debug("这个 pyobjc 版本没有权限查询接口，无法确认授权状态")
            return None
        self._trusted = ok
        if not ok:
            log.warning(
                "没有「辅助功能」授权，按键注入不会生效："
                "系统设置 → 隐私与安全性 → 辅助功能"
            )
        return ok

    def request_access(self) -> bool | None:
        """弹一次系统授权框（用户点了之后就永久有效）。仅工具 / 首次启动用。"""
        q = self._module()
        if q is None:
            return None
        try:
            fn = getattr(q, "CGRequestPostEventAccess", None)
            if not callable(fn):
                return None
            self._trusted = bool(fn())
            return self._trusted
        except Exception as exc:  # noqa: BLE001
            log.debug("申请输入投递权限失败：%s", exc)
            return None

    def _ensure_source(self, q):
        if self._source is not None:
            return self._source
        try:
            self._source = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        except Exception as exc:  # noqa: BLE001
            log.debug("CGEventSourceCreate 失败：%s", exc)
            self._source = None
        return self._source

    # ------------------------------------------------------------------ 注入
    def inject(self, vk: int, down: bool, flags: int = 0) -> bool:
        """注入一次按下/抬起。**任何异常都不外抛**，只记数。"""
        q = self._module()
        if q is None:
            return False
        with self._lock:
            try:
                source = self._ensure_source(q)
                event = q.CGEventCreateKeyboardEvent(source, int(vk), bool(down))
                if event is None:
                    self.failed += 1
                    self.last_error = f"CGEventCreateKeyboardEvent 返回 None（vk=0x{vk:02X}）"
                    return False
                if flags:
                    q.CGEventSetFlags(event, int(flags))
                # kCGHIDEventTap = 最早的那个点，注入后和物理按键不可区分
                q.CGEventPost(q.kCGHIDEventTap, event)
                self.injected += 1
                return True
            except Exception as exc:  # noqa: BLE001 - 注入失败不能拖垮调用方
                self.failed += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                # 只在第一次打，日志量可控
                if self.failed == 1:
                    log.warning("按键注入异常（后续同类只记数）：%s", self.last_error)
                return False

    # ------------------------------------------------------------------ CapsLock
    def caps_lock_on(self) -> bool | None:
        """读当前 CapsLock 是否开启。None = 读不到。"""
        q = self._module()
        if q is None:
            return None
        try:
            state = q.CGEventSourceFlagsState(q.kCGEventSourceStateCombinedSessionState)
            return bool(int(state) & int(getattr(q, "kCGEventFlagMaskAlphaShift", FLAG_ALPHA_SHIFT)))
        except Exception as exc:  # noqa: BLE001
            log.debug("读 CapsLock 失败：%s", exc)
            return None

    def set_caps_lock(self, on: bool) -> bool:
        """把 CapsLock 拨到指定状态。返回是否**发起**了动作（读不到状态时不动手）。"""
        current = self.caps_lock_on()
        if current is None:
            log.debug("读不到 CapsLock 状态，跳过对齐")
            return False
        if current == bool(on):
            return False
        from .keymap import kVK_CapsLock

        self.inject(kVK_CapsLock, True)
        self.inject(kVK_CapsLock, False)
        log.info("CapsLock 对齐 → %s", "开" if on else "关")
        return True

    def stats(self) -> dict[str, int | str]:
        return {
            "injected": self.injected,
            "failed": self.failed,
            "last_error": self.last_error,
        }


def secure_input_enabled() -> bool | None:
    """系统当前是否开着「安全键盘输入」（密码框、终端的安全键盘输入）。None = 查不出。

    pyobjc 的 `Quartz` 里没有 `IsSecureEventInputEnabled`（实测 2026-11-06），
    它住在 Carbon 里，只能用 ctypes 直接取。
    """
    try:
        import ctypes

        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
        return bool(carbon.IsSecureEventInputEnabled())
    except Exception as exc:  # noqa: BLE001
        log.debug("查安全输入状态失败：%s", exc)
        return None


__all__ = ["MacInjector", "secure_input_enabled"]
