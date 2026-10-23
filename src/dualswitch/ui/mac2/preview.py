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

import os
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
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from .widgets import ElidedLabel, IconButton, destroy

STATE_TEXT = {"ok": "已同步", "pend": "传输中", "fail": "失败", "local": "仅本机"}
STATE_STYLE = {"ok": "statOk", "pend": "statRun", "fail": "statBad", "local": "statIdle"}
ARROW_MARGIN = 12


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.1f} MB"


def display_size(record) -> int:  # noqa: ANN001
    """列表/预览上显示的大小：文件用它自己的字节数，其它用入库字节数。"""
    return int(getattr(record, "file_size", 0) or 0) or int(getattr(record, "size", 0) or 0)


def clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def short_dir(path: str) -> str:
    """文件所在目录（家目录缩成 `~`，跟访达/终端一致）。

    分隔符一律按 `/` 显示：mac 上本来就是 `/`，Windows 开发机上跑用例时
    也不该冒出反斜杠形式的目录（设计稿写的是 `~/Downloads`）。
    """
    if not path:
        return ""
    folder = (os.path.dirname(path) or "/").replace(os.sep, "/")
    home = os.path.expanduser("~").replace(os.sep, "/")
    if home and (folder == home or folder.startswith(home + "/")):
        folder = "~" + folder[len(home):]
    return folder


def file_name(record) -> str:  # noqa: ANN001
    """文件名：优先 `text`（后端 `make_file` 就是把 basename 写进 text），
    `text` 是整条路径时再退回 basename(path)。"""
    text = (record.text or "").strip()
    path = getattr(record, "path", "") or ""
    if text and os.sep not in text and "/" not in text:
        return text
    if path:
        return os.path.basename(path) or path
    return text


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
    """来源系统名：本机 = 本机平台（Mac），对端 = 另一台（Windows）。

    用户明确要「列表来源 Mac · 来源应用」，所以这里给平台名而不是「本机/对端」。
    """
    return "Mac" if source_system(record) == "apple" else "Windows"


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


def footer_line(record, ui_kind: str) -> str:  # noqa: ANN001
    """预览窗底栏：文件卡照 Frset 显示「大小 · 时刻」，其它沿用元信息行。"""
    if ui_kind == "file":
        return f"{human_size(display_size(record))} · {clock(record.ts)}"
    return meta_line(record, ui_kind)


