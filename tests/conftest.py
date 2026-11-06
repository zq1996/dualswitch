"""测试公共设施。

三件事，都是为了让"换台机器 / 换个解释器就一片红"不再发生：

1. **临时目录与 Qt 平台钉在 conftest 里** —— 以前每次跑都要手打
   `QT_QPA_PLATFORM=offscreen --basetemp=.pytest-tmp`，忘一个就报一堆莫名其妙的错。
2. **可选依赖矩阵 + 模拟缺失**（`DUALSWITCH_SIMULATE_MISSING`）。
   项目里有三类可选依赖：`hid`（罗技 HID++）、`Quartz/AppKit`（macOS 原生）、
   `pystray`（托盘）。缺了它们产品**本来就该安静降级**，测试也不许红 ——
   但以前 `test_mouse_logi_host` 就因为 `available()` 先判 hid 而红 3 条，
   看到的人第一反应是"我改坏了什么"。
   现在可以用 `DUALSWITCH_SIMULATE_MISSING=hid` 在**开发机上复现**这种环境，
   不用真的去装/卸依赖。
3. **报告头**打印解释器与缺失的可选依赖，让"红"之前先看到"你缺什么"。
"""

import importlib
import importlib.abc
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# --------------------------------------------------------------------------- #
# 环境：临时目录 + Qt 平台
# --------------------------------------------------------------------------- #
def _ensure_tmpdir() -> None:
    """把临时目录钉在仓库内 `build/tmp`。

    `tmp_path` 系 fixture 依赖 tempfile。有些受限环境（只读 /tmp、沙箱禁止删探针文件、
    或 /var/folders 能写但 mkdir 被拦）会让 tempfile 找不到可用目录，整套用例直接 ERROR。
    直接指定 `tempfile.tempdir` 可以跳过 tempfile 的"建了再删"探测，行为在各平台一致。
    """
    fallback = ROOT / "build" / "tmp"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    if os.access(fallback, os.W_OK):
        tempfile.tempdir = str(fallback)


def _ensure_qt_offscreen() -> None:
    """没显式要求真实剪贴板时，UI 一律走 offscreen。

    否则 headless 环境下 Qt 会尝试连 WindowServer，表现是一堆看不出因果的崩溃。
    真实剪贴板用例（`DUALSWITCH_CLIP_TEST=real`）需要 cocoa，所以那条路不覆盖。
    """
    if not os.environ.get("DUALSWITCH_CLIP_TEST"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_ensure_tmpdir()
_ensure_qt_offscreen()


# --------------------------------------------------------------------------- #
# 可选依赖
# --------------------------------------------------------------------------- #
#: 模块名 → pip 包名。缺了任何一项，**产品都会降级而不是崩**，测试同理。
OPTIONAL_MODULES: dict[str, str] = {
    "hid": "hidapi",
    "Quartz": "pyobjc-framework-Quartz",
    "AppKit": "pyobjc-framework-Cocoa",
    "objc": "pyobjc-framework-Cocoa",
    "pystray": "pystray",
}

#: `DUALSWITCH_SIMULATE_MISSING=hid,Quartz` —— 在开发机上假装它们没装。
SIMULATE_ENV = "DUALSWITCH_SIMULATE_MISSING"


class _MissingFinder(importlib.abc.MetaPathFinder):
    """让指定模块 import 失败，模拟"这台机器没装"。

    必须装在 `sys.meta_path` 最前面，并且只在 conftest 导入期装一次 ——
    测试模块的 import 都发生在这之后，所以能盖住。
    """

    def __init__(self, names: set[str]) -> None:
        self.names = names

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if fullname.split(".")[0] in self.names:
            raise ModuleNotFoundError(
                f"{fullname} 被 {SIMULATE_ENV} 屏蔽（模拟未安装）", name=fullname
            )
        return None


def _install_missing_finder() -> set[str]:
    raw = os.environ.get(SIMULATE_ENV, "")
    names = {n.strip() for n in raw.replace(";", ",").split(",") if n.strip()}
    if names:
        sys.meta_path.insert(0, _MissingFinder(names))
        # 已经 import 过的要摘掉，否则 finder 根本没机会被调用
        for name in list(names):
            sys.modules.pop(name, None)
    return names


def available(module: str) -> bool:
    """该模块此刻能不能 import（会尊重模拟开关）。"""
    try:
        importlib.import_module(module)
        return True
    except Exception:  # noqa: BLE001 - 任何异常都算"不可用"
        return False


def requires(*modules: str):
    """测试文件顶部的声明式门禁：`pytestmark = requires("AppKit")`。

    **只用于可选依赖**。硬依赖（PySide6）缺了就让它红 —— 那是装错了环境，
    不该被静默跳过掩盖。
    """
    missing = [m for m in modules if not available(m)]
    if not missing:
        return []
    return pytest.mark.skip(reason=f"缺少可选依赖：{', '.join(missing)}")


def optional_missing() -> dict[str, str]:
    """{模块名: pip 包名}，只含当前真的 import 不进来的。"""
    return {m: pkg for m, pkg in OPTIONAL_MODULES.items() if not available(m)}


_SIMULATED = _install_missing_finder()


def pytest_report_header(config):  # noqa: ARG001
    """跑之前先把环境摊开，省得把"缺依赖"误判成"改坏了"。"""
    lines = [f"解释器: {sys.executable} ({sys.version.split()[0]})"]
    missing = optional_missing()
    if missing:
        detail = "、".join(f"{m}({pkg})" for m, pkg in sorted(missing.items()))
        lines.append(f"可选依赖缺失: {detail}")
        lines.append("  → 相关功能会静默降级，对应用例跳过；这不是回归")
    if _SIMULATED:
        lines.append(f"⚠ 正在模拟缺失: {', '.join(sorted(_SIMULATED))}（{SIMULATE_ENV}）")
    return lines


# --------------------------------------------------------------------------- #
# 既有：测试一律不广播发现报文
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_discovery_broadcast(monkeypatch):
    """测试一律不广播发现报文（BUG-003）。

    测试里的引擎用默认 `ClipboardConfig()`，`discover_port` 就是 47824。只要开发机所在的
    网段里有另一台在跑 dualswitch 的机器，跑一次 `pytest` 就会把它的拨号目标改成测试引擎
    那个随机的临时端口 —— 对端日志会以每 3 秒一轮 `connecting → timed out` 一直刷。
    这个开关让引擎在发现端口上彻底不起 socket（`discover_port=0`）。
    """
    monkeypatch.setenv("DUALSWITCH_NO_DISCOVER", "1")
