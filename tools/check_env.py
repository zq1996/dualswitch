"""环境体检：这个解释器能不能跑全量测试、缺什么、会有什么降级。

    python3 tools/check_env.py          # 看矩阵
    python3 tools/check_env.py --strict # macOS 上缺 pyobjc 就退出码 1

**为什么单独做一个工具**：2026-11-06 在 `/usr/bin/python3`（3.9.6，没装 pyobjc 和
hidapi）上跑全量，出现 3 红 + 9 条静默跳过。红的那 3 条和代码无关，跳过的 9 条
却意味着"你其实没测到那一块"。这类问题在同一台机器上换解释器就会复现，
所以需要一条命令把差异摊开。

跑测试的规矩（见 tests/conftest.py 与 tests/test_env_contract.py）：

* 硬依赖（PySide6、pytest）缺了 → 让它红，那是装错了环境；
* 可选依赖（hid / Quartz / AppKit / pystray）缺了 → 产品降级、测试跳过，**不许红**。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

#: (模块, pip 包, 缺了会怎样, 是否本平台必需)
DEPS = [
    ("PySide6", "PySide6-Essentials", "整个图形界面与大部分用例都跑不了", True),
    ("hid", "hidapi", "罗技鼠标联动降级；键盘 HID++ 相关不可用", False),
    ("Quartz", "pyobjc-framework-Quartz", "macOS 全局热键、键盘注入不可用", "darwin"),
    ("AppKit", "pyobjc-framework-Cocoa", "macOS 剪贴板后端、mac2 界面用例跳过", "darwin"),
    ("pystray", "pystray", "托盘/菜单栏常驻降级为无界面常驻", False),
]


def _has(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:  # noqa: BLE001 - 任何异常都算不可用
        return False


def _required(dep_required) -> bool:
    if dep_required is True:
        return True
    if isinstance(dep_required, str):
        return sys.platform == dep_required
    return False


def main(argv: list[str]) -> int:
    strict = "--strict" in argv[1:]

    print(f"解释器  : {sys.executable}")
    print(f"版本    : {sys.version.split()[0]}  ({sys.platform})")
    try:
        import dualswitch

        print(f"项目    : 可导入（{Path(dualswitch.__file__).parent}）")
    except Exception as exc:  # noqa: BLE001
        print(f"项目    : ✗ 导入失败 —— {type(exc).__name__}: {exc}")

    print("\n依赖    :")
    missing_required: list[tuple[str, str]] = []
    missing_optional: list[tuple[str, str]] = []
    for module, package, impact, required in DEPS:
        ok = _has(module)
        mark = "✓" if ok else "✗"
        must = "必需" if _required(required) else "可选"
        print(f"  {mark} {module:<8} {must:<4} {package:<28} {'' if ok else impact}")
        if not ok:
            (missing_required if _required(required) else missing_optional).append(
                (module, package)
            )

    print()
    if missing_optional:
        names = "、".join(m for m, _ in missing_optional)
        print(f"可选依赖缺失：{names}")
        print("  → 相关功能静默降级，对应用例跳过；**这不是回归**，别去改别人的断言。")
        print("  → 想在开发机上复现「完全没装」的环境：")
        print(f"     DUALSWITCH_SIMULATE_MISSING={','.join(m for m, _ in missing_optional)} pytest")

    if missing_required:
        print(f"\n✗ 缺必需依赖：{'、'.join(p for _, p in missing_required)}")
        print('  本解释器装：pip install -e ".[dev,mouse,macos,gui]"')
        print("  或者换成跑应用的那个解释器 —— 两处必须是同一个，装到另一个 python3 等于没装。")
        if strict:
            return 1
        print("  （加 --strict 让这一条直接失败，CI / 提交前检查用）")
        return 0

    print("✓ 必需依赖齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
