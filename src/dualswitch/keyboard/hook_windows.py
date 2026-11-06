"""进程内**唯一**的 `WH_KEYBOARD_LL`：热键与键盘桥共用同一个钩子。

为什么必须唯一（`docs/KEYBOARD-BRIDGE.md` §8.6）：低级键盘钩子是**链式**的，
后装的先跑，谁 `return 1` 谁吞。两个钩子各自判断、各自吞，顺序按安装时间倒序，
吞键行为就变得不可预测 —— 而且没人能说清"这个键到底被谁吃了"。

所以：

    钩子回调 → ① 热键匹配（命中回调 app，按配置吞）
             → ② 键盘桥路由（编码入队，按闸门决定吞不吞）
             → ③ CallNextHookEx

`hotkey/windows.py::LowLevelHookBackend` 从「自己装钩子」改成「向这里注册一个
处理器」，对外行为（start/stop/describe/异常文案）一个字没变。

处理器签名：`handler(vk: int, down: bool, kb: KBDLLHOOKSTRUCT) -> bool`，
返回 True = 吞掉这个键。任何处理器抛异常都按「不吞」处理 —— 钩子里抛异常会
让整条输入链出问题，宁可漏吞一次也不能让键盘卡死。
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable

if sys.platform != "win32":  # pragma: no cover - 平台守卫
    raise ImportError("dualswitch.keyboard.hook_windows 只能在 Windows 上导入")

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
HC_ACTION = 0

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

#: `KBDLLHOOKSTRUCT.flags`
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80

_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD
]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetKeyState.argtypes = [ctypes.c_int]
_user32.GetKeyState.restype = ctypes.c_short
# 关键：不声明 restype 时 ctypes 默认 c_int，64 位下会把 HMODULE 截断，
# 导致 SetWindowsHookExW 报 WinError 126 (ERROR_MOD_NOT_FOUND)。
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]

    @property
    def injected(self) -> bool:
        return bool(self.flags & LLKHF_INJECTED)


Handler = Callable[[int, bool, KBDLLHOOKSTRUCT], bool]


def key_down(vk: int) -> bool:
    """这个键现在是不是按着（含本进程之外的物理状态）。"""
    return bool(_user32.GetAsyncKeyState(int(vk)) & 0x8000)


def caps_lock_on() -> bool:
    """CapsLock 的**锁定状态**（不是"按着"）。低位 = 开着。"""
    return bool(_user32.GetKeyState(0x14) & 0x0001)


class KeyboardHook:
    """一个进程一份的 LL 钩子。`acquire()` / `release()` 成对使用（引用计数）。

    钩子线程是**独立 daemon 线程**，自带消息循环 —— `WH_KEYBOARD_LL` 的回调在
    装钩子的那个线程里被调用，没有消息循环就收不到任何事件（这是 Windows 钩子
    最经典的坑：`SetWindowsHookExW` 成功、回调一次都不来）。
    """

    _shared: "KeyboardHook | None" = None
    _shared_lock = threading.Lock()

    def __init__(self) -> None:
        self.error: str | None = None
        self._lock = threading.RLock()
        self._handlers: list[tuple[str, Handler]] = []
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._hook = None
        self._proc = None
        self._ready = threading.Event()

    # ------------------------------------------------------------------ 单例
    @classmethod
    def shared(cls) -> "KeyboardHook":
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    # ------------------------------------------------------------------ 注册
    def acquire(self, handler: Handler, *, name: str = "", timeout: float = 2.0) -> None:
        """注册一个处理器；必要时装钩子。装不上抛 `OSError`（带人话原因）。"""
        with self._lock:
            if any(h == handler for _, h in self._handlers):
                return
            self._handlers.append((name or getattr(handler, "__name__", "handler"), handler))
            if self._thread is not None and self._hook:
                return
            if self._thread is not None:
                # 线程还在但钩子没了（消息循环已退出 / 被系统摘掉）：重启一次，
                # 否则新注册的处理器永远收不到事件 —— 现场表现是"热键和键盘桥
                # 都还在，但谁都不响应"。
                self._stop_locked()
            self._start_locked(timeout)

    def release(self, handler: Handler) -> None:
        """注销处理器；最后一个走了就把钩子摘掉（不留一个空钩子拖慢全系统输入）。

        比较用 `==` 不用 `is`：`self._on_event` 每次取值都是新的 bound method 对象，
        `is` 永远不相等（这个坑会让"摘不掉钩子"变成静默泄漏）。
        """
        with self._lock:
            self._handlers = [(n, h) for n, h in self._handlers if h != handler]
            if self._handlers or self._thread is None:
                return
            self._stop_locked()

    @property
    def installed(self) -> bool:
        return self._hook is not None

    def handlers(self) -> list[str]:
        with self._lock:
            return [n for n, _ in self._handlers]

    # ------------------------------------------------------------------ 生命周期
    def _start_locked(self, timeout: float) -> None:
        self.error = None
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="keyboard-hook", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.error:
            err = self.error
            self._handlers.clear()
            self._thread = None
            raise OSError(err)
        if not self._hook:
            self._handlers.clear()
            self._thread = None
            raise OSError("安装键盘钩子超时（未拿到句柄）")

    def _stop_locked(self) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.5)

    def _run(self) -> None:
        self._tid = int(_kernel32.GetCurrentThreadId())
        try:
            self._proc = _HOOKPROC(self._dispatch)
            hmod = _kernel32.GetModuleHandleW(None)
            self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
        except Exception as exc:  # noqa: BLE001 - 任何失败都只报错，不炸线程
            self.error = f"安装键盘钩子失败：{exc}"
            self._ready.set()
            return
        if not self._hook:
            self.error = (
                f"安装键盘钩子失败 (WinError {ctypes.get_last_error()})，"
                "可能被安全软件拦截。"
            )
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while True:
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:      # 0 = WM_QUIT，-1 = 出错
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

    # ------------------------------------------------------------------ 分发
    def _dispatch(self, n_code, w_param, l_param):  # noqa: ANN001
        if n_code != HC_ACTION:
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)
        try:
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        except (ValueError, TypeError):  # pragma: no cover - 理论上不会发生
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        msg = int(w_param)
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        vk = int(kb.vkCode)
        with self._lock:
            handlers = [h for _, h in self._handlers]
        for handler in handlers:
            try:
                if handler(vk, down, kb):
                    return 1
            except Exception:  # noqa: BLE001 - 钩子里绝不能让异常冒出去
                log.exception("键盘钩子处理器异常（已放行这个键）")
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)


def shared_hook() -> KeyboardHook:
    return KeyboardHook.shared()


__all__ = [
    "HC_ACTION",
    "KBDLLHOOKSTRUCT",
    "KeyboardHook",
    "LLKHF_EXTENDED",
    "LLKHF_INJECTED",
    "LLKHF_UP",
    "WH_KEYBOARD_LL",
    "WM_KEYDOWN",
    "WM_KEYUP",
    "WM_SYSKEYDOWN",
    "WM_SYSKEYUP",
    "caps_lock_on",
    "key_down",
    "shared_hook",
]
