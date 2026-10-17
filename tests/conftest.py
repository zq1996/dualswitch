import os
import sys
import tempfile
from pathlib import Path

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
