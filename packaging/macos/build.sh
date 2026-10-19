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
    --hidden-import "dualswitch.ui.chrome.mac" \
    --hidden-import "dualswitch.ui.chrome.win" \
    --add-data "$ROOT/src/dualswitch/ui/assets:dualswitch/ui/assets" \
    "${ICON_ARGS[@]}" \
    --distpath "$ROOT/dist" \
    --workpath "$ROOT/build" \
    --specpath "$ROOT/build" \
    "$ROOT/packaging/entrypoint.py"

# 翻译裁剪：Qt 自带 200 个各语言翻译（.qm，约 7MB），本工具只用中文，且代码里
# 没有安装 QTranslator —— 除 zh_CN 外全是死重量。打包后直接删掉，只留中文。
# 必须放在 codesign 之前：删文件会让签名失效，晚了就得重新签一遍。
PRUNED=0
while IFS= read -r -d '' qm; do
    if [[ "$qm" != *_zh_CN.qm ]]; then
        rm -f "$qm"
        PRUNED=$((PRUNED + 1))
    fi
done < <(find "$APP" -type f -name '*.qm' -print0)
echo "[ok] 翻译只保留中文（zh_CN），已删除 $PRUNED 个其它语言的 .qm"

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

# 签名：TCC（辅助功能）授权是「认签名」的，选错身份会导致每次重打包都要重新勾选。
#   · ad-hoc（--sign -）：designated requirement 是**纯 cdhash**，重打包换了哈希，
#     旧授权立刻失效 —— 症状是「开关明明开着，程序还说没授权」。
#   · 固定证书：DR 变成「identifier + 证书」，授权能跨重打包存活。
# 所以这里优先找一个名字以 dualswitch 开头的自签名证书（建一次即可，见文件末尾提示），
# 找不到才退回 ad-hoc；也可以用 CODESIGN_IDENTITY="名字" 显式指定。
IDENTITY="${CODESIGN_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
    IDENTITY="$(/usr/bin/security find-identity -v -p codesigning 2>/dev/null \
        | sed -n 's/.*"\(dualswitch[^"]*\)".*/\1/p' | head -1)"
fi
if [[ -z "$IDENTITY" ]]; then
    IDENTITY="-"
    echo "[warn] 没找到 dualswitch 自签名证书，退回 ad-hoc：重打包后辅助功能要重新勾选"
fi

/usr/bin/codesign --force --deep --sign "$IDENTITY" "$APP" >/dev/null 2>&1 || true
if /usr/bin/codesign --verify --deep "$APP" >/dev/null 2>&1; then
    if [[ "$IDENTITY" == "-" ]]; then
        echo "[ok] ad-hoc 签名已应用"
    else
        echo "[ok] 已用固定身份签名：${IDENTITY}（重打包不用重新授权）"
    fi
else
    echo "[warn] 签名校验失败（不影响本机运行）"
fi

# 打包自检：用字符串拼模块名的动态导入（importlib.import_module）会被分析器漏掉，
# 源码跑得好好的、双击 .app 却立刻闪退，日志里只有一句 ModuleNotFoundError。
# 这里让产物自己 import 一遍界面栈（--selfcheck），缺模块就构建失败。
# 注意别用 strings 查模块名：PYZ 是压缩的，正常产物也查不到。
if ! "$APP/Contents/MacOS/dualswitch" --selfcheck; then
    echo "[error] 打包产物缺少界面模块，多半是 chrome/__init__.py 又用了动态导入" >&2
    exit 1
fi

echo ""
echo "产物：$ROOT/$APP"
echo "首次运行后请到 系统设置 → 隐私与安全性 → 辅助功能 勾选 屏幕切换.app。"
echo "加入登录项：系统设置 → 通用 → 登录项 → 添加 屏幕切换.app"
