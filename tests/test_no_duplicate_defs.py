"""合并守卫：同一个类里不允许出现两个同名方法（模块级同理）。

2026-10-18 的两次合并里都出现了这个问题：Windows 端和 macOS 端各自往
`ui/window.py` / `ui/runner.py` 里加了一份 `attach_clipboard` / `show_clipboard`，
Python 靠「后定义覆盖前定义」照样能跑，`pytest` 也全绿 —— 但前一份是死代码，
后一份还丢掉过功能（`show_clipboard` 少了 `refresh()` + `setFocus()`，界面表现为
翻页后列表空白、键盘不响应）。

这种残留靠跑测试是发现不了的，只能靠静态检查。这里用 AST 兜底：
以后合并再留下重复定义，测试会直接指出「哪个文件、哪个类、哪两行」。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "dualswitch"


def _duplicates(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    rel = path.relative_to(SRC.parent.parent)

    def scan(container, owner: str) -> None:
        seen: dict[str, int] = {}
        for node in container:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in seen:
                problems.append(
                    f"{rel}:{owner}.{node.name}() 第 {seen[node.name]} 行已定义，"
                    f"第 {node.lineno} 行又定义了一次"
                )
            else:
                seen[node.name] = node.lineno

    scan(tree.body, "<module>")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            scan(node.body, node.name)
    return problems


def test_no_duplicate_definitions():
    files = sorted(SRC.rglob("*.py"))
    assert files, f"没找到源码：{SRC}"
    problems = [msg for f in files for msg in _duplicates(f)]
    assert not problems, "出现重复定义（多半是合并残留，后者会覆盖前者）：\n" + "\n".join(problems)
