"""不经过任何第三方栈的托盘图标：直接调 Win32 的 `Shell_NotifyIconW`。

## 为什么要有这一份

打包版实测过两套实现都「报成功、却没图标」：

* `QSystemTrayIcon`：`isVisible()=True`、图标非空，但 `geometry()` 为空，
  系统「选择哪些图标显示在任务栏上」里也没有本程序的条目；
* `pystray`（`dualswitch run` 那条老路）：同样没有图标。

两套互不相干的实现同时失灵，说明问题不在 Qt，而在「这个进程的托盘注册没被系统接受」。
它们的共同点是都只在内部记录一个「我调用过了」，失败也不说。所以这里自己调 API：
**每一次返回值都记日志，失败就把 `GetLastError()` 打出来** —— 到底是被安全软件拦了、
还是 shell 没响应，一眼能看明白。

实现要点：
* 用预定义的 `STATIC` 类建一个 0 尺寸的隐藏窗口当消息接收器（不用 RegisterClass）；
* `WNDPROC` 用 ctypes 回调，收到 `WM_APP+1` 再按 lParam 区分双击/右键；
* 窗口建在 Qt 主线程，Qt 的事件循环会照常派发它的消息，所以回调里可以安全地弹 QMenu。
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("dualswitch.tray")

_WM_APP = 0x8000
_WM_TRAY = _WM_APP + 1

_NIM_ADD, _NIM_MODIFY, _NIM_DELETE = 0, 1, 2
_NIF_MESSAGE, _NIF_ICON, _NIF_TIP, _NIF_INFO = 0x01, 0x02, 0x04, 0x10
_NIIF_INFO = 0x00000001
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_LR_DEFAULTSIZE = 0x00000040
_WS_POPUP = 0x80000000
_WM_LBUTTONUP = 0x0202
_WM_LBUTTONDBLCLK = 0x0203
_WM_RBUTTONUP = 0x0205
_WM_CONTEXTMENU = 0x007B
_NIN_SELECT = 0x0400
_GWLP_WNDPROC = -4


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("guidItem", _GUID),
    ]


try:                                  # 非 Windows 上模块仍要能 import（契约测试会 import 它）
    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )
except AttributeError:                 # pragma: no cover - macOS / Linux
    _WNDPROC = None


def _load_dlls():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

    set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    set_long.restype = ctypes.c_void_p
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32._ds_set_long = set_long

    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
    shell32.Shell_NotifyIconGetRect.argtypes = [
        ctypes.POINTER(_NOTIFYICONIDENTIFIER), ctypes.POINTER(wintypes.RECT)
    ]

    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    return user32, shell32, kernel32


class Win32StatusItem:
    """`Shell_NotifyIconW` 版托盘。接口与 `QtStatusItem` 保持一致。"""

    kind = "tray"

    def __init__(self, icon_path, on_activate: Callable[[], None]):
        self._user32, self._shell32, self._kernel32 = _load_dlls()
        self._on_activate = on_activate
        self._menu = None
        self._tip = ""
        self._hwnd = None
        self._hicon = None
        self._proc = None            # WNDPROC 必须留着，否则会被 GC 掉
        self._visible = False

        self._create_window()
        self._create_icon(icon_path)
        self._add()

    # ------------------------------------------------------------------ 建
    def _create_window(self) -> None:
        u = self._user32
        hinst = self._kernel32.GetModuleHandleW(None)
        hwnd = u.CreateWindowExW(
            0, "STATIC", "dualswitch-tray", _WS_POPUP, 0, 0, 0, 0, None, None, hinst, None
        )
        if not hwnd:
            log.error("创建托盘消息窗口失败：GetLastError=%s", ctypes.get_last_error())
            raise OSError("CreateWindowExW failed")
        self._hwnd = hwnd

        def _proc(hwnd, msg, wparam, lparam):  # noqa: ANN001
            try:
                if msg == _WM_TRAY:
                    if lparam in (_WM_LBUTTONDBLCLK, _NIN_SELECT):
                        self._on_activate()
                    elif lparam in (_WM_RBUTTONUP, _WM_CONTEXTMENU):
                        self._popup_menu()
            except Exception:            # 回调里绝不能抛，否则会连带窗口过程一起崩
                log.exception("托盘回调异常")
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = _WNDPROC(_proc)
        u._ds_set_long(hwnd, _GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))

    def _create_icon(self, icon_path) -> None:  # noqa: ANN001
        hicon = self._user32.LoadImageW(None, str(icon_path), _IMAGE_ICON, 16, 16, _LR_LOADFROMFILE)
        if not hicon:
            hicon = self._user32.LoadImageW(
                None, str(icon_path), _IMAGE_ICON, 0, 0, _LR_LOADFROMFILE | _LR_DEFAULTSIZE
            )
        if not hicon:
            log.error("LoadImage 读取 .ico 失败：%s，GetLastError=%s", icon_path, ctypes.get_last_error())
            raise OSError("LoadImageW failed")
        self._hicon = hicon

    def _nid(self, flags: int) -> _NOTIFYICONDATAW:
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = _WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = (self._tip or "屏幕切换")[:127]
        return nid

    def _add(self) -> None:
        nid = self._nid(_NIF_MESSAGE | _NIF_ICON | _NIF_TIP)
        ok = self._shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid))
        err = ctypes.get_last_error()
        if ok:
            self._visible = True
            log.warning(
                "已用自己的 Win32 调用把图标加进通知区域（窗口=0x%X，图标=0x%X，位置=%s）",
                self._hwnd, self._hicon, self.geometry() or "未知（可能被藏进 ^ 里）",
            )
            return
        # 失败就把错误码交出来：这是唯一能说明「为什么」的东西
        log.error(
            "Shell_NotifyIcon(NIM_ADD) 失败：GetLastError=%s（窗口=0x%X，图标=0x%X）"
            " —— 常见原因：安全软件拦截托盘注册 / shell 未就绪 / 进程不在交互式桌面",
            err, self._hwnd, self._hicon,
        )
        raise OSError(f"Shell_NotifyIcon NIM_ADD failed, err={err}")

    # ------------------------------------------------------------------ 接口
    def _popup_menu(self) -> None:
        if self._menu is None:
            return
        try:
            from PySide6.QtGui import QCursor

            self._menu.popup(QCursor.pos())
        except Exception:  # pragma: no cover
            log.exception("弹出托盘菜单失败")

    def set_tooltip(self, text: str) -> None:
        self._tip = text
        if not self._visible:
            return
        nid = self._nid(_NIF_TIP)
        if not self._shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(nid)):
            log.debug("更新托盘提示失败：GetLastError=%s", ctypes.get_last_error())

    def set_menu(self, entries) -> None:      # noqa: ANN001 - list[MenuEntry]
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        for entry in entries:
            if entry.kind == "sep":
                menu.addSeparator()
                continue
            action = QAction(entry.title, menu)
            if entry.kind == "label" or entry.action is None:
                action.setEnabled(False)
            else:
                action.triggered.connect(entry.action)
            action.setCheckable(entry.checked)
            action.setChecked(entry.checked)
            menu.addAction(action)
        self._menu = menu

    def show(self) -> None:
        if self._visible:
            return
        nid = self._nid(_NIF_MESSAGE | _NIF_ICON | _NIF_TIP)
        if self._shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid)):
            self._visible = True
            log.info("托盘图标已加入通知区域")
        else:
            log.error("重新加入托盘图标失败：GetLastError=%s", ctypes.get_last_error())

    def hide(self) -> None:
        if self._visible:
            nid = self._nid(0)
            self._shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(nid))
            self._visible = False
        try:
            if self._hwnd:
                self._user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if self._hicon:
                self._user32.DestroyIcon(self._hicon)
                self._hicon = None
        except Exception:  # pragma: no cover
            log.debug("清理托盘资源失败", exc_info=True)

    def is_visible(self) -> bool:
        return self._visible

    def is_available(self) -> bool:
        return True

    def geometry(self) -> str:
        try:
            nid = _NOTIFYICONIDENTIFIER()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONIDENTIFIER)
            nid.hWnd = self._hwnd
            nid.uID = 1
            rect = wintypes.RECT()
            hr = self._shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(rect))
            if hr != 0:      # S_OK 才算拿到；S_FALSE = 被藏进溢出区
                return ""
            return f"{rect.left},{rect.top} {rect.right - rect.left}x{rect.bottom - rect.top}"
        except Exception:  # pragma: no cover
            return ""

    def reassert(self) -> None:
        self.hide()
        self.show()

    def recreate(self) -> None:
        self.reassert()

    def notify(self, title: str, message: str, icon=None, msecs: int = 4000) -> None:  # noqa: ANN001, ARG002
        if not self._visible:
            return
        nid = self._nid(_NIF_INFO)
        nid.szInfoTitle = str(title)[:63]
        nid.szInfo = str(message)[:255]
        nid.dwInfoFlags = _NIIF_INFO
        if not self._shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(nid)):
            log.debug("托盘气泡失败：GetLastError=%s", ctypes.get_last_error())


def try_create(icon_path, on_activate: Callable[[], None]):  # noqa: ANN001
    """建一个 Win32 托盘；失败返回 None（错误码已经记进日志）。"""
    try:
        return Win32StatusItem(icon_path, on_activate)
    except Exception:
        log.exception("Win32 托盘创建失败")
        return None
