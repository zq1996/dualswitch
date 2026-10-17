#!/usr/bin/env bash
# macOS 打包为 .app（PyInstaller）
#
# 用法（在项目根目录执行）：
#   ./packaging/macos/build.sh
# 产物：dist/dualswitch.app（显示名「屏幕切换」）
#
# 说明：
#   · 打包入口 packaging/entrypoint.py 不带参数时直接进图形界面（菜单栏 + 主窗口）
#   · 图标用 design/icon/screen-switch.icns；没有就先跑 design/icon/build_icns.sh
#   · 打包后用 PlistBuddy 补 CFBundleDisplayName（Dock / 关于面板显示中文名）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
APP="dist/dualswitch.app"
ICNS="design/icon/screen-switch.icns"

"$PYTHON" -m pip install --quiet --upgrade pyinstaller pystray pillow \
    PySide6-Essentials \
    pyobjc-framework-Quartz pyobjc-framework-ApplicationServices pyobjc-framework-Cocoa

if [[ ! -f "$ICNS" ]]; then
    echo "[warn] 没有 $ICNS，先生成图标"
    ./design/icon/build_icns.sh || true
fi

ICON_ARGS=()
[[ -f "$ICNS" ]] && ICON_ARGS=(--icon "$ROOT/$ICNS")

# 注意：带 --specpath 时，PyInstaller 会把 --add-data / 入口脚本的相对路径
# 解析成「相对 spec 所在目录」，所以这里一律用绝对路径，避免 build/src/... 找不到。
"$PYTHON" -m PyInstaller \
    --noconfirm --clean \
    --name dualswitch \
    --windowed \
    --osx-bundle-identifier com.dualswitch.app \
    --paths "$ROOT/src" \
    --hidden-import "pystray._darwin" \
    --hidden-import "Quartz" \
    --hidden-import "ApplicationServices" \
    --hidden-import "AppKit" \
    --hidden-import "Foundation" \
    --add-data "$ROOT/src/dualswitch/ui/assets:dualswitch/ui/assets" \
    "${ICON_ARGS[@]}" \
    --distpath "$ROOT/dist" \
    --workpath "$ROOT/build" \
    --specpath "$ROOT/build" \
    "$ROOT/packaging/entrypoint.py"

# Dock / 菜单栏「关于」里显示中文名；LSUIElement=false 保留 Dock 图标（主窗口是真实窗口）
PLIST="$APP/Contents/Info.plist"
if [[ -f "$PLIST" ]]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName 屏幕切换" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 屏幕切换" "$PLIST"
    /usr/libexec/PlistBuddy -c "Add :LSUIElement bool false" "$PLIST" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 11.0" "$PLIST" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null || true
    # 版本号：PyInstaller 默认写 0.0.0，「关于」面板里很难看
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 1.0.0" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 1.0.0" "$PLIST"
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 1.0.0" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 1.0.0" "$PLIST"
fi

# ad-hoc 签名：TCC（辅助功能）按「签名 + 路径」记住授权，没签名的包每次重打包
# 都可能被系统当成新应用，导致用户要反复勾选。
/usr/bin/codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
/usr/bin/codesign --verify --deep "$APP" >/dev/null 2>&1 && echo "[ok] ad-hoc 签名已应用" || echo "[warn] 签名校验失败（不影响本机运行）"

echo ""
echo "产物：$ROOT/$APP"
echo "首次运行后请到 系统设置 → 隐私与安全性 → 辅助功能 勾选 屏幕切换.app。"
echo "加入登录项：系统设置 → 通用 → 登录项 → 添加 屏幕切换.app"
