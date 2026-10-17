"""平台外观的选择器。

只做一件事：按当前操作系统返回 `win` 或 `mac` 模块。两个模块的契约见 `base.py`。

⚠️ 这里的 import **必须是静态的**，不能写成 `importlib.import_module(f".{name}")`。
PyInstaller 只按字节码里的 import 语句收集模块，字符串拼出来的模块名它看不见，
打出来的 .app 会在 `theme` 导入时直接
`ModuleNotFoundError: No module named 'dualswitch.ui.chrome.mac'`（2026-10-17 踩过）。

两个平台模块顶层只 import `base`（`..theme` / `..widgets` 都在函数内导入），
所以放在模块顶层也不会与 `theme → chrome` 成环。
`tests/test_chrome_contract.py` 有一条用例专门盯着这里不许再退回动态导入。
"""

from __future__ import annotations

import sys

# 两个分支都是静态 import：PyInstaller 会同时收集，运行时只执行命中的那个。
if sys.platform == "darwin":
    from . import mac as _impl
else:
    from . import win as _impl


def current():
    """返回当前平台的外观模块。"""
    return _impl


def name() -> str:
    return _impl.NAME


__all__ = ["current", "name"]
