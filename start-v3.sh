#!/bin/sh
# 启动 V3 图形界面（开发用）。
#
# 为什么要单开一个脚本：V3 与 V2 是两个独立 worktree，V2 的「屏幕切换.app」里
# 装的是 V2 代码，双击它永远起不来 V3；而 `python -m dualswitch ui3` 又要求
# cwd 在 V3 目录、用 V3 的 .venv（系统 python3 没装 PySide6/pyobjc）。
#
# 配置默认沿用 build/v3-demo/config.json —— 现有的标定结果、配对信息都在那份里。
# 想用独立的用户配置（~/Library/Application Support/dualswitch-v3/config.json），
# 就设 DUALSWITCH_CONFIG 覆盖。

set -e
DS_ROOT="$(cd "$(dirname -- "$0")" && pwd)"
. "$DS_ROOT/tools/py.sh"
PYTHON="$(ds_python)" || { echo "找不到 python3" >&2; exit 1; }

if [ -z "${DUALSWITCH_CONFIG:-}" ]; then
    for candidate in \
        "$DS_ROOT/build/v3-demo/config.json" \
        "$DS_ROOT/../dualswitch/build/v3-demo/config.json"
    do
        if [ -f "$candidate" ]; then
            DUALSWITCH_CONFIG="$candidate"
            break
        fi
    done
fi
if [ -n "${DUALSWITCH_CONFIG:-}" ]; then
    export DUALSWITCH_CONFIG
    # 日志放在配置旁边，和之前那段调试保持一致
    DUALSWITCH_LOG="${DUALSWITCH_LOG:-$(dirname -- "$DUALSWITCH_CONFIG")/dualswitch.log}"
    export DUALSWITCH_LOG
fi

# --console：日志同时打到终端，方便一边点标定一边看读数；其余参数原样透传。
exec "$PYTHON" -m dualswitch ui3 --console "$@"
