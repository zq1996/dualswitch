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
    QObject,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
    QTextDocument,
)
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

#: 入场缩放：**Frset 1.3.16 逐帧实测**（CGWindowList 4ms 轮询它弹出的气泡窗口，
#: 562×399 的图片气泡；归一化缩放 = 实测宽度 / 最终宽度）。
#:
#: 实测结论：
#:   · 从 **3×2 的点**长出来（锚点就是箭头尖），不是原地抖一下
#:   · 255ms 到达峰值 572×407（**只过冲 1.8%**），470ms 回到 562×399
#:   · 透明度同步上升，207ms 就到 1.0（比尺寸早收）
#:   · 阻尼弹簧拟合：ω=24.85 rad/s（response≈253ms）、dampingFraction≈0.89
#:
#: 直接用实测点当关键帧（而不是套 spring 解析式）：尾部 240→473ms 那段
#: 从 1.018 缓慢回落，比单自由度弹簧收敛得慢，套公式会差 1.5%。
ZOOM_SAMPLES_MS: tuple[tuple[float, float], ...] = (
    (0, 0.000), (11, 0.071), (23, 0.187), (44, 0.324), (61, 0.459),
    (73, 0.585), (90, 0.694), (107, 0.783), (124, 0.854), (140, 0.909),
    (160, 0.948), (175, 0.977), (191, 0.995), (207, 1.009), (223, 1.014),
    (240, 1.018), (256, 1.018), (274, 1.016), (291, 1.014), (307, 1.012),
    (323, 1.011), (343, 1.009), (362, 1.007), (375, 1.004), (408, 1.002),
    (473, 1.000),
)
ZOOM_DURATION_MS = 473
ZOOM_KEYS: tuple[tuple[float, float], ...] = tuple(
    (ms / ZOOM_DURATION_MS, scale) for ms, scale in ZOOM_SAMPLES_MS
)
ZOOM_FADE_MS = 200
#: 透明度曲线（按 ZOOM_FADE_MS 归一）：207ms 到满，前期比 OutCubic 缓（实测 alpha）
FADE_KEYS: tuple[tuple[float, float], ...] = (
    (0.00, 0.0), (0.30, 0.46), (0.60, 0.90), (1.00, 1.0),
)
#: 动画步进：Qt 的动画驱动本身也是这个量级，16ms ≈ 60Hz
ANIM_TICK_MS = 16
#: 正文字体行距（Frset 1.3.16 实测约 1.7 倍：15px 字，行高约 26px）。
#: QSS 的 line-height 对 QLabel 无效，只能走富文本，见 `_rich_text`。
LINE_HEIGHT_PCT = 170


def _rich_text(text: str) -> str:
    """纯文本 → 带行距的富文本。转义 & < >，换行换成 <br>。

    只为了让 QLabel 认 line-height；文本内容本身一个字都不改。
    """
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    esc = esc.replace("\n", "<br>")
    return f'<div style="line-height:{LINE_HEIGHT_PCT}%">{esc}</div>'


