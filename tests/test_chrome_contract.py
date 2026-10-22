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

# 旧 ui/mac 已被 ui/mac2 取代；mac 上不再维护/运行旧平台契约用例。
if sys.platform == "darwin":
    pytest.skip("旧 ui/mac 已由 ui/mac2 取代", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

import dualswitch.ui as ui_pkg  # noqa: E402
from dualswitch.ui.chrome.base import Palette  # noqa: E402

CHROME_MODULES = ["win", "mac"]

# 字段名 → 允许的类型
FIELDS = {
    "NAME": str,
    "PLATFORM_LABEL": str,
    "PLATFORM_LOGO": str,
    "PEER_LOGO": str,
    "PALETTE": Palette,
    "NATIVE_CHROME": bool,
    "ROOT_QSS": str,
    "STATUSBAR_QSS": str,
    "QSS_EXTRA": str,
    "BODY_TOP": int,
    "INLINE_SETTINGS_ROW": bool,
    "INLINE_CLIPBOARD_ROW": bool,
    "EXTRA_H": int,
    "SETTINGS_IN_WINDOW": bool,
    "SETTINGS_ROW_HINT": str,
    "CLICK_HINT": str,
    "STATUS_ACTIVATE": str,
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

# 两端各一棵界面树（2026-10-20 起）：不许互相 import，公开 API 必须对齐
TREES = ("mac", "win")


def _tree_sources(name: str) -> list[Path]:
    return sorted((Path(ui_pkg.__file__).parent / name).glob("*.py"))


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


@pytest.mark.parametrize("name", CHROME_MODULES)
def test_platform_logo_asset_exists(name: str):
    """PLATFORM_LOGO / PEER_LOGO 必须真的能找到对应图片：拼错名字只会在运行时少一张图标。"""
    from dualswitch.ui import theme

    mod = importlib.import_module(f"dualswitch.ui.chrome.{name}")
    for kind in (mod.PLATFORM_LOGO, mod.PEER_LOGO):
        assert theme.asset(f"{kind}-logo.png").exists()
    assert mod.PEER_LOGO != mod.PLATFORM_LOGO


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


@pytest.mark.parametrize("name", CHROME_MODULES)
def test_status_activate_semantics(name: str):
    """点常驻图标后干什么只有两种合法语义；Windows 必须是「摊开主页面」。

    切换会改显示器输入源，是不可撤销的重操作 —— 放在"双击托盘"这种高误触动作上
    早晚出事（用户双击只是想看看窗口）。
    """
    from dualswitch.ui import theme
    from dualswitch.ui.chrome import current

    mod = importlib.import_module(f"dualswitch.ui.chrome.{name}")
    assert mod.STATUS_ACTIVATE in ("show_main", "switch")
    if name == current().NAME:
        # theme 只镜像「本平台」的 chrome，跨平台模块没得比
        assert theme.STATUS_ACTIVATE == mod.STATUS_ACTIVATE
    if name == "win":
        assert mod.STATUS_ACTIVATE == "show_main"


def test_windows_click_hint_matches_activate():
    """Windows 的提示文案必须与行为一致：文案写"打开主界面"，双击就得摊开主页面。

    （mac 侧 `CLICK_HINT` 写"单击图标打开菜单"、代码却是单击切换，是历史遗留，
     已记进 PROGRESS 待 mac 端负责人确认，别在这儿把它锁成"对的"。）
    """
    from dualswitch.ui.chrome import win

    assert win.STATUS_ACTIVATE == "show_main"
    assert "打开主界面" in win.CLICK_HINT


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


@pytest.mark.parametrize("name", TREES)
def test_ui_tree_does_not_import_the_other(name: str):
    """两棵界面树互不引用：一旦互相 import，就又变回「一个文件两个作者」。"""
    other = "win" if name == "mac" else "mac"
    offenders: list[str] = []
    for path in _tree_sources(name):
        source = path.read_text(encoding="utf-8")
        for needle in (f"from ..{other} import", f"from .{other} import", f"dualswitch.ui.{other}"):
            if needle in source:
                offenders.append(f"{name}/{path.name}: {needle}")
    assert not offenders, "界面树互相引用：" + "；".join(offenders)


def test_ui_trees_expose_the_same_api():
    """两棵树必须提供同名模块 + 同名公开接口。

    这条是 58ecb42 的教训：合并时 `ClipboardPage(open_external=...)` 这种
    「共享控件签名变了、调用方自动合并且无冲突」会一路合到启动才崩
    （`TypeError: unexpected keyword argument`）。把 API 钉住，签名漂移在测试里就红。
    """
    import inspect

    from dualswitch.ui import mac, win

    for mod_name in ("theme", "widgets", "window", "settings", "clipboard_page",
                     "statusitem", "runner", "focus_style"):
        assert hasattr(mac, mod_name) and hasattr(win, mod_name), f"两棵树都要有 {mod_name}"

    # 页面 / 窗口 / 设置页的信号与关键方法必须同名（值类型不比，只看存在性）
    checks = [
        (mac.clipboard_page.ClipboardPage, win.clipboard_page.ClipboardPage,
         ["backRequested", "copyRequested", "retryRequested", "cleared"],
         ["refresh", "_copy", "_retry", "_drop", "_clear"]),
        (mac.window.MainWindow, win.window.MainWindow,
         ["switchRequested", "openSettingsRequested", "openClipboardRequested", "probeRequested"],
         ["set_state", "set_busy", "set_foot", "show_clipboard_page", "show_main_page"]),
        (mac.settings.SettingsWindow, win.settings.SettingsWindow,
         ["saved", "clipboardRequested", "recordingChanged", "backRequested"],
         ["_save", "_reset"]),
    ]
    problems: list[str] = []
    for left, right, signals, methods in checks:
        for attr in signals + methods:
            if not hasattr(left, attr):
                problems.append(f"mac.{left.__name__} 缺 {attr}")
            if not hasattr(right, attr):
                problems.append(f"win.{right.__name__} 缺 {attr}")

    # run_ui 的调用签名必须一致（cli.py 只写一次，两棵树都得接得住）
    lsig = inspect.signature(mac.runner.run_ui)
    wsig = inspect.signature(win.runner.run_ui)
    assert list(lsig.parameters) == list(wsig.parameters), (
        f"run_ui 签名不一致：mac={list(lsig.parameters)} win={list(wsig.parameters)}"
    )
    assert not problems, "两棵树的公开接口对不齐：\n" + "\n".join(problems)


def test_menu_entries_are_platform_agnostic():
    """菜单条目是纯数据，两端共用：条目标题里不该出现某一端专属的点击方式。"""
    from dualswitch.config import Config
    from dualswitch.ui.statusitem import MenuEntry, build_menu_entries

    entries = build_menu_entries(Config(), "mac", "Right Option+Space", {})
    assert all(isinstance(e, MenuEntry) for e in entries)
    titles = [e.title for e in entries]
    assert any("当前画面：Mac" in t for t in titles)
    assert any("Windows" in t for t in titles)
