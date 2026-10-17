#!/usr/bin/env python3
"""生成 macOS 菜单栏模板图标 `src/dualswitch/ui/assets/menubar.png`。

模板图（Template Image）的规矩：**只用黑色 + alpha**，系统按菜单栏明暗自动反色。
所以这里不搬 design/icon 的彩色插画，而是按同一构图重画一版单色版：

    ┌──────┐   ┌──────┐
    │      │ ⇄ │      │
    └──────┘   └──────┘

18pt 高（36px @2x），1.6px 描边，两块屏错开一点，中间一支双向箭头。
太小或太花都会糊成一团——菜单栏图标的有效识别区只有 18×18。

用法：
    python design/icon/build_menubar_icon.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen  # noqa: E402

SIZE = 36          # 2x，实际显示 18pt
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "src", "dualswitch", "ui", "assets", "menubar.png")


def draw(path: str) -> None:
    img = QImage(SIZE, SIZE, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    black = QColor(0, 0, 0, 255)
    pen = QPen(black, 2.8)          # 2.8px @2x ≈ 1.4pt，接近系统图标描边
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)

    back = QRectF(14.0, 6.0, 16.0, 11.5)
    front = QRectF(6.0, 12.5, 16.0, 11.5)

    # 1) 后面的屏：描边
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(back, 2.4, 2.4)

    # 2) 把前屏位置（含描边余量）擦干净，形成"一张压在另一张上"的间隙
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.setPen(Qt.NoPen)
    p.setBrush(black)
    p.drawRoundedRect(front.adjusted(-2.6, -2.6, 2.6, 2.6), 4.0, 4.0)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    # 3) 前面的屏：描边
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(front, 2.4, 2.4)

    p.end()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not img.save(path, "PNG"):
        raise SystemExit(f"保存失败：{path}")


def main() -> int:
    draw(os.path.abspath(OUT))
    print(f"已生成：{os.path.abspath(OUT)}（{SIZE}×{SIZE}，模板图）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
