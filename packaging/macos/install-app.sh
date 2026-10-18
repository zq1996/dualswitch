#!/usr/bin/env bash
# 把打好的 .app 装进「应用程序」（/Applications）。
#
# 用法（在项目根目录执行）：
#   ./packaging/macos/install-app.sh              # 装到 /Applications/屏幕切换.app
#   ./packaging/macos/install-app.sh --login-item # 装完顺便加入登录项（开机自启）
#
# 前置：先 ./packaging/macos/build.sh 打出 dist/dualswitch.app。
#
# 为什么要动这几步：
#   · ditto 而不是 cp：保留 bundle 内的符号链接、扩展属性与签名，cp -R 会破坏框架结构
#   · 清 com.apple.quarantine：从网上下载的 zip 解开后带隔离属性，Gatekeeper 会拦
#   · 重新校验签名：辅助功能授权认签名，签名坏了热键就永久失效（还查不出原因）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$ROOT/dist/dualswitch.app"
DST="${INSTALL_PATH:-/Applications/屏幕切换.app}"
ADD_LOGIN_ITEM=0

for arg in "$@"; do
  case "$arg" in
    --login-item) ADD_LOGIN_ITEM=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) DST="$arg" ;;
  esac
done

if [[ ! -d "$SRC" ]]; then
  echo "[error] 找不到 $SRC，先跑 ./packaging/macos/build.sh" >&2
  exit 1
fi

APP_BIN="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$SRC/Contents/Info.plist" 2>/dev/null || echo dualswitch)"

# 正在运行的旧实例必须先退掉：覆盖 bundle 时它还在跑，Dock / 菜单栏会残留一个僵尸
if pgrep -f "/Contents/MacOS/$APP_BIN" >/dev/null 2>&1; then
  echo "正在退出已运行的实例…"
  pkill -f "/Contents/MacOS/$APP_BIN" || true
  sleep 1
fi

echo "安装：$SRC → $DST"
/usr/bin/ditto "$SRC" "$DST"
/usr/bin/xattr -dr com.apple.quarantine "$DST" 2>/dev/null || true

if /usr/bin/codesign --verify --deep "$DST" >/dev/null 2>&1; then
  IDENTITY="$(/usr/bin/codesign -dv "$DST" 2>&1 | sed -n 's/^Authority=//p' | head -1)"
  echo "[ok] 签名有效（${IDENTITY:-未知身份}）"
else
  echo "[warn] 签名校验失败：辅助功能授权可能失效，热键会起不来" >&2
fi

if [[ "$ADD_LOGIN_ITEM" == "1" ]]; then
  if /usr/bin/osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$DST\", hidden:false}" >/dev/null 2>&1; then
    echo "[ok] 已加入登录项（开机自启）"
  else
    echo "[warn] 自动加登录项被系统拒绝（需要「自动化」权限）。手动加："
    echo "       系统设置 → 通用 → 登录项与扩展 → 登录时打开 → ＋ → 选 $DST"
  fi
fi

echo ""
echo "已安装：$DST"
echo "  启动：open \"$DST\""
echo "  首次运行若提示缺少「辅助功能」：系统设置 → 隐私与安全性 → 辅助功能，"
echo "  勾选「屏幕切换」（列表里显示为 dualswitch）后切回应用即可。"
echo "  换路径后若开关本来是开的却仍报未授权：在列表里「−」移除再重新添加一次。"
