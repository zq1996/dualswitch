"""剪贴板页（设计 §6，Frset 交互全量移植）。

版面（780 宽内容区）：

    [🔍 搜索剪贴板……        ] [⏸] [🗑]        工具栏（全图标按钮）
    全部(7) 文字(3) 图片(2) 文件(1) 链接(1) 异常(2)   筛选 chips
    ┌────────────────────────────────────────┐
    │ [46px 缩略图+角标] 标题(≤2行)  [状态药丸] │ 行 68px
    │                  元信息行     [复制][打开][×]│ hover 显现
    └────────────────────────────────────────┘

交互红线：
  · 悬停**缩略图** 450ms 才弹预览窗（不是整行）
  · **单击行 = 直接复制**、双击行=预览、右键=更多操作、失败/仅本机药丸=重试
  · 「仅本机」≠「失败」：前者不是错误（灰），后者是错误（红）
  · 双态空态分开：真空「剪贴板为空」/ 筛选「当前筛选下暂无内容」

Windows 差异（设计 §2/§6.4）：
  · 「预览」= 用默认图片查看器打开（`os.startfile`），按钮 tooltip 写清楚
  · 「打开文件」= 在资源管理器中显示（`explorer /select,`）
  · 清空确认**不再弹 QDialog**，改由 runner 画窗口内遮罩（§8）：
    页面只发 `clearConfirmRequested(count)`，确认后由 runner 回调 `clear_confirmed()`

**暂停记录**是真功能（引擎层 `set_paused`）：暂停期间不采集新内容，恢复后也不会
把暂停期间复制的东西补发出去。

**删除**只有两种语义：行内 × = 删这一条；工具栏 🗑 = 清空全部（必须先确认）。
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import apps, icons, openers, theme
from .preview import (
    PreviewController,
    file_name,
    meta_line,
    source_system,
)
from .widgets import (
    Chip,
    IconButton,
    MetaLine,
    TwoLineLabel,
    badge_pixmap,
    destroy,
    safe_prefix,
    type_pixmap,
)

STATE_TEXT = {"ok": "已同步", "pend": "传输中", "fail": "失败", "local": "仅本机"}
STATE_STYLE = {"ok": "statOk", "pend": "statRun", "fail": "statBad", "local": "statIdle"}

CHIP_ORDER = ("all", "text", "image", "file", "link", "bad")
CHIP_LABEL = {
    "all": "全部", "text": "文字", "image": "图片",
    "file": "文件", "link": "链接", "bad": "异常",
}

ACTIONS_W = 109                  # 行尾动作区固定宽（4×25 + 3×3）—— 药丸右边缘对齐


# --------------------------------------------------------------------------- #
# 缩略图（46px + 右下角来源系统角标探出）
# --------------------------------------------------------------------------- #
class Thumb(QFrame):
    """46×46 r10 缩略图；右下角 22×22 来源系统角标（Windows 蓝牌 / Mac 灰牌）。

    控件整体 52×52：角标按设计 §6.3 探出缩略图 3px（right/bottom -3）。
    """

    thumbHoverEntered = Signal()
    thumbHoverLeft = Signal()

    PAD = 3   # 角标探出量

    def __init__(self, record, ui_kind: str, parent=None):  # noqa: ANN001
        super().__init__(parent)
        size = theme.THUMB + self.PAD * 2
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._ui_kind = ui_kind
        self._image: QPixmap | None = None
        self._badge = badge_pixmap(source_system(record), theme.BADGE)
        if record.png is not None and len(record.png) <= 8 * 1024 * 1024:
            image = QImage()
            if image.loadFromData(record.png, "PNG"):
                self._image = QPixmap.fromImage(image)
        self._tile = type_pixmap(ui_kind, theme.THUMB, getattr(record, "path", "") or "")

    def enterEvent(self, event):  # noqa: N802
        self.thumbHoverEntered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.thumbHoverLeft.emit()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tile = self.rect().adjusted(0, 0, -(self.PAD * 2), -(self.PAD * 2))
        path = QPainterPath()
        path.addRoundedRect(tile, theme.THUMB_R, theme.THUMB_R)
        if self._image is not None:
            p.save()
            p.setClipPath(path)
            scaled = self._image.scaled(
                theme.THUMB, theme.THUMB,
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
            )
            x = tile.x() + (theme.THUMB - scaled.width()) // 2
            y = tile.y() + (theme.THUMB - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
            p.restore()
        else:
            p.drawPixmap(tile.topLeft(), self._tile)
        # 来源系统角标：右下角探出 4px
        p.drawPixmap(theme.THUMB - theme.BADGE + self.PAD,
                     theme.THUMB - theme.BADGE + self.PAD, self._badge)
        p.end()


# --------------------------------------------------------------------------- #
# 列表行
# --------------------------------------------------------------------------- #
class ClipRow(QFrame):
    clicked = Signal(str)            # 单击 = 预览
    activated = Signal(str)          # 双击 = 复制
    copyRequested = Signal(str)
    openRequested = Signal(str)      # 图片→预览 / 链接→打开链接
    findRequested = Signal(str)      # 图片/文件→访达
    deleteRequested = Signal(str)
    retryRequested = Signal(str)
    thumbHoverEntered = Signal(object, str, object)   # (record, ui_kind, thumb)
    thumbHoverLeft = Signal()

    def __init__(self, record, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self.record = record
        self.ui_kind = openers.classify_record(
            record.kind, record.text, getattr(record, "path", "") or ""
        )
        self.setObjectName("clipRow")
        self.setFixedHeight(theme.ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._selected = False
        self._hover = 0.0
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(140)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 7, 13, 7)
        lay.setSpacing(12)

        self.thumb = Thumb(record, self.ui_kind)
        self.thumb.thumbHoverEntered.connect(
            lambda: self.thumbHoverEntered.emit(self.record, self.ui_kind, self.thumb)
        )
        self.thumb.thumbHoverLeft.connect(self.thumbHoverLeft)
        lay.addWidget(self.thumb, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(5)
        mono = self.ui_kind == "code"
        self.title = TwoLineLabel(self._title(record))
        self.title.setObjectName("clipTitleMono" if mono else "clipTitle")
        self.title.setFont(theme.qfont(11 if mono else theme.FS_ROW, 500 if mono else 550,
                                       mono=mono))
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.title.setFixedHeight(36)
        text_col.addWidget(self.title)

        self.meta = MetaLine()
        parts, icons_map = self._meta_parts(record, self.ui_kind)
        self.meta.set_parts(parts, icons_map)
        text_col.addWidget(self.meta)

        # 传输中：行下进度条（2.5px 规格取 2px 整像素）
        self.progress = QFrame()
        self.progress.setFixedHeight(2)
        self.progress.setObjectName("clipProg")
        self.progress.setVisible(record.state == "pend")
        self._prog_fill = QFrame(self.progress)
        self._prog_fill.setObjectName("clipProgFill")
        text_col.addWidget(self.progress)
        text_col.addStretch(1)
        lay.addLayout(text_col, 1)

        # 右列：药丸 + 动作（动作区固定宽度，hover 只切透明度 → 不抖）
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)
        right.setAlignment(Qt.AlignTop | Qt.AlignRight)
        self.pill = QPushButton(STATE_TEXT.get(record.state, record.state))
        self.pill.setObjectName(STATE_STYLE.get(record.state, "statIdle"))
        self.pill.setFont(theme.qfont(9.5, 700))
        self.pill.setFlat(True)
        self.pill.setFocusPolicy(Qt.NoFocus)
        self.pill.setFixedHeight(19)
        self.pill.setCursor(
            Qt.PointingHandCursor if record.state in ("fail", "local") else Qt.ArrowCursor
        )
        self.pill.clicked.connect(self._on_pill)
        right.addWidget(self.pill, 0, Qt.AlignRight)

        self._acts_host = QWidget()
        self._acts_host.setFixedWidth(ACTIONS_W)
        self._acts_host.setFixedHeight(25)
        acts_lay = QHBoxLayout(self._acts_host)
        acts_lay.setContentsMargins(0, 0, 0, 0)
        acts_lay.setSpacing(3)
        acts_lay.addStretch(1)
        self.acts = QWidget(self._acts_host)
        act_row = QHBoxLayout(self.acts)
        act_row.setContentsMargins(0, 0, 0, 0)
        act_row.setSpacing(3)
        act_row.addWidget(self._act_button("copy", "复制", self.copyRequested))
        if self.ui_kind == "image":
            # 截图：只有预览（没有文件可打开）
            act_row.addWidget(
                self._act_button("preview", "用默认图片查看器打开", self.openRequested)
            )
        elif self.ui_kind == "file":
            if record.png is not None:      # 图片文件：预览 + 在资源管理器中显示
                act_row.addWidget(
                    self._act_button("preview", "用默认图片查看器打开", self.openRequested)
                )
            act_row.addWidget(
                self._act_button("explorer", "在资源管理器中显示", self.findRequested)
            )
        elif self.ui_kind == "link":
            act_row.addWidget(self._act_button("openlink", "打开链接", self.openRequested))
        act_row.addWidget(self._act_button("close", "删除", self.deleteRequested, danger=True))
        acts_lay.addWidget(self.acts, 0, Qt.AlignRight)

        self._acts_effect = QGraphicsOpacityEffect(self.acts)
        self._acts_effect.setOpacity(0.0)
        self.acts.setGraphicsEffect(self._acts_effect)
        self._acts_anim = QPropertyAnimation(self._acts_effect, b"opacity", self)
        self._acts_anim.setDuration(140)
        self._acts_anim.setEasingCurve(QEasingCurve.OutCubic)

        right.addWidget(self._acts_host, 0, Qt.AlignRight)
        right.addStretch(1)
        lay.addLayout(right, 0)
        self._update_progress()

    def _act_button(self, icon_name: str, tip: str, signal, danger: bool = False) -> QPushButton:
        btn = IconButton(icon_name, tip, "act", danger=danger)
        btn.clicked.connect(lambda _=False, s=signal: s.emit(self.record.id))
        return btn

    # ---- hover 淡入淡出 ----
    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def set_selected(self, selected: bool) -> None:
        """键盘选中态：`card-3` 底 + 主色左描边 2px（§6.5）。"""
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self.update()

    def is_selected(self) -> bool:
        return self._selected

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        if self._selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.fillRect(self.rect(), QColor(t.card3))
            p.fillRect(0, 0, 2, self.height(), QColor(t.accent))
            p.end()
        elif self._hover > 0.0:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            color = QColor(t.card2)
            color.setAlphaF(min(1.0, self._hover))
            p.fillRect(self.rect(), color)
            p.end()

    def enterEvent(self, event):  # noqa: N802
        self._fade_acts(1.0)
        self._hover_anim.stop()
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._fade_acts(0.0)
        self._hover_anim.stop()
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def _fade_acts(self, target: float) -> None:
        self.acts.setAttribute(Qt.WA_TransparentForMouseEvents, target <= 0.0)
        self._acts_anim.stop()
        self._acts_anim.setEndValue(target)
        self._acts_anim.start()

    def _update_progress(self) -> None:
        width = max(0, int(self.progress.width() * (self.record.progress or 0.25)))
        self._prog_fill.setGeometry(0, 0, width, 2)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.progress.setVisible(self.record.state == "pend")
        self._update_progress()

    def mousePressEvent(self, event):  # noqa: N802
        # 单击行 = **直接复制**（用户 2026-10-21：点击就复制，不用再去点复制按钮）。
        # 预览走「悬停缩略图」或双击，不再占用单击。
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.record.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        # 双击 = 预览（截图 / 图片文件 / 其它有预览的类型），单击已经复制过了
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.record.id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802
        menu = QMenu(self)
        menu.setFont(theme.qfont(12))
        menu.addAction("复制", lambda: self.copyRequested.emit(self.record.id))
        if self.ui_kind == "image":
            # 截图没有文件可打开
            menu.addAction("用默认图片查看器打开", lambda: self.openRequested.emit(self.record.id))
        elif self.ui_kind == "link":
            menu.addAction("打开链接", lambda: self.openRequested.emit(self.record.id))
        elif self.ui_kind == "file":
            if self.record.png is not None:
                menu.addAction(
                    "用默认图片查看器打开", lambda: self.openRequested.emit(self.record.id)
                )
            menu.addAction(
                "在资源管理器中显示", lambda: self.findRequested.emit(self.record.id)
            )
        menu.addSeparator()
        menu.addAction("删除这一条", lambda: self.deleteRequested.emit(self.record.id))
        menu.exec(event.globalPos())

    def _on_pill(self) -> None:
        """仅本机 / 失败 = 重试入口；已同步 / 传输中 = 复制（Frset 双击复制语义）。"""
        if self.record.state in ("fail", "local"):
            self.retryRequested.emit(self.record.id)
        else:
            self.copyRequested.emit(self.record.id)

    @staticmethod
    def _title(record) -> str:  # noqa: ANN001
        if getattr(record, "path", ""):
            return file_name(record)          # 访达复制的文件：标题就是文件名
        if record.kind in ("image", "mixed") and record.png is not None:
            path = getattr(record, "path", "") or ""
            if path:
                return os.path.basename(path)
            return f"图片 {record.width}×{record.height}" if record.width else "图片"
        text = (record.text or "").replace("\n", " ").replace("\t", " ").strip()
        if len(text) > 160:
            # 按码位截断会把 👨‍👩‍👧‍👦 这类 ZWJ 表情切成碎片
            text = safe_prefix(text, 160) + "…"
        return text or "（空）"

    @staticmethod
    def _meta_parts(record, ui_kind: str):  # noqa: ANN001, ANN201
        """`Mac · [icon]微信 · 时间 · 大小 · 类型` 的分段（+ 来源应用图标）。"""
        kind_name = {
            "image": "图片", "link": "链接", "code": "代码",
            "text": "纯文本", "file": "文件",
        }.get(ui_kind, ui_kind)
        tokens = meta_line(record, ui_kind).split(" · ")
        parts: list[tuple[str, bool]] = [(tokens[0], True)]
        app = (getattr(record, "app", "") or "").strip()
        icons_map: dict[int, object] = {}
        if app and len(tokens) > 1 and tokens[1] == app:
            icon = apps.app_icon(getattr(record, "app_id", "") or "", 14)
            if icon is not None:
                icons_map[1] = icon
        parts.extend((tok, False) for tok in tokens[1:])
        if ui_kind != "file" and (not tokens[1:] or tokens[-1] != kind_name):
            parts.append((kind_name, False))
        return parts, icons_map


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
class ClipboardPage(QWidget):
    """剪贴板页。`refresh()` 由外部定时器驱动（页面不可见时不要调）。"""

    copyRequested = Signal(str)
    retryRequested = Signal(str)
    cleared = Signal()
    toastRequested = Signal(str)
    pausedChanged = Signal(bool)
    #: 请求窗口内确认遮罩（§8）：参数 = 当前记录条数。确认后 runner 回调 clear_confirmed()
    clearConfirmRequested = Signal(int)

    def __init__(self, engine=None, parent=None, open_external=None, hover_enabled=None):  # noqa: ANN001
        super().__init__(parent)
        self.engine = engine
        self._open_external = open_external
        # 设置页「悬浮预览」开关（None = 不限制，测试/独立预览用）
        self._hover_enabled = hover_enabled
        self._records = []
        self._rows: dict[str, ClipRow] = {}
        self._last_sig = ""
        self._filter = "all"
        self._query = ""
        self._paused = False
        self._selected: str | None = None      # 键盘选中行（§6.5）
        self._dirty = True
        self._preview = PreviewController(self)
        self._preview.bubble.copyRequested.connect(self._copy)
        self._preview.bubble.openRequested.connect(self._open)
        self._preview.bubble.findRequested.connect(self._find)
        self._build()

    @property
    def preview_controller(self) -> PreviewController:
        return self._preview

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 24)
        outer.setSpacing(11)

        # ---- 工具栏 ----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        search = QFrame()
        search.setObjectName("searchBox")
        search.setFixedHeight(theme.SEARCH_H)
        s_lay = QHBoxLayout(search)
        s_lay.setContentsMargins(12, 0, 12, 0)
        s_lay.setSpacing(8)
        icon = QLabel()
        icon.setFixedSize(14, 14)
        icon.setPixmap(icons.svg_pixmap("search", 14, theme.tokens().ink3, 2.0))
        s_lay.addWidget(icon)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("搜索剪贴板……")
        self.search_edit.setFont(theme.qfont(12))
        self.search_edit.setFrame(False)
        self.search_edit.installEventFilter(self)
        s_lay.addWidget(self.search_edit, 1)
        bar.addWidget(search, 1)

        self.btn_pause = IconButton("pause", "暂停记录剪贴板", "tool")
        self.btn_pause.clicked.connect(self._toggle_pause)
        bar.addWidget(self.btn_pause)

        self.btn_clear = IconButton("trash", "清理历史记录", "tool")
        self.btn_clear.clicked.connect(self._clean_history)
        bar.addWidget(self.btn_clear)
        outer.addLayout(bar)

        # ---- 筛选 chips ----
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chip_buttons: dict[str, Chip] = {}
        for key in CHIP_ORDER:
            chip = Chip(CHIP_LABEL[key])
            chip.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            chips.addWidget(chip)
            self._chip_buttons[key] = chip
        chips.addStretch(1)
        outer.addLayout(chips)

        # ---- 列表 ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_host = QWidget()
        self.list_host.setObjectName("clipList")
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_host)
        outer.addWidget(self.scroll, 1)

        # ---- 双态空态 ----
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(28, 20, 28, 20)
        el.setSpacing(9)
        self.empty_art = QLabel()
        self.empty_art.setAlignment(Qt.AlignCenter)
        self.empty_art.setFixedHeight(64)
        self.empty_title = QLabel("剪贴板为空")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setFont(theme.qfont(13.5, 650))
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_sub = QLabel("复制内容后会显示在这里，\n并自动同步到另一台机器")
        self.empty_sub.setObjectName("emptyText")
        self.empty_sub.setFont(theme.qfont(11.5))
        self.empty_sub.setAlignment(Qt.AlignCenter)
        self.empty_sub.setWordWrap(True)
        el.addStretch(1)
        el.addWidget(self.empty_art)
        el.addWidget(self.empty_title)
        el.addWidget(self.empty_sub)
        el.addStretch(1)
        self.empty.setVisible(False)
        outer.addWidget(self.empty, 1)

        self.search_edit.textChanged.connect(self._on_query)
        self._refresh_chips()
        self._update_empty()
        self.setFocusPolicy(Qt.StrongFocus)

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        if self.engine is None:
            return
        records = self.engine.records()
        sig = "|".join(f"{r.id}:{r.state}:{r.progress:.2f}" for r in records)
        if sig == self._last_sig and not self._dirty:
            return
        self._last_sig = sig
        self._dirty = False
        self._records = records
        self._rebuild_rows()
        self._refresh_chips()
        self._update_empty()

    def mark_dirty(self) -> None:
        self._dirty = True

    def records(self) -> list:
        return list(self._records)

    def _visible_records(self) -> list:
        out = []
        for rec in self._records:
            ui_kind = openers.classify_record(
                rec.kind, rec.text, getattr(rec, "path", "") or ""
            )
            if not self._match_filter(rec, ui_kind):
                continue
            if self._query and self._query not in self._row_text(rec, ui_kind):
                continue
            out.append(rec)
        return out

    def _match_filter(self, rec, ui_kind: str) -> bool:  # noqa: ANN001
        if self._filter == "all":
            return True
        if self._filter == "bad":
            return rec.state in ("fail", "local")
        if self._filter == "text":
            return ui_kind in ("text", "code")   # 代码归「文字」chip（§3.2 无代码档）
        return ui_kind == self._filter

    @staticmethod
    def _row_text(rec, ui_kind: str) -> str:  # noqa: ANN001
        # 文件能按文件名也能按目录搜到
        return f"{rec.text or ''} {getattr(rec, 'path', '') or ''} {meta_line(rec, ui_kind)}".lower()

    def _rebuild_rows(self) -> None:
        while self.list_lay.count() > 1:
            item = self.list_lay.takeAt(0)
            destroy(item.widget())
        self._rows.clear()
        self._preview.hide_now()
        visible = self._visible_records()
        for index, rec in enumerate(visible):
            row = ClipRow(rec)
            row.clicked.connect(self._on_row_click)
            row.activated.connect(self._on_row_activate)
            row.copyRequested.connect(self._copy)
            row.openRequested.connect(self._open)
            row.findRequested.connect(self._find)
            row.deleteRequested.connect(self._drop)
            row.retryRequested.connect(self._retry)
            row.thumbHoverEntered.connect(self._on_thumb_hover)
            row.thumbHoverLeft.connect(self._preview.schedule_close)
            if index:
                sep = QFrame()
                sep.setObjectName("clipSep")
                sep.setFixedHeight(1)
                self.list_lay.insertWidget(self.list_lay.count() - 1, sep)
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)
            self._rows[rec.id] = row
        # 选中行还在列表里就保持选中，否则清掉（筛选/搜索把行藏了）
        if self._selected in self._rows:
            self._rows[self._selected].set_selected(True)
        else:
            self._selected = None

    def _update_empty(self) -> None:
        visible = self._visible_records()
        if visible:
            self.empty.setVisible(False)
            self.scroll.setVisible(True)
            return
        self.scroll.setVisible(False)
        self.empty.setVisible(True)
        filtering = bool(self._query) or self._filter != "all"
        t = theme.tokens()
        if filtering:
            self.empty_art.setPixmap(icons.svg_pixmap("search", 40, t.ink3, 1.6))
            self.empty_title.setText("当前筛选下暂无内容")
            self.empty_sub.setText("换个筛选条件试试")
        else:
            self.empty_art.setPixmap(icons.svg_pixmap("clipboard", 40, t.ink3, 1.6))
            self.empty_title.setText("剪贴板为空")
            self.empty_sub.setText("复制内容后会显示在这里，\n并自动同步到另一台机器")

    def _refresh_chips(self) -> None:
        counts = dict.fromkeys(CHIP_ORDER, 0)
        for rec in self._records:
            ui_kind = openers.classify_record(
                rec.kind, rec.text, getattr(rec, "path", "") or ""
            )
            counts["all"] += 1
            if rec.state in ("fail", "local"):
                counts["bad"] += 1
            key = "text" if ui_kind == "code" else ui_kind
            if key in counts:
                counts[key] += 1
        for key, chip in self._chip_buttons.items():
            chip.set_count(counts[key])
            chip.setChecked(key == self._filter)
            chip.update()

    # ------------------------------------------------------------------ 交互
    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._preview.hide_now()
        self._rebuild_rows()
        self._refresh_chips()
        self._update_empty()

    def _on_query(self, text: str) -> None:
        self._query = text.strip().lower()
        self._preview.hide_now()
        self._rebuild_rows()
        self._update_empty()

    # ---- 暂停记录（真功能）----
    def _toggle_pause(self) -> None:
        self.set_paused(not self._paused)

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        self.btn_pause.set_icon_name("resume" if paused else "pause")
        self.btn_pause.set_primary(paused)      # 打开 = 实心高亮（§3.1）
        self.btn_pause.setToolTip(
            "恢复记录剪贴板" if paused else "暂停记录剪贴板"
        )
        self.btn_pause.update()
        engine = self.engine
        setter = getattr(engine, "set_paused", None)
        if callable(setter):
            setter(paused)
        self.pausedChanged.emit(paused)
        self.toastRequested.emit("已暂停记录剪贴板" if paused else "已恢复记录剪贴板")

    @property
    def paused(self) -> bool:
        return self._paused

    # ---- 清理历史 ----
    def _clean_history(self) -> None:
        """🗑 = 清空列表：先弹**窗口内确认遮罩**（§8），确认后才清。

        Windows2 起不再开 QDialog —— 整个应用只有一个窗口（§1.1 硬约束）。
        页面只负责发请求，遮罩由 runner 画在主窗口上。
        """
        self._clear_with_confirm()

    def _drop_older(self, days: int) -> None:
        deadline = time.time() - days * 86400
        dropped = 0
        for rec in list(self._records):
            if rec.ts < deadline and self.engine is not None:
                self.engine.drop_record(rec.id)
                dropped += 1
        self.mark_dirty()
        self.refresh()
        self.toastRequested.emit(
            f"已清理 {dropped} 条历史记录" if dropped else f"{days} 天内没有更早的记录"
        )

    def clear_records_with_confirm(self) -> None:
        """对外入口（设置页「清理」按钮也走这里）。"""
        self._clear_with_confirm()

    def _clear_with_confirm(self) -> None:
        total = len(self._records)
        if not total:
            self.toastRequested.emit("列表已经是空的")
            return
        self.clearConfirmRequested.emit(total)

    def clear_confirmed(self) -> None:
        """遮罩里点了「清空」→ 真清（runner 调用）。"""
        self._clear()

    def _clear(self) -> None:
        total = len(self._records)
        if self.engine is not None:
            self.engine.clear_records()
        self._selected = None
        self.mark_dirty()
        self.refresh()
        self.cleared.emit()
        self.toastRequested.emit(f"已清空 {total} 条" if total else "已清空本机剪贴板记录")

    def _on_row_click(self, record_id: str) -> None:
        """双击行 = 预览（立即弹，不等 450ms）。单击已经改成复制了。"""
        row = self._rows.get(record_id)
        if row is None:
            return
        self._select(record_id)
        self._preview.open_now(row.record, row.ui_kind, row.thumb)

    def _on_row_activate(self, record_id: str) -> None:
        """单击行 = 复制（同时把它设成键盘选中行，↑↓ 从这里继续走）。"""
        self._select(record_id)
        self._copy(record_id)

    def _on_thumb_hover(self, record, ui_kind: str, thumb) -> None:  # noqa: ANN001
        if self._hover_enabled is not None and not self._hover_enabled():
            return
        self._preview.schedule_open(record, ui_kind, thumb)

    def _copy(self, record_id: str) -> None:
        if self.engine is None:
            return
        ok = self.engine.copy_to_local(record_id)
        rec = next((r for r in self._records if r.id == record_id), None)
        abnormal = rec is not None and rec.state in ("fail", "local")
        if ok:
            self.copyRequested.emit(record_id)
            self.toastRequested.emit("已重新同步" if abnormal else "已复制")

    def _open(self, record_id: str) -> None:
        rec = next((r for r in self._records if r.id == record_id), None)
        if rec is None:
            return
        kind = openers.classify_record(rec.kind, rec.text, getattr(rec, "path", "") or "")
        if getattr(rec, "path", "") and kind == "file" and rec.png is not None:
            # 图片文件：用原图打开（不是拿缩略图再落临时文件）
            path = self._opener().open_image_path(rec.path)
            self.toastRequested.emit(f"用默认图片查看器打开：{path}")
        elif kind == "image" and rec.png is not None:
            if getattr(rec, "path", ""):
                path = self._opener().open_image_path(rec.path)
            else:
                path = self._opener().open_image_preview(rec.png, f"clip-{rec.id[:8]}.png")
            self.toastRequested.emit(f"用默认图片查看器打开：{path}")
        elif kind == "link":
            self._opener().open_url((rec.text or "").strip())
            self.toastRequested.emit("打开链接")
        elif kind == "file":
            self._find(record_id)
        else:
            self._copy(record_id)

    def _find(self, record_id: str) -> None:
        rec = next((r for r in self._records if r.id == record_id), None)
        if rec is None:
            return
        path = (getattr(rec, "path", "") or rec.detail or rec.text or "").strip()
        self._opener().reveal_in_explorer(path)
        self.toastRequested.emit(f"在资源管理器中显示：{path}")

    def _opener(self):  # noqa: ANN202
        return self._open_external or openers.WindowsOpeners()

    def _drop(self, record_id: str) -> None:
        """删掉这一条（只删一条，不动系统剪贴板、不通知对端）。"""
        if self.engine is not None:
            self.engine.drop_record(record_id)
        self._preview.hide_now()
        self.mark_dirty()
        self.refresh()
        self.toastRequested.emit("已删除 1 条")

    def _retry(self, record_id: str) -> None:
        if self.engine is not None:
            self.engine.repush(record_id)
        self.retryRequested.emit(record_id)
        self.mark_dirty()
        self.refresh()
        self.toastRequested.emit("已重新同步")

    # ------------------------------------------------------------------ 键盘
    def _select(self, record_id: str | None) -> None:
        if record_id == self._selected:
            return
        old = self._rows.get(self._selected or "")
        if old is not None:
            old.set_selected(False)
        self._selected = record_id
        row = self._rows.get(record_id or "")
        if row is not None:
            row.set_selected(True)

    def selected_id(self) -> str | None:
        return self._selected

    def _visible_ids(self) -> list[str]:
        return [rec.id for rec in self._visible_records()]

    def _move_selection(self, delta: int) -> None:
        ids = self._visible_ids()
        if not ids:
            return
        if self._selected in ids:
            index = ids.index(self._selected) + delta
            index = max(0, min(len(ids) - 1, index))
        else:
            index = 0 if delta > 0 else len(ids) - 1
        self._select(ids[index])
        row = self._rows.get(ids[index])
        if row is not None:
            self.scroll.ensureWidgetVisible(row)

    def eventFilter(self, obj, event):  # noqa: N802
        """搜索框里的 `Esc` / `Ctrl+F`（§6.1/§6.5）。"""
        if obj is self.search_edit and event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.handle_escape()
                return True
            if key == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
                self.search_edit.setFocus(Qt.ShortcutFocusReason)
                self.search_edit.selectAll()
                return True
        return super().eventFilter(obj, event)

    def handle_escape(self) -> bool:
        """`Esc`：先关预览气泡，再清搜索，最后取消选中（§6.5）。"""
        if self._preview.is_open():
            self._preview.hide_now()
            return True
        if self.search_edit.text():
            self.search_edit.clear()
            return True
        if self._selected is not None:
            self._select(None)
            return True
        return False

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.handle_escape()
            return
        if key == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self.search_edit.setFocus(Qt.ShortcutFocusReason)
            self.search_edit.selectAll()
            return
        if key == Qt.Key_Down:
            self._move_selection(1)
            return
        if key == Qt.Key_Up:
            self._move_selection(-1)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and self._selected:
            self._copy(self._selected)
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self._selected:
            self._drop(self._selected)
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._preview.hide_now()      # 滚动/尺寸变化立即关（§3.4）
