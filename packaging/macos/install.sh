#!/usr/bin/env bash
# macOS 开机自启（launchd 用户代理）
#
# 用法：
#   ./packaging/macos/install.sh                 # 自动找解释器（仓库内 .venv 优先）
#   ./packaging/macos/install.sh /path/python3   # 显式指定
#   ./packaging/macos/install.sh --dry-run       # 只渲染 plist 到屏幕，不写不加载
#
# 注意：launchd 拉起的是「非交互」进程，菜单栏图标可能不显示；
# 若需要稳定的菜单栏图标与辅助功能授权身份，建议用 build.sh 打成 .app 后
# 把 .app 加入「系统设置 → 通用 → 登录项」。
#
# ⚠️ 这里会把解释器路径**写死进 plist**。所以别指向
# `~/.workbuddy/binaries/python/envs/default` 这种会被别人重建的共享 venv ——
# 用仓库内 .venv（被删了就重跑本脚本）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DS_ROOT="$ROOT"          # py.sh 靠它定位仓库内 .venv
. "$ROOT/tools/py.sh"

DRY_RUN=0
PYTHON=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) PYTHON="$arg" ;;
  esac
done

if [[ -z "$PYTHON" ]]; then
  PYTHON="$(ds_python || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "找不到可用的 python3。先建仓库内环境：" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e \".[dev,mouse,macos,gui,tray]\"" >&2
  echo "或显式传入：$0 /path/to/python3" >&2
  exit 1
fi

HOME_DIR="$HOME"
PLIST_SRC="$ROOT/packaging/macos/com.dualswitch.agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.dualswitch.agent.plist"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "# 解释器: $PYTHON"
  echo "# 目标:   ${PLIST_DST}（dry-run，不写文件、不加载）"
  echo "# ----"
  sed -e "s|__PYTHON__|$PYTHON|g" \
      -e "s|__ROOT__|$ROOT|g" \
      -e "s|__HOME__|$HOME_DIR|g" \
      "$PLIST_SRC"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__ROOT__|$ROOT|g" \
    -e "s|__HOME__|$HOME_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DST"

launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "已安装并加载：$PLIST_DST"
echo "查看状态：launchctl list | grep dualswitch"
echo "查看日志：tail -f ~/Library/Logs/dualswitch.err.log"
echo ""
echo "首次运行请到 系统设置 → 隐私与安全性 → 辅助功能，勾选："
echo "  $PYTHON"
echo "否则无法监听全局热键（CGEventTap 会返回空）。"
