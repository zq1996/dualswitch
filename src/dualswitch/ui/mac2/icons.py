"""图标渲染：开源图标库（Lucide / Simple Icons）→ 任意尺寸、任意颜色的 QPixmap。

设计原则（本轮返工的核心）：

* **不再自绘图形**。所有控件图标来自 Lucide（ISC），平台 logo 来自 Simple Icons（CC0），
  路径数据内联在 `iconlib.py`，运行时不联网。
* **颜色由调用方决定**。图标是描边/实心蒙版，渲染时注入颜色 —— 浅色/深色、hover、
  选中反白都走同一份数据，不需要为每个状态生成 PNG。
* **按设备像素比渲染**。Retina 下按 2x 位图绘制并设置 devicePixelRatio，避免放大发虚。

`plate_pixmap()` / `badge_pixmap()` 负责「圆角方块 + 白色 logo」这套 macOS 图标注法
（hero 62px 平台牌、设备卡 32px、缩略图右下角 19px 来源角标）。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer

from .iconlib import FILLED, LUCIDE


def _qc(value: str):
    from . import theme

    return theme.qcolor(value)

#: 平台标识在牌面上的占比。Simple Icons 的 windows 是「满版四格」——留白太多会
#: 让白格吃满整块牌（看起来像蓝图标压白底），压到 0.66 才有「蓝牌白窗」的观感。
_LOGO_INSET = {"apple": 0.86, "windows": 0.66}

#: 平台牌渐变（index-v2.html `.hero-logo.win` / `.hero-logo.mac`）
PLATE_GRADIENT = {
    "windows": ("#4C8DFF", "#2C63D8"),
    "apple": ("#6B7484", "#3D4450"),
}

_cache: dict[tuple, QPixmap] = {}


# --------------------------------------------------------------------------- #
# 基础渲染
# --------------------------------------------------------------------------- #
def _ratio() -> float:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return 2.0
    screen = app.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 2.0


def _svg_markup(name: str, color: str, stroke: float) -> str:
    if name in FILLED:
        body = FILLED[name]
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="{color}" stroke="none">{body}</svg>'
        )
    body = LUCIDE.get(name)
    if body is None:
        return ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke:g}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def svg_pixmap(name: str, size: int, color: str, stroke: float = 2.0) -> QPixmap:
    """把图标渲染成 size×size 的位图（按屏幕 DPR 超采样）。"""
    ratio = _ratio()
    key = (name, int(size), color.lower(), round(float(stroke), 2), ratio)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    px = max(1, int(round(size * ratio)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    markup = _svg_markup(name, color, stroke)
    if markup:
        renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(p, QRectF(0, 0, px, px))
        p.end()
    pm.setDevicePixelRatio(ratio)
    _cache[key] = pm
    return pm


def icon(name: str, size: int, color: str, stroke: float = 2.0) -> QIcon:
    return QIcon(svg_pixmap(name, size, color, stroke))


def has_icon(name: str) -> bool:
    return name in LUCIDE or name in FILLED


def clear_cache() -> None:
    """主题切换后调用（颜色变了，缓存键也会变，这里只是兜底释放）。"""
    _cache.clear()


# --------------------------------------------------------------------------- #
# 平台牌 / 角标
# --------------------------------------------------------------------------- #
def _plate(system: str, size: int, radius: float, dim: float = 1.0) -> tuple[QPixmap, float]:
    ratio = _ratio()
    px = max(1, int(round(size * ratio)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    top, bottom = PLATE_GRADIENT.get(system, PLATE_GRADIENT["windows"])
    grad = QLinearGradient(QPointF(0, 0), QPointF(px, px))
    c1, c2 = QColor(top), QColor(bottom)
    if dim < 1.0:
        c1.setAlphaF(dim)
        c2.setAlphaF(dim)
    grad.setColorAt(0.0, c1)
    grad.setColorAt(1.0, c2)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), radius * ratio, radius * ratio)
    p.fillPath(path, QBrush(grad))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm, ratio


def plate_pixmap(system: str, size: int, radius: float | None = None, dim: float = 1.0) -> QPixmap:
    """圆角渐变牌 + 白色平台 logo（hero 62 / 设备卡 32）。"""
    key = ("plate", system, size, radius, round(dim, 2), _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    r = size * 0.26 if radius is None else radius
    pm, ratio = _plate(system, size, r, dim)
    logo_size = max(8, int(round(size * _LOGO_INSET.get(system, 0.86))))
    logo = svg_pixmap(system, logo_size, "#FFFFFF")
    p = QPainter(pm)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    lp = logo.width() / (logo.devicePixelRatio() or 1)
    p.drawPixmap(
        QPointF((size - lp) / 2.0, (size - lp) / 2.0),
        logo,
    )
    p.end()
    _cache[key] = pm
    return pm


def badge_pixmap(system: str, size: int, ring_color: str) -> QPixmap:
    """缩略图右下角来源系统角标：19px 圆角块 + 1.8px 卡片色描边 + 白 logo。"""
    key = ("badge", system, size, ring_color.lower(), _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ratio = _ratio()
    ring = 1.8
    total_n = int(round(size * ratio))
    inner_n = max(2, int(round((size - ring * 2) * ratio)))

    inner = plate_pixmap(system, size - ring * 2, radius=(size - ring * 2) * 0.29)
    pm = QPixmap(total_n, total_n)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    ring_px = ring * ratio
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, total_n, total_n), size * 0.29 * ratio, size * 0.29 * ratio
    )
    p.fillPath(path, _qc(ring_color))
    p.drawPixmap(QPointF(ring_px, ring_px), inner)
    p.end()
    pm.setDevicePixelRatio(ratio)
    _cache[key] = pm
    return pm


def plain_plate_pixmap(glyph: str, size: int, bg: str, fg: str, radius: float | None = None) -> QPixmap:
    """单色圆角牌 + 单色图标（unknown 态的「未检测到显示器」牌）。"""
    key = ("plain", glyph, size, bg.lower(), fg.lower(), radius, _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ratio = _ratio()
    px = max(1, int(round(size * ratio)))
    r = size * 0.26 if radius is None else radius
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), r * ratio, r * ratio)
    p.fillPath(path, QBrush(_qc(bg)))
    p.end()
    pm.setDevicePixelRatio(ratio)
    glyph_pm = svg_pixmap(glyph, max(10, int(round(size * 0.46))), fg)
    p = QPainter(pm)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    gw = glyph_pm.width() / (glyph_pm.devicePixelRatio() or 1)
    p.drawPixmap(QPointF((size - gw) / 2.0, (size - gw) / 2.0), glyph_pm)
    p.end()
    _cache[key] = pm
    return pm


def type_tile_pixmap(kind: str, size: int, bg: str, border: str, fg: str) -> QPixmap:
    """剪贴板类型缩略图：圆角瓷砖 + 居中类型图标（文字/代码/图片/链接/文件）。"""
    key = ("tile", kind, size, bg.lower(), border.lower(), fg.lower(), _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ratio = _ratio()
    px = max(1, int(round(size * ratio)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    radius = size * 0.217 * ratio           # 46 → 10
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, px - 1, px - 1), radius, radius)
    p.fillPath(path, _qc(bg))
    p.setPen(_qc(border))
    p.drawPath(path)
    p.end()
    pm.setDevicePixelRatio(ratio)

    glyph_name = {
        "text": "type", "code": "code", "image": "image",
        "link": "link-2", "file": "file-text",
    }.get(kind, "type")
    glyph_size = int(round(size * 0.5))
    glyph = svg_pixmap(glyph_name, glyph_size, fg, stroke=1.9)
    p = QPainter(pm)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    gw = glyph.width() / (glyph.devicePixelRatio() or 1)
    p.drawPixmap(QPointF((size - gw) / 2.0, (size - gw) / 2.0), glyph)
    p.end()
    _cache[key] = pm
    return pm
