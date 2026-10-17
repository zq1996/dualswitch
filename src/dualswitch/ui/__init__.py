"""Windows 图形界面（PySide6，可选依赖）。

核心切屏逻辑不依赖本包；未安装 PySide6 时 `dualswitch ui` 会给出安装提示并退出。
"""

from __future__ import annotations


def available() -> bool:
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return True


__all__ = ["available"]
