#!/bin/sh
# Screen Switch - Logitech Easy-Switch (HID++ host switch) wrapper for macOS/Linux.
#
# Same reason as logi-mouse.bat on Windows: give one stable entry point that
# uses the interpreter which actually has hidapi installed.
#
# Usage:
#   ./logi-mouse.sh list
#   ./logi-mouse.sh probe
#   ./logi-mouse.sh switch 2
#
# 解释器怎么选：走 tools/py.sh 的统一规则（$PYTHON / $DUALSWITCH_PY → 仓库内 .venv → PATH）。
#
# 以前这里自己按 `python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3`
# 的顺序试，结果是"包装到 A、脚本跑在 B"—— 报错只说 hidapi 没装，不说装在哪个解释器上，
# 排起来极费劲（2026-11-06 记）。现在只解析一次，缺 hid 就把**该装的那条命令**打出来。

set -e

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DS_ROOT=$ROOT
. "$ROOT/tools/py.sh"

PY=$(ds_python || true)
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
    echo "找不到 python3。先建仓库内环境：" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -e \".[dev,mouse,macos,gui,tray]\"" >&2
    exit 1
fi

if ! ds_python_has "$PY" hid; then
    echo "hidapi 没装在下面这个解释器里：" >&2
    echo "  $PY" >&2
    echo "装上它（必须是同一个解释器，装到别的 python3 等于没装）：" >&2
    echo "  $PY -m pip install hidapi" >&2
    exit 1
fi

exec "$PY" "$ROOT/tools/logi_host_switch.py" "$@"
