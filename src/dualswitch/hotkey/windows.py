"""Windows 全局热键后端。

两个实现：
* RegisterHotkeyBackend —— V1 默认。RegisterHotKey(MOD_ALT, VK_SPACE)，
  实现成本最低，但无法区分左右 Alt（需求文档 2.1 已声明的已知限制）。
* LowLevelHookBackend —— 可选增强（需求文档 2.2）。低级键盘钩子监听 VK_RMENU，
  可严格只响应右 Alt + Space，并吞掉按键避免弹出窗口系统菜单。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

from .base import Hotkey, HotkeyError, to_vk, to_win_modifiers

if sys.platform != "win32":  # pragma: no cover - 平台守卫
    raise ImportError("dualswitch.hotkey.windows 只能在 Windows 上导入")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
HC_ACTION = 0
WH_KEYBOARD_LL = 13

VK_RMENU = 0xA5
VK_LMENU = 0xA4

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
# 关键：不声明 restype 时 ctypes 默认 c_int，64 位下会把 HMODULE 截断，
# 导致 SetWindowsHookExW 报 WinError 126 (ERROR_MOD_NOT_FOUND)。
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def _hint_for_error(code: int) -> str:
    if code == ERROR_HOTKEY_ALREADY_REGISTERED:
        return (
            "热键已被其它程序占用。请关闭占用该热键的软件（常见：输入法、截图工具、"
            "PowerToys、游戏加速器），或修改配置文件中的 hotkey 后重试。"
        )
    return f"RegisterHotKey 失败 (WinError {code})。可尝试以管理员身份运行，或更换热键组合。"


class RegisterHotkeyBackend:
    name = "register"

    def __init__(self, hotkey: Hotkey, callback, hotkey_id: int = 0xB001):
        self.hotkey = hotkey
        self.callback = callback
        self.hotkey_id = hotkey_id
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._registered = False
        self._ready = threading.Event()

    def describe(self) -> str:
        return self.hotkey.describe()

    def start(self, timeout: float = 2.0) -> None:
        self._thread = threading.Thread(target=self._run, name="hotkey-register", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.error:
            raise HotkeyError(self.error)
        if not self._registered:
            raise HotkeyError("热键注册未完成（超时）")

    def _run(self) -> None:
        self._tid = _kernel32.GetCurrentThreadId()
        mods = to_win_modifiers(self.hotkey) | MOD_NOREPEAT
        try:
            vk = to_vk(self.hotkey.key)
        except HotkeyError as exc:
            self.error = str(exc)
            self._ready.set()
            return
        if not _user32.RegisterHotKey(None, self.hotkey_id, mods, vk):
            self.error = _hint_for_error(ctypes.get_last_error())
            self._ready.set()
            return

        self._registered = True
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while True:
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:  # 0 = WM_QUIT, -1 = 错误
                    break
                if msg.message == WM_HOTKEY and int(msg.wParam) == self.hotkey_id:
                    try:
                        self.callback()
                    except Exception:  # 回调异常不能杀死消息循环
                        pass
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            _user32.UnregisterHotKey(None, self.hotkey_id)
            self._registered = False

    def stop(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.5)


class LowLevelHookBackend:
    """严格修饰键匹配的低级键盘钩子。"""

    name = "hook"

    def __init__(self, hotkey: Hotkey, callback, swallow: bool = True):
        self.hotkey = hotkey
        self.callback = callback
        self.swallow = swallow
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._hook = None
        self._proc = None
        self._ready = threading.Event()
        self._vk = 0
        self._swallow_keyup = False

    def describe(self) -> str:
        return self.hotkey.describe()

    # ------------------------------------------------------------ 修饰键判断
    def _mods_ok(self) -> bool:
        def down(vk: int) -> bool:
            return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

        for mod in self.hotkey.modifiers:
            if mod == "ralt" and not down(VK_RMENU):
                return False
            if mod == "alt" and not (down(VK_RMENU) or down(VK_LMENU)):
                return False
            if mod == "ctrl" and not (down(0xA2) or down(0xA3)):
                return False
            if mod == "shift" and not (down(0xA0) or down(0xA1)):
                return False
            if mod == "win" and not (down(0x5B) or down(0x5C)):
                return False
        return True

    # ------------------------------------------------------------ 钩子
    def start(self, timeout: float = 2.0) -> None:
        try:
            self._vk = to_vk(self.hotkey.key)
        except HotkeyError as exc:
            raise HotkeyError(str(exc)) from exc
        self._thread = threading.Thread(target=self._run, name="hotkey-hook", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.error:
            raise HotkeyError(self.error)

    def _run(self) -> None:
        self._tid = _kernel32.GetCurrentThreadId()

        def _proc(n_code, w_param, l_param):  # noqa: ANN001
            if n_code == HC_ACTION:
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = int(kb.vkCode)
                msg = int(w_param)
                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN) and vk == self._vk and self._mods_ok():
                    self._swallow_keyup = self.swallow
                    try:
                        self.callback()
                    except Exception:
                        pass
                    if self.swallow:
                        return 1
                elif msg in (WM_KEYUP, WM_SYSKEYUP) and vk == self._vk and self._swallow_keyup:
                    self._swallow_keyup = False
                    if self.swallow:
                        return 1
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = _HOOKPROC(_proc)
        hmod = _kernel32.GetModuleHandleW(None)
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
        if not self._hook:
            self.error = f"安装键盘钩子失败 (WinError {ctypes.get_last_error()})，可能被安全软件拦截。"
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while True:
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

    def stop(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.5)


def probe_available(hotkey: Hotkey, hotkey_id: int = 0xB0FF) -> tuple[bool, str]:
    """诊断用：尝试注册再立即注销，不改变运行状态。"""
    mods = to_win_modifiers(hotkey) | MOD_NOREPEAT
    try:
        vk = to_vk(hotkey.key)
    except HotkeyError as exc:
        return False, str(exc)
    if _user32.RegisterHotKey(None, hotkey_id, mods, vk):
        _user32.UnregisterHotKey(None, hotkey_id)
        return True, "可注册"
    return False, _hint_for_error(ctypes.get_last_error())
