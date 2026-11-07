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

# 解释器解析只有一处规则（tools/py.sh）：$PYTHON / $DUALSWITCH_PY → 仓库内 .venv → PATH。
# 以前是裸 `python3`，在装了 Homebrew 的机器上会解析到 3.15（零依赖），
# 于是下面的 pip install 会往它里面现装一套，打出来的包用一个谁也没验证过的解释器。
DS_ROOT="$ROOT"          # py.sh 靠它定位仓库内 .venv
. "$ROOT/tools/py.sh"
PYTHON="$(ds_python)" || {
    echo "✗ 找不到 python3。先建仓库内环境：" >&2
    echo "    python3 -m venv .venv && .venv/bin/pip install -e \".[dev,mouse,macos,gui,tray]\"" >&2
    exit 1
}
APP="dist/dualswitch.app"
ICNS="design/icon/screen-switch.icns"

# 先体检再动手：把"缺依赖"报在这里，别让它变成 PyInstaller 深处一句看不懂的 ImportError。
if ! ds_python_has "$PYTHON" PySide6; then
    RESOLVED="$("$PYTHON" -c 'import sys; print(sys.executable)' 2>/dev/null || echo "$PYTHON")"
    cat >&2 <<EOF
✗ 打包解释器缺 PySide6：$RESOLVED

  显式指定装了项目依赖的那个解释器：
    PYTHON=/path/to/python3 ./packaging/macos/build.sh

  或者先建仓库内环境（推荐）：
    python3 -m venv .venv && .venv/bin/pip install -e ".[dev,mouse,macos,gui,tray]"

  体检：tools/check_env.py
EOF
    exit 1
fi
echo "打包解释器: $("$PYTHON" -c 'import sys; print(sys.executable, sys.version.split()[0])')"

# PyInstaller 默认把构建缓存写到 ~/Library/Application Support/pyinstaller。受限环境
# （只读 / 沙箱拦删）写不进去会直接失败，换到仓库内的 build/.pyinstaller-cache。
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT/build/.pyinstaller-cache}"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

# --clean 会删掉上一轮的 bincache，受限环境里常常被拦（报 Operation not permitted）。
# 需要干净构建时再用；日常增量构建 NO_CLEAN=1 跳过即可。
# 展开用 `${ARR[@]+"${ARR[@]}"}`：macOS 自带的是 bash 3.2，空数组配 `set -u` 会报
# "unbound variable"（踩过）。
CLEAN_ARGS=(--clean)
[[ "${NO_CLEAN:-0}" == "1" ]] && CLEAN_ARGS=()

# 只补缺的：环境已经齐了就跑 pip 都不跑（离线也能打包）。
# 以前这里无条件 `pip install --upgrade ...`，每打一次包都会把 PySide6 / pyobjc
# 悄悄升到最新版 —— "测试跑过的版本"和"打进包的版本"可能不是一个；网络慢的时候
# 还会卡在解析上十几分钟。
if ! ds_python_has "$PYTHON" PyInstaller; then
    "$PYTHON" -m pip install --quiet pyinstaller pystray pillow \
        PySide6-Essentials \
        pyobjc-framework-Quartz pyobjc-framework-ApplicationServices pyobjc-framework-Cocoa
fi

if [[ ! -f "$ICNS" ]]; then
    echo "[warn] 没有 $ICNS，先生成图标"
    ./design/icon/build_icns.sh || true
fi

ICON_ARGS=()
[[ -f "$ICNS" ]] && ICON_ARGS=(--icon "$ROOT/$ICNS")


# 注意：带 --specpath 时，PyInstaller 会把 --add-data / 入口脚本的相对路径
# 解析成「相对 spec 所在目录」，所以这里一律用绝对路径，避免 build/src/... 找不到。
"$PYTHON" -m PyInstaller \
    --noconfirm ${CLEAN_ARGS[@]+"${CLEAN_ARGS[@]}"} \
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
    --hidden-import "dualswitch.ui.mac2" \
    --hidden-import "dualswitch.ui.mac2.runner" \
    --hidden-import "dualswitch.ui.mac2.iconlib" \
    --hidden-import "dualswitch.ui.mac2.pairing_page" \
    --hidden-import "dualswitch.ui.mac2.preview" \
    --hidden-import "dualswitch.ui.mac2.statusmenu" \
    --hidden-import "dualswitch.ui.mac2.settings_page" \
    --hidden-import "dualswitch.ui.mac2.switch_page" \
    --hidden-import "dualswitch.ui.mac2.clipboard_page" \
    --hidden-import "dualswitch.ui.mac2.window" \
    --hidden-import "dualswitch.ui.mac2.widgets" \
    --hidden-import "dualswitch.ui.mac2.theme" \
    --hidden-import "PySide6.QtSvg" \
    --add-data "$ROOT/src/dualswitch/ui/assets:dualswitch/ui/assets" \
    "${ICON_ARGS[@]}" \
    --distpath "$ROOT/dist" \
    --workpath "$ROOT/build" \
    --specpath "$ROOT/build" \
    "$ROOT/packaging/entrypoint.py"

# 翻译裁剪：Qt 自带 200 个各语言翻译（.qm，约 7MB），本工具只用中文，且代码里
# 没有安装 QTranslator —— 除 zh_CN 外全是死重量。打包后直接删掉，只留中文。
# 必须放在 codesign 之前：删文件会让签名失效，晚了就得重新签一遍。
# 删不掉不算致命（受限环境会拦批量删除）：留着只多 7MB，构建要继续往下走。
PRUNED=0
SKIPPED=0
while IFS= read -r -d '' qm; do
    if [[ "$qm" != *_zh_CN.qm ]]; then
        if rm -f "$qm" 2>/dev/null; then
            PRUNED=$((PRUNED + 1))
        else
            SKIPPED=$((SKIPPED + 1))
        fi
    fi
done < <(find "$APP" -type f -name '*.qm' -print0)
if [[ "$SKIPPED" -gt 0 ]]; then
    echo "[warn] 有 $SKIPPED 个 .qm 删不掉（受限环境拦批量删除），产物会大 ~7MB" >&2
fi
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
