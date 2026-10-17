"""macOS 全局热键后端：Quartz CGEventTap。

默认监听「右 Option + Space」：keyCode 61 是右 Option，49 是 Space。
CGEventTap 能区分左右修饰键，因此不需要像 Windows 那样退化。

依赖：pyobjc-framework-Quartz（pip install pyobjc-framework-Quartz）。
权限：必须在「系统设置 → 隐私与安全性 → 辅助功能」中勾选运行本程序的
      终端 / Python / 打包后的 .app，否则 CGEventTapCreate 会返回 None。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from .base import Hotkey, HotkeyError, to_mac_keycode

if sys.platform != "darwin":  # pragma: no cover - 平台守卫
    raise ImportError("dualswitch.hotkey.macos 只能在 macOS 上导入")

KEYCODE_RIGHT_OPTION = 61

# 「辅助功能」面板的深链接。macOS 13+ 用 settings 扩展 id，旧系统回落到老的 preference 锚点。
ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility"
)

# 终端类程序的 TERM_PROGRAM → 系统设置里显示的名字
_TERM_NAMES = {
    "Apple_Terminal": "终端",
    "iTerm.app": "iTerm",
    "WarpTerminal": "Warp",
    "vscode": "Visual Studio Code",
    "Hyper": "Hyper",
    "ghostty": "Ghostty",
    "WezTerm": "WezTerm",
    "Tabby": "Tabby",
}

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


_prompted_once = False


def _ax_trusted(prompt: bool = False) -> bool:
    """直接问系统「可信吗」，不做任何抑制。"""
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


def accessibility_granted(prompt: bool = False) -> bool:
    """当前进程是否已拿到「辅助功能」权限。

    prompt=True 时系统会弹一次授权对话框 —— **这一步很关键**：TCC 只会把
    「主动请求过」的可执行文件写进列表，所以不弹窗时用户在设置里根本找不到本应用。

    但一个进程里只弹**一次**：热键每次重建（改配置、录制结束、授权后自动重试、
    切回窗口重试）都会走到这里，不设限的话对话框会反复弹——
    「已授权还一直弹窗让我授权」的观感就是这么来的。要再弹一次走 `request_accessibility()`。
    """
    global _prompted_once
    if prompt and _prompted_once:
        prompt = False
    if prompt:
        _prompted_once = True
    return _ax_trusted(prompt=prompt)


def permission_target() -> str:
    """该去系统设置里勾选谁。

    · 打包后（PyInstaller）→ 「屏幕切换」。注意 TCC 列表里显示的是 CFBundleName
      `dualswitch`，中文名只在 Dock / 关于面板生效，所以提示里两个名字都给，
      否则用户会照着提示找不到那一行。
    · 从终端跑脚本     → 授权对象是**终端本身**（TCC 按"负责进程"归属），
                         这也是用户「找不到本应用」的原因
    """
    if getattr(sys, "frozen", False):
        return "屏幕切换（列表中显示为 dualswitch）"
    term = os.environ.get("TERM_PROGRAM")
    if term:
        return _TERM_NAMES.get(term, term)
    return Path(sys.executable).name


def open_accessibility_settings() -> None:
    """直接打开 系统设置 → 隐私与安全性 → 辅助功能。"""
    try:
        subprocess.Popen(["open", ACCESSIBILITY_PANE])
    except Exception:  # pragma: no cover
        pass


def request_accessibility() -> bool:
    """要权限：先弹系统对话框（会把本进程加进列表），没弹成再打开设置面板。

    用户主动点的「去授权」，所以绕过「一个进程只弹一次」的抑制。
    """
    global _prompted_once
    _prompted_once = True
    granted = _ax_trusted(prompt=True)
    open_accessibility_settings()
    return granted


def permission_message() -> str:
    """给用户看的一句话：授权对象 + 怎么做。"""
    target = permission_target()
    if getattr(sys, "frozen", False):
        how = f"在列表里打开「{target}」"
    else:
        how = f"在列表里打开「{target}」（本程序是从它启动的，所以授权给它）"
    return (
        f"请在「隐私与安全性 → 辅助功能」里{how}，然后点「重试」或重启程序。"
        "若开关本来就是打开的却仍报未授权：那是旧构建留下的失效记录——"
        "ad-hoc 签名的授权是按二进制哈希认的，重新打包就失效了。"
        "选中本应用点列表下方的「−」移除，再重新添加一次即可。"
    )


def _accessibility_trusted(prompt: bool = False) -> bool:
    return accessibility_granted(prompt=prompt)


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
            # prompt=True 会弹出系统对话框；即使弹了，本次仍可能还没授权（用户要去点开关）
            raise HotkeyError("缺少「辅助功能」权限，无法监听全局按键。" + permission_message())
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
                + permission_message()
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
