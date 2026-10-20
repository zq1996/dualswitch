"""Windows 端的一整套界面实现（**只归 Windows 端作者改**）。

和 `ui/mac/` 完全对称、互不引用。Windows 侧的界面功能（设置内嵌翻页、
双击托盘摊开主界面、剪贴板页全屏预览 + ←/→ 翻页、焦点虚框治理）都长在这里，
改它们不需要动 mac 那份，也不会有冲突。

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
