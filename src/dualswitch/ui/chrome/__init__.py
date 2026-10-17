"""平台外观的选择器。

只做一件事：按当前操作系统返回 `win` 或 `mac` 模块。两个模块的契约见 `base.py`。

`current()` 是惰性导入，避免 `theme → chrome → theme` 的循环 import。
"""

from __future__ import annotations

import importlib
import sys

_module = None


def current():
    """返回当前平台的外观模块（带缓存）。"""
    global _module
    if _module is None:
        name = "mac" if sys.platform == "darwin" else "win"
        _module = importlib.import_module(f".{name}", __name__)
    return _module


def name() -> str:
    return current().NAME


__all__ = ["current", "name"]
