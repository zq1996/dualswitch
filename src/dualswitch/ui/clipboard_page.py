"""剪贴板页：主窗口里的一页（不是新窗口）。

规格见 `docs/UI-DESIGN-CLIPBOARD.md`。两端完全共用，**这里不出现 `sys.platform`**：
入口放哪儿由 `chrome.INLINE_CLIPBOARD_ROW` 决定，本机图标由 `theme.PLATFORM_LOGO` 决定，
本文件只管版面。

版面（440 宽）：

    ┌──────────────────────────────────────┐
    │ ‹ 返回  剪贴板              ● 已连接 │ 40  页头（和设置页的返回条同一套）
    ├──────────────────────────────────────┤
    │ ▣  截图 2560 × 1440          已同步  │ 52  列表（可滚，行自绘）
    │   def switch_source(...)    仅本机  │ 52
    │                                      │
    │ ┌ 预览 ────────────────────────────┐ │
    │ │ 预览 · 图片 2.4 MB   双击查看原图│ │ 176 预览卡
    │ │          （等比缩放）            │ │
    │ └──────────────────────────────────┘ │
    ├──────────────────────────────────────┤
    │ [复制到本机] [重发]      [清空记录]  │ 54  操作条（和设置页 footer 同底色）
    ├──────────────────────────────────────┤
    │ ● 仅内存保留 · 退出即清 · …          │ 34  状态栏（复用）
    └──────────────────────────────────────┘

三个刻意的取舍：

* **行不画分隔线**。十条记录十条线，加上预览卡和操作条，整页像张表格。
  现在靠 hover / 选中的圆角底色分组，选中态另加一条左侧主色条。
* **hover 用动画过渡**（130ms），不是 QSS 的瞬时换色 —— 后者在快速划鼠标时很扎眼。
* **图片双击看原图**走页面内的遮罩（`ImageViewer`），不开新窗口：整应用只有一扇窗，
  这条规则在 `docs/UI-DESIGN-CLIPBOARD.md` 第 1 节。

**为什么预览高度固定**：内容长度变化会让窗口"呼吸"，用户每次都要重新找眼睛落点。
固定高度、内部滚动，是这类低频查看界面的稳妥做法。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..sync.engine import ClipRecord
from . import theme
from .widgets import ElidedLabel, StatusBar, label, logo_pixmap, page_header

ROW_H = 52
PREVIEW_H = 176
FOOTER_H = 54
THUMB = 36

STATE_TEXT = {"ok": "已同步", "pend": "传输中", "fail": "失败", "local": "仅本机"}
STATE_NAME = {"ok": "stOk", "pend": "stPend", "fail": "stFail", "local": "stLocal"}
RETRYABLE = ("fail", "local")
THUMB_MAX_BYTES = 8 * 1024 * 1024

# 行背景（自绘）：hover 是极浅的灰蓝，选中用平台主色的最浅一档
HOVER_TINT = "#EFF3FA"
ROW_RADIUS = 9


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.1f} MB"


def _ago(ts: float) -> str:
    gap = max(0.0, time.time() - ts)
    if gap < 60:
        return "刚刚"
    if gap < 3600:
        return f"{int(gap // 60)} 分钟前"
    if gap < 86400:
        return f"{int(gap // 3600)} 小时前"
    return f"{int(gap // 86400)} 天前"


def _source_text(rec: ClipRecord) -> str:
    if rec.source == "remote":
        return rec.peer_name or "对端"
    return "本机"


def _mix(base: str, tint: str, t: float) -> QColor:
    """在两色之间插值（自绘过渡用；QSS 做不了）。"""
    a, b = QColor(base), QColor(tint)
    t = min(1.0, max(0.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


_LOGO_CACHE: dict[tuple[str, int], QPixmap] = {}


def _logo_pixmap(kind: str, size: int) -> QPixmap:
    """系统图标（Mac 用苹果标，Windows 用四格标），按「图标 + 尺寸」缓存。"""
    key = (kind, size)
    if key not in _LOGO_CACHE:
        _LOGO_CACHE[key] = logo_pixmap(kind, size)
    return _LOGO_CACHE[key]


def _record_logo(record: ClipRecord) -> str:
    """这条记录该画哪个图标 —— **看来源，不看本机**。

    来自对端的画对端的图标（Windows 上收到的 mac 内容画苹果标），
    本机复制出来的画本机图标。之前统一画本机图标，用户分不清哪条来自哪台。
    """
    return theme.PEER_LOGO if record.source == "remote" else theme.PLATFORM_LOGO


class Thumb(QWidget):
    """36×36 缩略图：图片画裁切后的图，文本画**来源端**的系统图标。

    图片解不出来（>8MB 不画缩略图 / PNG 损坏）时画一个线框小图，
    而不是退回系统图标 —— 那会让「这是张截图」看起来像「这是一段文字」。
    """

    def __init__(self, record: ClipRecord, parent=None):
        super().__init__(parent)
        self.setFixedSize(THUMB, THUMB)
        self._pix: QPixmap | None = None
        self._kind = record.kind
        self._logo_kind = _record_logo(record)
        self._logo = _logo_pixmap(self._logo_kind, 18)
        if record.png is not None and len(record.png) <= THUMB_MAX_BYTES:
            image = QImage()
            if image.loadFromData(record.png, "PNG"):
                self._pix = QPixmap.fromImage(image)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        p.setClipPath(path)
        if self._pix is not None:
            # 等比放大到铺满，再居中裁切（直接塞进 36×36 会把宽高比压歪）
            scaled = self._pix.scaled(
                QSize(THUMB, THUMB),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            src = QRectF(
                max(0, (scaled.width() - THUMB) / 2),
                max(0, (scaled.height() - THUMB) / 2),
                THUMB,
                THUMB,
            )
            p.drawPixmap(rect, scaled, src)
        else:
            p.fillRect(rect, QColor("#F4F6FA"))
            if self._kind == "image":
                self._paint_image_glyph(p, rect.center())
            else:
                target = QRectF(0, 0, 18, 18)
                target.moveCenter(rect.center())
                p.drawPixmap(target, self._logo, QRectF(self._logo.rect()))
        p.end()

    @staticmethod
    def _paint_image_glyph(p: QPainter, center) -> None:  # noqa: ANN001
        pen = p.pen()
        pen.setColor(QColor("#9AA5B6"))
        pen.setWidthF(1.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(center.x() - 7, center.y() - 6, 14, 12), 2, 2)
        p.drawEllipse(QRectF(center.x() - 3.4, center.y() - 3.4, 3.2, 3.2))
        p.drawLine(
            QRectF(center.x() - 5.6, center.y() + 4.2, 5.4, -5.2).topLeft(),
            QRectF(center.x() - 1.2, center.y() + 4.2, 5.4, -5.2).topLeft(),
        )


class StatePill(QLabel):
    """状态药丸。失败 / 仅本机两种状态本身就是重试入口（设计第 4 节）。"""

    clicked = Signal()

    def __init__(self, state: str, progress: float = 0.0, parent=None):
        super().__init__(parent)
        self._state = state
        self._pressed = False
        self.setAttribute(Qt.WA_Hover, True)
        self.set_state(state, progress)

    def set_state(self, state: str, progress: float = 0.0) -> None:
        self._state = state
        text = STATE_TEXT.get(state, state)
        if state == "pend" and progress:
            text = f"{text} {int(progress * 100)}%"
        self.setText(text)
        name = STATE_NAME.get(state, "stLocal")
        if self.objectName() != name:
            self.setObjectName(name)
            self.style().unpolish(self)
            self.style().polish(self)
        retryable = state in RETRYABLE
        self.setCursor(Qt.PointingHandCursor if retryable else Qt.ArrowCursor)
        self.setToolTip("点击重试" if retryable else "")

    # 药丸自己吃掉点击：不这样做，事件会漏到行上去变成「选中」
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self._state in RETRYABLE:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._pressed:
            self._pressed = False
            event.accept()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            return
        super().mouseReleaseEvent(event)


class ClipRow(QWidget):
    """一条记录：缩略图 + 主行 + 副行 + 状态药丸。

    背景和左侧选中条都在 `paintEvent` 里画：QSS 换背景是瞬时的，鼠标扫过去
    一行一行硬切很扎眼，这里用 130ms 的缓动过渡。
    """

    clicked = Signal(str)
    activated = Signal(str)      # 双击
    deleteRequested = Signal(str)
    retryRequested = Signal(str)

    def __init__(self, record: ClipRecord, parent=None):
        super().__init__(parent)
        self.record = record
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._selected = False
        self._hover = 0.0
        self._sel = 0.0

        self._hover_anim = self._make_anim(self._set_hover)
        self._sel_anim = self._make_anim(self._set_sel)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(10)
        lay.addWidget(Thumb(record), 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 1, 0, 0)
        text_col.setSpacing(2)

        title = ElidedLabel(self._title(record), mode=Qt.ElideRight)
        title.setMinimumWidth(60)
        title.setStyleSheet(
            f"color:{theme.INK};font-size:12.5px;"
            + ("font-family:'SF Mono','Menlo',monospace;" if record.kind != "image" else "")
        )
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        text_col.addWidget(title)

        # 副行也用 ElidedLabel：普通 QLabel 的 minimumSizeHint 是整段文字的宽度，
        # 会把整行撑到 500+，在 404 宽的可视区里把状态药丸直接挤出屏幕（旧版就是这个毛病）。
        sub = ElidedLabel(self._subtitle(record), mode=Qt.ElideRight)
        sub.setStyleSheet("color:#818B9C;font-size:10.5px;")
        sub.setAttribute(Qt.WA_TransparentForMouseEvents)
        text_col.addWidget(sub)
        lay.addLayout(text_col, 1)

        self.pill = StatePill(record.state, record.progress)
        self.pill.clicked.connect(lambda: self.retryRequested.emit(self.record.id))
        lay.addWidget(self.pill, 0, Qt.AlignTop)

        self._del = QPushButton("\u00d7")
        self._del.setCursor(Qt.PointingHandCursor)
        self._del.setFixedSize(18, 18)
        self._del.setToolTip("从列表里移除")
        self._del.setStyleSheet(
            "QPushButton{border:0;background:transparent;color:#B3BCCB;"
            "font-size:14px;border-radius:9px;padding:0;}"
            "QPushButton:hover{background:#FBE7E7;color:#C33B3B;}"
        )
        self._del.setVisible(False)
        self._del.clicked.connect(lambda: self.deleteRequested.emit(self.record.id))
        lay.addWidget(self._del, 0, Qt.AlignTop)

    # ------------------------------------------------------------------ 动画
    def _make_anim(self, setter) -> QVariantAnimation:  # noqa: ANN001
        anim = QVariantAnimation(self)
        anim.setDuration(130)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(setter)
        return anim

    def _set_hover(self, value) -> None:  # noqa: ANN001
        self._hover = float(value)
        self.update()

    def _set_sel(self, value) -> None:  # noqa: ANN001
        self._sel = float(value)
        self.update()

    def _fade(self, anim: QVariantAnimation, attr: str, target: float) -> None:
        current = getattr(self, attr)
        if abs(current - target) < 0.001:
            return
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(float(target))
        anim.start()

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        bg = None
        if self._sel > 0.001:
            bg = _mix("#FFFFFF", theme.ACCENT_SOFT, self._sel)
            if self._hover > 0.001:
                bg = _mix(bg.name(), HOVER_TINT, self._hover * 0.5)
        elif self._hover > 0.001:
            bg = _mix("#FFFFFF", HOVER_TINT, self._hover)

        if bg is not None and bg != QColor("#FFFFFF"):
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(rect, ROW_RADIUS, ROW_RADIUS)

        if self._sel > 0.001:
            bar = QRectF(rect.left() + 2.5, rect.top() + 13, 2.5, rect.height() - 26)
            p.setOpacity(self._sel)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(bar, 1.25, 1.25)
            p.setOpacity(1.0)
        p.end()

    @staticmethod
    def _title(rec: ClipRecord) -> str:
        if rec.kind == "image":
            return f"截图 {rec.width} × {rec.height}"
        text = (rec.text or "").replace("\n", " ").replace("\t", " ").strip()
        return text[:46] + ("…" if len(text) > 46 else "")

    @staticmethod
    def _subtitle(rec: ClipRecord) -> str:
        kind = {"text": "纯文本", "image": "PNG", "mixed": "图文"}.get(rec.kind, rec.kind)
        parts = [_source_text(rec), _ago(rec.ts), _human_size(rec.size), kind]
        return " · ".join(parts)

    # ------------------------------------------------------------------ 交互
    def set_selected(self, selected: bool, animate: bool = True) -> None:
        self._selected = selected
        if animate:
            self._fade(self._sel_anim, "_sel", 1.0 if selected else 0.0)
        else:
            self._sel_anim.stop()
            self._sel = 1.0 if selected else 0.0
            self.update()

    def enterEvent(self, event):  # noqa: N802
        self._del.setVisible(True)
        self._fade(self._hover_anim, "_hover", 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._del.setVisible(False)
        self._fade(self._hover_anim, "_hover", 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.record.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.record.id)
        super().mouseDoubleClickEvent(event)


class PreviewImage(QWidget):
    """预览图：按控件尺寸等比缩放（重绘时算，不缓存缩放结果），双击放大。"""

    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self.setCursor(Qt.ArrowCursor)

    def set_image(self, pix: QPixmap | None) -> None:
        self._pix = pix
        self.setCursor(Qt.PointingHandCursor if pix is not None else Qt.ArrowCursor)
        self.setToolTip("双击全屏预览" if pix is not None else "")
        self.update()

    def paintEvent(self, event):  # noqa: N802
        if self._pix is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        area = QRectF(self.rect())
        size = self._pix.size().scaled(area.size().toSize(), Qt.KeepAspectRatio)
        target = QRectF(0, 0, size.width(), size.height())
        target.moveCenter(area.center())
        p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
        p.end()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if self._pix is not None and event.button() == Qt.LeftButton:
            self.activated.emit()
            return
        super().mouseDoubleClickEvent(event)


class PreviewPane(QFrame):
    """固定高度的预览卡：图片等比缩放（双击看原图），文本可选中。"""

    enlargeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("clipPreview")
        self.setFixedHeight(PREVIEW_H)
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 10)
        lay.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.caption = label("预览", "pvHead")
        self.hint = label("", "pvHint")
        head.addWidget(self.caption, 0)
        head.addStretch(1)
        head.addWidget(self.hint, 0)
        lay.addLayout(head)

        self.placeholder = label("选中一条记录查看内容", "pvEmpty")
        self.placeholder.setAlignment(Qt.AlignCenter)

        self.image = PreviewImage()
        self.image.activated.connect(self.enlargeRequested)

        self.text = QPlainTextEdit()
        self.text.setObjectName("pvText")
        self.text.setReadOnly(True)
        self.text.setFrameShape(QFrame.NoFrame)
        self.text.setVisible(False)

        lay.addWidget(self.placeholder, 1)
        lay.addWidget(self.image, 1)
        lay.addWidget(self.text, 1)

    def _show_only(self, widget: QWidget) -> None:
        for child in (self.placeholder, self.image, self.text):
            child.setVisible(child is widget)

    def show_record(self, rec: ClipRecord | None) -> None:
        if rec is None:
            self.caption.setText("预览")
            self.hint.setText("选中一条看内容")
            self.image.set_image(None)
            self._show_only(self.placeholder)
            return
        if rec.png is not None:
            image = QImage()
            if image.loadFromData(rec.png, "PNG"):
                self.image.set_image(QPixmap.fromImage(image))
                self._show_only(self.image)
                self.caption.setText(f"预览 · 图片 {_human_size(rec.size)}")
                self.hint.setText("双击全屏预览")
                return
        self.image.set_image(None)
        self.text.setPlainText(rec.text or "")
        self._show_only(self.text)
        self.caption.setText(f"预览 · 文本 {_human_size(rec.size)}")
        self.hint.setText("可直接选中文本")


class ImageViewer(QWidget):
    """全屏看原图：顶层无边框窗口，`←` `→` 在图片记录间翻页，单击 / `Esc` 关闭。

    **必须是顶层窗口**：应用主窗口只有 440 宽，挂在页面里的遮罩再怎么放大
    也只有几百像素，Retina 截图根本看不清。全屏窗口跟着当前屏幕走
    （`owner.screen()`），多屏时开在用户正在看的那一块上。
    """

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowType.Window)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._shots: list[tuple[str, str, bytes]] = []   # (caption, record_id, png)
        self._index = 0
        self._pix: QPixmap | None = None
        self._owner = None

    # ------------------------------------------------------------------ 打开
    def show_shots(self, shots, index: int, owner: QWidget | None = None) -> bool:  # noqa: ANN001
        if not shots:
            return False
        self._shots = list(shots)
        self._index = max(0, min(index, len(self._shots) - 1))
        self._owner = owner
        if not self._load_current():
            return False

        screen = None
        if owner is not None:
            screen = owner.window().screen()
        if screen is None:
            screen = self.screen()
        self.setGeometry(screen.availableGeometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        return True

    def _load_current(self) -> bool:
        if not self._shots or not (0 <= self._index < len(self._shots)):
            return False
        png = self._shots[self._index][2]
        image = QImage()
        if not image.loadFromData(png, "PNG"):
            self._pix = None
            return False
        self._pix = QPixmap.fromImage(image)
        self.update()
        return True

    def is_open(self) -> bool:
        return self.isVisible()

    def _step(self, delta: int) -> None:
        if len(self._shots) < 2:
            return
        self._index = (self._index + delta) % len(self._shots)
        if not self._load_current() and delta > 0:
            self._index = (self._index + 1) % len(self._shots)
            self._load_current()

    # ------------------------------------------------------------------ 关闭
    def close_viewer(self) -> None:
        self.hide()
        self._pix = None
        self._shots = []
        self._owner = None

    def _close(self) -> None:
        owner = self._owner
        self.close_viewer()
        if owner is not None:
            owner.activateWindow()
            owner.setFocus()

    # ------------------------------------------------------------------ 绘制
    def _caption(self) -> str:
        if not self._shots:
            return ""
        text = self._shots[self._index][0]
        if len(self._shots) > 1:
            return f"{self._index + 1}/{len(self._shots)}  {text}"
        return text

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(15, 23, 42, 235))

        font = p.font()
        font.setPixelSize(13)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 214))
        p.drawText(QRectF(24, 18, self.width() - 240, 22),
                   Qt.AlignLeft | Qt.AlignVCenter, self._caption())
        p.setPen(QColor(255, 255, 255, 150))
        hint = "← → 切换 · 单击或 Esc 关闭"
        p.drawText(QRectF(self.width() - 216, 18, 192, 22),
                   Qt.AlignRight | Qt.AlignVCenter, hint)

        if self._pix is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            area = QRectF(self.rect()).adjusted(24, 56, -24, -24)
            size = self._pix.size().scaled(area.size().toSize(), Qt.KeepAspectRatio)
            target = QRectF(0, 0, size.width(), size.height())
            target.moveCenter(area.center())
            clip = QPainterPath()
            clip.addRoundedRect(target, 8, 8)
            p.setClipPath(clip)
            p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
        p.end()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._close()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._close()
            return
        if key == Qt.Key_Left:
            self._step(-1)
            return
        if key == Qt.Key_Right:
            self._step(1)
            return
        super().keyPressEvent(event)


class ClipboardPage(QWidget):
    """剪贴板页。`refresh()` 由外部定时器驱动（页面不可见时不要调）。"""

    backRequested = Signal()
    retryRequested = Signal(str)
    copyRequested = Signal(str)
    cleared = Signal()

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._records: list[ClipRecord] = []
        self._rows: dict[str, ClipRow] = {}
        self._selected: str | None = None
        self._last_sig: str | None = None
        self._conn_sig = ""
        self.viewer = ImageViewer()          # 顶层全屏窗口，不属于页面布局
        self._build()

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._header())

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(18, 6, 18, 10)
        body_lay.setSpacing(10)

        body_lay.addWidget(self._list_area(), 1)
        body_lay.addWidget(self._empty_area(), 1)

        self.preview = PreviewPane()
        self.preview.enlargeRequested.connect(self._enlarge)
        body_lay.addWidget(self.preview)
        outer.addWidget(body, 1)

        outer.addWidget(self._footer())

        self.statusbar = StatusBar()
        self.statusbar.text.setText("仅内存保留 · 退出即清 · 敏感内容不记录")
        self.statusbar.ver.setText("")
        outer.addWidget(self.statusbar)

        self.setFocusPolicy(Qt.StrongFocus)

    def _header(self) -> QWidget:
        """和设置页同一个 `page_header`：返回 / 页名（整页正中）/ 连接状态，同一水平线。"""
        # 这里**不能**用 ElidedLabel：它的 sizeHint 按「当前已省略的文字」算，
        # 一开始被压到最小宽度 → 立刻省略 → hint 就只有省略后的宽度，越省越窄
        # （实测"●  已连接"被缩成 40px 的"●…接"）。文案本来就短，直接放普通标签。
        self.conn = label("\u25cf  未连接", "clipConn")
        self.header = page_header("剪贴板", on_back=self.backRequested, right=self.conn)
        self.back = self.header.back_btn
        return self.header

    def _list_area(self) -> QWidget:
        self.scroll = QScrollArea()
        self.scroll.setObjectName("clipList")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")

        self.list_host = QWidget()
        # 不要给 list_host 设 setStyleSheet("background:transparent")：控件级样式表会
        # 连子控件一起接管，把状态药丸（#stOk 等）的应用级背景整条盖掉 —— 实测药丸只剩文字。
        # 但 QScrollArea::setWidget() 会**强行**把容器的 autoFillBackground 打开，
        # 于是它用调色板的窗口色（灰）刷一遍，把窗口白底盖住。所以这一行必须在 setWidget 之后。
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(2)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_host)
        self.list_host.setAutoFillBackground(False)
        return self.scroll

    def _empty_area(self) -> QWidget:
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(24, 0, 24, 0)
        el.setSpacing(10)

        plate = QFrame()
        plate.setObjectName("clipEmptyIcon")
        plate.setFixedSize(56, 56)
        plate_lay = QVBoxLayout(plate)
        plate_lay.setContentsMargins(0, 0, 0, 0)
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setPixmap(_logo_pixmap(theme.PLATFORM_LOGO, 26))
        plate_lay.addWidget(logo)

        self.empty_title = label("还没有内容", "clipEmptyTitle")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_sub = label("复制点什么，然后按热键切一次屏", "clipEmptyText")
        self.empty_sub.setAlignment(Qt.AlignCenter)
        self.empty_sub.setWordWrap(True)

        self.pair_btn = QPushButton("显示配对码")
        self.pair_btn.setObjectName("ghost")
        self.pair_btn.setFixedHeight(30)
        self.pair_btn.setCursor(Qt.PointingHandCursor)

        self.empty_actions = QHBoxLayout()
        self.empty_actions.setSpacing(8)
        self.empty_actions.addStretch(1)
        self.empty_actions.addWidget(self.pair_btn)
        self.empty_actions.addStretch(1)

        el.addStretch(1)
        el.addWidget(plate, 0, Qt.AlignHCenter)
        el.addWidget(self.empty_title)
        el.addWidget(self.empty_sub)
        el.addLayout(self.empty_actions)
        el.addStretch(1)
        self.empty.setVisible(False)
        return self.empty

    def _footer(self) -> QWidget:
        """操作条：主按钮用 solid，次按钮用 ghost，销毁性操作用红字 ghost。"""
        bar = QFrame()
        bar.setObjectName("clipBar")
        bar.setFixedHeight(FOOTER_H)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(18, 0, 18, 0)
        bl.setSpacing(8)

        self.btn_copy = QPushButton("复制到本机")
        self.btn_copy.setObjectName("solid")
        self.btn_retry = QPushButton("重发")
        self.btn_retry.setObjectName("ghost")
        for btn in (self.btn_copy, self.btn_retry):
            btn.setFixedHeight(30)
            btn.setCursor(Qt.PointingHandCursor)

        self.btn_clear = QPushButton("清空记录")
        self.btn_clear.setObjectName("clipDanger")
        self.btn_clear.setFixedHeight(30)
        self.btn_clear.setCursor(Qt.PointingHandCursor)

        self.btn_copy.clicked.connect(self._copy_selected)
        self.btn_retry.clicked.connect(self._retry_selected)
        self.btn_clear.clicked.connect(self._clear)
        bl.addWidget(self.btn_copy)
        bl.addWidget(self.btn_retry)
        bl.addStretch(1)
        bl.addWidget(self.btn_clear)

        self.footer = bar
        return bar

    def hideEvent(self, event):  # noqa: N802
        # 翻回主页面时把全屏预览收掉：它是顶层窗口，页面藏了它还会挂在屏幕上
        self.viewer.close_viewer()
        super().hideEvent(event)

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        """按引擎记录重画。内容没变就什么都不做（300ms 定时器会一直调）。"""
        if self.engine is None:
            return
        records = self.engine.records()
        sig = "|".join(f"{r.id}:{r.state}" for r in records)
        connected = self.engine.status().get("authenticated") == "yes"
        peer = self.engine.status().get("peer_device", "-")
        conn_sig = f"{connected}:{peer}"
        if sig == self._last_sig and conn_sig == self._conn_sig:
            return
        self._last_sig = sig
        self._conn_sig = conn_sig

        self._records = records
        self._rebuild_rows()
        self._update_conn(connected)
        self._update_empty(records)
        self.statusbar.ver.setText(f"{len(records)} 条")

    def _update_conn(self, connected: bool) -> None:
        if connected:
            self.conn.setText("\u25cf  已连接")
            self.conn.setStyleSheet("color:#0E9C6A;")
        else:
            self.conn.setText("\u25cf  未连接")
            self.conn.setStyleSheet("color:#A9B2C1;")

    def _update_empty(self, records: list[ClipRecord]) -> None:
        paired = bool(getattr(self.engine.cfg, "psk", "")) if self.engine else False
        show_empty = not records
        self.empty.setVisible(show_empty)
        self.scroll.setVisible(not show_empty)
        self.preview.setVisible(not show_empty)
        self.footer.setVisible(not show_empty)      # 没记录时按钮点了也没反应，收起来
        if show_empty:
            if paired:
                self.empty_title.setText("还没有内容")
                self.empty_sub.setText("复制点什么，然后按热键切一次屏")
                self.pair_btn.setVisible(False)
            else:
                self.empty_title.setText("两台机器连上才能互通")
                self.empty_sub.setText("点下面的按钮生成配对码，再到另一台机器上输入同一个码")
                self.pair_btn.setVisible(True)

    def _rebuild_rows(self) -> None:
        # 只重建变化的行；简单起见整表重建（最多 10 行，代价可忽略）
        while self.list_lay.count() > 1:
            item = self.list_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
        if self._selected not in [r.id for r in self._records]:
            self._selected = self._records[0].id if self._records else None
        for rec in self._records:
            row = ClipRow(rec)
            row.set_selected(rec.id == self._selected, animate=False)
            row.clicked.connect(self._select)
            row.activated.connect(self._copy)
            row.deleteRequested.connect(self._drop)
            row.retryRequested.connect(self._retry)
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)
            self._rows[rec.id] = row
        current = next((r for r in self._records if r.id == self._selected), None)
        self.preview.show_record(current)

    def _select(self, record_id: str) -> None:
        if record_id == self._selected:
            return
        old = self._rows.get(self._selected or "")
        if old is not None:
            old.set_selected(False)
        self._selected = record_id
        row = self._rows.get(record_id)
        if row is not None:
            row.set_selected(True)
        current = next((r for r in self._records if r.id == record_id), None)
        self.preview.show_record(current)

    # ------------------------------------------------------------------ 动作
    def _image_shots(self) -> list[tuple[str, str, bytes]]:
        """全部图片记录（`caption, id, png`）—— 全屏预览里左右翻页用。"""
        shots: list[tuple[str, str, bytes]] = []
        for rec in self._records:
            if rec.png is None:
                continue
            title = f"截图 {rec.width} × {rec.height}" if rec.kind == "image" else "图片"
            shots.append((f"{title} · {_human_size(rec.size)}", rec.id, rec.png))
        return shots

    def _enlarge(self) -> None:
        """双击预览图 → 全屏看原图（顶层窗口，← → 翻页）。"""
        rec = next((r for r in self._records if r.id == self._selected), None)
        if rec is None or rec.png is None:
            return
        shots = self._image_shots()
        index = next((i for i, shot in enumerate(shots) if shot[1] == rec.id), 0)
        if not self.viewer.show_shots(shots, index, owner=self):
            self.preview.hint.setText("这张图读不出来")

    def _copy_selected(self) -> None:
        if self._selected:
            self._copy(self._selected)

    def _copy(self, record_id: str) -> None:
        if self.engine is not None and self.engine.copy_to_local(record_id):
            self.copyRequested.emit(record_id)

    def _retry_selected(self) -> None:
        if self._selected:
            self._retry(self._selected)

    def _retry(self, record_id: str) -> None:
        if self.engine is not None:
            self.engine.repush(record_id)
            self.retryRequested.emit(record_id)
            self._last_sig = None      # 强制下次刷新，让状态立刻变成「传输中」
            self.refresh()

    def _drop(self, record_id: str) -> None:
        if self.engine is not None:
            self.engine.drop_record(record_id)
            self._last_sig = None
            self.refresh()

    def _clear(self) -> None:
        if self.engine is not None:
            self.engine.clear_records()
            self._last_sig = None
            self.refresh()
            self.cleared.emit()

    # ------------------------------------------------------------------ 键盘
    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.backRequested.emit()
            return
        if key in (Qt.Key_Up, Qt.Key_Down):
            self._move(key == Qt.Key_Down)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._copy_selected()
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            if self._selected:
                self._drop(self._selected)
            return
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier) and key == Qt.Key_C:
            self._copy_selected()
            return
        super().keyPressEvent(event)

    def _move(self, down: bool) -> None:
        if not self._records:
            return
        ids = [r.id for r in self._records]
        try:
            index = ids.index(self._selected or ids[0])
        except ValueError:
            index = 0
        index = min(len(ids) - 1, index + 1) if down else max(0, index - 1)
        self._select(ids[index])
        row = self._rows.get(ids[index])
        if row is not None:
            self.scroll.ensureWidgetVisible(row, 0, 8)
