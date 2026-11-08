#!/usr/bin/env python
"""「点了没反应」现场取证：这个点的最上层窗口到底是谁。

用法（界面卡住、点哪都不好使的时候）：

    python tools/diag_click_block.py

把鼠标**停在那个点不动的按钮上**再运行 —— 脚本读当前光标位置，问 Windows
「这个点最上层的窗口是哪个」，然后把它属于哪个进程、多大、是否置顶、是否
鼠标穿透全部打出来。再顺手列出所有「可见 + 置顶」的窗口（遮挡嫌疑人）。

    python tools/diag_click_block.py --topmost    # 只列置顶窗口，不读光标

为什么需要它：`Qt.Tool | WindowStaysOnTopHint` 这类**顶层小窗口**（悬浮预览、
tooltip、自绘浮层）只要没被隐藏，就会盖住下面的控件把鼠标事件全吃掉；从
Python 侧看主窗口一切正常（`isVisible()` 都是 True），只有问 Win32 才知道
上面还压着一层。2026-11-08 的「设置完鼠标后点菜单没反应」就是这么定位的：
`PreviewBubble` 的 `hide_now()` 被「鼠标还在气泡上」拦住 → 气泡永久置顶。

只读，不改任何东西，不发 DDC、不碰剪贴板。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 这几个必须写 argtypes：64 位下 HWND 是 8 字节，按默认 int 传会被截断
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = ctypes.c_bool
user32.GetClassNameW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


def _proc_name(hwnd: int) -> str:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return "?"
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return f"pid {pid.value}"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return f"{buf.value.rsplit(chr(92), 1)[-1]} (pid {pid.value})"
        return f"pid {pid.value}"
    finally:
        kernel32.CloseHandle(h)


def _text(hwnd: int, getter) -> str:
    buf = ctypes.create_unicode_buffer(512)
    getter(hwnd, buf, 512)
    return buf.value


def _describe(hwnd: int) -> list[str]:
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    flags = []
    if ex & WS_EX_TOPMOST:
        flags.append("置顶")
    if ex & WS_EX_TOOLWINDOW:
        flags.append("工具窗")
    if ex & WS_EX_TRANSPARENT:
        flags.append("鼠标穿透")
    else:
        flags.append("**吃点击**")
    title = _text(hwnd, user32.GetWindowTextW) or "（无标题）"
    cls = _text(hwnd, user32.GetClassNameW) or "?"
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    return [
        f"  窗口句柄 : 0x{hwnd:X}",
        f"  进程     : {_proc_name(hwnd)}",
        f"  类名     : {cls}",
        f"  标题     : {title}",
        f"  位置尺寸 : ({rect.left},{rect.top}) {w}×{h}",
        f"  扩展样式 : {', '.join(flags)}  (exstyle=0x{ex:X})",
    ]


def probe_cursor() -> int:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hwnd = user32.WindowFromPoint(pt)
    print(f"\n光标位置 ({pt.x},{pt.y}) 处的窗口：")
    if not hwnd:
        print("  （没有窗口 —— 光标在桌面上）")
        return 0
    for line in _describe(hwnd):
        print(line)
    root = user32.GetAncestor(hwnd, GA_ROOT)
    if root and root != hwnd:
        print("\n  上面那个是子窗口，它的顶层窗口：")
        for line in _describe(root):
            print(line)
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    if not (ex & WS_EX_TRANSPARENT):
        print(
            "\n结论：这个点最上层是个**不吃鼠标穿透**的窗口。"
            "\n      如果它不是主窗口本身，它就是「点不动」的元凶 —— "
            "按上面的进程名/类名去找是哪个浮层没被隐藏。"
        )
    return 0


def list_topmost() -> int:
    """枚举所有「可见 + 置顶」的顶层窗口：这些都能盖住主窗口。"""
    rows: list[tuple[int, str, str, str]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        if not (ex & WS_EX_TOPMOST):
            return True
        title = _text(hwnd, user32.GetWindowTextW)
        cls = _text(hwnd, user32.GetClassNameW)
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        size = f"{rect.right - rect.left}×{rect.bottom - rect.top}"
        rows.append((hwnd, title or cls or "?", _proc_name(hwnd), size))
        return True

    user32.EnumWindows(_cb, 0)
    print(f"\n可见且置顶的顶层窗口（共 {len(rows)} 个，都可能压住主窗口）：")
    if not rows:
        print("  没有。")
    for hwnd, title, proc, size in rows:
        print(f"  0x{hwnd:X}  {size:>11}  {proc:<28} {title}")
    return 0


def main() -> int:
    if sys.platform != "win32":
        print("只在 Windows 上有意义。")
        return 1
    print("=" * 68)
    print("点击被拦截现场取证（只读）")
    print("=" * 68)
    if "--topmost" in sys.argv:
        list_topmost()
        return 0
    probe_cursor()
    list_topmost()
    print(
        "\n提示：把鼠标放到另一个点不动的按钮上再跑一次，如果两次命中的是同一个"
        "\n      非主窗口的窗口，那个窗口就是遮挡源。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
