#!/usr/bin/env python
"""v2 产品图标生成脚本（自绘几何，不是 Frset 素材拷贝）。

对照 `design/redesign/icons/frset-extracted/` 的光学重量（描边粗细、圆角、留白）重绘，
输出到 `src/dualswitch/ui/assets/`：

  来源系统角标  badge-mac.png / badge-win.png          （19×19 @2x = 38）
  剪贴板类型    type-text/image/link/file.png          （44×44 @2x = 88）
  行内工具      tool-*.png                              （20×20 @2x = 40）
  侧栏导航      nav-*.png                               （24×24 @2x = 48）
  空态插画      empty-clip.png / empty-filter.png       （82×62 @2x = 164×124）

红线：frset-extracted/ 只作视觉比对，**不打进产品包**；这里所有形状都是
QPainter 自绘（圆角矩形 / 圆 / 折线 / 贝塞尔），与提取图无像素拷贝关系。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 托管环境的 PySide6（脚本可独立运行）
sys.path.insert(
    0,
    "/Users/zhqi/.workbuddy/binaries/python/envs/default/lib/python3.13/site-packages",
)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QTransform,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "src" / "dualswitch" / "ui" / "assets"
SCALE = 2  # 视网膜

WHITE = QColor("#FFFFFF")
INK = QColor("#4B5566")
ACCENT = QColor("#4E87FF")


def canvas(w: int, h: int) -> QImage:
    img = QImage(w * SCALE, h * SCALE, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    img.setDevicePixelRatio(SCALE)
    return img


def painter(img: QImage) -> QPainter:
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    return p


def save(img: QImage, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    img.save(str(path))
    print(f"  {name:22s} {img.width()}x{img.height()}")


# --------------------------------------------------------------------------- #
# 线性小图标（工具行 / 侧栏）：1.7 描边、圆头圆角，24 视觉格
# --------------------------------------------------------------------------- #
def _stroke(p: QPainter, color: QColor, width: float = 1.7) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def draw_tool(name: str, fn, color: QColor = INK, size: int = 20) -> None:
    img = canvas(size, size)
    p = painter(img)
    _stroke(p, color)
    fn(p, size)
    p.end()
    save(img, name)


def _glyph_copy(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawRoundedRect(QRectF(7 * r, 7 * r, 9.5 * r, 9.5 * r), 2.2 * r, 2.2 * r)
    path = QPainterPath()
    path.moveTo(13 * r, 7 * r)
    path.lineTo(13 * r, 5 * r)
    path.lineTo(5 * r, 5 * r)
    path.lineTo(5 * r, 13 * r)
    path.lineTo(7 * r, 13 * r)
    p.drawPath(path)


def _glyph_trash(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawLine(QPointF(4.5 * r, 6.5 * r), QPointF(15.5 * r, 6.5 * r))
    path = QPainterPath()
    path.moveTo(8 * r, 6.5 * r)
    path.lineTo(8 * r, 5.4 * r)
    path.lineTo(12 * r, 5.4 * r)
    path.lineTo(12 * r, 6.5 * r)
    p.drawPath(path)
    path = QPainterPath()
    path.moveTo(6.3 * r, 6.5 * r)
    path.lineTo(7 * r, 15 * r)
    path.lineTo(13 * r, 15 * r)
    path.lineTo(13.7 * r, 6.5 * r)
    p.drawPath(path)
    p.drawLine(QPointF(8.9 * r, 9 * r), QPointF(8.9 * r, 12.6 * r))
    p.drawLine(QPointF(11.1 * r, 9 * r), QPointF(11.1 * r, 12.6 * r))


def _glyph_pause(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawLine(QPointF(8 * r, 5.5 * r), QPointF(8 * r, 14.5 * r))
    p.drawLine(QPointF(12 * r, 5.5 * r), QPointF(12 * r, 14.5 * r))


def _glyph_resume(p: QPainter, s: float) -> None:
    r = s / 20.0
    path = QPainterPath()
    path.moveTo(6.5 * r, 5 * r)
    path.lineTo(14.5 * r, 10 * r)
    path.lineTo(6.5 * r, 15 * r)
    path.closeSubpath()
    p.drawPath(path)


def _glyph_search(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawEllipse(QRectF(4.6 * r, 4.6 * r, 7.4 * r, 7.4 * r))
    p.drawLine(QPointF(10.6 * r, 10.6 * r), QPointF(14.6 * r, 14.6 * r))


def _glyph_finder(p: QPainter, s: float) -> None:
    r = s / 20.0
    path = QPainterPath()
    path.moveTo(3.5 * r, 7 * r)
    path.lineTo(16.5 * r, 7 * r)
    path.lineTo(16.5 * r, 15 * r)
    path.lineTo(3.5 * r, 15 * r)
    path.closeSubpath()
    p.drawPath(path)
    path = QPainterPath()
    path.moveTo(3.5 * r, 7 * r)
    path.lineTo(5.4 * r, 3.8 * r)
    path.lineTo(13 * r, 3.8 * r)
    path.lineTo(14.6 * r, 7 * r)
    p.drawPath(path)


def _glyph_link(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawArc(QRectF(2.8 * r, 7 * r, 8 * r, 8 * r), 90 * 16, 180 * 16)
    p.drawArc(QRectF(9.2 * r, 5 * r, 8 * r, 8 * r), 270 * 16, 180 * 16)
    p.drawLine(QPointF(6.6 * r, 10 * r), QPointF(13.4 * r, 10 * r))


def _glyph_preview(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawRoundedRect(QRectF(2.5 * r, 4.5 * r, 15 * r, 11 * r), 2.2 * r, 2.2 * r)
    p.drawEllipse(QRectF(5.6 * r, 6.8 * r, 2.2 * r, 2.2 * r))
    path = QPainterPath()
    path.moveTo(3.6 * r, 13.6 * r)
    path.lineTo(7.4 * r, 10 * r)
    path.lineTo(10.2 * r, 12.4 * r)
    path.lineTo(12.4 * r, 10.4 * r)
    path.lineTo(16.4 * r, 14 * r)
    p.drawPath(path)


def _glyph_close(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawLine(QPointF(5.5 * r, 5.5 * r), QPointF(14.5 * r, 14.5 * r))
    p.drawLine(QPointF(14.5 * r, 5.5 * r), QPointF(5.5 * r, 14.5 * r))


def _glyph_retry(p: QPainter, s: float) -> None:
    r = s / 20.0
    p.drawArc(QRectF(4.2 * r, 4.2 * r, 11.6 * r, 11.6 * r), 300 * 16, 240 * 16)
    path = QPainterPath()
    path.moveTo(15.4 * r, 4.2 * r)
    path.lineTo(15.4 * r, 8.4 * r)
    path.lineTo(11.2 * r, 8.4 * r)
    p.drawPath(path)


# --------------------------------------------------------------------------- #
# 侧栏导航（18 视觉格 → 24 图）
# --------------------------------------------------------------------------- #
def draw_nav(name: str, fn, color: QColor = INK) -> None:
    img = canvas(24, 24)
    p = painter(img)
    _stroke(p, color, 1.8)
    fn(p, 24)
    p.end()
    save(img, name)


def _nav_switch(p: QPainter, s: float) -> None:
    r = s / 24.0
    p.drawLine(QPointF(3 * r, 8 * r), QPointF(16 * r, 8 * r))
    path = QPainterPath()
    path.moveTo(12.5 * r, 4.6 * r)
    path.lineTo(16 * r, 8 * r)
    path.lineTo(12.5 * r, 11.4 * r)
    p.drawPath(path)
    p.drawLine(QPointF(21 * r, 16 * r), QPointF(8 * r, 16 * r))
    path = QPainterPath()
    path.moveTo(11.5 * r, 12.6 * r)
    path.lineTo(8 * r, 16 * r)
    path.lineTo(11.5 * r, 19.4 * r)
    p.drawPath(path)


def _nav_clip(p: QPainter, s: float) -> None:
    r = s / 24.0
    p.drawRoundedRect(QRectF(5 * r, 4.5 * r, 14 * r, 15.5 * r), 2.6 * r, 2.6 * r)
    path = QPainterPath()
    path.moveTo(9.3 * r, 4.5 * r)
    path.lineTo(9.3 * r, 3.6 * r)
    path.lineTo(14.7 * r, 3.6 * r)
    path.lineTo(14.7 * r, 4.5 * r)
    p.drawPath(path)


def _nav_settings(p: QPainter, s: float) -> None:
    r = s / 24.0
    p.drawEllipse(QRectF(9.2 * r, 9.2 * r, 5.6 * r, 5.6 * r))
    for i in range(8):
        t = QTransform()
        t.translate(12 * r, 12 * r)
        t.rotate(i * 45)
        p.save()
        p.setTransform(t)
        p.drawLine(QPointF(0, -8.2 * r), QPointF(0, -6.2 * r))
        p.restore()


# --------------------------------------------------------------------------- #
# 类型瓷砖（44 视觉格）：渐变圆角底 + 白色字形 —— 对照提取图的光学重量
# --------------------------------------------------------------------------- #
def draw_tile(name: str, fn, top: str, bottom: str) -> None:
    img = canvas(44, 44)
    p = painter(img)
    grad = QLinearGradient(0, 0, 44, 44)
    grad.setColorAt(0.0, QColor(top))
    grad.setColorAt(1.0, QColor(bottom))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(0.5, 0.5, 43, 43), 11, 11)
    p.setPen(Qt.NoPen)
    p.setBrush(WHITE)
    fn(p, 44)
    p.end()
    save(img, name)


def _tile_text(p: QPainter, s: float) -> None:
    """衬线感的 T（横细竖粗），对照 clipboard_type_text 的字面重量。"""
    r = s / 44.0
    p.drawRect(QRectF(13 * r, 11.5 * r, 18 * r, 3.6 * r))          # 横
    p.drawRect(QRectF(20.2 * r, 11.5 * r, 3.6 * r, 21 * r))        # 竖
    p.drawRect(QRectF(17.6 * r, 29.5 * r, 8.8 * r, 3.0 * r))       # 底衬脚


def _tile_image(p: QPainter, s: float) -> None:
    r = s / 44.0
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(10 * r, 11 * r, 24 * r, 22 * r), 3.2 * r, 3.2 * r)
    # 用底色镂出山与太阳（负形）
    p.setBrush(QColor("#5B6BE8"))
    p.drawEllipse(QRectF(14 * r, 14.5 * r, 4.4 * r, 4.4 * r))
    path = QPainterPath()
    path.moveTo(10 * r, 30 * r)
    path.lineTo(17 * r, 21.5 * r)
    path.lineTo(22.4 * r, 27 * r)
    path.lineTo(26.8 * r, 22.6 * r)
    path.lineTo(34 * r, 30 * r)
    path.closeSubpath()
    p.drawPath(path)


def _tile_link(p: QPainter, s: float) -> None:
    """链条（自绘几何；提取图是 Safari 指南针 —— 品牌图形不抄）。"""
    r = s / 44.0
    pen = QPen(WHITE, 3.4)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(10.5 * r, 16 * r, 13 * r, 13 * r), 90 * 16, 180 * 16)
    p.drawArc(QRectF(20.5 * r, 13 * r, 13 * r, 13 * r), 270 * 16, 180 * 16)
    p.drawLine(QPointF(16 * r, 22.5 * r), QPointF(28 * r, 22.5 * r))


def _tile_file(p: QPainter, s: float) -> None:
    r = s / 44.0
    p.setPen(Qt.NoPen)
    path = QPainterPath()
    path.moveTo(13 * r, 9.5 * r)
    path.lineTo(24 * r, 9.5 * r)
    path.lineTo(31 * r, 16.5 * r)
    path.lineTo(31 * r, 34.5 * r)
    path.lineTo(13 * r, 34.5 * r)
    path.closeSubpath()
    p.drawPath(path)
    p.setBrush(QColor("#4E5DE0"))
    p.drawRect(QRectF(17 * r, 21 * r, 10 * r, 2 * r))
    p.drawRect(QRectF(17 * r, 25.5 * r, 14 * r, 2 * r))
    p.drawRect(QRectF(17 * r, 30 * r, 7 * r, 2 * r))
    fold = QPainterPath()
    fold.moveTo(24 * r, 9.5 * r)
    fold.lineTo(24 * r, 16.5 * r)
    fold.lineTo(31 * r, 16.5 * r)
    fold.closeSubpath()
    p.drawPath(fold)


def _tile_code(p: QPainter, s: float) -> None:
    """尖括号 + 斜杠（代码）。"""
    r = s / 44.0
    pen = QPen(WHITE, 3.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    left = QPainterPath()
    left.moveTo(17.5 * r, 15 * r)
    left.lineTo(10.5 * r, 22 * r)
    left.lineTo(17.5 * r, 29 * r)
    p.drawPath(left)
    right = QPainterPath()
    right.moveTo(26.5 * r, 15 * r)
    right.lineTo(33.5 * r, 22 * r)
    right.lineTo(26.5 * r, 29 * r)
    p.drawPath(right)
    p.drawLine(QPointF(23.5 * r, 13.5 * r), QPointF(20.5 * r, 30.5 * r))


# --------------------------------------------------------------------------- #
# 来源系统角标（19 视觉格）：彩色圆角底 + 白色小徽标
# --------------------------------------------------------------------------- #
def draw_badge(name: str, fn, top: str, bottom: str) -> None:
    img = canvas(19, 19)
    p = painter(img)
    grad = QLinearGradient(0, 0, 19, 19)
    grad.setColorAt(0.0, QColor(top))
    grad.setColorAt(1.0, QColor(bottom))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(0, 0, 19, 19), 5.5, 5.5)
    p.setBrush(WHITE)
    fn(p, 19)
    p.end()
    save(img, name)


def _badge_mac(p: QPainter, s: float) -> None:
    """白苹果：双叶身体 − 右咬痕 + 旋转叶子（缩到角标里仍可辨）。"""
    r = s / 19.0
    body = QPainterPath()
    body.moveTo(9.5 * r, 7.2 * r)
    body.cubicTo(11.2 * r, 4.6 * r, 14.2 * r, 4.2 * r, 15.5 * r, 6.2 * r)
    body.cubicTo(17 * r, 8.4 * r, 16.4 * r, 12.4 * r, 14.7 * r, 14.9 * r)
    body.cubicTo(13.5 * r, 16.8 * r, 11.6 * r, 17.9 * r, 10.1 * r, 16.8 * r)
    body.cubicTo(9.9 * r, 16.6 * r, 9.7 * r, 16.6 * r, 9.5 * r, 16.8 * r)
    body.cubicTo(8.1 * r, 17.9 * r, 6.1 * r, 16.8 * r, 4.9 * r, 14.9 * r)
    body.cubicTo(3.1 * r, 12.4 * r, 2.5 * r, 8.4 * r, 4 * r, 6.2 * r)
    body.cubicTo(5.3 * r, 4.2 * r, 8.3 * r, 4.6 * r, 9.5 * r, 7.2 * r)
    body.closeSubpath()
    bite = QPainterPath()
    bite.addEllipse(QPointF(17.2 * r, 9 * r), 2.9 * r, 2.9 * r)
    p.drawPath(body.subtracted(bite))
    leaf = QPainterPath()
    leaf.addEllipse(QRectF(-2.4 * r, -0.85 * r, 4.8 * r, 1.7 * r))
    t = QTransform()
    t.translate(11.1 * r, 3.3 * r)
    t.rotate(-35)
    p.drawPath(t.map(leaf))


def _badge_win(p: QPainter, s: float) -> None:
    r = s / 19.0
    gap = 1.1 * r
    half = (19 * r - 6.2 * r - gap) / 2
    for x, y in [(4.6 * r, 4.6 * r), (4.6 * r + half + gap, 4.6 * r),
                 (4.6 * r, 4.6 * r + half + gap), (4.6 * r + half + gap, 4.6 * r + half + gap)]:
        p.drawRoundedRect(QRectF(x, y, half, half), 0.7 * r, 0.7 * r)


# --------------------------------------------------------------------------- #
# 空态插画（82×62 视觉格）
# --------------------------------------------------------------------------- #
def draw_empty_clip() -> None:
    img = canvas(82, 62)
    p = painter(img)
    gray = QColor("#9AA3B4")
    _stroke(p, gray, 2.2)
    # 剪贴板板身 + 顶部夹子
    p.drawRoundedRect(QRectF(16, 10, 40, 46), 6, 6)
    path = QPainterPath()
    path.moveTo(28, 10)
    path.lineTo(28, 6.5)
    path.lineTo(44, 6.5)
    path.lineTo(44, 10)
    p.drawPath(path)
    # 加号
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#B7C3D6"))
    p.drawEllipse(QRectF(50, 38, 22, 22))
    pen = QPen(QColor("#FFFFFF"), 2.6)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(61, 43.5), QPointF(61, 54.5))
    p.drawLine(QPointF(55.5, 49), QPointF(66.5, 49))
    p.end()
    save(img, "empty-clip.png")


def draw_empty_filter() -> None:
    img = canvas(82, 62)
    p = painter(img)
    gray = QColor("#9AA3B4")
    _stroke(p, gray, 2.4)
    p.drawEllipse(QRectF(18, 10, 32, 32))
    p.drawLine(QPointF(42, 34), QPointF(58, 50))
    # 叉号徽标
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#B7C3D6"))
    p.drawEllipse(QRectF(50, 38, 20, 20))
    pen = QPen(QColor("#FFFFFF"), 2.4)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(55.5, 43.5), QPointF(64.5, 52.5))
    p.drawLine(QPointF(64.5, 43.5), QPointF(55.5, 52.5))
    p.end()
    save(img, "empty-filter.png")


# --------------------------------------------------------------------------- #
def main() -> None:
    QApplication.instance() or QApplication(["-platform", "offscreen"])
    print("生成 v2 产品图标 →", OUT)

    # 工具行（20）
    draw_tool("tool-copy.png", _glyph_copy)
    draw_tool("tool-trash.png", _glyph_trash)
    draw_tool("tool-pause.png", _glyph_pause)
    draw_tool("tool-resume.png", _glyph_resume)
    draw_tool("tool-search.png", _glyph_search)
    draw_tool("tool-finder.png", _glyph_finder)
    draw_tool("tool-openlink.png", _glyph_link)
    draw_tool("tool-preview.png", _glyph_preview)
    draw_tool("tool-close.png", _glyph_close)
    draw_tool("tool-retry.png", _glyph_retry)

    # 侧栏（24）
    draw_nav("nav-switch.png", _nav_switch)
    draw_nav("nav-clip.png", _nav_clip)
    draw_nav("nav-settings.png", _nav_settings)

    # 类型瓷砖（44）
    draw_tile("type-text.png", _tile_text, "#7B7BF0", "#4F4FD8")
    draw_tile("type-image.png", _tile_image, "#6E9BFF", "#3C6FE0")
    draw_tile("type-link.png", _tile_link, "#6E9BFF", "#3C6FE0")
    draw_tile("type-file.png", _tile_file, "#8FA8F5", "#4E5DE0")
    draw_tile("type-code.png", _tile_code, "#7B7BF0", "#4F4FD8")

    # 来源系统角标（19）
    draw_badge("badge-mac.png", _badge_mac, "#6B7484", "#3D4450")
    draw_badge("badge-win.png", _badge_win, "#4C8DFF", "#2C63D8")

    # 空态插画
    draw_empty_clip()
    draw_empty_filter()
    print("完成。")


if __name__ == "__main__":
    main()
