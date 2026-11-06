"""模块级 logger 不许在函数里被同名赋值（局部遮蔽）。

**为什么要有这条**：2026-11-06 在 `cli.py::_start_clip_engine` 里为键盘跟随加了一句
`log = logging.getLogger(__name__)`，放在 `if cfg.keyboard_follow.enabled:` 分支里。
于是整个函数体里的 `log` 都变成局部变量，而默认配置下那句赋值**永远不会执行** ——
后面那句 "剪贴板同步已挂载" 的 `log.info` 直接 `UnboundLocalError`。

要命的是它炸在 `except` 里：这个函数的 except 本意是"剪贴板坏了只降级"，结果
先抛 UnboundLocalError，再把原始异常盖住。`pytest` 全绿（没有用例跑 GUI 启动），
只有用户真敲 `python -m dualswitch ui2` 才看到。

所以用 AST 兜底：**模块级已经定义了 `log`/`logger` 的文件，函数内部不许再赋值同名变量**。
需要单独实例就换个名字（`kbd_log` 之类）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "dualswitch"

#: 只有这些名字值得管：项目里模块级 logger 的固定叫法
WATCHED = ("log", "logger")


def _module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _assigned_in_own_scope(fn: ast.AST) -> tuple[set[str], set[str]]:
    """函数**自身作用域**里被赋值的名字 + 声明成 global 的名字。

    嵌套函数/类是新作用域，它里面的赋值不会遮蔽外层，所以整棵子树跳过 ——
    但对嵌套函数本身会在 `ast.walk` 里单独检查一次。
    """
    assigned: set[str] = set()
    declared_global: set[str] = set()
    stack = list(getattr(fn, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Global):
            declared_global.update(node.names)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            assigned.add(node.id)
        stack.extend(ast.iter_child_nodes(node))
    return assigned, declared_global


def shadow_problems(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    watched = {n for n in _module_level_names(tree) if n in WATCHED}
    if not watched:
        return []
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigned, declared_global = _assigned_in_own_scope(node)
        for name in sorted(watched & assigned - declared_global):
            problems.append(
                f"{path.name}:{node.lineno} {node.name}() 局部赋值 `{name}`，"
                f"遮蔽了模块级 logger（同函数里在赋值前用 {name} 会 UnboundLocalError）"
            )
    return problems


def test_no_function_shadows_module_logger():
    problems: list[str] = []
    checked = 0
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if {n for n in _module_level_names(tree) if n in WATCHED}:
            checked += 1
        problems.extend(shadow_problems(path))
    # 防止守空转：项目里确实有一批模块级 logger
    assert checked >= 5, f"只扫到 {checked} 个带模块级 logger 的文件，守卫可能失效了"
    assert not problems, "发现 logger 被局部遮蔽：\n" + "\n".join(problems)


def test_guard_actually_catches_the_bug(tmp_path):
    """守卫自己要有用例：拿 2026-11-06 那段真实形状喂给它，必须报出来。"""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import logging\n"
        "log = logging.getLogger('x')\n"
        "def f(flag):\n"
        "    if flag:\n"
        "        log = logging.getLogger('y')\n"
        "        log.info('hi')\n"
        "    log.info('boom')\n",
        encoding="utf-8",
    )
    problems = shadow_problems(bad)
    assert len(problems) == 1
    assert "f()" in problems[0] and "`log`" in problems[0]


def test_explicit_global_is_allowed(tmp_path):
    """显式 `global log` 是合法写法，别误报。"""
    ok = tmp_path / "ok.py"
    ok.write_text(
        "import logging\n"
        "log = logging.getLogger('x')\n"
        "def f():\n"
        "    global log\n"
        "    log = logging.getLogger('y')\n",
        encoding="utf-8",
    )
    assert shadow_problems(ok) == []


def test_nested_function_shadowing_is_also_flagged(tmp_path):
    """嵌套函数里同名赋值也算遮蔽 —— 这是**故意的过近似**。

    静态分析判不出"赋值是不是条件分支里的"（2026-11-06 那个 bug 就是脏在
    `if enabled:` 里面，赋值和使用的先后顺序完全正常，坏在分支没进去）。
    既然判不准，规则就取最保守的：**一个函数里只要出现同名赋值就算遮蔽**。
    规避成本极低（换个名字 `kbd_log`），漏判的代价是启动直接崩。
    """
    shadowed = tmp_path / "nested.py"
    shadowed.write_text(
        "import logging\n"
        "log = logging.getLogger('x')\n"
        "def outer():\n"
        "    def inner():\n"
        "        log = 1\n"
        "        return log\n"
        "    return inner()\n",
        encoding="utf-8",
    )
    problems = shadow_problems(shadowed)
    assert len(problems) == 1
    assert "inner()" in problems[0]


def test_function_without_module_logger_is_ignored(tmp_path):
    """模块没有 logger 时，函数里叫 log 只是普通变量，不该误报。"""
    ok = tmp_path / "no_logger.py"
    ok.write_text(
        "def f():\n"
        "    log = []\n"
        "    log.append(1)\n"
        "    return log\n",
        encoding="utf-8",
    )
    assert shadow_problems(ok) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
