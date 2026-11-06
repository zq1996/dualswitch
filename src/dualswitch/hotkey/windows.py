"""Windows 全局热键后端。

两个实现：
* RegisterHotkeyBackend —— 默认。RegisterHotKey(MOD_ALT, VK_SPACE)，
  实现成本最低，但无法区分左右 Alt（需求文档 2.1 已声明的已知限制）。
* LowLevelHookBackend —— 可选增强（需求文档 2.2）。低级键盘钩子监听 VK_RMENU，
  可严格只响应右 Alt + Space，并吞掉按键避免弹出窗口系统菜单。

**2026-11-06：钩子本身搬到 `keyboard/hook_windows.py`。** 键盘跟随也要装
`WH_KEYBOARD_LL`，而低级钩子是链式的（后装的先跑、各自吞），两个钩子一起工作
吞键行为不可预测（`docs/KEYBOARD-BRIDGE.md` §8.6）。现在全进程只有一个钩子，
本后端改成"向它注册一个匹配器"，对外行为（start/stop/describe/错误文案）不变。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

from .base import Hotkey, HotkeyError, to_vk, to_win_modifiers

if sys.platform != "win32":  # pragma: no cover - 平台守卫
    raise ImportError("dualswitch.hotkey.windows 只能在 Windows 上导入")

from ..keyboard.hook_windows import KBDLLHOOKSTRUCT, key_down, shared_hook

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
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


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
    """严格修饰键匹配的低级键盘钩子。

    **不自己装钩子**：向 `keyboard/hook_windows.py` 的进程唯一钩子注册一个匹配器
    （2026-11-06）。这样键盘跟随打开时也只有一条链，不会出现"两个钩子抢着吞"。
    """

    name = "hook"

    def __init__(self, hotkey: Hotkey, callback, swallow: bool = True):
        self.hotkey = hotkey
        self.callback = callback
        self.swallow = swallow
        self.error: str | None = None
        self._hook = None
        self._vk = 0
        self._swallow_keyup = False
        self._started = False

    def describe(self) -> str:
        return self.hotkey.describe()

    # ------------------------------------------------------------ 修饰键判断
    def _mods_ok(self) -> bool:
        for mod in self.hotkey.modifiers:
            if mod == "ralt" and not key_down(VK_RMENU):
                return False
            if mod == "alt" and not (key_down(VK_RMENU) or key_down(VK_LMENU)):
                return False
            if mod == "ctrl" and not (key_down(0xA2) or key_down(0xA3)):
                return False
            if mod == "shift" and not (key_down(0xA0) or key_down(0xA1)):
                return False
            if mod == "win" and not (key_down(0x5B) or key_down(0x5C)):
                return False
        return True

    # ------------------------------------------------------------ 钩子
    def start(self, timeout: float = 2.0) -> None:
        try:
            self._vk = to_vk(self.hotkey.key)
        except HotkeyError as exc:
            raise HotkeyError(str(exc)) from exc
        if self._started:
            return
        hook = shared_hook()
        try:
            hook.acquire(self._on_event, name=f"hotkey:{self.hotkey.describe()}", timeout=timeout)
        except OSError as exc:
            self.error = str(exc)
            raise HotkeyError(self.error) from exc
        self._hook = hook
        self._started = True

    def _on_event(self, vk: int, down: bool, kb: KBDLLHOOKSTRUCT) -> bool:  # noqa: ARG002
        if down:
            if vk == self._vk and self._mods_ok():
                self._swallow_keyup = self.swallow
                try:
                    self.callback()
                except Exception:  # noqa: BLE001 - 回调异常不能杀死钩子
                    pass
                return bool(self.swallow)
            return False
        # 抬起：只有按下时吞过才吞，否则会把普通按键的抬起吃掉
        if vk == self._vk and self._swallow_keyup:
            self._swallow_keyup = False
            return bool(self.swallow)
        return False

    def stop(self) -> None:
        hook = self._hook
        self._hook = None
        self._started = False
        self._swallow_keyup = False
        if hook is not None:
            try:
                hook.release(self._on_event)
            except Exception:  # noqa: BLE001 - 停不下来也不能连累退出
                pass



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
