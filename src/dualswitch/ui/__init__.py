"""图形界面（PySide6，可选依赖）。

核心切屏逻辑不依赖本包；未安装 PySide6 时 `dualswitch ui` 会给出安装提示并退出。

## 结构（2026-10-20 起）

    ui/chrome/            平台外观契约：win.py 与 mac.py **各一个文件**（本来就一端一份）
    ui/clipboard_feed.py  没有同步引擎时的本机记录源（mac 端写，Windows 只读）
    ui/mac/               macOS 整套界面：theme / widgets / window / settings /
                          clipboard_page / statusitem / runner
    ui/win/               Windows 整套界面：同名八件，与 mac **零共享**

两端界面代码物理分开，是因为合并历史里 UI 层两端撞过 14 次、业务后台只撞过 1 次
（后台不冲突靠的就是「一个文件一个作者」，见 `clipboard/windows.py`、`sync/*`）。
拆开之后：**改界面只动自己那一棵树，冲突面归零**；剩下的唯一接缝是下面这个选路函数，
两端都不需要改它。

代价是两棵树各有一份相似代码（约 2200 行），所以**共享行为（视觉规范、交互语义）
只能靠文档约束**：`docs/UI-DESIGN-MAC.md`、`docs/UI-DESIGN.md`、`docs/PLATFORM-SPLIT.md`。
在一端修好一个 bug 时，先判断另一端是否同样存在。
"""

from __future__ import annotations

NAMES = (
    "clipboard_page",
    "focus_style",
    "runner",
    "settings",
    "statusitem",
    "theme",
    "widgets",
    "window",
)


def available() -> bool:
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return True


def platform_ui():
    """当前平台那一套界面模块（唯一一处选路，两端都不需要改）。

    惰性导入：`available()` 要能在**没装 PySide6** 时被调用，所以本包顶层不能
    import 任何一棵树。
    """
    from .chrome import current

    if current().NAME == "macos":
        from . import mac as pkg
    else:
        from . import win as pkg
    return pkg


__all__ = ["available", "platform_ui", "NAMES"]
