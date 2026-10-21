"""悬浮预览窗（INTERACTIONS §3.4，Frset 交互定稿）。

触发：**只悬停缩略图** 450ms（`hoverPreviewOpenDelay`）——不是整行。
关闭：延时 220ms（`hoverPreviewCloseDelay`）；预览窗自身 hover 可停留；
滚动 / 换页 / 换行立即关。

位置：气泡在缩略图**左侧**、箭头朝右指向缩略图；左侧放不下翻右侧；
箭头垂直对准缩略图中线；超出屏幕夹紧。

尺寸：随内容自适应 + 分类上限（图≤412×330 / 文字≤392×270 / 代码≤400×290 /
文件卡≤380 / 链接块≤380）。入场：fade 180ms + 位置弹簧 320ms（OutBack ≈ 过冲回弹）。
"""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QRect,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from .widgets import ElidedLabel, IconButton, destroy

STATE_TEXT = {"ok": "已同步", "pend": "传输中", "fail": "失败", "local": "仅本机"}
STATE_STYLE = {"ok": "statOk", "pend": "statRun", "fail": "statBad", "local": "statIdle"}
ARROW_MARGIN = 10


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.1f} MB"


def ago(ts: float) -> str:
    gap = max(0.0, time.time() - ts)
    if gap < 60:
        return "刚刚"
    if gap < 3600:
        return f"{int(gap // 60)} 分钟前"
    if gap < 86400:
        return f"{int(gap // 3600)} 小时前"
    return f"{int(gap // 86400)} 天前"


def source_label(record) -> str:  # noqa: ANN001
    if record.source == "remote":
        return record.peer_name or "对端"
    return "本机"


def source_system(record) -> str:
    """来源系统（角标）：本机 → 本机系统；remote → 另一台。"""
    from ..chrome import current as chrome

    local = chrome().PLATFORM_LOGO  # apple | windows
    if record.source == "remote":
        return "windows" if local == "apple" else "apple"
    return local


def state_pill(record) -> QLabel:  # noqa: ANN001
    pill = QLabel(STATE_TEXT.get(record.state, record.state))
    pill.setObjectName(STATE_STYLE.get(record.state, "statIdle"))
    pill.setAttribute(Qt.WA_TransparentForMouseEvents)
    return pill


