"""macOS 端的一整套界面实现（**只归 macOS 端作者改**）。

这个包里没有一行共享给 Windows 的代码：`theme / widgets / window / settings /
clipboard_page / statusitem / runner` 全是 macOS 自己的版本。就按
`eb74ca7`（2026-10-19 mac 端最后一个界面提交）落下来，之后 mac 侧的界面改动
只改这里，不需要看 `ui/win/`，也不会有冲突。

为什么这么拆：合并历史里 UI 层两端撞了 14 次、业务后台只撞了 1 次 —— 后台之所以
不冲突，是因为 `clipboard/windows.py` 只归 Windows、`sync/*` 只归 macOS
（一个文件一个作者）。这里只是把那条边界补到界面上。

**不要在别的平台导入本包**：只用 `dualswitch.ui.platform_ui()` 取当前平台那一套。
"""

from __future__ import annotations

# 静态 import（不写 importlib 动态导入）：PyInstaller 靠 AST 收集模块，
# 动态导入会让打包版一启动就 ModuleNotFoundError。
from . import (  # noqa: F401
    clipboard_page,
    focus_style,
    runner,
    settings,
    statusitem,
    theme,
    widgets,
    window,
)

__all__ = [
    "clipboard_page",
    "focus_style",
    "runner",
    "settings",
    "statusitem",
    "theme",
    "widgets",
    "window",
]
