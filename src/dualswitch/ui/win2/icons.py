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

#: 扩展名分类 → 文件字形。Frset 的文件卡就是按这个分派的：
#: `.png` 是「文档 + 小图」，`.dmg` 是「文档 + 拉链/箭头」，其余是空白文档。
IMAGE_EXTS = {
    "png", "jpg", "jpeg", "heic", "heif", "gif", "webp", "tif", "tiff",
    "bmp", "ico", "jfif", "svg", "avif", "psd",
}
ARCHIVE_EXTS = {
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "dmg", "pkg",
    "iso", "jar", "apk", "ipa", "deb", "rpm",
}


def ext_of(path_or_name: str) -> str:
    """`/a/b/ChatGPTInstaller.dmg` → `dmg`（小写，无点；没有扩展名返回空串）。"""
    name = (path_or_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].strip().lower()


def file_glyph(path_or_name: str) -> str:
    """扩展名 → Lucide 字形名（`file-image` / `file-archive` / `file`）。"""
    ext = ext_of(path_or_name)
    if ext in IMAGE_EXTS:
        return "file-image"
    if ext in ARCHIVE_EXTS:
        return "file-archive"
    return "file"


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
def _canvas(size: int):
    """按 DPR 建画布，并**先设 DPR 再画**：之后所有坐标都是逻辑像素。

    这是修「角标只有四分之一有内容」的关键 —— 之前先画后设 DPR，嵌套 pixmap 的
    逻辑尺寸会被当成设备像素，位置和大小全错。
    """
    ratio = _ratio()
    px = max(1, int(round(size * ratio)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    return pm, p


def _plate_bg(p: QPainter, system: str, size: float, radius: float, dim: float = 1.0) -> None:
    """在给定画布上画渐变圆角底（逻辑坐标）。"""
    top, bottom = PLATE_GRADIENT.get(system, PLATE_GRADIENT["windows"])
    grad = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    c1, c2 = QColor(top), QColor(bottom)
    if dim < 1.0:
        c1.setAlphaF(dim)
        c2.setAlphaF(dim)
    grad.setColorAt(0.0, c1)
    grad.setColorAt(1.0, c2)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.fillPath(path, QBrush(grad))


def _draw_logo(p: QPainter, system: str, size: float, ratio_logo: float) -> None:
    logo_size = max(6, int(round(size * ratio_logo)))
    logo = svg_pixmap(system, logo_size, "#FFFFFF")
    lw = logo.width() / (logo.devicePixelRatio() or 1)
    p.drawPixmap(QPointF((size - lw) / 2.0, (size - lw) / 2.0), logo)


def plate_pixmap(system: str, size: int, radius: float | None = None, dim: float = 1.0) -> QPixmap:
    """圆角渐变牌 + 白色平台 logo（hero 62 / 设备卡 32）。"""
    key = ("plate", system, size, radius, round(dim, 2), _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    r = size * 0.26 if radius is None else radius
    pm, p = _canvas(size)
    _plate_bg(p, system, size, r, dim)
    _draw_logo(p, system, size, _LOGO_INSET.get(system, 0.86))
    p.end()
    _cache[key] = pm
    return pm


def badge_pixmap(system: str, size: int, ring_color: str) -> QPixmap:
    """缩略图右下角来源系统角标：圆角块 + 2px 卡片色描边 + 白 logo。

    全部在一张画布上按逻辑坐标画（先设 DPR），不再嵌一层带 DPR 的 pixmap ——
    之前那版角标只有左上四分之一有内容、其余空白，就是嵌套 pixmap 的 DPR 打架。
    """
    key = ("badge", system, size, ring_color.lower(), _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ring = 2.0
    pm, p = _canvas(size)
    # 外圈（= 卡片色的「描边」，把角标从缩略图上挖出来）
    p.setPen(Qt.NoPen)
    p.setBrush(_qc(ring_color))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    # 内圈渐变底
    inner = size - ring * 2
    p.save()
    p.translate(ring, ring)
    _plate_bg(p, system, inner, inner * 0.26)
    _draw_logo(p, system, inner, 0.56)
    p.restore()
    p.end()
    _cache[key] = pm
    return pm


def plain_plate_pixmap(glyph: str, size: int, bg: str, fg: str, radius: float | None = None,
                       border: str | None = None) -> QPixmap:
    """单色圆角牌 + 单色图标（unknown 态的「未检测到显示器」牌）。"""
    key = ("plain", glyph, size, bg.lower(), fg.lower(), radius, border, _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    r = size * 0.26 if radius is None else radius
    pm, p = _canvas(size)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), r, r)
    p.fillPath(path, QBrush(_qc(bg)))
    if border:
        p.setPen(_qc(border))
        p.drawPath(path)
    glyph_pm = svg_pixmap(glyph, max(10, int(round(size * 0.46))), fg)
    gw = glyph_pm.width() / (glyph_pm.devicePixelRatio() or 1)
    p.drawPixmap(QPointF((size - gw) / 2.0, (size - gw) / 2.0), glyph_pm)
    p.end()
    _cache[key] = pm
    return pm


def file_pixmap(path_or_name: str, size: int, fg: str = "", stroke: float = 1.5) -> QPixmap:
    """文件卡图标：Lucide 文档字形 + 下面一行扩展名（照 Frset 的「文档 + PNG」）。

    `path_or_name` 决定字形（图片文件=文档+小图、压缩/安装包=文档+拉链）和下面印的字。
    没扩展名时只画文档，不留空行。
    """
    ext = ext_of(path_or_name).upper()[:5]
    glyph_name = file_glyph(path_or_name)
    from . import theme

    color = fg or theme.tokens().ink2
    key = ("fileicon", glyph_name, ext, size, color.lower(), stroke, _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached

    ratio = _ratio()
    label_h = 13 if ext else 0
    px_w = max(1, int(round(size * ratio)))
    px_h = max(1, int(round((size + label_h) * ratio)))
    pm = QPixmap(px_w, px_h)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(ratio)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    glyph = svg_pixmap(glyph_name, size, color, stroke=stroke)
    gw = glyph.width() / (glyph.devicePixelRatio() or 1)
    p.drawPixmap(QPointF((size - gw) / 2.0, 0.0), glyph)
    if ext:
        from PySide6.QtGui import QFont

        # 这是画进 46px 缩略图角标的「文件扩展名」字样（PNG/PDF…），属于**图标内容**，
        # 不参与 theme 的字阶（FS_* 只管页面文字）。9px 是为了塞进 13px 的角标条。
        font = QFont()
        font.setFamilies(theme.MONO_FAMILIES)
        font.setPixelSize(9)
        font.setWeight(QFont.DemiBold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.4)
        p.setFont(font)
        p.setPen(_qc(color))
        p.drawText(
            QRectF(0, size - 1, size, label_h + 2),
            int(Qt.AlignHCenter | Qt.AlignTop),
            ext,
        )
    p.end()
    _cache[key] = pm
    return pm


def type_tile_pixmap(
    kind: str, size: int, bg: str, border: str | None, fg: str,
    glyph_override: str = "",
) -> QPixmap:
    """剪贴板类型缩略图：圆角瓷砖 + 居中类型图标（文字/代码/图片/链接/文件）。

    `glyph_override` 给「文件」用：按扩展名换成 `file-image` / `file-archive`。
    """
    key = ("tile", kind, glyph_override, size, bg.lower(), (border or "").lower(),
           fg.lower(), _ratio())
    cached = _cache.get(key)
    if cached is not None:
        return cached
    pm, p = _canvas(size)
    radius = size * 0.217           # 46 → 10
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), radius, radius)
    p.fillPath(path, QBrush(_qc(bg)))
    if border:
        p.setPen(_qc(border))
        p.drawPath(path)

    glyph_name = glyph_override or {
        "text": "type", "code": "code", "image": "image",
        "link": "link-2", "file": "file",
    }.get(kind, "type")
    glyph_size = int(round(size * 0.52))
    glyph = svg_pixmap(glyph_name, glyph_size, fg, stroke=2.1)
    gw = glyph.width() / (glyph.devicePixelRatio() or 1)
    p.drawPixmap(QPointF((size - gw) / 2.0, (size - gw) / 2.0), glyph)
    p.end()
    _cache[key] = pm
    return pm
