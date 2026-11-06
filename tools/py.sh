# 解释器解析 —— 全项目只有这一处规则，脚本一律 source 它，别再各写各的。
#
# 优先级（显式的优先，然后仓库内 venv，最后才 PATH）：
#   1. $PYTHON                       历史名字，老命令仍然有效
#   2. $DUALSWITCH_PY                更明确的新名字
#   3. $DS_ROOT/.venv/bin/python     仓库内环境（.gitignore 已忽略）
#   4. PATH 里的 python3
#
# 为什么不让脚本直接写 `python3`：这台机器上 PATH 的 python3 是 Homebrew 3.15
# （零依赖），系统 /usr/bin/python3 是 CLT 3.9.6（也没有 pyobjc）。打包和跑测试
# 都踩过"解析到别的解释器"的坑，所以统一收口。
#
# 用法（调用方必须先设 DS_ROOT）：
#
#     DS_ROOT="$(cd "$(dirname -- "$0")/.." && pwd)"
#     . "$DS_ROOT/tools/py.sh"
#     PYTHON="$(ds_python)" || { echo "找不到 python3" >&2; exit 1; }
#
# 刻意写成 POSIX sh：logi-mouse.sh 用的是 #!/bin/sh，macOS 自带 bash 3.2 也要能跑。

ds_python() {
    if [ -n "${PYTHON:-}" ]; then
        printf '%s\n' "$PYTHON"
        return 0
    fi
    if [ -n "${DUALSWITCH_PY:-}" ]; then
        printf '%s\n' "$DUALSWITCH_PY"
        return 0
    fi
    if [ -n "${DS_ROOT:-}" ] && [ -x "$DS_ROOT/.venv/bin/python" ]; then
        printf '%s\n' "$DS_ROOT/.venv/bin/python"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    return 1
}

# 解释器能不能 import 某个模块。给"先体检再动手"用（打包、hid）。
ds_python_has() {
    "$1" -c "import $2" >/dev/null 2>&1
}
