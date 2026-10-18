"""剪贴板页：主窗口里的一页（`QStackedWidget`），**不新开窗口**。

规格见 `docs/UI-DESIGN-CLIPBOARD.md`，可交互稿 `design/clipboard/index.html`
（`?page=clip` 直接进这一页，`?empty=1` 看空态）。

页面只依赖一个「记录源」对象（将来是 `sync/engine.py` 的 `SyncEngine`），
用的全是普通方法，不要求继承什么基类：

    records() -> list[ClipRecord]      # 最新的在前
    status()  -> dict                  # {"peer": str, "connected": bool, "enabled": bool}
    copy_to_local(id) -> bool          # 写回本机剪贴板
    resend(id) -> None                 # 重发（没有对端时可以等同复制）
    remove(id) -> None                 # 删一条记录
    clear() -> None                    # 清空记录（不动系统剪贴板）

页面里**不出现任何平台判断**：入口在标题栏还是列表、窗口要不要加高，
都由 `chrome` 的 `INLINE_CLIPBOARD_ROW` / `EXTRA_H` 决定。

几个刻意的做法：

* **刷新靠签名比对**：`refresh()` 每 300ms 调一次，但只有「记录集合 / 状态」真的变了
  才重建行 —— 否则每次刷新都重建控件，滚动位置和 hover 态都会抖。
* **列表高度随条数长**（52 × n，最多 252）：只有一条记录时留一大片空白比列表短一截更难看。
* **不弹新窗口**：双击行 = 复制到本机，看原图这件事不做（那必然要第二个窗口）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import StatusBar, label

log = logging.getLogger("dualswitch.ui.clipboard")

#: 列表最大高度：4 行（52）可见 + 第 5 行露 20px，提示可滚
LIST_MAX_H = 252
ROW_H = 52
#: 超过这个大小的图不画缩略图：为画一个 36px 方块解码 8MB PNG 不值得
THUMB_MAX_BYTES = 8 * 1024 * 1024

_MONO_FAMILIES = ["Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Courier New"]


@dataclass(frozen=True)
class ClipRecord:
    """面板要的一条记录（`engine.records()` 就返回这个）。"""

    id: str                     # = ClipItem.digest
    kind: str                   # text | image
    text: str | None = None
    png: bytes | None = None
    width: int = 0
    height: int = 0
    source: str = "local"       # local | remote
    peer_name: str = ""
    ts: float = field(default_factory=time.time)
    state: str = "local"        # ok | pend | fail | local
    progress: float = 0.0
    size: int = 0


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _mono_font(px: float) -> QFont:
    """等宽字体：代码要能看出缩进，但两端都没有同名等宽字体，得按可用性挑。"""
    font = QFont()
    font.setFamilies(_MONO_FAMILIES)
    font.setPixelSize(max(9, int(round(px))))
    return font


def _available_mono() -> str:
    families = set(QFontDatabase.families())
    for name in _MONO_FAMILIES:
        if name in families:
            return name
    return "monospace"


def _ago(ts: float) -> str:
    delta = max(0.0, time.time() - ts)
    if delta < 45:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1048576:.1f} MB"


def _first_line(text: str, limit: int = 160) -> str:
    line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    return line[:limit]


def record_title(record: ClipRecord) -> str:
    if record.kind == "image":
        return f"图片 {record.width} × {record.height}"
    title = _first_line(record.text or "")
    return title or "（空白文本）"


def record_meta(record: ClipRecord) -> str:
    if record.kind == "image":
        return "PNG"
    text = (record.text or "").strip()
    if text.startswith(("http://", "https://")):
        return "链接"
    return "纯文本"


_STATE_TEXT = {
    "ok": ("已同步", "stOk"),
    "pend": ("传输中", "stPend"),
    "fail": ("失败 · 重试", "stFail"),
    "local": ("仅本机", "stLocal"),
}


def _state_pill(record: ClipRecord) -> tuple[str, str]:
    text, name = _STATE_TEXT.get(record.state, _STATE_TEXT["local"])
    if record.state == "pend" and record.progress > 0:
        text = f"传输中 {int(record.progress * 100)}%"
    return text, name


class _ElideRight(QLabel):
    """右省略标签：`QLabel` 自己没有 ellipsis，长代码行会顶掉右侧状态药丸。"""

    def __init__(self, text: str = "", name: str = "", parent=None):
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._apply()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._apply()

    def _apply(self) -> None:
        width = max(0, self.width() - 1)
        shown = self._full if width < 16 else self.fontMetrics().elidedText(self._full, Qt.ElideRight, width)
        if shown != super().text():
            super().setText(shown)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply()


class _Thumb(QWidget):
    """36×36 缩略图：图片按 cover 裁切，文本画一个等宽 T。"""

    def __init__(self, record: ClipRecord, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pixmap = self._decode(record)

    @staticmethod
    def _decode(record: ClipRecord) -> QPixmap | None:
        if record.kind != "image" or not record.png or len(record.png) > THUMB_MAX_BYTES:
            return None
        image = QPixmap()
        if not image.loadFromData(record.png, "PNG"):
            return None
        return image

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, 35, 35)
        path_radius = 7
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.setBrush(QColor("#F4F6FA"))
        p.drawRoundedRect(rect, path_radius, path_radius)

        if self._pixmap is not None:
            p.save()
            p.setClipRect(rect)
            scaled = self._pixmap.scaled(36, 36, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(int((36 - scaled.width()) / 2), int((36 - scaled.height()) / 2), scaled)
            p.restore()
            p.setPen(QPen(QColor(theme.LINE), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, path_radius, path_radius)
        else:
            p.setPen(QColor("#7A869A"))
            p.setFont(_mono_font(15))
            p.drawText(rect, Qt.AlignCenter, "T")
        p.end()


class _Pill(QLabel):
    """状态药丸。失败 / 仅本机态本身就是重试按钮（别让人去底部找按钮）。"""

    clicked = Signal()

    def __init__(self, text: str, name: str, clickable: bool, parent=None):
        super().__init__(text, parent)
        self.setObjectName(name)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("点击重试")

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ClipRow(QFrame):
    """一条记录：缩略图 + 两行文字 + 状态药丸。"""

    clicked = Signal(str)
    activated = Signal(str)             # 双击 = 复制到本机
    pillClicked = Signal(str)
    menuRequested = Signal(str, QPoint)

    def __init__(self, record: ClipRecord, parent=None):
        super().__init__(parent)
        self.record = record
        self.setObjectName("citem")
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("rid", record.id)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 8, 13, 8)
        lay.setSpacing(10)

        lay.addWidget(_Thumb(record), 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        is_text = record.kind == "text"
        self.title = _ElideRight(record_title(record), "ctitleMono" if is_text else "ctitle")
        if is_text:
            self.title.setFont(_mono_font(11.8))
        self.meta = QLabel()
        self.meta.setObjectName("cmeta")
        self.meta.setText(self._meta_html(record))
        col.addWidget(self.title)
        col.addWidget(self.meta)
        lay.addLayout(col, 1)

        text, name = _state_pill(record)
        self.pill = _Pill(text, name, clickable=record.state in ("fail", "local"))
        self.pill.clicked.connect(lambda: self.pillClicked.emit(record.id))
        lay.addWidget(self.pill, 0, Qt.AlignVCenter)

        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)

    @staticmethod
    def _meta_html(record: ClipRecord) -> str:
        """`来源 · 时间 · 大小 · 类型`，来源加粗 —— 它是最容易看错的一列。"""
        source = "本机" if record.source == "local" else (record.peer_name or "对端")
        dot = '<span style="color:#C6CDDA"> · </span>'
        return (
            f'<b style="color:{theme.INK2}">{source}</b>{dot}'
            f"{_ago(record.ts)}{dot}{_human_size(record.size)}{dot}{record_meta(record)}"
        )

    def set_selected(self, selected: bool) -> None:
        name = "citemSel" if selected else "citem"
        if self.objectName() != name:
            self.setObjectName(name)
            self.style().unpolish(self)
            self.style().polish(self)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.record.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.record.id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802
        self.menuRequested.emit(self.record.id, event.globalPos())
        event.accept()


class _EmptyIcon(QWidget):
    """空态图标：52×52 圆角块 + 三行文字 + 放大镜（和设计稿同一套线条）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 52)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.setBrush(QColor("#F4F7FC"))
        p.drawRoundedRect(QRectF(0.5, 0.5, 51, 51), 14, 14)

        pen = QPen(QColor("#8E98A8"), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(11, 17, 27, 17)
        p.drawLine(11, 24, 24, 24)
        p.drawLine(11, 31, 20, 31)
        p.drawEllipse(QRectF(25.5, 28.5, 9.5, 9.5))
        p.drawLine(33.6, 36.6, 39, 42)
        p.end()


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
class ClipboardPage(QWidget):
    """剪贴板页。`source` 见模块说明；传 None 时永远显示空态（方便单测）。"""

    backRequested = Signal()

    def __init__(self, source=None, parent=None):
        super().__init__(parent)
        self.source = source
        self._records: list[ClipRecord] = []
        self._rows: dict[str, ClipRow] = {}
        self._selected: str | None = None
        self._signature: tuple | None = None
        self._pv_png: bytes | None = None

        self.setFocusPolicy(Qt.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.scroll = QScrollArea()
        self.scroll.setObjectName("clipScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        self.scroll.setFixedHeight(LIST_MAX_H)
        self.list_wrap = QWidget()
        self.list_wrap.setObjectName("clipList")
        self.list_lay = QVBoxLayout(self.list_wrap)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_wrap)
        root.addWidget(self.scroll)

        self.preview = self._build_preview()
        root.addWidget(self.preview, 1)

        self.empty = self._build_empty()
        self.empty.setVisible(False)
        root.addWidget(self.empty, 1)

        self.bar = self._build_bar()
        root.addWidget(self.bar)

        self.statusbar = StatusBar()
        self.statusbar.text.setText("仅内存保留 · 退出即清 · 敏感内容不记录")
        self.statusbar.ver.setText("最近 10 条")
        root.addWidget(self.statusbar)

        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.refresh)

        self._render_empty(True)

    # ------------------------------------------------------------------ 组装
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("clipHead")
        bar.setFixedHeight(40)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(8)

        self.btn_back = QPushButton("\u2039  返回")
        self.btn_back.setObjectName("clipBack")
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.setFixedHeight(26)
        self.btn_back.clicked.connect(self.backRequested)
        lay.addWidget(self.btn_back, 0, Qt.AlignVCenter)

        lay.addWidget(label("剪贴板", "clipTitle"), 0, Qt.AlignVCenter)
        lay.addStretch(1)

        self.conn_dot = QLabel()
        self.conn_dot.setFixedSize(6, 6)
        self.conn_text = label("未连接", "clipConn")
        lay.addWidget(self.conn_dot, 0, Qt.AlignVCenter)
        lay.addWidget(self.conn_text, 0, Qt.AlignVCenter)
        self._set_connected(False, "")
        return bar

    def _build_preview(self) -> QWidget:
        box = QFrame()
        box.setObjectName("clipPreview")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(13, 9, 13, 11)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(7)
        self.pv_label = label("预览", "pvHead")
        self.pv_kind = label("", "pvKind")
        self.pv_hint = label("", "pvHint")
        head.addWidget(self.pv_label, 0, Qt.AlignVCenter)
        head.addWidget(self.pv_kind, 0, Qt.AlignVCenter)
        head.addStretch(1)
        head.addWidget(self.pv_hint, 0, Qt.AlignVCenter)
        lay.addLayout(head)

        self.pv_body = QWidget()
        body_lay = QVBoxLayout(self.pv_body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self.pv_image = QLabel()
        self.pv_image.setObjectName("pvImg")
        self.pv_image.setAlignment(Qt.AlignCenter)
        self.pv_image.setMinimumHeight(60)
        self.pv_image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.pv_text = QPlainTextEdit()
        self.pv_text.setObjectName("pvText")
        self.pv_text.setReadOnly(True)
        self.pv_text.setFrameShape(QFrame.NoFrame)
        self.pv_text.setFont(_mono_font(11.5))
        self.pv_text.document().setDocumentMargin(7)
        self.pv_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.pv_none = label("选中一条查看预览", "pvEmpty")
        self.pv_none.setAlignment(Qt.AlignCenter)

        body_lay.addWidget(self.pv_image)
        body_lay.addWidget(self.pv_text)
        body_lay.addWidget(self.pv_none)
        lay.addWidget(self.pv_body, 1)
        return box

    def _build_empty(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(34, 0, 34, 0)
        lay.setSpacing(0)
        lay.addStretch(1)
        icon = _EmptyIcon()
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(icon)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(14)
        self.empty_title = label("还没有内容", "clipEmptyTitle")
        self.empty_title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_title)
        lay.addSpacing(7)
        self.empty_text = label(
            "复制点什么，然后按热键切一次屏。\n文字和图片会自动带过去，并记在这里。",
            "clipEmptyText",
        )
        self.empty_text.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_text)
        lay.addStretch(1)
        return box

    def _build_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("clipBar")
        bar.setFixedHeight(48)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(13, 0, 13, 0)
        lay.setSpacing(7)

        self.btn_copy = QPushButton("复制到本机")
        self.btn_copy.setObjectName("clipPrimary")
        self.btn_copy.setFixedHeight(30)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_selected)

        self.btn_resend = QPushButton("重发")
        self.btn_resend.setObjectName("clipBtn")
        self.btn_resend.setFixedHeight(30)
        self.btn_resend.setCursor(Qt.PointingHandCursor)
        self.btn_resend.clicked.connect(self._resend_selected)

        self.btn_clear = QPushButton("清空记录")
        self.btn_clear.setObjectName("clipDanger")
        self.btn_clear.setFixedHeight(30)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)

        lay.addWidget(self.btn_copy, 0, Qt.AlignVCenter)
        lay.addWidget(self.btn_resend, 0, Qt.AlignVCenter)
        lay.addStretch(1)
        lay.addWidget(self.btn_clear, 0, Qt.AlignVCenter)
        return bar

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        """拉一次记录。300ms 定时器 / 进入页面 / 动作之后都会调它。"""
        records = self._fetch_records()
        signature = tuple((r.id, r.state, round(r.progress, 3)) for r in records)
        if signature == self._signature:
            return
        self._signature = signature
        self._records = records
        self._rebuild()

    def _fetch_records(self) -> list[ClipRecord]:
        if self.source is None:
            return []
        getter = getattr(self.source, "records", None)
        if not callable(getter):
            return []
        try:
            return list(getter())
        except Exception:  # noqa: BLE001 - 数据源坏了不能把界面打挂
            log.warning("读取剪贴板记录失败", exc_info=True)
            return []

    def _rebuild(self) -> None:
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        # 清掉 layout 里的行（末尾那个 stretch 保留）
        while self.list_lay.count() > 1:
            item = self.list_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not self._records:
            self._selected = None
            self._render_empty(True)
            self._set_actions_enabled(False)
            self._update_connection()
            return

        for index, record in enumerate(self._records):
            row = ClipRow(record)
            row.clicked.connect(self._select)
            row.activated.connect(self._copy_record)
            row.pillClicked.connect(self._retry_record)
            row.menuRequested.connect(self._show_row_menu)
            self.list_lay.insertWidget(index, row)
            self._rows[record.id] = row

        self.scroll.setFixedHeight(min(LIST_MAX_H, ROW_H * len(self._records)))
        self._render_empty(False)
        self._set_actions_enabled(True)

        keep = self._selected if self._selected in self._rows else self._records[0].id
        self._select(keep)
        self._update_connection()

    def _render_empty(self, empty: bool) -> None:
        """空态：收起列表、预览和操作栏，内容在整页垂直居中（设计稿第 7 节）。"""
        self.scroll.setVisible(not empty)
        self.preview.setVisible(not empty)
        self.bar.setVisible(not empty)
        self.empty.setVisible(empty)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.btn_copy.setEnabled(enabled)
        self.btn_resend.setEnabled(enabled)
        self.btn_clear.setEnabled(enabled)

    def _update_connection(self) -> None:
        status = {}
        getter = getattr(self.source, "status", None)
        if callable(getter):
            try:
                status = getter() or {}
            except Exception:  # noqa: BLE001
                status = {}
        self._set_connected(bool(status.get("connected")), str(status.get("peer") or ""))

    def _set_connected(self, connected: bool, peer: str) -> None:
        color = "#34C759" if connected else "#C6CDDA"
        self.conn_dot.setStyleSheet(f"background:{color};border-radius:3px;")
        self.conn_text.setText(peer if connected and peer else ("已连接" if connected else "未连接"))

    # ------------------------------------------------------------------ 选择
    def selected_id(self) -> str | None:
        return self._selected

    def select(self, rid: str | None) -> None:
        self._select(rid)

    def _select(self, rid: str | None) -> None:
        if rid is not None and rid not in self._rows:
            return
        self._selected = rid
        for key, row in self._rows.items():
            row.set_selected(key == rid)
        self._render_preview(self._records_by_id().get(rid) if rid else None)
        self._set_actions_enabled(rid is not None)

    def _records_by_id(self) -> dict[str, ClipRecord]:
        return {r.id: r for r in self._records}

    def _move(self, step: int) -> None:
        if not self._records:
            return
        ids = [r.id for r in self._records]
        try:
            index = ids.index(self._selected) if self._selected in ids else 0
        except ValueError:
            index = 0
        index = max(0, min(len(ids) - 1, index + step))
        self._select(ids[index])
        row = self._rows.get(ids[index])
        if row is not None:
            self.scroll.ensureWidgetVisible(row, 0, 8)

    # ------------------------------------------------------------------ 预览
    def _render_preview(self, record: ClipRecord | None) -> None:
        has = record is not None
        self.pv_none.setVisible(not has)
        self.pv_image.setVisible(False)
        self.pv_text.setVisible(False)
        self.pv_kind.setText("")
        self.pv_hint.setText("")

        if record is None:
            return

        if record.kind == "image" and record.png:
            self.pv_kind.setText(f"图片 · {_human_size(record.size)}")
            self.pv_hint.setText("双击行复制到本机")
            self._pv_png = record.png
            self.pv_image.setVisible(True)
            self._render_image()
            return

        self.pv_kind.setText(f"文本 · {_human_size(record.size)}")
        self.pv_hint.setText("可直接选中文本")
        self._pv_png = None
        self.pv_text.setPlainText(record.text or "")
        self.pv_text.setVisible(True)

    def _render_image(self) -> None:
        if not self._pv_png:
            return
        image = QPixmap()
        if not image.loadFromData(self._pv_png, "PNG"):
            self.pv_image.setText("图片解码失败")
            return
        width = max(80, self.pv_body.width() - 2)
        height = max(40, self.pv_body.height() - 2)
        self.pv_image.setPixmap(
            image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._pv_png and self.pv_image.isVisible():
            self._render_image()

    # ------------------------------------------------------------------ 动作
    def _copy_record(self, rid: str) -> None:
        if not self._call("copy_to_local", rid):
            return
        original = self.btn_copy.text()
        self.btn_copy.setText("已复制")
        QTimer.singleShot(1100, lambda: self.btn_copy.setText(original))

    def _copy_selected(self) -> None:
        if self._selected:
            self._copy_record(self._selected)

    def _resend_selected(self) -> None:
        if not self._selected:
            return
        self._call("resend", self._selected)
        self.refresh()

    def _retry_record(self, rid: str) -> None:
        record = self._records_by_id().get(rid)
        if record is None or record.state not in ("fail", "local"):
            return
        self._call("resend", rid)
        self.refresh()

    def _remove_record(self, rid: str) -> None:
        self._call("remove", rid)
        if self._selected == rid:
            self._selected = None
        self._signature = None      # 强制下一次 refresh 重建
        self.refresh()

    def _clear(self) -> None:
        self._call("clear")
        self._selected = None
        self._signature = None
        self.refresh()

    def _call(self, method: str, *args) -> bool:
        func = getattr(self.source, method, None)
        if not callable(func):
            return False
        try:
            result = func(*args)
        except Exception:  # noqa: BLE001 - 数据源异常不该弹栈到界面
            log.warning("剪贴板动作 %s 失败", method, exc_info=True)
            return False
        return True if result is None else bool(result)

    def _show_row_menu(self, rid: str, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("复制到本机", lambda: self._copy_record(rid))
        menu.addAction("删除这条记录", lambda: self._remove_record(rid))
        if callable(getattr(self.source, "clear_system_clipboard", None)):
            menu.addSeparator()
            menu.addAction("清空本机剪贴板", lambda: self._call("clear_system_clipboard"))
        menu.exec(pos)

    # ------------------------------------------------------------------ 键盘
    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.backRequested.emit()
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            self._move(1 if key == Qt.Key_Down else -1)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._copy_selected()
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            if self._selected:
                self._remove_record(self._selected)
            return
        if event.matches(QKeySequence.Copy):
            self._copy_selected()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ 生命周期
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._signature = None          # 重新进页面一定拉一次
        self.refresh()
        self.setFocus(Qt.OtherFocusReason)
        self._timer.start()

    def hideEvent(self, event):  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()              # 页面不可见就不刷新，低频页面别在后台空转
