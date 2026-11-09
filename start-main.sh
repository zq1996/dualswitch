#!/bin/sh
# 启动 main 分支的图形界面（开发用，macOS）。
#
# 为什么不直接双击「屏幕切换.app」：那个 bundle 里装的是打包那一刻的代码，
# 改完源码看不到效果。也不要用 /usr/bin/python3 —— 它没装 hidapi / pyobjc，
# 界面能开，但 DDC 读值和全局热键都是哑的。
#
# 用法：
#   ./start-main.sh               # 启动（日志同时打到终端）
#   ./start-main.sh --dry-run     # 只记录，不真的发 Set
#   ./start-main.sh --log-level DEBUG
#
# 配置默认是 ~/Library/Application Support/dualswitch/config.json。
# 想用另一份（例如共享给 V3 调试的那份），覆盖环境变量即可：
#   DUALSWITCH_CONFIG=/path/to/config.json ./start-main.sh

set -e
DS_ROOT="$(cd "$(dirname -- "$0")" && pwd)"
. "$DS_ROOT/tools/py.sh"
PYTHON="$(ds_python)" || { echo "找不到可用的 python3（需要仓库内的 .venv）" >&2; exit 1; }

cd "$DS_ROOT"
# --console：日志同时打到终端，方便一边点界面一边看读数；其余参数原样透传。
exec "$PYTHON" -m dualswitch ui2 --console "$@"
