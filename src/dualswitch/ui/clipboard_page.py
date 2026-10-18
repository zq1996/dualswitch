"""剪贴板页：主窗口里的一页（不是新窗口）。

规格见 `docs/UI-DESIGN-CLIPBOARD.md`。两端完全共用，**这里不出现 `sys.platform`**：
入口放哪儿由 `chrome.INLINE_CLIPBOARD_ROW` 决定，本文件只管版面。

版面（440 宽）：

    ┌──────────────────────────────────────┐
    │ ‹ 返回    剪贴板            ● 对端名 │ 40  页头
    ├──────────────────────────────────────┤
    │ ▣  截图 2560×1440            已同步  │ 52  列表（可滚）
    │ T  def switch_source(...)    仅本机  │ 52
    ├──────────────────────────────────────┤
    │ （预览区，固定高度，不随内容跳动）   │ 176
    ├──────────────────────────────────────┤
    │ [复制到本机] [重发]      [清空记录]  │ 48
    ├──────────────────────────────────────┤
    │ ● 仅内存保留 · 退出即清 · …          │ 34
    └──────────────────────────────────────┘

**为什么预览和列表的高度都固定**：内容长度变化会让窗口"呼吸"，用户每次都要重新找
眼睛落点。固定高度、内部滚动，是这类低频查看界面的稳妥做法。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
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
from .widgets import ElidedLabel, StatusBar, logo_pixmap

HEADER_H = 40
ROW_H = 52
LIST_H = 252
PREVIEW_H = 176
ACTION_H = 48

STATE_TEXT = {"ok": "已同步", "pend": "传输中", "fail": "失败", "local": "仅本机"}
STATE_COLOR = {
    "ok": ("#0E7C4A", "#E9F7F0"),
    "pend": ("#1E4FD8", "#EAF0FF"),
    "fail": ("#A82B2B", "#FDECEC"),
    "local": ("#7C8698", "#F1F3F7"),
}
THUMB_MAX_BYTES = 8 * 1024 * 1024


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


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """按 t∈[0,1] 在两种颜色间线性插值（hover 背景过渡用）。"""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


class Thumb(QWidget):
    """36×36 缩略图：图片画缩放后的图，文本画本机系统图标。"""

    def __init__(self, record: ClipRecord, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self._kind = record.kind
        self._pix: QPixmap | None = None
        if record.png is not None and len(record.png) <= THUMB_MAX_BYTES:
            image = QImage()
            if image.loadFromData(record.png, "PNG"):
                self._pix = QPixmap.fromImage(
                    image.scaled(36, 36, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                )
        elif record.kind == "text":
            # 文本记录没有图：按**来源**画系统 logo —— 本机复制用本机图标，
            # 对端传来的就是对端系统的图标（mac 上的 remote 必然来自 Windows，反之亦然）
            if record.source == "remote":
                logo = "windows" if theme.SYSTEM_LOGO == "apple" else "apple"
            else:
                logo = theme.SYSTEM_LOGO
            self._pix = logo_pixmap(logo, 22)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 7, 7)
        p.setClipPath(path)
        p.fillRect(rect, QColor("#F4F6FA"))
        if self._pix is not None:
            if self._kind == "image":
                # 图片铺满整个缩略图
                p.drawPixmap(rect, self._pix)
            else:
                # 系统图标居中，不拉伸
                target = self._pix
                x = rect.x() + (rect.width() - target.width()) // 2
                y = rect.y() + (rect.height() - target.height()) // 2
                p.drawPixmap(x, y, target)
        p.end()


class StatePill(QLabel):
    """状态药丸：固定高度 + 文字垂直居中（高度随字体撑会导致各行基线不齐）。"""

    def __init__(self, state: str, progress: float = 0.0, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.set_state(state, progress)

    def set_state(self, state: str, progress: float = 0.0) -> None:
        fg, bg = STATE_COLOR.get(state, STATE_COLOR["local"])
        text = STATE_TEXT.get(state, state)
        if state == "pend" and progress:
            text = f"{text} {int(progress * 100)}%"
        self.setText(text)
        self.setStyleSheet(
            f"color:{fg};background:{bg};border-radius:5px;"
            f"padding:0 6px;font-size:10px;font-weight:600;"
        )


class ClipRow(QFrame):
    """一条记录：缩略图 + 主行 + 副行 + 状态药丸。

    背景自绘：hover 用 QPropertyAnimation 平滑淡入淡出（不再瞬切 setStyleSheet），
    选中的浅蓝底 + 左侧主色条是明确点击，保持瞬时、不掺动画。
    """

    clicked = Signal(str)
    activated = Signal(str)      # 双击
    deleteRequested = Signal(str)
    retryRequested = Signal(str)

    def __init__(self, record: ClipRecord, parent=None):
        super().__init__(parent)
        self.record = record
        self.setObjectName("clipRow")
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._selected = False
        self._hover = 0.0
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(140)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(10)
        lay.addWidget(Thumb(record), 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        title = ElidedLabel(self._title(record), mode=Qt.ElideRight)
        title.setMinimumWidth(60)
        title.setStyleSheet(
            f"color:{theme.INK};font-size:12.5px;"
            + ("font-family:'SF Mono','Menlo',monospace;" if record.kind != "image" else "")
        )
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        text_col.addWidget(title)

        sub = QLabel(self._subtitle(record))
        sub.setStyleSheet("color:#818B9C;font-size:10.5px;")
        sub.setAttribute(Qt.WA_TransparentForMouseEvents)
        text_col.addWidget(sub)
        lay.addLayout(text_col, 1)

        self.pill = StatePill(record.state, record.progress)
        lay.addWidget(self.pill, 0, Qt.AlignVCenter)

        self._del = QPushButton("\u00d7")
        self._del.setCursor(Qt.PointingHandCursor)
        self._del.setFixedSize(16, 16)
        self._del.setStyleSheet(
            "QPushButton{border:none;color:#A9B2C1;font-size:13px;background:transparent;}"
            "QPushButton:hover{color:#A82B2B;}"
        )
        self._del.setVisible(False)
        self._del.clicked.connect(lambda: self.deleteRequested.emit(self.record.id))
        lay.addWidget(self._del, 0, Qt.AlignVCenter)

    # hover 动画属性（QPropertyAnimation 驱动）
    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()
        if self._selected:
            p.fillRect(r, QColor("#F3F7FF"))
            p.fillRect(0, 0, 3, r.height(), QColor(theme.ACCENT))
        elif self._hover > 0.0:
            p.fillRect(r, _blend(QColor("#FFFFFF"), QColor("#F8FAFD"), self._hover))
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

    # ---- 交互 ----
    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.update()

    def enterEvent(self, event):  # noqa: N802
        self._del.setVisible(True)
        self._hover_anim.stop()
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._del.setVisible(False)
        self._hover_anim.stop()
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            if self.record.state in ("fail", "local"):
                # 药丸本身就是重试入口（设计第 4 节）
                self.retryRequested.emit(self.record.id)
            self.clicked.emit(self.record.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.record.id)
        super().mouseDoubleClickEvent(event)


class PreviewPane(QFrame):
    """固定高度的预览区：图片等比缩放，文本可选中。

    图片可双击放大（发出 `imageActivated`，由页面层弹查看器）。
    """

    imageActivated = Signal(object)   # ClipRecord

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPane")
        self.setFixedHeight(PREVIEW_H)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("QFrame#previewPane{background:#FAFBFD;border-top:1px solid #EEF1F6;}")
        self._current: ClipRecord | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.caption = QLabel("预览")
        self.caption.setStyleSheet("color:#818B9C;font-size:10.5px;")
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#A9B2C1;font-size:10.5px;")
        head.addWidget(self.caption, 0)
        head.addStretch(1)
        head.addWidget(self.hint, 0)
        lay.addLayout(head)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setCursor(Qt.PointingHandCursor)
        self.image.setStyleSheet("background:transparent;")
        lay.addWidget(self.image, 1)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet(
            "QPlainTextEdit{background:#FFFFFF;border:1px solid #E5E9F1;border-radius:9px;"
            f"padding:6px;color:{theme.INK};font-size:11.5px;"
            "font-family:'SF Mono','Menlo',monospace;}"
        )
        self.text.setVisible(False)
        lay.addWidget(self.text, 1)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self._current is not None and self._current.is_image:
            self.imageActivated.emit(self._current)
            return
        super().mouseDoubleClickEvent(event)

    def show_record(self, rec: ClipRecord | None) -> None:
        self._current = rec
        if rec is None:
            self.caption.setText("预览")
            self.hint.setText("选中一条看内容")
            self.image.clear()
            self.image.setVisible(True)
            self.image.setCursor(Qt.ArrowCursor)
            self.text.setVisible(False)
            return
        if rec.png is not None:
            image = QImage()
            if image.loadFromData(rec.png, "PNG"):
                pix = QPixmap.fromImage(
                    image.scaled(414, PREVIEW_H - 34, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                self.image.setPixmap(pix)
                self.image.setVisible(True)
                self.image.setCursor(Qt.PointingHandCursor)
                self.text.setVisible(False)
                self.caption.setText(f"预览  图片 · {_human_size(rec.size)}")
                self.hint.setText("双击查看原图")
                return
        self.image.setVisible(False)
        self.text.setVisible(True)
        self.text.setPlainText(rec.text or "")
        self.caption.setText(f"预览  文本 · {_human_size(rec.size)}")
        self.hint.setText("可直接选中文本")


class ImageViewer(QWidget):
    """双击预览图后的原图查看浮层：覆盖整个剪贴板页，点任意处或 Esc 关闭。

    不是独立窗口（遵守「整个应用只有一个窗口」的约定），而是页面里的一个透明
    遮罩层。原图在遮罩内等比缩放到可用空间，双击放大到 100% 后可用滚动条细看。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("imageViewer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#imageViewer{background:rgba(17,21,28,0.82);}"
            "QLabel#viewerImg{background:transparent;}"
            "QLabel#viewerHint{color:#C9D2E0;font-size:11px;}"
        )
        self.setFocusPolicy(Qt.StrongFocus)
        self._zoom = False
        self._pix: QPixmap | None = None
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(250)
        self._close_timer.timeout.connect(self.hide)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        self.hint = QLabel("点击关闭 · 双击切换 100%/适应")
        self.hint.setObjectName("viewerHint")
        self.hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:horizontal,QScrollBar:vertical{width:8px;background:transparent;}"
            "QScrollBar::handle:horizontal,QScrollBar::handle:vertical{background:#4A5468;"
            "border-radius:4px;min-width:18px;min-height:18px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0;}"
        )
        self.img = QLabel()
        self.img.setObjectName("viewerImg")
        self.img.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.img)
        lay.addWidget(self.scroll, 1)

    def show_image(self, rec: ClipRecord) -> None:
        if rec.png is None:
            return
        image = QImage()
        if not image.loadFromData(rec.png, "PNG"):
            return
        self._close_timer.stop()
        self._pix = QPixmap.fromImage(image)
        self._zoom = False
        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        self._apply_scale()
        self.setFocus()

    def _apply_scale(self) -> None:
        if self._pix is None:
            return
        if self._zoom:
            self.img.setPixmap(self._pix)
            self.img.adjustSize()
            self.hint.setText("点击关闭 · 双击回到适应窗口（可滚动）")
        else:
            avail = self.scroll.viewport().size()
            scaled = self._pix.scaled(
                max(1, avail.width() - 4),
                max(1, avail.height() - 4),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.img.setPixmap(scaled)
            self.img.adjustSize()
            self.hint.setText("点击关闭 · 双击查看 100%")

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if not self._zoom:
            self._apply_scale()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            # 延迟关闭：若紧接着是双击，则让双击接管（切缩放）
            self._close_timer.start()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._close_timer.stop()
            self._zoom = not self._zoom
            self._apply_scale()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide()
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
        self._build()

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 页头：三个元素统一垂直居中、字号成体系（返回 12.5 / 标题 13.5 / 状态 11）
        head = QWidget()
        head.setFixedHeight(HEADER_H)
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 0, 14, 0)
        hl.setSpacing(8)

        self.back = QPushButton("\u2039 返回")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.setFixedHeight(26)
        self.back.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;color:{theme.ACCENT_DEEP};"
            "font-size:12.5px;padding:0 4px;}"
            "QPushButton:hover{color:#0B3BB0;}"
        )
        self.back.clicked.connect(self.backRequested)
        hl.addWidget(self.back, 0, Qt.AlignVCenter)

        title = QLabel("剪贴板")
        title.setStyleSheet(f"color:{theme.INK};font-size:13.5px;font-weight:600;")
        hl.addStretch(1)
        hl.addWidget(title, 0, Qt.AlignVCenter)
        hl.addStretch(1)

        # 连接状态：圆点用小色块画（● 字符的基线天生和文字对不齐）
        self.conn_dot = QLabel()
        self.conn_dot.setFixedSize(7, 7)
        self.conn_text = QLabel("未连接")
        self.conn_text.setStyleSheet("color:#A9B2C1;font-size:11px;")
        conn_box = QWidget()
        conn_lay = QHBoxLayout(conn_box)
        conn_lay.setContentsMargins(0, 0, 0, 0)
        conn_lay.setSpacing(5)
        conn_lay.addWidget(self.conn_dot, 0, Qt.AlignVCenter)
        conn_lay.addWidget(self.conn_text, 0, Qt.AlignVCenter)
        self.conn_box = conn_box
        hl.addWidget(conn_box, 0, Qt.AlignVCenter)
        outer.addWidget(head)

        # 列表
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(LIST_H)
        self.scroll.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}"
            "QScrollBar:vertical{width:6px;background:transparent;}"
            "QScrollBar::handle:vertical{background:#D9DFE9;border-radius:3px;min-height:20px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self.list_host = QWidget()
        self.list_host.setStyleSheet("background:transparent;")
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_host)
        outer.addWidget(self.scroll)

        # 空态
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(28, 0, 28, 0)
        el.setSpacing(10)
        self.empty_title = QLabel("还没有内容")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet(f"color:{theme.INK};font-size:13px;")
        self.empty_sub = QLabel("复制点什么，然后按热键切一次屏")
        self.empty_sub.setAlignment(Qt.AlignCenter)
        self.empty_sub.setWordWrap(True)
        self.empty_sub.setStyleSheet("color:#818B9C;font-size:11.5px;")
        el.addStretch(1)
        el.addWidget(self.empty_title)
        el.addWidget(self.empty_sub)
        self.empty_actions = QHBoxLayout()
        self.empty_actions.setSpacing(8)
        self.pair_btn = QPushButton("显示配对码")
        self.pair_btn.setCursor(Qt.PointingHandCursor)
        self.pair_btn.setObjectName("ghost")
        self.empty_actions.addStretch(1)
        self.empty_actions.addWidget(self.pair_btn)
        self.empty_actions.addStretch(1)
        el.addLayout(self.empty_actions)
        el.addStretch(1)
        self.empty.setVisible(False)
        outer.addWidget(self.empty, 1)

        # 预览
        self.preview = PreviewPane()
        outer.addWidget(self.preview)

        # 原图查看浮层：覆盖在页面上，不另开窗口
        self.viewer = ImageViewer(self)
        self.viewer.setVisible(False)
        self.preview.imageActivated.connect(self.viewer.show_image)

        # 操作栏（样式用全局 QSS 的 #clipBar：裸 setStyleSheet 会级联到子按钮，
        # 把 clipPrimary 的渐变盖成白底白字）
        bar = QWidget()
        bar.setObjectName("clipBar")
        bar.setFixedHeight(ACTION_H)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(8)
        self.btn_copy = QPushButton("复制到本机")
        self.btn_copy.setObjectName("clipPrimary")
        self.btn_copy.setFixedHeight(30)
        self.btn_retry = QPushButton("重发")
        self.btn_retry.setObjectName("clipBtn")
        self.btn_retry.setFixedHeight(30)
        for btn in (self.btn_copy, self.btn_retry):
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
        outer.addWidget(bar)

        self.statusbar = StatusBar()
        self.statusbar.text.setText("仅内存保留 · 退出即清 · 敏感内容不记录")
        self.statusbar.ver.setText("")
        outer.addWidget(self.statusbar)

        self.setFocusPolicy(Qt.StrongFocus)

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
            self.conn_dot.setStyleSheet("background:#0E9C6A;border-radius:3.5px;")
            self.conn_text.setText("已连接")
            self.conn_text.setStyleSheet("color:#0E9C6A;font-size:11px;")
        else:
            self.conn_dot.setStyleSheet("background:#C3CCDB;border-radius:3.5px;")
            self.conn_text.setText("未连接")
            self.conn_text.setStyleSheet("color:#A9B2C1;font-size:11px;")

    def _update_empty(self, records: list[ClipRecord]) -> None:
        paired = bool(getattr(self.engine.cfg, "psk", "")) if self.engine else False
        show_empty = not records
        self.empty.setVisible(show_empty)
        self.scroll.setVisible(not show_empty)
        self.preview.setVisible(not show_empty)
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
            row.set_selected(rec.id == self._selected)
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
            if self.viewer.isVisible():
                self.viewer.hide()
            else:
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

    # ------------------------------------------------------------------ 浮层
    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        # 原图查看浮层覆盖整个页面
        self.viewer.setGeometry(self.rect())
