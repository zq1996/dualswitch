"""v2 自绘控件：图标按钮、键帽、筛选 chip、开关、分段、元信息行、行容器。

图标一律来自 `icons.py`（Lucide / Simple Icons），颜色按状态在绘制时注入 ——
QSS 改不了图片颜色，所以带状态的图标控件（`IconButton` / `NavButton` / `ToolButton`）
统一自绘，避免「选中了但图标还是灰的」这类观感问题。

键帽是用户点名的组件（INTERACTIONS §0.3）：每键独立圆角块、键间 3px、
不是文本拼接。字号用像素（`theme.qfont`），否则 11pt 会把「Space」撑成两行。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme

#: 工具图标名 → Lucide 图标名
TOOL_ICONS = {
    "search": "search",
    "pause": "pause",
    "resume": "play",
    "trash": "trash-2",
    "clear": "trash-2",
    "copy": "copy",
    "preview": "scan-eye",
    "image": "image",
    "finder": "folder-open",
    "openlink": "external-link",
    "close": "x",
    "delete": "x",
    "check": "check",
    "open": "app-window",
    "switch": "arrow-left-right",
    "force": "download",
    "settings": "settings",
    "quit": "power",
    "clip": "clipboard",
}

#: 侧栏导航图标
NAV_ICONS = {"switch": "arrow-left-right", "clip": "clipboard", "settings": "settings"}


# --------------------------------------------------------------------------- #
# 图标取用
# --------------------------------------------------------------------------- #
def tool_pixmap(name: str, size: int = 16, color: str | None = None) -> QPixmap:
    lucide = TOOL_ICONS.get(name, name)
    return icons.svg_pixmap(lucide, size, color or theme.tokens().ink2)


def nav_pixmap(name: str, size: int = 17, color: str | None = None) -> QPixmap:
    return icons.svg_pixmap(NAV_ICONS.get(name, name), size, color or theme.tokens().ink3)


def type_pixmap(kind: str, size: int = theme.THUMB) -> QPixmap:
    """剪贴板类型缩略图（圆角瓷砖 + 类型图标）。"""
    t = theme.tokens()
    fg = t.accent if kind in ("image", "link") else t.ink2
    return icons.type_tile_pixmap(kind, size, t.card2, t.line_soft, fg)


def badge_pixmap(source_system: str, size: int = theme.BADGE) -> QPixmap:
    """来源系统角标：Mac=灰底白苹果 / Windows=蓝底白四格（缩略图右下角）。"""
    system = "apple" if source_system in ("apple", "mac") else "windows"
    return icons.badge_pixmap(system, size, theme.tokens().card)


def plate_pixmap(system: str, size: int, dim: float = 1.0) -> QPixmap:
    """平台圆角渐变牌（hero 62 / 设备卡 32）。"""
    return icons.plate_pixmap(system, size, dim=dim)


def logo_pixmap(kind: str, size: int, color: str | None = None) -> QPixmap:
    """平台 logo 本身（白/自定色），不带底。"""
    system = "apple" if kind in ("apple", "mac") else "windows"
    return icons.svg_pixmap(system, size, color or "#FFFFFF")


# --------------------------------------------------------------------------- #
# 图标按钮（自绘：hover / checked 换色）
# --------------------------------------------------------------------------- #
class IconButton(QPushButton):
    """正方形图标按钮（工具行 / 行内动作 / 预览底栏共用）。

    `variant`:
      · ``tool``  —— 34×34、带描边（剪贴板工具栏）
      · ``act``   —— 25×25、浅底（行内 hover 动作）
      · ``pv``    —— 26×26、浅底（预览窗底栏）
      · ``ghost`` —— 28×28、无底（标题栏工具钮）
    """

    VARIANTS = {
        "tool": (34, 9, 16),
        "act": (25, 6, 13),
        "pv": (26, 7, 14),
        "ghost": (28, 7, 15),
    }

    def __init__(self, icon_name: str, tip: str = "", variant: str = "tool",
                 primary: bool = False, danger: bool = False, parent=None):
        super().__init__(parent)
        self._icon_name = TOOL_ICONS.get(icon_name, icon_name)
        self._variant = variant
        self._primary = primary
        self._danger = danger
        size, radius, glyph = self.VARIANTS.get(variant, self.VARIANTS["tool"])
        self._radius = radius
        self._glyph = glyph
        self.setFixedSize(size, size)
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCheckable(variant == "ghost")

    def set_primary(self, primary: bool) -> None:
        if primary != self._primary:
            self._primary = primary
            self.update()

    def set_icon_name(self, name: str) -> None:
        self._icon_name = TOOL_ICONS.get(name, name)
        self.update()

    def icon_name(self) -> str:
        return self._icon_name

    def _colors(self) -> tuple[str, str, str]:
        """(背景, 描边, 图标色)。"""
        t = theme.tokens()
        hovered = self.underMouse()
        checked = self.isChecked() and self._variant == "ghost"
        if self._primary:
            bg = t.accent_deep if hovered else t.accent
            return bg, bg, t.on_accent
        if self._danger:
            return (t.danger if hovered else t.card3), "transparent", (
                t.on_accent if hovered else t.ink2
            )
        if self._variant == "ghost":
            if checked:
                return t.accent_soft, "transparent", t.accent_deep
            return (t.card3 if hovered else "transparent"), "transparent", (
                t.ink if hovered else t.ink3
            )
        if self._variant == "act":
            if hovered:
                return t.accent, "transparent", t.on_accent
            return t.card3, "transparent", t.ink2
        if self._variant == "pv":
            return (t.line if hovered else t.card3), "transparent", t.ink2
        # tool
        if hovered:
            return t.accent_soft, t.accent, t.accent_deep
        return t.card, t.line, t.ink2

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bg, border, fg = self._colors()
        rect = self.rect().adjusted(0, 0, -1, -1)
        if bg != "transparent":
            p.setPen(theme.qcolor(border) if border != "transparent" else Qt.NoPen)
            p.setBrush(theme.qcolor(bg))
            p.drawRoundedRect(rect, self._radius, self._radius)
        glyph = icons.svg_pixmap(self._icon_name, self._glyph, fg)
        gw = glyph.width() / (glyph.devicePixelRatio() or 1)
        p.drawPixmap(
            int((self.width() - gw) / 2),
            int((self.height() - gw) / 2),
            glyph,
        )
        p.end()

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)


# --------------------------------------------------------------------------- #
# 侧栏导航按钮
# --------------------------------------------------------------------------- #
class NavButton(QAbstractButton):
    """侧栏行：图标 + 文案 + 计数徽章；选中 = 主色实底 + 反白图标。"""

    def __init__(self, icon_name: str, text: str, parent=None):
        super().__init__(parent)
        self._icon_name = NAV_ICONS.get(icon_name, icon_name)
        self._text = text
        self._count = ""
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_count(self, text: str) -> None:
        if text == self._count:
            return
        self._count = text
        self.update()

    def text(self) -> str:  # noqa: N802 - 与 QPushButton 对齐，测试用
        return self._text

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, 0, 0)
        on = self.isChecked()
        hovered = self.underMouse()
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(t.accent))
            p.drawRoundedRect(rect, 8, 8)
        elif hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(t.card3))
            p.drawRoundedRect(rect, 8, 8)

        glyph = icons.svg_pixmap(
            self._icon_name, 17, t.on_accent if on else (t.ink if hovered else t.ink3), 2.0
        )
        gw = glyph.width() / (glyph.devicePixelRatio() or 1)
        p.drawPixmap(10, int((self.height() - gw) / 2), glyph)

        fg = theme.qcolor(t.on_accent if on else (t.ink if hovered else t.ink2))
        p.setPen(fg)
        f = theme.qfont(12.5, 620 if on else 550)
        p.setFont(f)
        text_x = 10 + 17 + 10
        fm = QFontMetrics(f)
        p.drawText(
            text_x,
            0,
            self.width() - text_x - 10,
            self.height(),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._text,
        )

        if self._count:
            cf = theme.qfont(9.5, 700)
            cfm = QFontMetrics(cf)
            badge_w = cfm.horizontalAdvance(self._count) + 12
            badge_h = 16
            bx = self.width() - 10 - badge_w
            by = (self.height() - badge_h) / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 64) if on else theme.qcolor(t.accent_soft))
            p.drawRoundedRect(int(bx), int(by), badge_w, badge_h, 8, 8)
            p.setPen(theme.qcolor(t.on_accent if on else t.accent_deep))
            p.setFont(cf)
            p.drawText(int(bx), int(by), badge_w, badge_h, Qt.AlignCenter, self._count)
        p.end()

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)


# --------------------------------------------------------------------------- #
# 键帽
# --------------------------------------------------------------------------- #
class KeyCap(QLabel):
    """单个键帽：18 高（宽键 30），r5，10px 字。宽度随文字自适应，不换行。"""

    def __init__(self, text: str, on_accent: bool = False, ghost: bool = False, parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(False)
        f = theme.qfont(theme.KEY_FONT_PX, 650)
        self.setFont(f)
        fm = QFontMetrics(f)
        min_w = theme.KEY_WIDE_W if theme.is_wide_key(text) else theme.KEY_W
        width = max(min_w, fm.horizontalAdvance(text) + 11)
        self.setFixedSize(width, theme.KEY_H)
        if ghost:
            self.setObjectName("keycapGhost")
        elif on_accent:
            self.setObjectName("keycapOnAccent")
        else:
            self.setObjectName("keycap")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def sizeHint(self):  # noqa: N802
        return QSize(self.width(), self.height())


class KeyCaps(QWidget):
    """一组键帽：`⌥` `Space`，键间 gap 3px。"""

    def __init__(self, keys: list[str], on_accent: bool = False, ghost: bool = False, parent=None):
        super().__init__(parent)
        self._on_accent = on_accent
        self._ghost = ghost
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.KEY_GAP)
        lay.addStretch(1)
        self._caps: list[KeyCap] = []
        self.set_keys(keys)

    def set_keys(self, keys: list[str]) -> None:
        lay = self.layout()
        while lay.count() > 1:                    # 末尾的 stretch 留着
            item = lay.takeAt(0)
            destroy(item.widget())
        self._caps = []
        for index, label_text in enumerate(keys):
            cap = KeyCap(label_text, on_accent=self._on_accent, ghost=self._ghost)
            lay.insertWidget(index, cap)
            self._caps.append(cap)
        self.setVisible(bool(keys))

    def labels(self) -> list[str]:
        return [c.text() for c in self._caps]


def keycaps_from_describe(describe: str, **kwargs) -> KeyCaps:
    return KeyCaps(theme.keycap_labels(describe), **kwargs)


# --------------------------------------------------------------------------- #
# 文本
# --------------------------------------------------------------------------- #
def label(text: str, name: str = "", size: float | None = None, weight=None,
          mono: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    if size is not None or weight is not None or mono:
        lb.setFont(theme.qfont(size or theme.APP_FONT_PX, weight, mono=mono))
    return lb


class ElidedLabel(QLabel):
    """单行尾部省略（预览头 / 文件路径用）。"""

    def __init__(self, text: str = "", name: str = "", parent=None, mode=Qt.ElideRight):
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._mode = mode
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._apply()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._apply()

    def full_text(self) -> str:
        return self._full

    def _apply(self) -> None:
        fm = QFontMetrics(self.font())
        width = self.width() - 1
        shown = self._full if width <= 12 else fm.elidedText(self._full, self._mode, width)
        if shown != super().text():
            super().setText(shown)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply()


class TwoLineLabel(QLabel):
    """最多两行、超出省略的标题（剪贴板行题，12.5px）。"""

    def __init__(self, text: str = "", parent=None, max_lines: int = 2):
        super().__init__(parent)
        self._full = text
        self._max = max_lines
        self.setWordWrap(True)
        self.setFont(theme.qfont(theme.FS_ROW, 550))
        self.setText(self._elide(text))

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setText(self._elide(text))

    def full_text(self) -> str:
        return self._full

    def _wrap(self, text: str, fm: QFontMetrics, max_w: int) -> list[str]:
        """按词优先折行（英文别被劈成 origi/n），词太长才退化成按字符切。"""
        lines: list[str] = []
        current = ""
        for word in text.split(" "):
            candidate = word if not current else f"{current} {word}"
            if fm.horizontalAdvance(candidate) <= max_w:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if fm.horizontalAdvance(word) <= max_w:
                current = word
                continue
            piece = ""
            for ch in word:
                if fm.horizontalAdvance(piece + ch) <= max_w:
                    piece += ch
                else:
                    lines.append(piece)
                    piece = ch
            current = piece
        if current:
            lines.append(current)
        return lines

    def _elide(self, text: str) -> str:
        # QLabel 没有原生两行省略：先按词折行，超行时给末行补 …
        fm = QFontMetrics(self.font())
        max_w = max(80, self.width() - 2) if self.width() > 10 else 260
        lines = self._wrap(text, fm, max_w)
        if len(lines) <= self._max:
            return "\n".join(lines)
        head = lines[: self._max - 1]
        tail = lines[self._max - 1]
        while tail and fm.horizontalAdvance(tail + "…") > max_w:
            tail = tail[:-1]
        return "\n".join(head + [tail + "…"])

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.setText(self._elide(self._full))


class MetaLine(QWidget):
    """元信息行：`来源(加粗) · 应用 · 时间 · 大小 · 类型`，2.5px 圆点分隔。"""

    DOT = 2.5
    GAP = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: list[tuple[str, bool]] = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(14)

    def set_parts(self, parts: list[tuple[str, bool]]) -> None:
        """parts: [(文本, 是否加粗)]"""
        self._parts = [(p, b) for p, b in parts if p]
        self.updateGeometry()
        self.update()

    def parts(self) -> list[tuple[str, bool]]:
        return list(self._parts)

    def text(self) -> str:  # noqa: N802 - 测试/调试用：拼回一行
        return " · ".join(p for p, _ in self._parts)

    def sizeHint(self):  # noqa: N802
        return QSize(self._measure(), 14)

    def _fonts(self) -> tuple:
        return theme.qfont(theme.META_FS, 700), theme.qfont(theme.META_FS, 400)

    def _measure(self) -> int:
        bold, normal = self._fonts()
        width = 0
        for i, (text, strong) in enumerate(self._parts):
            fm = QFontMetrics(bold if strong else normal)
            width += fm.horizontalAdvance(text)
            if i:
                width += self.GAP * 2 + self.DOT
        return width + 2

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bold, normal = self._fonts()
        x = 0
        for i, (text, strong) in enumerate(self._parts):
            f = bold if strong else normal
            fm = QFontMetrics(f)
            if i:
                x += self.GAP
                p.setPen(Qt.NoPen)
                p.setBrush(theme.qcolor(t.ink3))
                cy = self.height() / 2 - self.DOT / 2 + 0.5
                p.drawEllipse(int(x), int(cy), int(self.DOT * 1.6), int(self.DOT * 1.6))
                x += self.DOT * 1.6 + self.GAP
            p.setFont(f)
            p.setPen(theme.qcolor(t.ink2 if strong else t.ink3))
            w = fm.horizontalAdvance(text)
            p.drawText(
                int(x), 0, w + 2, self.height(), Qt.AlignLeft | Qt.AlignVCenter, text
            )
            x += w
        p.end()


# --------------------------------------------------------------------------- #
# 筛选 chip（文案 + 计数两段式，胶囊形）
# --------------------------------------------------------------------------- #
class Chip(QAbstractButton):
    """`全部 7` —— 主文案 11px + 计数 9.5px（降透明度），胶囊形。"""

    H = 26

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._count = ""
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._font = theme.qfont(11, 550)
        self._cfont = theme.qfont(9.5, 700)
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_count(self, count: int) -> None:
        self._count = str(count)
        self.setFixedWidth(self._measure())
        self.update()

    def text(self) -> str:  # noqa: N802
        return self._text

    def _measure(self) -> int:
        fm = QFontMetrics(self._font)
        cfm = QFontMetrics(self._cfont)
        return fm.horizontalAdvance(self._text) + cfm.horizontalAdvance(self._count) + 11 * 2 + 5

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        on = self.isChecked()
        hovered = self.underMouse()
        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = rect.height() / 2
        bg = t.accent if on else t.card
        border = t.accent if (on or hovered) else t.line
        p.setPen(theme.qcolor(border))
        p.setBrush(theme.qcolor(bg))
        p.drawRoundedRect(rect, radius, radius)

        fg = theme.qcolor(t.on_accent if on else (t.accent_deep if hovered else t.ink2))
        p.setFont(self._font)
        p.setPen(fg)
        fm = QFontMetrics(self._font)
        x = 11
        p.drawText(x, 0, fm.horizontalAdvance(self._text) + 2, self.height(),
                   Qt.AlignLeft | Qt.AlignVCenter, self._text)
        x += fm.horizontalAdvance(self._text) + 5
        cfm = QFontMetrics(self._cfont)
        p.setFont(self._cfont)
        c = theme.qcolor(t.on_accent if on else t.ink3)
        if on:
            c.setAlpha(190)
        p.setPen(c)
        p.drawText(x, 0, cfm.horizontalAdvance(self._count) + 2, self.height(),
                   Qt.AlignLeft | Qt.AlignVCenter, self._count)
        p.end()

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)


# --------------------------------------------------------------------------- #
# 开关 / 分段
# --------------------------------------------------------------------------- #
class StatusDot(QWidget):
    """状态灯：实心圆 + 可选外圈同色光环（设计稿 `.dev-led.on` 的 box-shadow）。"""

    def __init__(self, size: int = 8, ring: int = 3, parent=None):
        super().__init__(parent)
        self._dot = size
        self._ring = ring
        self._state = True
        self.setFixedSize(size + ring * 2, size + ring * 2)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_active(self, active: bool) -> None:
        if active == self._state:
            return
        self._state = active
        self.update()

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        if self._state:
            halo = theme.qcolor(t.ok)
            halo.setAlpha(38)
            p.setBrush(halo)
            p.drawEllipse(0, 0, self.width(), self.height())
            p.setBrush(theme.qcolor(t.ok))
        else:
            p.setBrush(theme.qcolor(t.line))
        off = self._ring
        p.drawEllipse(off, off, self._dot, self._dot)
        p.end()


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(t.ok if on else t.line))
        p.drawRoundedRect(0, 0, 38, 22, 11, 11)
        knob_x = 18 if on else 2
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(knob_x, 2, 18, 18)
        p.end()

    def sizeHint(self):  # noqa: N802
        return QSize(38, 22)


class Segmented(QFrame):
    """两/三段单选（切换模式 / 保留历史 / 外观）。"""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str, parent=None):
        super().__init__(parent)
        self.setObjectName("segTrack")
        self._value = current
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, text in options:
            btn = QPushButton(text)
            btn.setObjectName("segBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setFont(theme.qfont(10.5, 550))
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            btn.setChecked(value == current)
            btn.clicked.connect(lambda _=False, v=value: self.set_value(v, emit=True))
            lay.addWidget(btn, 1)
            self._group.addButton(btn)
            self._buttons[value] = btn
        self.setFixedHeight(28)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        self._value = value
        for v, btn in self._buttons.items():
            btn.setChecked(v == value)
        if emit:
            self.changed.emit(value)


# --------------------------------------------------------------------------- #
# 通用小件
# --------------------------------------------------------------------------- #
def destroy(widget: QWidget | None) -> None:
    """立刻从画面撤下再排期销毁。

    只 `deleteLater()` 不够：控件在事件循环处理前仍是可见子控件，会在原位置重复
    绘制（键帽、预览正文都踩过这个坑，表现为文字/边框叠影）。
    """
    if widget is None:
        return
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def hline(color: str | None = None) -> QFrame:
    line = QFrame()
    line.setObjectName("setSep")
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{color or theme.tokens().line_soft};border:0;")
    return line


def dot(color: str, size: int = 6, name: str = "") -> QLabel:
    """状态灯小圆点（侧栏连接状态 / 设备卡）。"""
    d = QLabel()
    if name:
        d.setObjectName(name)
    d.setFixedSize(size, size)
    d.setStyleSheet(f"background:{color};border-radius:{size // 2}px;border:0;")
    return d


def settings_row(title: str, sub: str, control: QWidget | None) -> QFrame:
    row = QFrame()
    row.setObjectName("setRow")
    row.setMinimumHeight(50)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(15, 12, 15, 12)
    lay.setSpacing(12)
    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(3)
    col.addWidget(label(title, "setTitle"))
    if sub:
        s = label(sub, "setSub")
        s.setWordWrap(True)
        col.addWidget(s)
    lay.addLayout(col, 1)
    if control is not None:
        lay.addWidget(control, 0, Qt.AlignVCenter)
    return row
