#!/usr/bin/env python3
"""生成 macOS 菜单栏模板图标 `src/dualswitch/ui/assets/menubar.png`。

模板图（Template Image）只允许**黑色 + alpha**，系统按菜单栏明暗自动反色。

构图（2026-10-22 用户指定）：取 Dock 图标的「白色屏幕」当底板，屏内两支箭头做成
**镂空**（透明），也就是底板实心、箭头位置是洞。箭头几何**原样照抄** Dock 图标
（`build_icon.py` BODY 里那两条 stroke 路径，含半尖三角头 / 圆头 / miter 18），
这里只做等比缩放，不改任何路径数据。

为什么必须镂空而不是「黑底白箭头」：模板图只看 alpha，白色像素也是不透明，
画成白色箭头等于整块实心，系统反色时不会出现白色的箭头。
所以用 SVG mask 把箭头从底板里挖掉。

`ARROW_SCALE`：箭头相对 Dock 图标原比例的放大倍数。18pt 有效区里箭头缝越宽越好认，
1.0 = 与 Dock 图完全一致（默认），1.2~1.35 更清楚但底板会只剩一圈边。

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

SIZE = 36        # 2x；显示为 18pt
PAD = 1.0        # 四周留白（36 画布单位）
ARROW_SCALE = 1.0
SIDE = 620       # 底板边长（1024 坐标系，正方形）
RX_RATIO = 0.20  # 圆角 = 边长 × 该比例（0.09 接近直角，0.20 接近 App 图标）

# Dock 图标几何，原样照抄（design/icon/build_icon.py 的 BODY）：
# 屏幕 640×400，中心 (512,478)；箭头组 scale 0.8107，stroke 100
SCREEN_CX, SCREEN_CY = 512, 478
ARROWS = (
    '<g transform="translate(512.00,478.00) scale(0.8107)" fill="none" '
    'stroke="#000000" stroke-width="100" stroke-linecap="round" '
    'stroke-linejoin="miter" stroke-miterlimit="18">'
    '<path d="M 200.00 -76.00 L -200.00 -76.00 L -120.33 -142.85"/>'
    '<path d="M -200.00 76.00 L 200.00 76.00 L 120.33 142.85"/>'
    "</g>"
)


def build_svg() -> str:
    w = SIZE - PAD * 2
    sx = w / SIDE
    x = PAD
    y = (SIZE - w) / 2
    left = SCREEN_CX - SIDE / 2
    top = SCREEN_CY - SIDE / 2
    rx = SIDE * RX_RATIO
    arrows = ARROWS
    if ARROW_SCALE != 1.0:
        # 只放大箭头组（等比、以屏心为原点），路径数据不动
        arrows = (f'<g transform="translate({SCREEN_CX},{SCREEN_CY}) scale({ARROW_SCALE}) '
                  f'translate(-{SCREEN_CX},-{SCREEN_CY})">{ARROWS}</g>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" '
        f'viewBox="0 0 {SIZE} {SIZE}">'
        '<defs><mask id="k" maskUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">'
        '<rect x="0" y="0" width="1024" height="1024" fill="#FFFFFF"/>'
        f"{arrows}"
        "</mask></defs>"
        f'<g transform="translate({x:.3f},{y:.3f}) scale({sx:.6f}) '
        f'translate({-left:.3f},{-top:.3f})">'
        f'<rect x="{left}" y="{top}" width="{SIDE}" height="{SIDE}" rx="{rx:.2f}" '
        'fill="#000000" mask="url(#k)"/></g>'
        "</svg>"
    )


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
    print(f"已生成：{OUT}（{SIZE}×{SIZE}，模板图，显示 18pt，箭头 {ARROW_SCALE}×）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
