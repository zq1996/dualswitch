#!/usr/bin/env bash
# macOS 开机自启（launchd 用户代理）
#
# 用法：
#   ./packaging/macos/install.sh /opt/homebrew/bin/python3
#
# 注意：launchd 拉起的是「非交互」进程，菜单栏图标可能不显示；
# 若需要稳定的菜单栏图标与辅助功能授权身份，建议用 build.sh 打成 .app 后
# 把 .app 加入「系统设置 → 通用 → 登录项」。

set -euo pipefail

PYTHON="${1:-}"
if [[ -z "$PYTHON" ]]; then
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "找不到 python3，请显式传入解释器路径" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOME_DIR="$HOME"
PLIST_SRC="$ROOT/packaging/macos/com.dualswitch.agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.dualswitch.agent.plist"

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
