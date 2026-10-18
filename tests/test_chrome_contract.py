"""平台分层契约测试。

两件事：
1. `chrome/win.py` 与 `chrome/mac.py` 必须提供完全一致的字段与函数（漏一个，另一端启动就 AttributeError）
2. 共享 UI 文件里不许再出现平台分支（否则两端并行开发又会撞同一批文件）

改动契约时，先改 `docs/PLATFORM-SPLIT.md` 第 3 节，再同步这里。
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

import dualswitch.ui as ui_pkg  # noqa: E402
from dualswitch.ui.chrome.base import Palette  # noqa: E402

CHROME_MODULES = ["win", "mac"]

# 字段名 → 允许的类型
FIELDS = {
    "NAME": str,
    "PLATFORM_LABEL": str,
    "PALETTE": Palette,
    "NATIVE_CHROME": bool,
    "ROOT_QSS": str,
    "STATUSBAR_QSS": str,
    "QSS_EXTRA": str,
    "BODY_TOP": int,
    "INLINE_SETTINGS_ROW": bool,
    "SETTINGS_IN_WINDOW": bool,
    "SETTINGS_ROW_HINT": str,
    "CLICK_HINT": str,
    "REOPEN_ON_ACTIVATE": bool,
    "DOCK_FOLLOWS_WINDOW": bool,
    "STRICT_BACKEND": str,
    "BACKEND_OPTIONS": list,
    "MATCH_DESC": str,
    "HOTKEY_COPY": dict,
    "APP_ICON_NAMES": tuple,
}

FUNCS = [
    "titlebar",
    "build_status_item",
    "build_fallback_status_item",
    "build_app_menu",
    "activate",
    "set_dock_visible",
    "notify",
    "open_path",
    # 「辅助功能」权限一组（mac 独有痛点，Windows 是恒真空壳）
    "accessibility_granted",
    "request_accessibility",
    "open_accessibility_settings",
    "permission_target",
]

HOTKEY_KEYS = {"strict_title", "strict_desc", "tray_title", "tray_desc", "swallow_desc"}

# 这些文件是两端共享的，不允许出现平台判断
SHARED_FILES = [
    "theme.py",
    "widgets.py",
    "window.py",
    "settings.py",
    "runner.py",
    "statusitem.py",
]
FORBIDDEN = ("sys.platform", "platform.system(", "IS_MAC")


@pytest.mark.parametrize("name", CHROME_MODULES)
def test_chrome_module_contract(name: str):
    mod = importlib.import_module(f"dualswitch.ui.chrome.{name}")

    for field, kind in FIELDS.items():
        assert hasattr(mod, field), f"chrome/{name}.py 缺少契约字段 {field}"
        value = getattr(mod, field)
        assert isinstance(value, kind), (
            f"chrome/{name}.py 的 {field} 应是 {kind.__name__}，实际是 {type(value).__name__}"
        )

    for fn in FUNCS:
        assert callable(getattr(mod, fn, None)), f"chrome/{name}.py 缺少函数 {fn}()"

    assert HOTKEY_KEYS <= set(mod.HOTKEY_COPY), f"chrome/{name}.py 的 HOTKEY_COPY 缺键"
    assert mod.PALETTE.accent.startswith("#")
    assert mod.PALETTE.font_families, "字体族不能为空"
    assert mod.BACKEND_OPTIONS, "DDC 后端下拉不能为空"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("name", CHROME_MODULES)
def test_chrome_titlebar_matches_native_flag(name: str, qapp):
    """自绘标题栏与 NATIVE_CHROME 必须自洽：系统画窗口时就不该再返回自绘标题栏。"""
    mod = importlib.import_module(f"dualswitch.ui.chrome.{name}")
    bar = mod.titlebar("测试")
    if mod.NATIVE_CHROME:
        assert bar is None, "系统画窗口的平台不应返回自绘标题栏"
    else:
        assert bar is not None, "无系统标题栏的平台必须提供自绘标题栏"


def test_current_follows_platform():
    from dualswitch.ui.chrome import current

    expected = "macos" if sys.platform == "darwin" else "windows"
    assert current().NAME == expected


def test_chrome_selector_imports_statically():
    """选择器一旦退回 importlib 动态导入，PyInstaller 就收集不到 mac/win 模块。

    后果很隐蔽：源码方式跑得好好的，打出来的 .app 一启动就
    `ModuleNotFoundError: No module named 'dualswitch.ui.chrome.mac'`（22:07 那次闪退）。
    只看真实代码（AST），文档里提到 importlib 不算数。
    """
    selector = Path(ui_pkg.__file__).parent / "chrome" / "__init__.py"
    tree = ast.parse(selector.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "importlib" for alias in node.names), (
                "chrome/__init__.py 不要 import importlib，动态导入会让打包漏模块"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "import_module", (
                "chrome/__init__.py 不要用 import_module，打包会漏模块"
            )

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            imported.update(alias.name for alias in node.names)
    assert {"mac", "win"} <= imported, "两个平台模块都要写成静态 `from . import ...`"


def test_shared_ui_has_no_platform_branch():
    """共享文件里出现平台分支，就等于把两端的改动重新绑回同一个文件。"""
    ui_dir = Path(ui_pkg.__file__).parent
    offenders: list[str] = []
    for filename in SHARED_FILES:
        source = (ui_dir / filename).read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in source:
                offenders.append(f"{filename}: {needle}")
    assert not offenders, "共享 UI 文件出现平台分支：" + "；".join(offenders)


def test_menu_entries_are_platform_agnostic():
    """菜单条目是纯数据，两端共用：条目标题里不该出现某一端专属的点击方式。"""
    from dualswitch.config import Config
    from dualswitch.ui.statusitem import MenuEntry, build_menu_entries

    entries = build_menu_entries(Config(), "mac", "Right Option+Space", {})
    assert all(isinstance(e, MenuEntry) for e in entries)
    titles = [e.title for e in entries]
    assert any("当前画面：Mac" in t for t in titles)
    assert any("Windows" in t for t in titles)