def sample_keys(keys: tuple[tuple[float, float], ...], progress: float) -> float:
    """在 (进度, 值) 关键帧上线性取值（段内线性，端点夹住）。"""
    if progress <= keys[0][0]:
        return keys[0][1]
    for (p0, v0), (p1, v1) in zip(keys, keys[1:]):
        if progress <= p1:
            span = p1 - p0
            return v1 if span <= 0 else v0 + (v1 - v0) * (progress - p0) / span
    return keys[-1][1]


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

    #: 逐像素量出来的（Frset 1.3.16 文本气泡右缘，物理像素 ÷2）：
    #: 尖端凸出卡片 **11**，三角高 **30**，直边（斜率恒 0.78，不是圆弧）。
    #: 旧值 12×18 又扁又矮，看着「尖得不对」就是这个。
    W, H = 11, 30

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
# 入场用的缩放画布
# --------------------------------------------------------------------------- #
class ZoomLayer(QWidget):
    """把气泡**拍成位图**后整体缩放绘制，动画结束再交回真实控件。

    为什么不像 Frset 那样直接改窗口尺寸：气泡内部每一块（正文宽度、按钮、
    换行）都是按**最终宽度**钉死的。窗口一旦从小往大长，Qt 会按小尺寸重新布局，
    正文先挤成两行、按钮被压出边框——就是 10-21 那次「按钮贴边框」的真相。
    Frset 是 SwiftUI，布局是矢量重排所以没这个问题；Qt 只能改用位图缩放：
    布局一次算到底，动画期间只是这张图在放大，观感等价而布局不动。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pix = QPixmap()
        self._anchor = QPoint(0, 0)
        self._factor = 1.0
        #: 淡入做在层内（画的时候 setOpacity），**不动窗口 windowOpacity** ——
        #: 顶层窗口设成 0 透明后销毁时序会乱（offscreen 下实测报
        #: `QObject: shared QObject was deleted directly`），而且 offscreen 平台本来就不支持。
        self._alpha = 1.0
        self.hide()

    def set_frame(self, pix: QPixmap, anchor: QPoint) -> None:
        self._pix = pix
        self._anchor = anchor
        self.update()

    def set_factor(self, factor: float) -> None:
        self._factor = factor
        self.update()

    def set_alpha(self, alpha: float) -> None:
        self._alpha = max(0.0, min(1.0, alpha))
        self.update()

    def paintEvent(self, event):  # noqa: N802
        if self._pix.isNull():
            return
        dpr = self._pix.devicePixelRatio() or 1.0
        # grab() 给的是物理像素，这里换算回逻辑尺寸再乘缩放
        w = self._pix.width() / dpr * self._factor
        h = self._pix.height() / dpr * self._factor
        # 锚点（箭头尖）在动画里不许移动：以它为不动点向左上/右展开
        x = self._anchor.x() - self._anchor.x() * self._factor
        y = self._anchor.y() - self._anchor.y() * self._factor
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(self._alpha)
        p.drawPixmap(QRectF(x, y, w, h), self._pix, QRectF(self._pix.rect()))
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
        self._inner_lay = inner_lay

        # ---- 头部 ----
        head = QFrame()
        head.setObjectName("pvHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 10, 12, 8)
        hl.setSpacing(6)
        self._head_title = ElidedLabel("", "pvTitle")
        self._pill_box = QHBoxLayout()
        self._pill_box.setContentsMargins(0, 0, 0, 0)
        # Frset 实测：标题只有类型名，右侧是**灰底上的线性小图标**（打开/复制），
        # 没有类型图标、没有彩色主按钮。图标放这里，底栏只留信息。
        self._btn_preview = IconButton("preview", "用图片预览打开", "pv")
        self._btn_find = IconButton("finder", "打开文件（在访达中显示）", "pv")
        self._btn_copy = IconButton("copy", "复制", "pv")
        hl.addWidget(self._head_title, 1)
        hl.addLayout(self._pill_box, 0)
        for b in (self._btn_preview, self._btn_find, self._btn_copy):
            hl.addWidget(b, 0, Qt.AlignVCenter)
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
        self._btn_preview.setVisible(False)
        self._btn_find.setVisible(False)
        self._btn_preview.clicked.connect(lambda: self.openRequested.emit(self._record_id))
        self._btn_find.clicked.connect(lambda: self.findRequested.emit(self._record_id))
        self._btn_copy.clicked.connect(lambda: self.copyRequested.emit(self._record_id))
        self._foot = foot
        inner_lay.addWidget(foot)

        self._arrow = PopoverArrow(self)
        # 入场缩放层：盖住整块（含箭头留白），动画期间只画这张位图
        self._zoom = ZoomLayer(self)
        self._zoom.setGeometry(self.rect())
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

        # 图片类铺满：正文没有 14×2 的内边距
        body_pad = 0 if kind == "image" else 28
        text_w = max(80, inner_w - body_pad)
        avail_h = max(40, max_h - head_h - foot_h - body_pad)
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
        body_h = body_pad + sum(heights) + spacing * max(0, len(heights) - 1)
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
        h = head_h + max(56, body_h) + foot_h
        # Frset 实测：文本/代码/链接即使只有一行，气泡也是 246 高
        # （346×246、586×246、556×246 三组实测完全一致）；图片按比例走，不受此限。
        if kind != "image":
            h = max(h, self.MIN_H)
        w, h = self._clamp_popover(kind, w, h, max_w, max_h)
        # 白卡撑满：最小高度抬出来的那块给正文，别在气泡底部留一片灰
        self._body_wrap.setFixedHeight(max(56, h - head_h - foot_h))
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
        if label.textFormat() == Qt.RichText:
            doc = QTextDocument()
            doc.setDefaultFont(label.font())
            doc.setDocumentMargin(0)
            doc.setHtml(label.text())
            doc.setTextWidth(max(20, width))
            return int(doc.size().height())
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
                # pixmap 尺寸是**物理像素**。Retina 下必须除以 devicePixelRatio 才是
                # 逻辑高度（QLabel.sizeHint() 在这点上同样给物理高度），
                # 否则 301 的图会被当成 602 算，窗口高度直接翻倍、图被挤成一条缝。
                dpr = float(pix.devicePixelRatio() or 1.0)
                return int(round(pix.height() / dpr)) + 2
            fm = QFontMetrics(child.font())
            if child.wordWrap():
                flags = Qt.TextWordWrap | Qt.TextExpandTabs
                need = fm.boundingRect(
                    QRect(0, 0, text_w, 20000), int(flags), child.text()
                ).height()
            else:
                need = fm.height()
            # 带内边距的正文块（代码 11px、链接 11px）必须把上下内边距算进去，
            # 否则文字最后一行会被正文容器裁掉半截（2026-11-08 用户截图实证）。
            m = child.contentsMargins()
            return need + m.top() + m.bottom() + 4
        return max(18, child.sizeHint().height())

    def _natural_width(self, max_w: int) -> int:
        kind = getattr(self, "_ui_kind", "text")
        pad = 28 + ARROW_MARGIN
        if kind == "image":
            # 铺满：气泡宽就是图片宽（Frset 实测 562 宽的图 → 562 宽的气泡）
            pix = getattr(self, "_image_w", 0)
            if not pix:
                return min(max_w, theme.PV_IMG_MIN_W)
            return min(max_w, max(theme.PV_IMG_MIN_W, pix))
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
        self._zoom.setGeometry(self.rect())

    # ------------------------------------------------------------- 入场缩放
    @property
    def anchor_point(self) -> QPoint:
        """箭头尖的位置 —— 缩放动画的不动点（气泡从这里长出去）。"""
        x = self.width() - 1 if self._arrow_right else 0
        return QPoint(x, self._arrow_y)

    def zoom_prepare(self) -> None:
        """show 之前：先盖上一层 factor=0 的缩放层，窗口的第一帧什么都不画。

        这样就不必把窗口设成透明来「藏」那一下——不用 `setWindowOpacity`。
        """
        self._zoom.setGeometry(self.rect())
        self._zoom.set_factor(0.0)
        self._zoom.set_alpha(0.0)
        self._inner.hide()
        self._arrow.hide()
        self._zoom.show()
        self._zoom.raise_()

    def zoom_capture(self) -> None:
        """show 之后（布局与 QSS 已落定）：把卡片拍成位图，交接给缩放层。

        `_inner.show()` 到 `_inner.hide()` 之间没有回到事件循环，所以这次
        「临时显示」不会被真正画到屏幕上，不会有闪一帧整卡的问题。
        """
        self._inner.show()
        self._arrow.show()
        pix = self.grab()
        self._inner.hide()
        self._arrow.hide()
        self._zoom.setGeometry(self.rect())
        self._zoom.set_frame(pix, self.anchor_point)
        self._zoom.set_factor(0.0)
        self._zoom.show()
        self._zoom.raise_()

    def set_zoom(self, factor: float) -> None:
        self._zoom.set_factor(factor)

    def set_zoom_alpha(self, alpha: float) -> None:
        self._zoom.set_alpha(alpha)

    def zoom_end(self) -> None:
        """收尾：位图只是动画载体，交回真实控件。"""
        self._zoom.hide()
        self._inner.show()
        self._arrow.show()
        self._arrow.raise_()

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
        # Frset 实测：标题就是**类型名**（文本/代码/链接/图片/文件），不是内容摘要
        title = {
            "image": "图片", "file": "文件", "link": "链接", "code": "代码", "text": "文本",
        }.get(ui_kind, "文本")
        self._head_title.setText(title)
        self._replace_pill(record)
        self.clear_body()

        # Frset 实测结构：图片类的图**直接铺满**（没有白卡、没有左右留白）；
        # 文本/代码/链接/文件是「浅灰底上浮一张白卡」，左右各内缩 12。
        flat = ui_kind == "image"
        self._inner_lay.setContentsMargins(*(0, 0, 0, 0) if flat else (12, 0, 12, 6))
        self._body_lay.setContentsMargins(*(0, 0, 0, 0) if flat else (14, 14, 14, 14))
        name = "pvBodyFlat" if flat else "pvBody"
        if self._body_wrap.objectName() != name:
            self._body_wrap.setObjectName(name)
            self._body_wrap.style().unpolish(self._body_wrap)
            self._body_wrap.style().polish(self._body_wrap)

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
        has_path = bool(getattr(record, "path", "") or "")
        self._btn_preview.setVisible(ui_kind == "image")
        # 图片文件同样保留「在访达中显示」（Frset: 图片多给一个打开入口）
        self._btn_find.setVisible(ui_kind == "file" or (ui_kind == "image" and has_path))
        self.sync_size()

    #: 分类上限（INTERACTIONS §3.4），值是**窗口**尺寸，不是内容尺寸
    SIZE_CAPS = {
        "image": (theme.PV_IMG_MAX_W, theme.PV_IMG_MAX_H),
        # 文本/代码的高度上限交给屏幕：内容多长就长多高，长到屏幕放不下时内部滚动
        "text": (theme.PV_TXT_MAX_W, 100_000),
        "code": (theme.PV_CODE_MAX_W, 100_000),
        # 文件卡带了图片预览，上限要放得下「图 200 + 文件名 + 目录」
        "file": (theme.PV_CARD_MAX_W, 470),
        "link": (theme.PV_CARD_MAX_W, 320),
    }

    #: 非图片气泡的最小高度（Frset 实测：文本/代码/链接都是 246）
    MIN_H = 246

    #: 窗口上限里不能给图片的部分：头部 40 + 底栏 49
    #: 图片类是铺满的，正文不再有 14×2 内边距（Frset 实测 562×522，图正好 562×449）
    IMG_CHROME_H = 40 + 49

    @classmethod
    def _image_area(cls) -> tuple[int, int]:
        """图片最大可渲染区域 = 窗口上限 − chrome（与 sync_size 用的是同一组数）。

        图片类是**铺满**的（Frset 实测：图左侧紧贴气泡边缘），所以宽度不给内边距。
        """
        return (
            theme.PV_IMG_MAX_W - ARROW_MARGIN,
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

        label = QLabel(_rich_text(shown))
        label.setTextFormat(Qt.RichText)
        # 正文是富文本（为了行距），原文留在属性上：测试与「取正文」都读它
        label.setProperty("plainText", shown)
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
    #: 图片文件在文件卡里的预览图高度上限（宽度跟着卡片走）：够看清内容，
    #: 又不至于把卡片撑成第二个「图片预览」。
    FILE_SHOT_H = 200

    def _build_file(self, record) -> None:  # noqa: ANN001
        """文件卡（照 Frset）：**图片文件出真图**，其它文件给文档图标；下面都跟
        文件名与所在目录，全部居中。

        2026-11-08 修：之前一律只画 52px 文档图标，图片文件在预览窗里看不到图 ——
        缩略图有、预览没有，等于白丢了「看内容」这件事（`clipboard/macos.py` 本来
        就为图片文件抽了 png，Windows 端的注释也写着「预览窗能出图」）。
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

        inner_w = self.FILE_CARD_W - 32
        shot = self._file_shot(record, inner_w)
        if shot is not None:
            lay.addWidget(shot, 0, Qt.AlignHCenter)
            lay.addSpacing(12)
        else:
            icon = QLabel()
            icon.setObjectName("pvFileIcon")
            icon.setAlignment(Qt.AlignCenter)
            icon.setPixmap(icons.file_pixmap(file_name(record), 52))
            lay.addWidget(icon, 0, Qt.AlignHCenter)
            lay.addSpacing(9)

        name_w = inner_w
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

    def _file_shot(self, record, avail_w: int) -> QLabel | None:  # noqa: ANN001
        """图片文件的实际预览图（等比缩进卡片宽度，最高 FILE_SHOT_H）。

        Retina 上仍按 devicePixelRatio 渲物理像素再折算，和 `_build_image` 同一套；
        高度必须 `setFixedHeight(逻辑)` —— 否则 QLabel.sizeHint 给物理高度，
        卡片高度会翻倍（`_measure_body_child` 对 QFrame 走的就是 sizeHint）。
        """
        if record.png is None:
            return None
        image = QImage()
        if not image.loadFromData(record.png, "PNG"):
            return None
        pix = QPixmap.fromImage(image)
        if pix.isNull():
            return None
        factor = min(
            1.0,
            avail_w / max(1, pix.width()),
            self.FILE_SHOT_H / max(1, pix.height()),
        )
        disp_w = max(1, int(pix.width() * factor))
        disp_h = max(1, int(pix.height() * factor))
        dpr = float(self.devicePixelRatioF() or 1.0)
        scaled = pix.scaled(
            max(1, int(disp_w * dpr)),
            max(1, int(disp_h * dpr)),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        label = QLabel()
        label.setObjectName("pvFileShot")
        label.setAlignment(Qt.AlignCenter)
        label.setPixmap(scaled)
        label.setFixedHeight(disp_h)
        return label


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
        self._zoom_token = 0
        self._current_id: str | None = None
        self._bubble_hovered = False
        # 等一帧再抓位图。**必须是自己的 QTimer**：`QTimer.singleShot(0, lambda…)`
        # 不随 controller 销毁而取消，测试/退出时那条回调会打到已删除的对象上
        # （`QObject: shared QObject was deleted directly`）。
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.setInterval(0)
        self._start_timer.timeout.connect(self._on_start_timeout)
        # 入场动画用常驻定时器自己算，不用 QPropertyAnimation/QVariantAnimation：
        # 运行中的 QAbstractAnimation 在 controller 被销毁时会留下「shared QObject
        # was deleted directly」的告警（测试进程里必现），而且曲线本来就要按实测
        # 关键帧逐点取值，自己算反而更直白。
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(ANIM_TICK_MS)
        self._anim_timer.timeout.connect(self._anim_step)
        self._anim_t0 = 0.0

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
        # 先盖一层 factor=0 的缩放层再 show：第一帧什么都不画，不会闪一下整卡
        self.bubble.zoom_prepare()
        self.bubble.show()
        self._zoom_token += 1
        self._start_timer.start()

    def _on_start_timeout(self) -> None:
        self._start_zoom(self._zoom_token)

    def _start_zoom(self, token: int) -> None:
        if token != self._zoom_token or not self.bubble.isVisible():
            return
        self.bubble.zoom_capture()
        self._anim_t0 = time.perf_counter()
        self._anim_timer.start()

    def _anim_step(self) -> None:
        elapsed = (time.perf_counter() - self._anim_t0) * 1000.0
        progress = min(1.0, elapsed / ZOOM_DURATION_MS)
        self.bubble.set_zoom(sample_keys(ZOOM_KEYS, progress))
        self.bubble.set_zoom_alpha(sample_keys(FADE_KEYS, min(1.0, elapsed / ZOOM_FADE_MS)))
        if progress >= 1.0:
            self._anim_timer.stop()
            self.bubble.zoom_end()

    def _do_close(self) -> None:
        if self._bubble_hovered and self.bubble.isVisible():
            return                      # 鼠标还在气泡上：不关
        self._zoom_token += 1           # 作废还没跑的那次入场
        self._start_timer.stop()
        self._anim_timer.stop()
        self.bubble.zoom_end()
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


