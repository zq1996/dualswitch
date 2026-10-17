#!/usr/bin/env bash
# 兼容入口：真正的实现是 design/icon/build_icns.py（纯 Python，不依赖 sips/iconutil）
#
# 保留这个文件名只是为了让旧文档/习惯命令还能用。

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "${PYTHON:-python3}" "$ROOT/design/icon/build_icns.py"