def meta_line(record, ui_kind: str) -> str:
    """`来源 · 时间 · 大小[ · 尺寸] · 类型`（ClipRecord 无应用名字段，应用段省略）。"""
    kind_name = {
        "image": "图片", "link": "链接", "code": "代码", "text": "纯文本", "file": "文件",
    }.get(ui_kind, ui_kind)
    parts = [source_label(record), ago(record.ts), human_size(record.size)]
    if ui_kind == "image" and record.width and record.height:
        parts.append(f"{record.width}×{record.height}")
    parts.append(kind_name)
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# 气泡本体
# --------------------------------------------------------------------------- #
class PreviewBubble(QFrame):
    """带指向箭头的气泡：圆角卡片 + 朝锚点侧的箭头（Qt 顶层矩形含箭头留白）。"""

    copyRequested = Signal(str)
    openRequested = Signal(str)
    findRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground, True)   # 箭头留白区透明
        self._arrow_right = True
        self._arrow_y = 40
        self._record_id = ""

        # 圆角卡片（箭头贴在它朝锚点的那条边上）
        self._inner = QFrame(self)
        self._inner.setObjectName("pvRoot")
        inner_lay = QVBoxLayout(self._inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(0)

        # ---- 头部 ----
        head = QFrame()
        head.setObjectName("pvHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 11, 14, 10)
        hl.setSpacing(9)
        self._head_icon = QLabel()
        self._head_icon.setFixedSize(18, 18)
        self._head_title = ElidedLabel("", "pvTitle")
        self._pill_box = QHBoxLayout()
        self._pill_box.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self._head_icon, 0, Qt.AlignVCenter)
        hl.addWidget(self._head_title, 1)
        hl.addLayout(self._pill_box, 0)
        self._head = head
        inner_lay.addWidget(head)

        # ---- 正文 ----
        body_wrap = QFrame()
        body_wrap.setObjectName("pvBody")
        self._body_lay = QVBoxLayout(body_wrap)
        self._body_lay.setContentsMargins(14, 14, 14, 14)
        self._body_lay.setSpacing(9)
        self._body_wrap = body_wrap
        inner_lay.addWidget(body_wrap, 1)

        # ---- 底栏 ----
        foot = QFrame()
        foot.setObjectName("pvFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(14, 10, 14, 12)
        fl.setSpacing(8)
        self._meta = ElidedLabel("", "pvMeta")
        fl.addWidget(self._meta, 1)
        # 底栏按钮全图标（用户要求：剪贴板页不留文字按钮）
        self._btn_preview = IconButton("preview", "用图片预览打开", "pv")
        self._btn_find = IconButton("finder", "在访达中显示", "pv")
        self._btn_copy = IconButton("copy", "复制", "pv", primary=True)
        for b in (self._btn_preview, self._btn_find, self._btn_copy):
            fl.addWidget(b, 0, Qt.AlignVCenter)
        self._btn_preview.setVisible(False)
        self._btn_find.setVisible(False)
        self._btn_preview.clicked.connect(lambda: self.openRequested.emit(self._record_id))
        self._btn_find.clicked.connect(lambda: self.findRequested.emit(self._record_id))
        self._btn_copy.clicked.connect(lambda: self.copyRequested.emit(self._record_id))
        self._foot = foot
        inner_lay.addWidget(foot)

    # ------------------------------------------------------------------ 几何
    def set_arrow(self, right: bool, y: int) -> None:
        self._arrow_right = right
        self._arrow_y = y
        self.update()

    @property
    def arrow_right(self) -> bool:
        return self._arrow_right

    def sync_size(self) -> None:
        """按内容自适应 + 分类上限（§3.4）。

        宽度先按「内容自然宽度」算（图片按像素宽、文字按最长一行），再用上限夹紧；
        高度交给布局按**实际宽度**重排后再量 —— 只读 sizeHint 会因为 QLabel 的
        wordWrap 尺寸提示不准而把正文裁掉一截。
        """
        kind = getattr(self, "_ui_kind", "text")
        max_w, max_h = self.SIZE_CAPS.get(kind, self.SIZE_CAPS["text"])
        w = self._natural_width(max_w)
        inner_w = max(120, w - ARROW_MARGIN)
        self._inner.setFixedWidth(inner_w)
        for child in self._body_wrap.findChildren(QLabel):
            if not (child.wordWrap() or child.objectName() in ("pvText", "pvCode", "pvLink")):
                continue
            text_w = max(80, inner_w - 28)
            child.setFixedWidth(text_w)
            # QLabel 的 sizeHint 不含换行后的高度，自己按宽度量一遍再钉死高度，
            # 否则气泡会把正文最后一行裁掉（第一版就踩了）。
            fm = QFontMetrics(child.font())
            flags = Qt.TextWordWrap | Qt.TextExpandTabs
            need = fm.boundingRect(QRect(0, 0, text_w, 10000), int(flags), child.text()).height()
            need += fm.leading() * 0
            pad = 26 if child.objectName() == "pvCode" else 4
            limit = max_h - 150
            child.setFixedHeight(max(18, min(need + pad, limit)))
        # 三块高度直接相加：内层 layout 的 sizeHint 会被缓存成旧值（正文被裁的元凶）
        self._body_wrap.updateGeometry()
        head_h = self._head.sizeHint().height()
        body_h = self._body_wrap.sizeHint().height()
        foot_h = self._foot.sizeHint().height()
        h = max(120, min(head_h + body_h + foot_h, max_h))
        self.resize(w, h)
        self._apply_inner_geometry()

    def _natural_width(self, max_w: int) -> int:
        kind = getattr(self, "_ui_kind", "text")
        pad = 28 + ARROW_MARGIN
        if kind == "image":
            pix = getattr(self, "_image_w", 0)
            return min(max_w, max(200, pix + pad)) if pix else min(max_w, 240)
        text = getattr(self, "_body_text", "") or ""
        f = theme.qfont(10.5, 400, mono=(kind == "code")) if kind == "code" else theme.qfont(11.5)
        fm = QFontMetrics(f)
        longest = max((fm.horizontalAdvance(line) for line in text.splitlines()), default=0)
        return min(max_w, max(210, longest + pad + 18))

    def _apply_inner_geometry(self) -> None:
        if self._arrow_right:
            self._inner.setGeometry(0, 0, self.width() - ARROW_MARGIN, self.height())
        else:
            self._inner.setGeometry(ARROW_MARGIN, 0, self.width() - ARROW_MARGIN, self.height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_inner_geometry()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = theme.tokens()
        size = 13
        y = max(18, min(self.height() - 34, self._arrow_y)) - size / 2
        if self._arrow_right:
            x = self.width() - ARROW_MARGIN - size / 2 + 3
        else:
            x = ARROW_MARGIN - size / 2 - 3
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.card))
        p.translate(x + size / 2, y + size / 2)
        p.rotate(45)
        p.drawRoundedRect(-size / 2, -size / 2, size, size, 3, 3)
        p.end()

    def enterEvent(self, event):  # noqa: N802
        # 预览窗自身 hover 可停留（__isPreviewPopoverHovered）
        ctrl = getattr(self.window(), "_preview_controller", None)
        if ctrl is not None:
            ctrl.notify_bubble_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        ctrl = getattr(self.window(), "_preview_controller", None)
        if ctrl is not None:
            ctrl.notify_bubble_hover(False)
        super().leaveEvent(event)

    # ------------------------------------------------------------------ 填充
    def clear_body(self) -> None:
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            destroy(item.widget())

    def show_record(self, record, ui_kind: str) -> None:  # noqa: ANN001
        self._record_id = record.id
        self._ui_kind = ui_kind
        glyph = {
            "image": "image", "file": "file-text", "link": "link-2",
            "code": "code", "text": "type",
        }.get(ui_kind, "type")
        self._head_icon.setPixmap(
            icons.svg_pixmap(glyph, 18, theme.tokens().accent, 1.9)
        )
        if record.kind == "image":
            title = f"图片 {record.width}×{record.height}" if record.width else "图片"
        else:
            title = (record.text or "").splitlines()[0][:80] if record.text else "（空）"
        self._head_title.setText(title)
        self._replace_pill(record)
        self.clear_body()

        builders = {
            "image": self._build_image,
            "link": self._build_link,
            "code": self._build_code,
            "file": self._build_file,
        }
        builders.get(ui_kind, self._build_text)(record)

        self._meta.setText(meta_line(record, ui_kind))
        self._btn_preview.setVisible(ui_kind == "image")
        self._btn_find.setVisible(ui_kind in ("image", "file"))
        self.sync_size()

    #: 分类上限（INTERACTIONS §3.4）
    SIZE_CAPS = {
        "image": (theme.PV_IMG_MAX_W, theme.PV_IMG_MAX_H),
        "text": (theme.PV_TXT_MAX_W, theme.PV_TXT_MAX_H),
        "code": (theme.PV_CODE_MAX_W, theme.PV_CODE_MAX_H),
        "file": (theme.PV_CARD_MAX_W, 320),
        "link": (theme.PV_CARD_MAX_W, 320),
    }

    def _replace_pill(self, record) -> None:  # noqa: ANN001
        while self._pill_box.count():
            item = self._pill_box.takeAt(0)
            destroy(item.widget())
        self._pill_box.addWidget(state_pill(record))

    def _build_image(self, record) -> None:  # noqa: ANN001
        self._body_text = ""
        self._image_w = int(record.width or 0)
        image = QImage()
        ok = record.png is not None and image.loadFromData(record.png, "PNG")
        pix_label = QLabel()
        pix_label.setAlignment(Qt.AlignCenter)
        if ok:
            pix = QPixmap.fromImage(image)
            # 上限减去头/底/留白，整块随内容自适应（§3.4 尺寸表）
            scaled = pix.scaled(
                theme.PV_IMG_MAX_W - 28,
                theme.PV_IMG_MAX_H - 130,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            pix_label.setPixmap(scaled)
        self._body_lay.addWidget(pix_label, 0, Qt.AlignHCenter)
        if record.width and record.height:
            note = QLabel(f"{record.width}×{record.height} · {human_size(record.size)}")
            note.setObjectName("pvNote")
            self._body_lay.addWidget(note, 0, Qt.AlignHCenter)

    def _build_text(self, record) -> None:  # noqa: ANN001
        self._body_text = record.text or ""
        body = QLabel(self._body_text[:1200])
        body.setObjectName("pvText")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setMaximumHeight(theme.PV_TXT_MAX_H - 60)
        self._body_lay.addWidget(body)
        total = len(record.text or "")
        if total > 60:
            note = QLabel(f"当前已显示 60 / {total} 个字符")
            note.setObjectName("pvNote")
            self._body_lay.addWidget(note)

    def _build_code(self, record) -> None:  # noqa: ANN001
        self._body_text = record.text or ""
        body = QLabel(self._body_text[:1200])
        body.setObjectName("pvCode")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setContentsMargins(11, 11, 11, 11)
        body.setMaximumHeight(theme.PV_CODE_MAX_H - 60)
        self._body_lay.addWidget(body)

    def _build_link(self, record) -> None:  # noqa: ANN001
        self._body_text = record.text or ""
        block = QLabel(self._body_text)
        block.setObjectName("pvLink")
        block.setWordWrap(True)
        block.setContentsMargins(11, 11, 11, 11)

        self._body_lay.addWidget(block)

    def _build_file(self, record) -> None:  # noqa: ANN001
        card = QFrame()
        card.setObjectName("pvFileCard")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(13, 13, 13, 13)
        lay.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(icons.svg_pixmap("file-text", 36, theme.tokens().accent, 1.5))
        lay.addWidget(icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        name = ElidedLabel(record.text or "", "pvTitle")
        name.setFont(theme.qfont(11.5, 650))
        path = ElidedLabel(record.detail or "", "pvNote", mode=Qt.ElideMiddle)
        col.addWidget(name)
        col.addWidget(path)
        lay.addLayout(col, 1)
        card.setMaximumWidth(theme.PV_CARD_MAX_W - 28)
        card.setFixedWidth(theme.PV_CARD_MAX_W - 28 - ARROW_MARGIN)
        self._body_lay.addWidget(card)


# --------------------------------------------------------------------------- #
# 控制器：450ms / 220ms 延时 + 定位 + 动画
# --------------------------------------------------------------------------- #
class PreviewController(QObject):
    """挂在剪贴板页上；只有缩略图的 enter/leave 才能驱动它。"""

    OPEN_DELAY_MS = theme.PV_OPEN_DELAY_MS
    CLOSE_DELAY_MS = theme.PV_CLOSE_DELAY_MS

    def __init__(self, page=None):  # noqa: ANN001
        super().__init__(page)
        self._page = page
        self.bubble = PreviewBubble()
        self.bubble._preview_controller = self  # noqa: SLF001 - hover 停留的回连
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(self.OPEN_DELAY_MS)
        self._open_timer.timeout.connect(self._do_open)
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(self.CLOSE_DELAY_MS)
        self._close_timer.timeout.connect(self._do_close)
        self._pending: tuple | None = None
        self._anim: QPropertyAnimation | None = None
        self._fade: QPropertyAnimation | None = None
        self._current_id: str | None = None
        self._bubble_hovered = False

    # ---- 触发口 ----
    def schedule_open(self, record, ui_kind: str, anchor: QWidget) -> None:  # noqa: ANN001
        if self._current_id == record.id and self.bubble.isVisible():
            self._close_timer.stop()
            return
        self._close_timer.stop()
        self._open_timer.stop()
        self._do_close()
        self._pending = (record, ui_kind, anchor)
        self._open_timer.start()

    def open_now(self, record, ui_kind: str, anchor: QWidget) -> None:  # noqa: ANN001
        """单击行 = 立即预览（不走 450ms 延时）。"""
        self._close_timer.stop()
        self._open_timer.stop()
        self._pending = (record, ui_kind, anchor)
        self._do_open()

    def schedule_close(self) -> None:
        self._open_timer.stop()
        self._pending = None
        self._close_timer.start()

    def notify_bubble_hover(self, hovered: bool) -> None:
        self._bubble_hovered = hovered
        if hovered:
            self._close_timer.stop()

    def hide_now(self) -> None:
        """滚动 / 换页 / 换行：立即关，不等延时。"""
        self._open_timer.stop()
        self._close_timer.stop()
        self._pending = None
        self._do_close()

    def is_open(self) -> bool:
        return self.bubble.isVisible()

    def current_id(self) -> str | None:
        return self._current_id

    # ---- 内部 ----
    def _do_open(self) -> None:
        if self._pending is None:
            return
        record, ui_kind, anchor = self._pending
        self._pending = None
        if anchor is None:
            return
        try:
            anchor.objectName()          # 控件被 deleteLater 后这里会抛 RuntimeError
        except RuntimeError:
            return
        self.bubble.show_record(record, ui_kind)
        self._position(anchor)
        self._current_id = record.id
        self.bubble.show()
        self._animate_in()

    def _do_close(self) -> None:
        if self._bubble_hovered and self.bubble.isVisible():
            return                      # 鼠标还在气泡上：不关
        if self.bubble.isVisible():
            self.bubble.hide()
        self._current_id = None

    def _position(self, anchor: QWidget) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        thumb_tl = anchor.mapToGlobal(anchor.rect().topLeft())
        thumb_r = QRect(thumb_tl, anchor.size())
        size = self.bubble.size()
        w, h = size.width(), size.height()
        margin = 14

        # 优先左侧，箭头朝右（不遮挡列表文字）；放不下翻右侧
        if thumb_r.left() - w - margin >= screen.left() + 12:
            left = thumb_r.left() - w - margin
            arrow_right = True
        else:
            left = thumb_r.right() + margin
            arrow_right = False
        top = thumb_r.center().y() - 24
        if top + h > screen.bottom() - 12:
            top = screen.bottom() - h - 12
        if top < screen.top() + 12:
            top = screen.top() + 12
        self.bubble.setGeometry(left, top, w, h)
        # 箭头对准缩略图中线
        self.bubble.set_arrow(arrow_right, thumb_r.center().y() - top)

    def _animate_in(self) -> None:
        bubble = self.bubble
        final = bubble.geometry()
        origin_x = final.right() if bubble.arrow_right else final.left()
        start = QRect(
            int(origin_x + (final.left() - origin_x) * 0.94),
            int(final.center().y() + (final.top() - final.center().y()) * 0.94),
            max(1, int(final.width() * 0.94)),
            max(1, int(final.height() * 0.94)),
        )
        bubble.setGeometry(start)
        if self._anim is not None:
            self._anim.stop()
        anim = QPropertyAnimation(bubble, b"geometry", bubble)
        anim.setDuration(320)
        anim.setEasingCurve(QEasingCurve.OutBack)   # 过冲 ≈ spring 的 1.015 帧
        anim.setStartValue(start)
        anim.setEndValue(final)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._anim = anim

        if self._fade is not None:
            self._fade.stop()
        fade = QPropertyAnimation(bubble, b"windowOpacity", bubble)
        fade.setDuration(180)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.start(QAbstractAnimation.DeleteWhenStopped)
        self._fade = fade
