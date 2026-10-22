#!/usr/bin/env python3
"""生成 macOS 菜单栏模板图标 `src/dualswitch/ui/assets/menubar.png`。

模板图（Template Image）只允许**黑色 + alpha**，系统按菜单栏明暗自动反色。

构图上与 Dock 图标同源（两支反向箭头），但菜单栏有效识别区只有 18×18pt，
所以这里用**完整的三角头**而不是 Dock 图上那个「半个尖」——半尖在 18px 下
只剩两个小疙瘩，认不出来（实测对比过）。

用法：
    python3 design/icon/build_menubar_icon.py
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "src" / "dualswitch" / "ui" / "assets" / "menubar.png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

SIZE = 36  # 2x；显示为 18pt

# 36×36 画布上的两支箭头（上朝左、下朝右），纯黑填充
BODY = (
    '<g fill="#000000">'
    '<path d="M 30 10.0 L 30 14.4 L 13 14.4 L 13 18.2 L 4.6 12.2 L 13 6.2 L 13 10.0 Z"/>'
    '<path d="M 6 25.8 L 6 21.4 L 23 21.4 L 23 17.8 L 31.4 23.8 L 23 29.8 L 23 25.8 Z"/>'
    "</g>"
)


def build_svg(size: int = SIZE) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 36 36">{BODY}</svg>')


def main() -> int:
    if not Path(CHROME).exists():
        raise SystemExit(f"找不到 Chrome：{CHROME}")
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "mb.html"
        html.write_text(
            "<!doctype html><meta charset=utf-8>"
            "<style>html,body{margin:0;padding:0;background:transparent}svg{display:block}</style>"
            + build_svg()
        )
        OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--disable-software-rasterizer", "--disable-dev-shm-usage",
             "--hide-scrollbars", "--default-background-color=00000000",
             "--force-device-scale-factor=1", f"--window-size={SIZE},{SIZE}",
             f"--screenshot={OUT}", f"file://{html}"],
            check=True, capture_output=True,
        )
    print(f"已生成：{OUT}（{SIZE}×{SIZE}，模板图，显示 18pt）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
