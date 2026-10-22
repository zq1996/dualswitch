"""测试目录的布局守卫：平台专有的测试文件必须自带平台门禁。

**为什么要有这条用例**：项目里有两棵 UI 树（`ui/mac2` 只在 darwin 分发、`ui/win2` 只在
win32 分发）和一批平台专有后端（`clipboard/macos.py`、`clipboard/windows.py`）。这些文件
的用例只在对应平台上才有意义，但「每次提交前跑全量」要求两端都在自己机器上跑完整个
tests/ 目录。文件如果没有平台门禁，对端就会看到一片红，然后为了变绿去改**别人的**用例、
把断言改成迁就运行平台 —— 来回各改一遍，谁也不知道原本要测什么（2026-10-22 用户明确
要求终止这个循环）。

**规则**：文件名带 `_macos` / `_windows` / `_mac_tap` / `_mac2` / `_win2` 后缀的测试文件，
必须在模块顶部声明「不在本平台就整文件 skip」，二选一：

    if sys.platform != "win32":
        pytest.skip("...", allow_module_level=True)

    pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="...")

判定放在 import 被测模块**之前**，否则在错误平台上连收集阶段都会 import 失败。共享层
（`sync/`、`config.py`、`app.py`、契约测试）的用例不在此列：它们必须在两端都真跑。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

#: 旧 `ui/mac` 树已被 `ui/mac2` 取代并冻结（2026-10-22 用户要求）：
#: 这几个文件在 mac 上整文件跳过，Windows 侧仍照常运行旧 `ui/win` 覆盖。
#: 它们不是"共享层必须两端都跑"的对象，而是历史遗留的平台 UI 用例。
FROZEN_MAC_UI_SUITES = {
    "test_chrome_contract.py",
    "test_clipboard_page.py",
    "test_focus_style.py",
    "test_ui.py",
}

#: 文件名后缀 → 该文件只属于哪个平台（`sys.platform` 口径）
SUFFIX_PLATFORM = {
    "_macos": "darwin",
    "_mac_tap": "darwin",
    "_mac2": "darwin",
    "_windows": "win32",
    "_win2": "win32",
}


def _platform_of(path: Path) -> str | None:
    """按文件名后缀推出所属平台；共享测试返回 None。"""
    stem = path.stem  # 去掉 .py，例：test_ui_win2
    for suffix, platform in SUFFIX_PLATFORM.items():
        if stem.endswith(suffix):
            return platform
    return None


def _declares_gate(tree: ast.Module, platform: str) -> bool:
    """模块级是否声明了「非 <platform> 就整文件跳过」。"""
    for node in tree.body:
        # 写法一：if sys.platform != "win32": pytest.skip(..., allow_module_level=True)
        if isinstance(node, ast.If):
            if _is_platform_guard(node.test, platform) and any(
                _is_module_level_skip(stmt) for stmt in node.body
            ):
                return True
        # 写法二：pytestmark = pytest.mark.skipif(sys.platform != "darwin", ...)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is not None and _is_skipif_on_platform(value, platform):
                return True
    return False


def _is_platform_guard(test: ast.expr, platform: str) -> bool:
    """匹配 `sys.platform != "<platform>"`（也接受 `!=` 的反写 `"<platform>" != sys.platform`）。"""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.NotEq):
        return False
    sides = (test.left, test.comparators[0])
    has_platform_attr = any(
        isinstance(s, ast.Attribute) and s.attr == "platform"
        and isinstance(s.value, ast.Name) and s.value.id == "sys"
        for s in sides
    )
    has_platform_literal = any(
        isinstance(s, ast.Constant) and s.value == platform for s in sides
    )
    return has_platform_attr and has_platform_literal


def _is_module_level_skip(stmt: ast.stmt) -> bool:
    """匹配 `pytest.skip(<任何参数>, allow_module_level=True)`。"""
    if not isinstance(stmt, ast.Expr):
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "skip"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pytest"
    ):
        return False
    return any(
        kw.arg == "allow_module_level"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in call.keywords
    )


def _is_skipif_on_platform(value: ast.expr, platform: str) -> bool:
    """匹配 `pytest.mark.skipif(<平台判据>, reason=...)`，允许套在 list 里。"""
    nodes: list[ast.expr] = []
    if isinstance(value, ast.List):
        nodes = list(value.elts)
    else:
        nodes = [value]
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "skipif"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "mark"
        ):
            continue
        if node.args and _is_platform_guard(node.args[0], platform):
            return True
    return False


def _python_test_files() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py") if p.name != Path(__file__).name)


def test_platform_specific_suites_declare_a_gate():
    """每个平台专有测试文件都必须声明平台门禁（漏了就会逼对端改断言）。"""
    missing: list[str] = []
    for path in _python_test_files():
        platform = _platform_of(path)
        if platform is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not _declares_gate(tree, platform):
            missing.append(f"{path.name}（应只在 {platform} 上跑）")
    assert not missing, (
        "下列平台专有测试文件缺少模块级平台门禁，会导致对端平台上整片红：\n  "
        + "\n  ".join(missing)
        + '\n请加：if sys.platform != "<平台>": pytest.skip("<原因>", allow_module_level=True)'
    )


def test_every_platform_specific_file_is_recognised():
    """反向检查：带平台后缀的文件都要能被上面的规则识别，别漏配。"""
    unrecognised = [
        p.name for p in _python_test_files()
        if _platform_of(p) is None
        and any(mod in p.stem for mod in ("mac", "win"))
    ]
    assert not unrecognised, (
        "这些文件名看起来像平台专有，但后缀不在 SUFFIX_PLATFORM 里：\n  "
        + "\n  ".join(unrecognised)
        + "\n要么改成共享用例，要么补进 SUFFIX_PLATFORM 并加门禁。"
    )


def test_shared_suites_are_not_gated():
    """共享层测试不能被整文件跳过，否则两端都在静默丢覆盖。"""
    gated = [
        p.name for p in _python_test_files()
        if _platform_of(p) is None
        and p.name not in FROZEN_MAC_UI_SUITES
        and any(
            isinstance(node, ast.If) and any(_is_module_level_skip(s) for s in node.body)
            for node in ast.parse(p.read_text(encoding="utf-8")).body
        )
    ]
    assert not gated, (
        "共享层测试出现模块级平台跳过（两端会同时失去覆盖）：\n  " + "\n  ".join(gated)
    )


@pytest.mark.parametrize(
    "source, platform, expected",
    [
        ('if sys.platform != "win32":\n    pytest.skip("x", allow_module_level=True)', "win32", True),
        ('if sys.platform != "darwin":\n    pytest.skip("x", allow_module_level=True)', "win32", False),
        ('if sys.platform == "darwin":\n    pytest.skip("x", allow_module_level=True)', "darwin", False),
        ('pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="x")', "darwin", True),
        ('pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="x")]', "win32", True),
        ('pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="x")', "darwin", False),
    ],
)
def test_gate_detector_recognises_both_spellings(source, platform, expected):
    """守卫自己也要有黄金样本：两种写法都要认，写反了要认不出。"""
    tree = ast.parse(source)
    assert _declares_gate(tree, platform) is expected