def meta_line(record, ui_kind: str) -> str:
    """`来源 · 时间 · 大小[ · 尺寸] · 类型`（ClipRecord 无应用名字段，应用段省略）。"""
    kind_name = {
        "image": "图片", "link": "链接", "code": "代码", "text": "纯文本", "file": "文件",
    }.get(ui_kind, ui_kind)
    parts = [source_label(record)]
    app = (getattr(record, "app", "") or "").strip()
    if app:
        parts.append(app)
    if ui_kind == "file":
        # 文件行给「目录」比给「大小 + 类型」有用得多（Frset 的做法）
        parts += [ago(record.ts), short_dir(getattr(record, "path", ""))]
        return " · ".join(parts)
    parts += [ago(record.ts), human_size(display_size(record))]
    if ui_kind == "image" and record.width and record.height:
        parts.append(f"{record.width}×{record.height}")
    parts.append(kind_name)
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# 指向箭头
# --------------------------------------------------------------------------- #
class PopoverArrow(QWidget):
    """气泡指向缩略图的三角箭（设计稿 `.preview .arrow`）。

    **必须做成子控件**：父级的 paintEvent 先画、子控件后画，箭头画在父级里会被
    圆角卡片整个盖掉（第一版「箭头不见了」就是这个原因）。三角底边压在卡片边上，
    填充卡片色，外观上跟气泡连成一体。
    """

    W, H = 12, 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._point_right = True

    def set_direction(self, point_right: bool) -> None:
        self._point_right = bool(point_right)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        mid = self.H / 2
        if self._point_right:
            path.moveTo(self.W, mid)
            path.lineTo(0, 1.5)
            path.lineTo(0, self.H - 1.5)
        else:
            path.moveTo(0, mid)
            path.lineTo(self.W, 1.5)
            path.lineTo(self.W, self.H - 1.5)
        path.closeSubpath()
        p.fillPath(path, theme.qcolor(t.card))
        # 描边色别用 line_soft：白气泡配浅描边等于没有边，用户会以为「箭头丢了」
        edge = theme.qcolor(t.line if t.name == "light" else "#38404C")
        p.setPen(edge)
        p.drawPath(path)
        # 底边那一笔会被卡片压住，再补一层卡片色把缝隙填掉
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(t.card))
        if self._point_right:
            p.drawRect(0, 1, 2, self.H - 2)
        else:
            p.drawRect(self.W - 2, 1, 2, self.H - 2)
        p.end()


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
        self._btn_find = IconButton("finder", "打开文件（在访达中显示）", "pv")
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

        self._arrow = PopoverArrow(self)
        # 文本正文（滚动容器）引用；换记录时 clear_body 会把它们销毁
        self._text_label: QLabel | None = None
        self._text_note: QLabel | None = None
        self._text_scroll: QScrollArea | None = None
        self._text_holder: QWidget | None = None
        self._text_pad = 0

    # ------------------------------------------------------------------ 几何
    def set_arrow(self, right: bool, y: int) -> None:
        self._arrow_right = right
        self._arrow_y = y
        self._arrow.set_direction(right)
        self._place_arrow()

    @property
    def arrow_right(self) -> bool:
        return self._arrow_right

    def sync_size(self) -> None:
        """按内容自适应 + 分类上限（§3.4）。

        高度 = 头 + 正文 + 底栏，三块**都钉死**：
          · 头 / 底栏用 sizeHint 固定 —— 底栏被压就会裁掉按钮（表现为按钮贴边框）；
          · 正文逐个孩子量高（QLabel 的 sizeHint 对换行、带 pixmap 的都不准），
            加起来再算，最后把正文容器也固定高度，绝不允许它溢出到页脚上。
        """
        kind = getattr(self, "_ui_kind", "text")
        max_w, max_h = self.SIZE_CAPS.get(kind, self.SIZE_CAPS["text"])
        # 内容多长就长多高，但不超过屏幕（超了正文内部滚动，不裁字）
        max_h = min(max_h, self._screen_cap())
        w = self._natural_width(max_w)
        inner_w = max(120, w - ARROW_MARGIN)
        self._inner.setFixedWidth(inner_w)

        head_h = self._head.sizeHint().height()
        foot_h = self._foot.sizeHint().height()
        self._head.setFixedHeight(head_h)
        self._foot.setFixedHeight(foot_h)

        text_w = max(80, inner_w - 28)
        # 正文可用高度：上限 - 头 - 底栏 - body 上下内边距（14+14）
        avail_h = max(40, max_h - head_h - foot_h - 28)
        heights: list[int] = []
        for i in range(self._body_lay.count()):
            child = self._body_lay.itemAt(i).widget()
            if child is None:            # stretch
                continue
            if child is self._text_scroll:
                height = self._fit_text_area(text_w, avail_h)
            else:
                height = self._measure_body_child(child, text_w)
            child.setFixedHeight(height)
            heights.append(height)

        spacing = self._body_lay.spacing()
        body_h = 28 + sum(heights) + spacing * max(0, len(heights) - 1)
        budget = max_h - head_h - foot_h
        if body_h > budget:
            # 只压正文：从最后一个文本块开始收，留 60px 下限
            excess = body_h - budget
            for child in reversed(self._body_lay.findChildren(QLabel)):
                if excess <= 0:
                    break
                shrink = min(excess, max(0, child.height() - 40))
                if shrink:
                    child.setFixedHeight(child.height() - shrink)
                    excess -= shrink
            body_h = budget
        self._body_wrap.setFixedHeight(max(56, body_h))

        h = head_h + max(56, body_h) + foot_h
        w, h = self._clamp_popover(kind, w, h, max_w, max_h)
        self.resize(w, h)
        self._apply_inner_geometry()

    @staticmethod
    def _clamp_popover(kind: str, w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
        """窗口尺寸 = clamp(内容 + chrome, 下限, 上限)（Frset 模型，2026-10-23 实测）。

        上限已经在 `SIZE_CAPS` 里按内容类型定好，这里只补**下限** ——
        而且只给图片补：小截图不放大（糊），但气泡也不能塌成一条，
        给它一个最小窗口、图居中留白，看起来仍然是个正经的预览窗。
        文本/代码/文件没有下限，它们本来就该贴着内容走。
        """
        if kind == "image":
            w = max(w, min(theme.PV_IMG_MIN_W, max_w))
            h = max(h, min(theme.PV_IMG_MIN_H, max_h))
        return min(w, max_w), min(h, max_h)

    @staticmethod
    def _screen_cap() -> int:
        """预览窗最高能长到多少：屏幕可用高度减去一点边距（不超出屏幕）。"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is None:
            return 720
        return max(240, int(screen.availableGeometry().height()) - 120)

    def _label_height(self, label: QLabel, width: int) -> int:
        """`label` 在给定宽度下按词换行后的真实高度（sizeHint 不含换行高度）。"""
        fm = QFontMetrics(label.font())
        flags = int(Qt.TextWordWrap | Qt.TextExpandTabs)
        return max(
            fm.height(),
            fm.boundingRect(QRect(0, 0, max(20, width), 1_000_000), flags, label.text()).height(),
        )

    def _fit_text_area(self, text_w: int, avail_h: int) -> int:
        """文本正文：**完整显示**，只在超过屏幕时让正文内部滚动。

        用户明确要求预览不截断文字（2026-10-21）：
          · 内容不高 → 预览窗就长那么高，一个滚动条都不出现；
          · 内容高过屏幕 → 预览窗到屏幕高度，正文可滚（内容一个不少）；
          · 只有超过 `PV_TEXT_MAX_CHARS` 的极端文本才切掉，避免排版把机器拖卡。
        """
        label = self._text_label
        scroll = self._text_scroll
        if label is None or scroll is None:
            return min(avail_h, 80)

        sbw = scroll.verticalScrollBar().sizeHint().width() or 14
        pad = int(getattr(self, "_text_pad", 0) or 0)
        label.setFixedWidth(text_w)
        need = self._label_height(label, text_w) + pad
        note_h = 0
        if self._text_note is not None:
            note_h = self._text_note.sizeHint().height() + 6

        if need + note_h <= avail_h:
            label.setFixedHeight(need)
            scroll.setFixedWidth(text_w)
            scroll.setFixedHeight(need + note_h)
            return need + note_h

        # 要滚动：给滚动条让出宽度，重新量一次字号尺寸
        inner_w = max(60, text_w - sbw)
        label.setFixedWidth(inner_w)
        need = self._label_height(label, inner_w) + pad
        label.setFixedHeight(need)
        scroll.setFixedWidth(text_w)
        scroll.setFixedHeight(avail_h)
        return avail_h

    @staticmethod
    def _measure_body_child(child, text_w: int) -> int:  # noqa: ANN001
        """正文单个孩子的高度：pixmap 按图高，文本按实际换行量。"""
        if isinstance(child, QLabel):
            pix = child.pixmap()
            if pix is not None and not pix.isNull():
                return pix.height() + 2
            fm = QFontMetrics(child.font())
            if child.wordWrap():
                flags = Qt.TextWordWrap | Qt.TextExpandTabs
                need = fm.boundingRect(
                    QRect(0, 0, text_w, 20000), int(flags), child.text()
                ).height()
            else:
                need = fm.height()
            if child.objectName() == "pvCode":
                return need + 26        # 代码块的 11px 内边距 + 描边
            return need + 4
        return max(18, child.sizeHint().height())

    def _natural_width(self, max_w: int) -> int:
        kind = getattr(self, "_ui_kind", "text")
        pad = 28 + ARROW_MARGIN
        if kind == "image":
            pix = getattr(self, "_image_w", 0)
            return min(max_w, max(200, pix + pad)) if pix else min(max_w, 240)
        if kind == "file":
            # 文件卡是固定宽度，气泡必须跟着它（否则卡片被裁掉一截）
            return min(max_w, self.FILE_CARD_W + pad)
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
        self._place_arrow()

    def _place_arrow(self) -> None:
        """三角底边贴住卡片边缘，垂直方向对准缩略图中线（自动夹紧）。"""
        arrow = getattr(self, "_arrow", None)
        if arrow is None:
            return
        inner_w = self.width() - ARROW_MARGIN
        x = inner_w - 1 if self._arrow_right else ARROW_MARGIN - arrow.W + 1
        y = int(self._arrow_y - arrow.H / 2)
        y = max(10, min(self.height() - arrow.H - 10, y))
        arrow.move(x, y)
        arrow.raise_()
        arrow.show()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_inner_geometry()

    def enterEvent(self, event):  # noqa: N802
        # 预览窗自身 hover 可停留（__isPreviewPopoverHovered）
        ctrl = getattr(self.window(), "_preview_controller", None)
        if ctrl is not None:
            ctrl.notify_bubble_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        # 鼠标离开气泡就该收起来（用户 2026-10-21）：原来是「离开缩略图才关」，
        # 从缩略图直接移到窗外/别的应用时气泡会挂着不走。留 220ms 缓冲给「移回缩略图」。
        ctrl = getattr(self.window(), "_preview_controller", None)
        if ctrl is not None:
            ctrl.notify_bubble_hover(False)
            ctrl.schedule_close()
        super().leaveEvent(event)

    # ------------------------------------------------------------------ 填充
    def clear_body(self) -> None:
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            destroy(item.widget())
        self._text_label = None
        self._text_note = None
        self._text_scroll = None
        self._text_holder = None
        self._text_pad = 0

    def show_record(self, record, ui_kind: str) -> None:  # noqa: ANN001
        self._record_id = record.id
        self._ui_kind = ui_kind
        glyph = {
            "image": "image", "file": icons.file_glyph(getattr(record, "path", "") or ""),
            "link": "link-2", "code": "code", "text": "type",
        }.get(ui_kind, "type")
        # 文件卡的瓷砖底色是最浅的一档灰，拿来画字形会看不见 → 单独用 ink2
        tint = (
            theme.tokens().ink2 if ui_kind == "file" else theme.type_tile(ui_kind)[0]
        )
        self._head_icon.setPixmap(icons.svg_pixmap(glyph, 18, tint, 2.0))
        if ui_kind == "file":
            title = "文件"                       # 文件名放卡片里（照 Frset 的文件卡）
        elif ui_kind == "image" and record.kind in ("image", "mixed"):
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
        self._body_lay.addStretch(1)      # 正文顶部对齐，别让 QLabel 被拉高

        self._meta.setText(footer_line(record, ui_kind))
        # 按钮规则（用户 2026-10-21）：
        #   截图 → 只有预览；图片文件 → 预览 + 打开文件；普通文件 → 只有打开文件
        self._btn_preview.setVisible(
            ui_kind == "image" or (ui_kind == "file" and record.png is not None)
        )
        self._btn_find.setVisible(ui_kind == "file")
        self.sync_size()

    #: 分类上限（INTERACTIONS §3.4），值是**窗口**尺寸，不是内容尺寸
    SIZE_CAPS = {
        "image": (theme.PV_IMG_MAX_W, theme.PV_IMG_MAX_H),
        # 文本/代码的高度上限交给屏幕：内容多长就长多高，长到屏幕放不下时内部滚动
        "text": (theme.PV_TXT_MAX_W, 100_000),
        "code": (theme.PV_CODE_MAX_W, 100_000),
        "file": (theme.PV_CARD_MAX_W, 320),
        "link": (theme.PV_CARD_MAX_W, 320),
    }

    #: 窗口上限里不能给图片的部分：头部 40 + 底栏 49 + 正文上下内边距 28
    #: （注脚行已删，见 _build_image；它跟标题/底栏重复，白吃 32px 高度）
    IMG_CHROME_H = 40 + 49 + 28

    @classmethod
    def _image_area(cls) -> tuple[int, int]:
        """图片最大可渲染区域 = 窗口上限 − chrome（与 sync_size 用的是同一组数）。"""
        return (
            theme.PV_IMG_MAX_W - 28 - ARROW_MARGIN,
            theme.PV_IMG_MAX_H - cls.IMG_CHROME_H,
        )

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
            # 只缩不放：QPixmap.scaled 的 KeepAspectRatio 会**放大**小图，
            # 100px 的截图被拉到 300px 只会糊（显示宽度同时决定气泡宽度）。
            avail_w, avail_h = self._image_area()
            factor = min(1.0, avail_w / max(1, pix.width()), avail_h / max(1, pix.height()))
            disp_w = max(1, int(pix.width() * factor))
            disp_h = max(1, int(pix.height() * factor))
            # Retina：位图要按 devicePixelRatio 渲染**真实像素**，再用
            # setDevicePixelRatio 折算回逻辑尺寸。否则 521 逻辑像素的位图在 2x 屏上
            # 会被插值放大 2 倍，截图里的文字直接糊掉（2026-10-23 用户实测反馈）。
            dpr = float(self.devicePixelRatioF() or 1.0)
            scaled = pix.scaled(
                max(1, int(disp_w * dpr)),
                max(1, int(disp_h * dpr)),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            pix_label.setPixmap(scaled)
            self._image_w = disp_w       # 逻辑宽度 —— 决定气泡宽，不能用 scaled.width()（那是物理像素）
        self._body_lay.addWidget(pix_label, 0, Qt.AlignHCenter)
        # 尺寸/大小不在正文重复：标题栏已有「图片 1560×1216」，底栏也有「… · 2.3MB · 1560×1216」。
        # 正文里再摆一行注脚会白白吃掉 32px 高度 —— 图片区跟着矮一截（Frset 是 524，我们原来只有 477）。

    def _build_text(self, record) -> None:  # noqa: ANN001
        self._build_scroll_body(record.text or "", "pvText")

    def _build_code(self, record) -> None:  # noqa: ANN001
        self._build_scroll_body(record.text or "", "pvCode")

    def _build_scroll_body(self, text: str, object_name: str) -> None:
        """文本正文：**完整渲染**，长到超过屏幕才内部滚动（用户 2026-10-21）。

        只有超过 `PV_TEXT_MAX_CHARS` 的极端文本才切一部分 —— 那是为了保护排版性能，
        不是视觉裁切，而且会在正文底下写清楚「只渲染了前 N 字」。
        """
        cap = int(getattr(theme, "PV_TEXT_MAX_CHARS", 20000))
        shown = text[:cap]
        self._body_text = shown          # 测宽 / 量高都用这一段

        label = QLabel(shown)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if object_name == "pvCode":
            label.setContentsMargins(11, 11, 11, 11)
            self._text_pad = 22

        holder = QWidget()
        holder.setObjectName("pvTextBox")
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(label)
        col.addStretch(1)

        note = None
        if len(text) > cap:
            note = QLabel(f"文本很长，只渲染了前 {cap} 字（共 {len(text)} 字）；复制仍是全文")
            note.setObjectName("pvNote")
            note.setWordWrap(True)
            col.addWidget(note)

        scroll = QScrollArea()
        scroll.setObjectName("pvScroll")
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")

        self._text_label = label
        self._text_holder = holder
        self._text_note = note
        self._text_scroll = scroll
        self._body_lay.addWidget(scroll)

    def _build_link(self, record) -> None:  # noqa: ANN001
        self._body_text = record.text or ""
        block = QLabel(self._body_text)
        block.setObjectName("pvLink")
        block.setWordWrap(True)
        block.setContentsMargins(11, 11, 11, 11)

        self._body_lay.addWidget(block)

    #: 文件卡正文宽度（预览窗内宽固定，文件名按这个宽度量换行）
    FILE_CARD_W = theme.PV_CARD_MAX_W - 28 - ARROW_MARGIN

    def _build_file(self, record) -> None:  # noqa: ANN001
        """文件卡（照 Frset）：文档图标 + 扩展名、文件名、所在目录，全部居中。

        图片文件也是文件卡（缩略图里已经能看到图了，卡片只回答「这是哪个文件」）。
        """
        self._body_text = file_name(record)
        self._image_w = 0
        card = QFrame()
        card.setObjectName("pvFileCard")
        card.setFixedWidth(self.FILE_CARD_W)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 20, 16, 18)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        icon = QLabel()
        icon.setObjectName("pvFileIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(icons.file_pixmap(file_name(record), 52))
        lay.addWidget(icon, 0, Qt.AlignHCenter)
        lay.addSpacing(9)

        name_w = self.FILE_CARD_W - 32
        name = QLabel(file_name(record))
        name.setObjectName("pvFileName")
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        name.setFixedWidth(name_w)
        # QLabel 的 sizeHint 不含换行后的高度 → 自己按宽度量，最多两行
        fm = QFontMetrics(name.font())
        flags = Qt.TextWordWrap | int(Qt.AlignHCenter)
        need = fm.boundingRect(QRect(0, 0, name_w, 2000), int(flags), name.text()).height()
        lines = max(1, min(2, round(need / max(1, fm.height()))))
        name.setFixedHeight(fm.height() * lines + 4)
        lay.addWidget(name, 0, Qt.AlignHCenter)
        lay.addSpacing(3)

        folder = QLabel(short_dir(getattr(record, "path", "")))
        folder.setObjectName("pvFileDir")
        folder.setAlignment(Qt.AlignCenter)
        folder.setFixedHeight(15)
        lay.addWidget(folder, 0, Qt.AlignHCenter)

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
        # 翻到右侧时可能顶出屏幕右缘（窗口上限放宽到 562 后更容易），夹回来
        left_min = screen.left() + 12
        left_max = screen.right() - w - 12
        left = left_min if left_max < left_min else min(max(left, left_min), left_max)
        self.bubble.setGeometry(left, top, w, h)
        # 箭头对准缩略图中线
        self.bubble.set_arrow(arrow_right, thumb_r.center().y() - top)

    def _ensure_anims(self) -> None:
        """入场动画对象常驻复用。

        **不要用 `DeleteWhenStopped`**：跑完一次后 C++ 对象就被回收，下一次再
        `stop()` 会抛 `RuntimeError: Internal C++ object already deleted`，
        表现是「第二次悬停不弹预览」（2026-10-21 实测）。
        """
        if self._anim is None:
            self._anim = QPropertyAnimation(self.bubble, b"geometry", self)
            self._anim.setDuration(300)
            self._anim.setEasingCurve(QEasingCurve.OutCubic)
        if self._fade is None:
            self._fade = QPropertyAnimation(self.bubble, b"windowOpacity", self)
            self._fade.setDuration(180)
            self._fade.setEasingCurve(QEasingCurve.OutCubic)

    def _animate_in(self) -> None:
        """入场：淡入 + 轻微「弹一下」（1.5% 过冲），**绝不从缩小开始**。

        气泡内部是按最终宽度钉死宽度的；如果窗口从 94% 长起来，内容会先溢出、
        右边被裁一刀（就是「按钮贴边框」的真相）。所以只向上过冲、不回缩到更小。
        """
        bubble = self.bubble
        final = bubble.geometry()
        grow_w = max(2, int(final.width() * 0.015))
        grow_h = max(2, int(final.height() * 0.012))
        overshoot = QRect(
            final.x() - grow_w,
            final.y() - grow_h,
            final.width() + grow_w * 2,
            final.height() + grow_h * 2,
        )
        self._ensure_anims()
        anim = self._anim
        anim.stop()
        anim.setKeyValueAt(0.0, final)
        anim.setKeyValueAt(0.35, overshoot)
        anim.setKeyValueAt(1.0, final)
        anim.start()

        fade = self._fade
        fade.stop()
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.start()

