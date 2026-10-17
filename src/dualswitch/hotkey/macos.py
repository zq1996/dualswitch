"""macOS 全局热键后端：Quartz CGEventTap。

默认监听「右 Option + Space」：keyCode 61 是右 Option，49 是 Space。
CGEventTap 能区分左右修饰键，因此不需要像 Windows 那样退化。

依赖：pyobjc-framework-Quartz（pip install pyobjc-framework-Quartz）。
权限：必须在「系统设置 → 隐私与安全性 → 辅助功能」中勾选运行本程序的
      终端 / Python / 打包后的 .app，否则 CGEventTapCreate 会返回 None。
"""

from __future__ import annotations

import sys
import threading

from .base import Hotkey, HotkeyError, to_mac_keycode

if sys.platform != "darwin":  # pragma: no cover - 平台守卫
    raise ImportError("dualswitch.hotkey.macos 只能在 macOS 上导入")

KEYCODE_RIGHT_OPTION = 61

# CGEventFlags
FLAG_SHIFT = 0x00020000
FLAG_CONTROL = 0x00040000
FLAG_ALTERNATE = 0x00080000
FLAG_COMMAND = 0x00100000


def _import_quartz():
    try:
        import Quartz  # type: ignore
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise HotkeyError(
            "macOS 全局热键需要 pyobjc：请执行 "
            "pip install pyobjc-framework-Quartz pyobjc-framework-ApplicationServices"
        ) from exc
    return Quartz


def _accessibility_trusted(prompt: bool = False) -> bool:
    try:
        from ApplicationServices import (  # type: ignore
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except ImportError:  # pragma: no cover - 老版本 pyobjc
        return True
    try:
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:  # pragma: no cover
        return True


class QuartzEventTapBackend:
    name = "cgEventTap"

    def __init__(self, hotkey: Hotkey, callback, swallow: bool = True):
        self.hotkey = hotkey
        self.callback = callback
        self.swallow = swallow
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._run_loop = None
        self._tap = None
        self._ralt_down = False
        self._keycode = 0

    def describe(self) -> str:
        return self.hotkey.mac_describe()

    def start(self, timeout: float = 3.0) -> None:
        self._Quartz = _import_quartz()
        self._keycode = to_mac_keycode(self.hotkey.key)
        if not _accessibility_trusted(prompt=True):
            raise HotkeyError(
                "缺少「辅助功能」权限，无法监听全局按键。请到 系统设置 → 隐私与安全性 → 辅助功能 "
                "中勾选运行本程序的终端 / Python / dualswitch.app，然后重新启动本程序。"
            )
        self._thread = threading.Thread(target=self._run, name="hotkey-quartz", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.error:
            raise HotkeyError(self.error)

    # ------------------------------------------------------------ 内部
    def _flags_ok(self, flags: int) -> bool:
        for mod in self.hotkey.modifiers:
            if mod == "ralt" and not (flags & FLAG_ALTERNATE and self._ralt_down):
                return False
            if mod == "alt" and not (flags & FLAG_ALTERNATE):
                return False
            if mod == "ctrl" and not (flags & FLAG_CONTROL):
                return False
            if mod == "shift" and not (flags & FLAG_SHIFT):
                return False
            if mod == "win" and not (flags & FLAG_COMMAND):
                return False
        return True

    def _run(self) -> None:
        Q = self._Quartz

        def _callback(proxy, event_type, event, refcon):  # noqa: ANN001
            try:
                if event_type == Q.kCGEventFlagsChanged:
                    keycode = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
                    if keycode == KEYCODE_RIGHT_OPTION:
                        flags = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventFlags)
                        self._ralt_down = bool(flags & FLAG_ALTERNATE)
                    return event
                if event_type == Q.kCGEventKeyDown:
                    keycode = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
                    flags = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventFlags)
                    if keycode == self._keycode and self._flags_ok(flags):
                        try:
                            self.callback()
                        except Exception:
                            pass
                        return None if self.swallow else event
            except Exception:  # pragma: no cover - 回调绝不能中断 tap
                pass
            return event

        mask = (1 << Q.kCGEventKeyDown) | (1 << Q.kCGEventKeyUp) | (1 << Q.kCGEventFlagsChanged)
        tap = Q.CGEventTapCreate(
            Q.kCGSessionEventTap,
            Q.kCGHeadInsertEventTap,
            Q.kCGEventTapOptionDefault,
            mask,
            _callback,
            None,
        )
        if tap is None:
            self.error = (
                "CGEventTapCreate 返回空：通常是缺少辅助功能权限，或被安全软件拦截。"
                "请到 系统设置 → 隐私与安全性 → 辅助功能 授权后重启程序。"
            )
            self._ready.set()
            return

        self._tap = tap
        run_loop_source = Q.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._run_loop = Q.CFRunLoopGetCurrent()
        Q.CFRunLoopAddSource(self._run_loop, run_loop_source, Q.kCFRunLoopCommonModes)
        Q.CGEventTapEnable(tap, True)
        self._ready.set()
        try:
            Q.CFRunLoopRun()
        finally:
            Q.CGEventTapEnable(tap, False)

    def stop(self) -> None:
        Q = getattr(self, "_Quartz", None)
        if Q is not None and self._run_loop is not None:
            try:
                Q.CFRunLoopStop(self._run_loop)
            except Exception:  # pragma: no cover
                pass
        if self._thread:
            self._thread.join(timeout=1.5)
