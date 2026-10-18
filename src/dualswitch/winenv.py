"""Windows 进程环境探测：完整性级别（UIPI）。

托盘图标靠 `Shell_NotifyIcon` 把图标交给 explorer。Windows 的 UIPI 规则不允许
**低完整性级别**的进程向更高完整性的窗口发消息 —— 而 explorer 在本机是以 High
（管理员提权 / UAC 关闭）运行的，于是：

  · 低/中级别进程调用 `Shell_NotifyIcon` **静默失败**：Qt 不抛异常、`isVisible()`
    照样返回 True，`geometry()` 取不到矩形（系统里根本没这一格）；
  · pystray 更直接：`ChangeWindowMessageFilterEx` 抛 `[WinError 5] 拒绝访问`。

2026-10-18 实测（Windows 10 22H2，explorer = High）：

| exe 的完整性标签 | 进程实际级别 | 托盘图标 |
|---|---|---|
| `Low Mandatory Level`（继承自 D:\\work） | Low (0x1000) | 永远不出现 |
| `Medium Mandatory Level` | Medium (0x2000) | 不出现（低于 explorer） |
| `High Mandatory Level` | High (0x3000) | **出现**（`位置=2256,1400 24x40`） |

所以「托盘没图标」不一定是代码问题：先看这个文件里的两个数字。
本模块只读不写，任何异常都吞掉 —— 它不该成为启动失败的理由。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes

RID_UNTRUSTED = 0x0000
RID_LOW = 0x1000
RID_MEDIUM = 0x2000
RID_HIGH = 0x3000
RID_SYSTEM = 0x4000

_NAMES = {
    RID_UNTRUSTED: "Untrusted",
    RID_LOW: "Low",
    RID_MEDIUM: "Medium",
    0x2100: "Medium-Plus",
    RID_HIGH: "High",
    RID_SYSTEM: "System",
    0x5000: "Protected",
}

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25


def _enabled() -> bool:
    return sys.platform == "win32"


def _kernel32():  # noqa: ANN202
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.GetCurrentProcess.restype = wintypes.HANDLE
    return k


def _advapi32():  # noqa: ANN202
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    a.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    a.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    a.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    a.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    a.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    return a


def integrity_level(pid: int | None = None) -> int | None:
    """取进程 token 的完整性级别 RID；拿不到返回 None。

    `pid=None` 表示当前进程。
    """
    if not _enabled():
        return None
    kernel32 = _kernel32()
    advapi32 = _advapi32()
    handle = None
    owned = False
    try:
        if pid is None:
            handle = kernel32.GetCurrentProcess()
        else:
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            owned = True
            if not handle:
                return None

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            size = wintypes.DWORD()
            advapi32.GetTokenInformation(
                token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
            )
            if not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(
                token, TOKEN_INTEGRITY_LEVEL, buf, size.value, ctypes.byref(size)
            ):
                return None
            # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes; } }
            sid = ctypes.c_void_p.from_buffer(buf).value
            if not sid:
                return None
            count = advapi32.GetSidSubAuthorityCount(sid)[0]
            return int(advapi32.GetSidSubAuthority(sid, count - 1)[0])
        finally:
            kernel32.CloseHandle(token)
    except Exception:  # pragma: no cover - 探测失败不该影响启动
        return None
    finally:
        if owned and handle:
            kernel32.CloseHandle(handle)


def shell_integrity_level() -> int | None:
    """资源管理器（任务栏的宿主）的完整性级别。"""
    if not _enabled():
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetShellWindow.restype = wintypes.HWND
        hwnd = user32.GetShellWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        return integrity_level(pid.value)
    except Exception:  # pragma: no cover
        return None


def describe(rid: int | None) -> str:
    if rid is None:
        return "未知"
    return f"{_NAMES.get(rid, hex(rid))}(0x{rid:04X})"


def blocked(ours: int | None, shell: int | None) -> bool:
    """我方级别低于 explorer 时，托盘注册会被 UIPI 挡掉（纯函数，便于单测）。"""
    if ours is None or shell is None:
        return False
    return ours < shell


def tray_restriction() -> tuple[bool, str]:
    """返回 (是否被完整性级别挡住, 说明)。

    第二个返回值直接写日志/给用户看；没被挡住时是空串。
    """
    if not _enabled():
        return False, ""
    ours = integrity_level()
    shell = shell_integrity_level()
    if not blocked(ours, shell):
        return False, ""
    return True, (
        f"程序以 {describe(ours)} 运行，低于资源管理器（{describe(shell)}）："
        "Windows 不允许低级别进程向任务栏注册图标，托盘图标会被系统静默拒绝"
    )


def fix_hint() -> str:
    """给用户的修复命令（把 exe 所在目录的完整性标签抬到 High）。"""
    exe = os.path.abspath(sys.executable)
    folder = os.path.dirname(exe)
    return f'icacls "{folder}" /setintegritylevel (OI)(CI)High /T /C'


def log_environment(log: logging.Logger | None = None) -> tuple[bool, str]:
    """把「我方级别 / explorer 级别 / 是否被挡」写进日志，返回 tray_restriction() 的结果。"""
    log = log or logging.getLogger("dualswitch")
    if not _enabled():
        return False, ""
    log.info(
        "完整性级别：本进程=%s 资源管理器=%s（exe=%s）",
        describe(integrity_level()),
        describe(shell_integrity_level()),
        sys.executable,
    )
    is_blocked, reason = tray_restriction()
    if is_blocked:
        log.error("%s", reason)
        log.error("修复：%s（或把 dist 目录的完整性标签改回默认后重新打包）", fix_hint())
    return is_blocked, reason
