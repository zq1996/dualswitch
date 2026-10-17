#!/usr/bin/env bash
# macOS 打包为 .app（PyInstaller）
#
# 用法（在项目根目录执行）：
#   ./packaging/macos/build.sh
# 产物：dist/dualswitch.app

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

"$PYTHON" -m pip install --quiet --upgrade pyinstaller pystray pillow \
    pyobjc-framework-Quartz pyobjc-framework-ApplicationServices

"$PYTHON" -m PyInstaller \
    --noconfirm --clean \
    --name dualswitch \
    --windowed \
    --osx-bundle-identifier com.dualswitch.app \
    --paths "src" \
    --hidden-import "pystray._darwin" \
    --hidden-import "Quartz" \
    --hidden-import "ApplicationServices" \
    --distpath "dist" \
    --workpath "build" \
    --specpath "build" \
    "packaging/entrypoint.py"

echo ""
echo "产物：$ROOT/dist/dualswitch.app"
echo "首次运行后请到 系统设置 → 隐私与安全性 → 辅助功能 勾选 dualswitch.app。"
echo "加入登录项：系统设置 → 通用 → 登录项 → 添加 dualswitch.app"
