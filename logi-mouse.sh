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
# First run needs hidapi:
#   python3 -m pip install --user hidapi
#   Homebrew python may need: python3 -m pip install --break-system-packages hidapi

set -e
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PY=${PYTHON:-}
if [ -z "$PY" ]; then
    for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        if command -v "$c" >/dev/null 2>&1; then PY=$c; break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "python3 not found. Install Python 3 first." >&2
    exit 1
fi

if ! "$PY" -c "import hid" >/dev/null 2>&1; then
    echo "hidapi is not installed for $PY" >&2
    echo "Run one of:" >&2
    echo "  $PY -m pip install --user hidapi" >&2
    echo "  $PY -m pip install --break-system-packages hidapi" >&2
    exit 1
fi

exec "$PY" "$ROOT/tools/logi_host_switch.py" "$@"
