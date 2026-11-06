"""环境契约：缺可选依赖时，测试必须还是绿的。

**为什么要有这组用例**：2026-11-06 在 `/usr/bin/python3`（没装 hidapi）上跑全量，
`test_mouse_logi_host` 红了 3 条。那是环境差异，不是代码回归，但现场看起来
和回归一模一样 —— 于是有人会去改**别人的**断言来"修好"它，改完就对端红。
这跟 2026-10-22 那次"平台门禁"的教训是同一类，只是从平台换成了依赖。

规矩（写在这里，也写进 docs/TROUBLESHOOTING.md）：

* **硬依赖**（PySide6、pytest）缺失 → 让它红，那是装错了环境。
* **可选依赖**（hid / Quartz / AppKit / pystray）缺失 → 产品本来就降级，
  测试只许跳过或断言"降级行为"，**不许失败**。

`DUALSWITCH_SIMULATE_MISSING` 让这件事在开发机上可复现，不用真的卸依赖。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_pytest(args: list[str], simulate: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DUALSWITCH_SIMULATE_MISSING"] = simulate
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("DUALSWITCH_CLIP_TEST", None)      # 别让外部开关把真实剪贴板用例放进来
    env.pop("DUALSWITCH_NO_DISCOVER", None)    # 交给 conftest 的 fixture 设
    return subprocess.run(
        # `-o addopts=` 清掉工程的 `-q`：`-q` 会把报告头整块压掉，
        # 而下面那条用例正是靠报告头断言"模拟开关生效"。
        [sys.executable, "-m", "pytest", "-o", "addopts=", *args,
         "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'bt'}"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300,
    )


def test_simulation_really_hides_the_module(tmp_path):
    """先把"模拟开关确实生效"钉死 —— 不然下面那条用例是空转。"""
    proc = _run_pytest(["tests/test_mouse_logi_host.py", "--collect-only"], "hid", tmp_path)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "正在模拟缺失" in proc.stdout, "报告头没提示模拟开关，说明 finder 没装上"


def test_mouse_suite_stays_green_without_hidapi(tmp_path):
    """缺 hidapi 时，鼠标联动的用例必须全绿（降级路径仍然被测到）。"""
    proc = _run_pytest(["tests/test_mouse_logi_host.py", "-q", "--tb=short"],
                       "hid", tmp_path)
    assert proc.returncode == 0, (
        "缺 hidapi 时鼠标用例红了 —— 产品本该安静降级，测试也要跟着降级，"
        "不要靠「这台机器正好装了 hid」来通过\n" + proc.stdout[-3000:]
    )


def test_keyboard_suite_stays_green_without_pyobjc(tmp_path):
    """缺 pyobjc 时键盘用例也要全绿：注入端用的是假 Quartz，本来就不该依赖真货。"""
    proc = _run_pytest(["tests/test_keyboard.py", "-q", "--tb=short"], "Quartz,AppKit", tmp_path)
    assert proc.returncode == 0, proc.stdout[-3000:]
