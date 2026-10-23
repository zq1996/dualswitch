import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


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


_ensure_tmpdir()


@pytest.fixture(autouse=True)
def _no_discovery_broadcast(monkeypatch):
    """测试一律不广播发现报文（BUG-003）。

    测试里的引擎用默认 `ClipboardConfig()`，`discover_port` 就是 47824。只要开发机所在的
    网段里有另一台在跑 dualswitch 的机器，跑一次 `pytest` 就会把它的拨号目标改成测试引擎
    那个随机的临时端口 —— 对端日志会以每 3 秒一轮 `connecting → timed out` 一直刷。
    这个开关让引擎在发现端口上彻底不起 socket（`discover_port=0`）。
    """
    monkeypatch.setenv("DUALSWITCH_NO_DISCOVER", "1")
