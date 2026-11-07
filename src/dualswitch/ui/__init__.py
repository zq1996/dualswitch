"""图形界面（PySide6，可选依赖）。

核心切屏逻辑不依赖本包；未安装 PySide6 时 `dualswitch ui2` 会给出安装提示并退出。

## 结构

    ui/chrome/            平台外观契约：win.py 与 mac.py **各一个文件**（本来就一端一份）
    ui/clipboard_feed.py  没有同步引擎时的本机记录源（mac 端写，Windows 只读）
    ui/mac2/              macOS 整套界面（v2）：theme / widgets / window / settings_page /
                          clipboard_page / pairing_page / switch_page / statusitem / runner …
    ui/win2/              Windows 整套界面（v2）：同名一套，与 mac **零共享**

两端界面代码物理分开，是因为合并历史里 UI 层两端撞过 14 次、业务后台只撞过 1 次
（后台不冲突靠的就是「一个文件一个作者」，见 `clipboard/windows.py`、`sync/*`）。
拆开之后：**改界面只动自己那一棵树，冲突面归零**。

代价是两棵树各有一份相似代码，所以**共享行为（视觉规范、交互语义）只能靠文档约束**：
`docs/UI-MAC2-IMPLEMENTATION.md`、`docs/UI-WINDOWS2-DESIGN.md`、`docs/PLATFORM-SPLIT.md`。
在一端修好一个 bug 时，先判断另一端是否同样存在。

## 历史

v1 的 `ui/mac` 与 `ui/win` 已于 2026-11-07 删除（v2 稳定后清理，需要时从 git 历史取回）。
当时那 8 个「兼容垫片」（`ui/theme.py`、`ui/window.py` 等）随之下线；
新代码请直接 `from dualswitch.ui.mac2 import ...` 或 `from dualswitch.ui.win2 import ...`。
"""

from __future__ import annotations


def available() -> bool:
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return True


__all__ = ["available"]
