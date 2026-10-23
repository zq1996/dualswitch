"""配对电脑页（INTERACTIONS-v2 §12）：7 个状态、6 位码、倒计时、解除确认。

页面只做「显示 + 收集输入」，协议、密钥切换、落盘全在 `sync/engine.py` 里完成。
配对页是主窗口 QStackedWidget 里的一页，不新开窗口；页内也没有返回按钮 ——
离开这一页（切页 / 关窗）由 runner 调 `deactivate()`，配对码立即作废。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...sync.engine import parse_peer
from . import icons, theme
from .widgets import destroy, label, plate_pixmap

PIN_LEN = 6
SCAN_EMPTY_S = 10.0
PEER_REFRESH_S = 1.5


# --------------------------------------------------------------------------- #
# 小部件
# --------------------------------------------------------------------------- #
class Spinner(QWidget):
    """38×38 的转圈（800ms/圈，与设计稿一致）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 38)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)          # 18° × 40ms ≈ 800ms/圈
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._angle = (self._angle + 18) % 360
        self.update()

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(3, 3, -3, -3)
        p.setPen(QPen(theme.qcolor(t.accent_soft), 3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect)
        p.setPen(QPen(theme.qcolor(t.accent), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, int(-self._angle * 16), int(-90 * 16))
        p.end()


class _PinEdit(QLineEdit):
    def __init__(self, group: "PinGroup", index: int, parent=None):
        super().__init__(parent)
        self._group = group
        self._index = index
        self.setObjectName("pinInput")
        self.setFixedSize(42, 52)
        self.setMaxLength(1)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(theme.qfont(22, 700, mono=True))
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d?"), self))
        self.textChanged.connect(lambda _t: group.changed.emit())

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Backspace, Qt.Key_Delete) and not self.text() and self._index > 0:
            prev = self._group.edit(self._index - 1)
            prev.setText("")
            prev.setFocus()
            return
        text = event.text()
        if text and len(text) == 1 and text.isdigit():
            self.setText(text)
            self._group.advance(self._index)
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):  # noqa: N802
        digits = "".join(ch for ch in (source.text() or "") if ch.isdigit())
        if digits:
            self._group.fill(self._index, digits)
        else:
            super().insertFromMimeData(source)


class PinGroup(QWidget):
    """6 个独立输入框：自动跳格、退格回退、整段粘贴。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addStretch(1)
        self._edits: list[_PinEdit] = []
        for i in range(PIN_LEN):
            edit = _PinEdit(self, i)
            lay.addWidget(edit)
            self._edits.append(edit)
        lay.addStretch(1)
        self.setFixedHeight(52)

    def edit(self, index: int) -> _PinEdit:
        return self._edits[max(0, min(PIN_LEN - 1, index))]

    def advance(self, index: int) -> None:
        if index < PIN_LEN - 1:
            self._edits[index + 1].setFocus()

    def fill(self, start: int, digits: str) -> None:
        digits = "".join(ch for ch in digits if ch.isdigit())
        pos = start
        for ch in digits:
            if pos >= PIN_LEN:
                break
            self._edits[pos].setText(ch)
            pos += 1
        self._edits[min(PIN_LEN - 1, pos)].setFocus()

    def pin(self) -> str:
        return "".join(e.text() for e in self._edits)

    def clear(self) -> None:
        for e in self._edits:
            e.setText("")

    def set_editable(self, editable: bool) -> None:
        for e in self._edits:
            e.setEnabled(editable)

    def focus_first(self) -> None:
        self._edits[0].setFocus()


def _card(parent: QWidget | None = None) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(parent)
    card.setObjectName("pairCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(15, 15, 15, 15)
    lay.setSpacing(10)
    return card, lay


def _small(text: str, name: str = "pairHint") -> QLabel:
    lb = label(text, name)
    lb.setWordWrap(True)
    return lb


def _pill(text: str, name: str) -> QLabel:
    lb = label(text, name)
    lb.setAlignment(Qt.AlignCenter)
    return lb


def _set_style_name(widget: QWidget, name: str) -> None:
    widget.setObjectName(name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _fmt_left(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _ago(ts: float) -> str:
    if not ts:
        return ""
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return f"{delta} 秒前"
    if delta < 3600:
        return f"{delta // 60} 分钟前"
    return f"{delta // 3600} 小时前"


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
class PairingPage(QWidget):
    toastRequested = Signal(str)
    pairedChanged = Signal(bool)

    def __init__(self, engine=None, parent=None, default_port: int = 47823):  # noqa: ANN001
        super().__init__(parent)
        self._engine = engine
        self._default_port = int(default_port)
        self._active = False
        self._state = "discover"
        self._selected: dict[str, Any] = {}
        self._pending_peer: dict[str, Any] | None = None
        self._last_pin = ""
        self._pair_started = 0.0
        self._worker: threading.Thread | None = None
        self._worker_result: dict[str, Any] | None = None
        self._peer_rows: dict[str, dict[str, Any]] = {}
        self._peer_row_order: list[str] = []
        self._scan_started = time.monotonic()
        self._last_peer_refresh = 0.0
        self._repairing = False
        self._built = False
        self._replace_box: QFrame | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        outer.addWidget(scroll, 1)

        host = QWidget()
        self._lay = QVBoxLayout(host)
        self._lay.setContentsMargins(20, 18, 20, 24)
        self._lay.setSpacing(12)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)
        title = label("配对电脑")
        title.setFont(theme.qfont(17, 680))
        title_col.addWidget(title)
        title_col.addWidget(_small("配对一次，之后同一个局域网内自动同步剪贴板", "pairHint"))
        head.addLayout(title_col, 1)
        self.top_pill = _pill("未配对", "pairPillIdle")
        head.addWidget(self.top_pill, 0, Qt.AlignTop)
        self._lay.addLayout(head)

        from PySide6.QtWidgets import QStackedWidget

        self.stack = QStackedWidget()
        self._states: dict[str, int] = {}
        self._build_discover()
        self._build_show_code()
        self._build_enter_code()
        self._build_connecting()
        self._build_success()
        self._build_paired()
        self._build_failed()
        self._lay.addWidget(self.stack, 1)
        self._lay.addStretch(1)
        scroll.setWidget(host)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ------------------------------------------------------------------ 状态页
    def _add_state(self, name: str, widget: QWidget) -> None:
        self._states[name] = self.stack.addWidget(widget)

    def _build_discover(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.mismatch_bar = QFrame()
        self.mismatch_bar.setObjectName("pairError")
        mb = QHBoxLayout(self.mismatch_bar)
        mb.setContentsMargins(11, 9, 11, 9)
        mb.setSpacing(7)
        self.mismatch_text = label("配对信息不一致，需要重新配对", "pairErrorText")
        self.mismatch_text.setWordWrap(True)
        mb.addWidget(self.mismatch_text, 1)
        self.mismatch_bar.hide()
        lay.addWidget(self.mismatch_bar)

        self.repair_bar = QFrame()
        self.repair_bar.setObjectName("pairNote")
        rb = QHBoxLayout(self.repair_bar)
        rb.setContentsMargins(11, 9, 11, 9)
        self.repair_text = label("", "pairNoteText")
        self.repair_text.setWordWrap(True)
        rb.addWidget(self.repair_text, 1)
        self.repair_bar.hide()
        lay.addWidget(self.repair_bar)

        card, card_lay = _card()
        hd = QHBoxLayout()
        hd.setContentsMargins(0, 0, 0, 0)
        hd.setSpacing(8)
        hd.addWidget(label("附近的电脑", "pairCardTitle"))
        self.scan_hint = _small("正在搜索…", "pairHint")
        hd.addWidget(self.scan_hint, 0, Qt.AlignRight)
        hd.addStretch(1)
        btn_refresh = QPushButton("↻")
        btn_refresh.setObjectName("iconBtn")
        btn_refresh.setFixedSize(26, 26)
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.setToolTip("重新搜索")
        btn_refresh.setFont(theme.qfont(13, 650))
        btn_refresh.clicked.connect(self._research)
        hd.addWidget(btn_refresh, 0)
        card_lay.addLayout(hd)

        self.peer_list = QWidget()
        self.peer_list_lay = QVBoxLayout(self.peer_list)
        self.peer_list_lay.setContentsMargins(0, 0, 0, 0)
        self.peer_list_lay.setSpacing(8)
        card_lay.addWidget(self.peer_list)

        self.empty_box = QWidget()
        eb = QVBoxLayout(self.empty_box)
        eb.setContentsMargins(4, 8, 4, 4)
        eb.setSpacing(6)
        t = label("没有发现附近的电脑", "pairCardTitle")
        t.setAlignment(Qt.AlignCenter)
        eb.addWidget(t)
        for text in (
            "两台电脑连在同一个 Wi-Fi / 局域网",
            "两边都打开「启用跨端剪贴板」",
            "防火墙允许本应用访问局域网",
        ):
            item = _small(f"· {text}", "pairHelp")
            item.setAlignment(Qt.AlignCenter)
            eb.addWidget(item)
        erow = QHBoxLayout()
        erow.setContentsMargins(0, 6, 0, 0)
        erow.addStretch(1)
        b1 = QPushButton("重新搜索")
        b1.setObjectName("peerBtnGhost")
        b1.setFixedHeight(28)
        b1.setCursor(Qt.PointingHandCursor)
        b1.clicked.connect(self._research)
        b2 = QPushButton("手动输入 IP")
        b2.setObjectName("peerBtnGhost")
        b2.setFixedHeight(28)
        b2.setCursor(Qt.PointingHandCursor)
        b2.clicked.connect(self._toggle_manual_ip)
        erow.addWidget(b1)
        erow.addWidget(b2)
        erow.addStretch(1)
        eb.addLayout(erow)
        self.empty_box.hide()
        card_lay.addWidget(self.empty_box)

        card_lay.addWidget(_small("两台电脑需要连在同一个 Wi-Fi / 局域网，并都打开「启用跨端剪贴板」。", "pairHelp"))
        lay.addWidget(card)

        links = QVBoxLayout()
        links.setContentsMargins(2, 0, 2, 0)
        links.setSpacing(2)
        b_show = QPushButton("换一种方式：在这台 Mac 上显示配对码")
        b_show.setObjectName("linkBtn")
        b_show.setCursor(Qt.PointingHandCursor)
        b_show.clicked.connect(self._start_show_code)
        links.addWidget(b_show)
        b_manual = QPushButton("找不到另一台？手动输入 IP 地址…")
        b_manual.setObjectName("linkBtn")
        b_manual.setCursor(Qt.PointingHandCursor)
        b_manual.clicked.connect(self._toggle_manual_ip)
        links.addWidget(b_manual)
        lay.addLayout(links)

        self.manual_row = QWidget()
        mr = QHBoxLayout(self.manual_row)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.setSpacing(8)
        self.edit_ip = QLineEdit()
        self.edit_ip.setObjectName("pairIp")
        self.edit_ip.setFixedHeight(32)
        self.edit_ip.setPlaceholderText("例如 192.168.1.31 或 192.168.1.31:47823")
        self.edit_ip.returnPressed.connect(self._manual_connect)
        b_connect = QPushButton("连接")
        b_connect.setObjectName("peerBtn")
        b_connect.setFixedHeight(32)
        b_connect.setCursor(Qt.PointingHandCursor)
        b_connect.clicked.connect(self._manual_connect)
        mr.addWidget(self.edit_ip, 1)
        mr.addWidget(b_connect, 0)
        self.manual_row.hide()
        lay.addWidget(self.manual_row)

        note = QFrame()
        note.setObjectName("pairNote")
        nl = QHBoxLayout(note)
        nl.setContentsMargins(11, 9, 11, 9)
        nl.addWidget(_small(
            "企业 Wi-Fi 常会拦截广播，这时用手动 IP。首次连接如果失败，检查防火墙是否允许本应用访问局域网。",
            "pairNoteText",
        ))
        lay.addWidget(note)
        lay.addStretch(1)          # 空白留在下方，note 不再被拉成一大块背景

        self._add_state("discover", page)

    def _build_show_code(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        card, cl = _card()
        cl.setAlignment(Qt.AlignHCenter)

        kicker = _small("在这台 Mac 上显示配对码", "pairHint")
        kicker.setAlignment(Qt.AlignCenter)
        cl.addWidget(kicker)

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(9)
        row.addStretch(1)
        self._pin_blocks: list[QLabel] = []
        for _ in range(PIN_LEN):
            block = QLabel("—")
            block.setObjectName("pinBlock")
            block.setFixedSize(46, 56)
            block.setAlignment(Qt.AlignCenter)
            block.setFont(theme.qfont(25, 700, mono=True))
            row.addWidget(block)
            self._pin_blocks.append(block)
        row.addStretch(1)
        cl.addLayout(row)

        self.pin_count = _small("5:00 后失效 · 只能使用一次", "pinCount")
        self.pin_count.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.pin_count)

        steps = QVBoxLayout()
        steps.setContentsMargins(8, 8, 8, 0)
        steps.setSpacing(3)
        first = _small("1. 在另一台电脑上点左侧栏「配对电脑」", "pairSteps")
        second = _small("2. 在「附近的电脑」里选择这台电脑", "pairSteps")
        third = _small("3. 输入上面这 6 位配对码", "pairSteps")
        for s in (first, second, third):
            steps.addWidget(s)
        cl.addLayout(steps)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 8, 0, 0)
        actions.setSpacing(9)
        actions.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("ghostBtn")
        b_cancel.setFixedHeight(36)
        b_cancel.setCursor(Qt.PointingHandCursor)
        b_cancel.clicked.connect(self._cancel_to_discover)
        b_new = QPushButton("换一个配对码")
        b_new.setObjectName("ghostBtn")
        b_new.setFixedHeight(36)
        b_new.setCursor(Qt.PointingHandCursor)
        b_new.clicked.connect(self._renew_code)
        actions.addWidget(b_cancel)
        actions.addWidget(b_new)
        actions.addStretch(1)
        cl.addLayout(actions)

        lay.addWidget(card)
        lay.addStretch(1)
        self._add_state("showcode", page)

    def _build_enter_code(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        card, cl = _card()

        self.selected_row = QFrame()
        self.selected_row.setObjectName("pairSelected")
        sr = QHBoxLayout(self.selected_row)
        sr.setContentsMargins(11, 10, 11, 10)
        sr.setSpacing(11)
        self.selected_logo = QLabel()
        self.selected_logo.setFixedSize(36, 36)
        sr.addWidget(self.selected_logo, 0)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        self.selected_name = label("", "peerName")
        self.selected_meta = label("", "peerMeta")
        col.addWidget(self.selected_name)
        col.addWidget(self.selected_meta)
        sr.addLayout(col, 1)
        b_change = QPushButton("换一台")
        b_change.setObjectName("peerBtnGhost")
        b_change.setFixedHeight(28)
        b_change.setCursor(Qt.PointingHandCursor)
        b_change.clicked.connect(self._change_peer)
        sr.addWidget(b_change, 0)
        cl.addWidget(self.selected_row)

        kicker = _small("输入另一台电脑上显示的 6 位配对码", "pairHint")
        kicker.setAlignment(Qt.AlignCenter)
        cl.addWidget(kicker)

        self.pin_group = PinGroup()
        self.pin_group.changed.connect(self._on_pin_changed)
        cl.addWidget(self.pin_group)

        self.pin_hint = _small("配对码在另一台电脑的「配对电脑」页显示，5 分钟内有效 · 还可以试 5 次", "pairHint")
        self.pin_hint.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.pin_hint)

        self.pin_error = QFrame()
        self.pin_error.setObjectName("pairError")
        pe = QHBoxLayout(self.pin_error)
        pe.setContentsMargins(11, 9, 11, 9)
        self.pin_error_text = label("", "pairErrorText")
        self.pin_error_text.setWordWrap(True)
        pe.addWidget(self.pin_error_text, 1)
        self.pin_error.hide()
        cl.addWidget(self.pin_error)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(9)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("ghostBtn")
        b_cancel.setFixedHeight(36)
        b_cancel.setCursor(Qt.PointingHandCursor)
        b_cancel.clicked.connect(self._cancel_to_discover)
        self.btn_pair = QPushButton("配对")
        self.btn_pair.setObjectName("primary")
        self.btn_pair.setFixedHeight(36)
        self.btn_pair.setCursor(Qt.PointingHandCursor)
        self.btn_pair.setEnabled(False)
        self.btn_pair.clicked.connect(self._do_pair)
        actions.addWidget(b_cancel)
        actions.addWidget(self.btn_pair, 1)
        cl.addLayout(actions)

        lay.addWidget(card)
        lay.addStretch(1)
        self._add_state("entercode", page)

    def _build_connecting(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        card, cl = _card()
        cl.setAlignment(Qt.AlignHCenter)
        self.spinner = Spinner()
        cl.addWidget(self.spinner, 0, Qt.AlignHCenter)
        self.connecting_title = label("正在配对…", "pairCenterTitle")
        self.connecting_title.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.connecting_title)
        self.connecting_sub = _small("正在建立加密通道并交换密钥，请稍候", "pairCenterSub")
        self.connecting_sub.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.connecting_sub)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.addStretch(1)
        b_cancel = QPushButton("取消")
        b_cancel.setObjectName("ghostBtn")
        b_cancel.setFixedHeight(36)
        b_cancel.setCursor(Qt.PointingHandCursor)
        b_cancel.clicked.connect(self._cancel_to_discover)
        row.addWidget(b_cancel)
        row.addStretch(1)
        cl.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        self._add_state("connecting", page)

    def _build_success(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        card, cl = _card()
        cl.setAlignment(Qt.AlignHCenter)
        ring = _ring_label("check", ok=True)
        cl.addWidget(ring, 0, Qt.AlignHCenter)
        self.success_title = label("配对成功", "pairCenterTitle")
        self.success_title.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.success_title)
        self.success_sub = _small(
            "现在两台电脑之间复制的文字和图片，都会在切屏时自动同步。", "pairCenterSub"
        )
        self.success_sub.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.success_sub)
        self.success_facts = _facts_grid([("对端设备 ID", ""), ("局域网地址", "")])
        cl.addWidget(self.success_facts[0])
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(9)
        b_test = QPushButton("复制测试文本")
        b_test.setObjectName("ghostBtn")
        b_test.setFixedHeight(36)
        b_test.setCursor(Qt.PointingHandCursor)
        b_test.clicked.connect(self._copy_test_text)
        b_done = QPushButton("完成")
        b_done.setObjectName("primary")
        b_done.setFixedHeight(36)
        b_done.setCursor(Qt.PointingHandCursor)
        b_done.clicked.connect(lambda: self._show_paired())
        row.addWidget(b_test)
        row.addWidget(b_done, 1)
        cl.addLayout(row)
        lay.addWidget(card)
        lay.addStretch(1)
        self._add_state("success", page)

    def _build_paired(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        card, cl = _card()

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        self.paired_logo = QLabel()
        self.paired_logo.setFixedSize(36, 36)
        top.addWidget(self.paired_logo, 0)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        self.paired_name = label("", "peerName")
        self.paired_meta = label("", "peerMeta")
        col.addWidget(self.paired_name)
        col.addWidget(self.paired_meta)
        top.addLayout(col, 1)
        self.paired_pill = _pill("已配对", "pairPillOk")
        top.addWidget(self.paired_pill, 0)
        cl.addLayout(top)

        self.paired_facts = _facts_grid(
            [("设备 ID", ""), ("地址", ""), ("配对时间", ""), ("连接方式", "局域网 · 已加密")]
        )
        cl.addWidget(self.paired_facts[0])

        self.legacy_note = QFrame()
        self.legacy_note.setObjectName("pairNote")
        ln = QHBoxLayout(self.legacy_note)
        ln.setContentsMargins(11, 9, 11, 9)
        ln.addWidget(_small(
            "当前是旧版配置的配对（没有设备绑定）。重新配对后会记住这台设备。", "pairNoteText"
        ))
        self.legacy_note.hide()
        cl.addWidget(self.legacy_note)

        note = QFrame()
        note.setObjectName("pairNote")
        nl = QHBoxLayout(note)
        nl.setContentsMargins(11, 9, 11, 9)
        nl.addWidget(_small(
            "配对信息保存在本机；换 Wi-Fi、重启或 IP 变化都不需要重新配对。", "pairNoteText"
        ))
        cl.addWidget(note)

        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(9)
        b_repair = QPushButton("重新配对")
        b_repair.setObjectName("ghostBtn")
        b_repair.setFixedHeight(34)
        b_repair.setCursor(Qt.PointingHandCursor)
        b_repair.clicked.connect(self._begin_repair)
        b_unpair = QPushButton("解除配对")
        b_unpair.setObjectName("dangerBtn")
        b_unpair.setFixedHeight(34)
        b_unpair.setCursor(Qt.PointingHandCursor)
        b_unpair.clicked.connect(self._ask_unpair)
        row.addWidget(b_repair)
        row.addWidget(b_unpair)
        cl.addLayout(row)

        self.confirm_box = QFrame()
        self.confirm_box.setObjectName("pairConfirm")
        cb = QVBoxLayout(self.confirm_box)
        cb.setContentsMargins(14, 12, 14, 12)
        cb.setSpacing(4)
        self.confirm_title = label("", "pairConfirmTitle")
        cb.addWidget(self.confirm_title)
        eb = _small(
            "解除后本机剪贴板只在本机记录，不再同步到这台电脑；历史记录会保留，另一台电脑不会收到通知。",
            "pairConfirmText",
        )
        cb.addWidget(eb)
        crow = QHBoxLayout()
        crow.setContentsMargins(0, 6, 0, 0)
        crow.setSpacing(9)
        b_no = QPushButton("取消")
        b_no.setObjectName("ghostBtn")
        b_no.setFixedHeight(30)
        b_no.setCursor(Qt.PointingHandCursor)
        b_no.clicked.connect(self._hide_unpair)
        b_yes = QPushButton("解除配对")
        b_yes.setObjectName("dangerBtn")
        b_yes.setFixedHeight(30)
        b_yes.setCursor(Qt.PointingHandCursor)
        b_yes.clicked.connect(self._do_unpair)
        crow.addWidget(b_no)
        crow.addWidget(b_yes, 1)
        cb.addLayout(crow)
        self.confirm_box.hide()
        cl.addWidget(self.confirm_box)

        lay.addWidget(card)

        tip, tl = _card()
        tl.addWidget(label("只在需要时操作", "pairCardTitle"))
        tl.addWidget(_small(
            "重新配对会先断开现有连接，再走一次 6 位码流程；两台电脑都要在 5 分钟内完成。", "pairHelp"
        ))
        lay.addWidget(tip)
        lay.addStretch(1)
        self._add_state("paired", page)

    def _build_failed(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        card, cl = _card()
        cl.setAlignment(Qt.AlignHCenter)
        cl.addWidget(_ring_label("x", ok=False), 0, Qt.AlignHCenter)
        self.failed_title = label("没能连上对端", "pairCenterTitle")
        self.failed_title.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.failed_title)
        self.failed_sub = _small("请确认两台电脑在同一个局域网，且没有被防火墙拦截。", "pairCenterSub")
        self.failed_sub.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.failed_sub)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(9)
        b_back = QPushButton("返回设备列表")
        b_back.setObjectName("ghostBtn")
        b_back.setFixedHeight(36)
        b_back.setCursor(Qt.PointingHandCursor)
        b_back.clicked.connect(self._cancel_to_discover)
        self.btn_retry = QPushButton("重试")
        self.btn_retry.setObjectName("primary")
        self.btn_retry.setFixedHeight(36)
        self.btn_retry.setCursor(Qt.PointingHandCursor)
        self.btn_retry.clicked.connect(self._retry_pair)
        row.addWidget(b_back)
        row.addWidget(self.btn_retry, 1)
        cl.addLayout(row)
        lay.addWidget(card)

        tip, tl = _card()
        tl.addWidget(_small(
            "仍然失败？在另一台电脑上打开「配对电脑」并显示配对码，然后在这台电脑用「输入配对码」重试；也可以手动输入 IP。",
            "pairHelp",
        ))
        lay.addWidget(tip)
        lay.addStretch(1)
        self._add_state("failed", page)

    # ------------------------------------------------------------------ 入口
    def activate(self) -> None:
        self._active = True
        st = self._status()
        if st.get("state") == "showing" and st.get("pin"):
            self._show_show_code(str(st["pin"]))
            return
        if st.get("paired") and st.get("outcome") != "failed":
            self._show_paired()
            return
        notice = ""
        if st.get("mismatch"):
            notice = "配对信息不一致，需要重新配对"
        self._show_discover(notice=notice)

    def deactivate(self) -> None:
        """离开配对页 = 取消：码立即失效，不落盘。"""
        self._active = False
        if self._engine is not None and self._state in ("showcode", "entercode", "connecting"):
            try:
                self._engine.cancel_pairing()
            except Exception:  # noqa: BLE001
                pass
        if self._state in ("showcode", "entercode", "connecting"):
            self._state = "discover"

    def status_text(self) -> str:
        st = self._status()
        if st.get("mismatch"):
            return "需重新配对"
        return "已配对" if st.get("paired") else "未配对"

    def status_tone(self) -> str:
        st = self._status()
        if st.get("mismatch"):
            return "bad"
        return "ok" if st.get("paired") else "idle"

    # ------------------------------------------------------------------ 状态切换
    def _set_state(self, name: str) -> None:
        self._state = name
        if name in self._states:
            self.stack.setCurrentIndex(self._states[name])
        if name == "connecting":
            self.spinner.start()
        elif hasattr(self, "spinner"):
            self.spinner.stop()
        self._update_pill()

    def _update_pill(self) -> None:
        st = self._status()
        if self._state == "failed":
            text, name = "配对失败", "pairPillBad"
        elif self._state == "success":
            text, name = "配对成功", "pairPillOk"
        elif self._state in ("showcode", "entercode", "connecting"):
            text, name = "等待配对", "pairPillIdle"
        elif st.get("mismatch"):
            text, name = "需重新配对", "pairPillBad"
        elif st.get("paired"):
            text, name = "已配对", "pairPillOk"
        else:
            text, name = "未配对", "pairPillIdle"
        self.top_pill.setText(text)
        _set_style_name(self.top_pill, name)

    def _show_discover(self, notice: str = "") -> None:
        self._selected = {}
        self._pending_peer = None
        self._hide_replace_confirm()
        self._scan_started = time.monotonic()
        self._last_peer_refresh = 0.0
        st = self._status()
        self.mismatch_bar.setVisible(bool(st.get("mismatch")))
        if st.get("mismatch"):
            self.mismatch_text.setText("配对信息不一致，需要重新配对")
        self.repair_bar.setVisible(bool(notice) and not st.get("mismatch"))
        self.repair_text.setText(notice)
        if st.get("mismatch"):
            self.repair_bar.hide()
        self._set_state("discover")
        self._refresh_peers(force=True)

    def _show_show_code(self, pin: str) -> None:
        self._selected = {}
        self._pair_started = time.monotonic()
        self._set_state("showcode")
        self._render_pin(pin)
        st = self._status()
        left = float(st.get("expires_in") or 300.0)
        self.pin_count.setText(f"{_fmt_left(left)} 后失效 · 只能使用一次")

    def _show_enter(self) -> None:
        self.pin_group.clear()
        self.pin_group.set_editable(True)
        self.pin_group.focus_first()
        self.pin_error.hide()
        self.btn_pair.setEnabled(False)
        self._update_attempts()
        self._set_state("entercode")

    def _show_connecting(self) -> None:
        name = self._selected.get("host") or "对端"
        self.connecting_title.setText(f"正在和 {name} 配对…")
        self.connecting_sub.setText("正在建立加密通道并交换密钥，请稍候")
        self._pair_started = time.monotonic()
        self._worker_result = None
        self._set_state("connecting")

    def _show_success(self, name: str, device: str, addr: str) -> None:
        self.success_title.setText(f"已和 {name or '对端'} 配对")
        self.success_facts[1]["对端设备 ID"].setText(device or "—")
        self.success_facts[1]["局域网地址"].setText(addr or "—")
        self._set_state("success")
        self.pairedChanged.emit(True)

    def _show_paired(self) -> None:
        st = self._status()
        name = st.get("peer_name") or st.get("peer_device") or "另一台电脑"
        self.paired_name.setText(name)
        device = str(st.get("peer_device") or "")
        self.paired_logo.setPixmap(
            plate_pixmap("windows" if device.startswith("windows") else "apple", 36)
        )
        conn = "已连接" if st.get("connected") else "对端离线，等待重新连接"
        self.paired_meta.setText(conn)
        self.paired_facts[1]["设备 ID"].setText(st.get("peer_device") or "—")
        self.paired_facts[1]["地址"].setText(st.get("addr") or "—")
        self.paired_facts[1]["配对时间"].setText(_fmt_time(float(st.get("paired_at") or 0)))
        self.legacy_note.setVisible(bool(st.get("legacy")))
        self.confirm_box.hide()
        self._set_state("paired")
        self._update_pill()

    def _show_failed(self, title: str, sub: str) -> None:
        self.failed_title.setText(title)
        self.failed_sub.setText(sub)
        self.btn_retry.setEnabled(bool(self._last_pin))
        self._set_state("failed")

    # ------------------------------------------------------------------ 发现
    def _research(self) -> None:
        self._scan_started = time.monotonic()
        self._last_peer_refresh = 0.0
        if self._engine is not None:
            try:
                self._engine.refresh_discovery()
            except Exception:  # noqa: BLE001
                pass
        self._refresh_peers(force=True)

    def _toggle_manual_ip(self) -> None:
        show = not self.manual_row.isVisible()
        self.manual_row.setVisible(show)
        if show:
            self.edit_ip.setFocus()

    def _manual_connect(self) -> None:
        text = self.edit_ip.text().strip()
        peer = parse_peer(text, self._default_port)
        if peer is None:
            self.toastRequested.emit("请输入 IP 地址，例如 192.168.1.31")
            return
        info = {}
        for item in self._peers():
            if item.get("ip") == peer[0]:
                info = dict(item)
                break
        info.update({
            "ip": peer[0],
            "port": peer[1],
            "host": info.get("host") or peer[0],
        })
        self._select_peer(info)

    def _peers(self) -> list[dict[str, Any]]:
        if self._engine is None:
            return []
        try:
            return list(self._engine.discovered_peers() or [])
        except Exception:  # noqa: BLE001
            return []

    def _refresh_peers(self, force: bool = False) -> None:
        if self._state != "discover":
            return
        now = time.monotonic()
        if not force and now - self._last_peer_refresh < PEER_REFRESH_S:
            return
        self._last_peer_refresh = now
        peers = self._peers()
        ids = [str(p.get("device") or "") for p in peers if p.get("device")]
        if ids != self._peer_row_order:
            self._rebuild_peer_rows(peers)
        else:
            for p in peers:
                self._update_peer_row(p)
        has_peers = bool(peers)
        waited = now - self._scan_started
        empty = (not has_peers) and waited >= SCAN_EMPTY_S
        self.peer_list.setVisible(not empty)
        self.empty_box.setVisible(empty)
        if has_peers:
            self.scan_hint.setText(f"找到 {len(peers)} 台")
        elif empty:
            self.scan_hint.setText("没有发现")
        else:
            self.scan_hint.setText("正在搜索…")

    def _rebuild_peer_rows(self, peers: list[dict[str, Any]]) -> None:
        while self.peer_list_lay.count():
            item = self.peer_list_lay.takeAt(0)
            destroy(item.widget())
        self._peer_rows.clear()
        self._peer_row_order = []
        for p in peers:
            device = str(p.get("device") or "")
            if not device:
                continue
            row, widgets = self._peer_row_widget(p)
            self.peer_list_lay.addWidget(row)
            self._peer_rows[device] = widgets
            self._peer_row_order.append(device)
            # 建行时按钮文案是构造默认值「配对」，正确文案只在 _update_peer_row 里算。
            # 不在这里补一次，已配对 / 对方正在显示配对码的设备会先显示错文案约 1.5 秒
            # （ISSUE-008，Windows 端 BUG-002 提的同一处）。
            self._update_peer_row(p)

    def _peer_row_widget(self, peer: dict[str, Any]) -> tuple[QFrame, dict[str, Any]]:
        row = QFrame()
        row.setObjectName("peerRow")
        row.setFixedHeight(58)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        logo = QLabel()
        logo.setFixedSize(36, 36)
        logo.setPixmap(plate_pixmap("windows" if peer.get("plat") == "windows" else "apple", 36))
        lay.addWidget(logo, 0)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        name = label(str(peer.get("host") or peer.get("ip") or "另一台电脑"), "peerName")
        tag = label(_platform_name(peer), "peerTag")
        name_row.addWidget(name, 0)
        name_row.addWidget(tag, 0)
        name_row.addStretch(1)
        col.addLayout(name_row)
        meta = label(_peer_meta(peer), "peerMeta")
        col.addWidget(meta)
        lay.addLayout(col, 1)

        btn = QPushButton("配对")
        btn.setObjectName("peerBtn")
        btn.setFixedHeight(28)
        btn.setCursor(Qt.PointingHandCursor)
        device = str(peer.get("device") or "")
        btn.clicked.connect(lambda _=False, d=device: self._pick_peer_by_id(d))
        lay.addWidget(btn, 0)

        return row, {"row": row, "logo": logo, "name": name, "tag": tag, "meta": meta, "btn": btn}

    def _update_peer_row(self, peer: dict[str, Any]) -> None:
        device = str(peer.get("device") or "")
        widgets = self._peer_rows.get(device)
        if widgets is None:
            return
        widgets["name"].setText(str(peer.get("host") or peer.get("ip") or "另一台电脑"))
        widgets["tag"].setText(_platform_name(peer))
        widgets["meta"].setText(_peer_meta(peer))
        text, ghost = self._peer_button_text(peer)
        widgets["btn"].setText(text)
        _set_style_name(widgets["btn"], "peerBtnGhost" if ghost else "peerBtn")

    def _peer_button_text(self, peer: dict[str, Any]) -> tuple[str, bool]:
        st = self._status()
        device = str(peer.get("device") or "")
        if st.get("paired") and device and device == st.get("peer_device"):
            return ("重新配对" if self._repairing else "管理"), True
        if peer.get("paired"):
            return "替换配对", True
        return "配对", False

    def _pick_peer_by_id(self, device: str) -> None:
        peer = next((p for p in self._peers() if str(p.get("device")) == device), None)
        if peer is None:
            return
        self._pick_peer(peer)

    def _pick_peer(self, peer: dict[str, Any]) -> None:
        st = self._status()
        device = str(peer.get("device") or "")
        if st.get("paired") and device and device == st.get("peer_device"):
            if self._repairing:
                self._select_peer(peer)      # 重新配对同一台：不用再确认「替换」
            else:
                self._show_paired()
            return
        need_confirm = bool(st.get("paired") or peer.get("paired"))
        if need_confirm:
            self._ask_replace(peer)
            return
        self._select_peer(peer)

    def _select_peer(self, peer: dict[str, Any]) -> None:
        self._selected = dict(peer)
        self._repairing = False
        self._pending_peer = None
        self._hide_replace_confirm()
        self.selected_logo.setPixmap(
            plate_pixmap("windows" if peer.get("plat") == "windows" else "apple", 36)
        )
        self.selected_name.setText(str(peer.get("host") or peer.get("ip") or "另一台电脑"))
        self.selected_meta.setText(_peer_meta(peer, with_state=False))
        self._show_enter()
        if peer.get("pairing") is False:
            self.toastRequested.emit("请先在另一台电脑上点「显示配对码」")

    def _change_peer(self) -> None:
        self._cancel_to_discover()

    _replace_box: QFrame | None = None
    _replace_target: dict[str, Any] | None = None

    def _ask_replace(self, peer: dict[str, Any]) -> None:
        st = self._status()
        self._replace_target = dict(peer)
        self._pending_peer = dict(peer)
        if self._replace_box is None:
            self._replace_box = QFrame()
            self._replace_box.setObjectName("pairConfirm")
            lay = QVBoxLayout(self._replace_box)
            lay.setContentsMargins(14, 12, 14, 12)
            lay.setSpacing(4)
            self._replace_title = label("", "pairConfirmTitle")
            lay.addWidget(self._replace_title)
            row = QHBoxLayout()
            row.setContentsMargins(0, 6, 0, 0)
            row.setSpacing(9)
            b_no = QPushButton("取消")
            b_no.setObjectName("ghostBtn")
            b_no.setFixedHeight(30)
            b_no.setCursor(Qt.PointingHandCursor)
            b_no.clicked.connect(self._hide_replace_confirm)
            b_yes = QPushButton("继续配对")
            b_yes.setObjectName("primary")
            b_yes.setFixedHeight(30)
            b_yes.setCursor(Qt.PointingHandCursor)
            b_yes.clicked.connect(self._confirm_replace)
            row.addWidget(b_no)
            row.addWidget(b_yes, 1)
            lay.addLayout(row)
        old = st.get("peer_name") or st.get("peer_device") or "现有设备"
        name = str(peer.get("host") or peer.get("ip") or "这台电脑")
        if st.get("paired") and str(peer.get("device") or "") != st.get("peer_device"):
            self._replace_title.setText(f"配对其他电脑会替换现有的 {old}，继续？")
        else:
            self._replace_title.setText(f"{name} 已和别的电脑配对，继续会替换它现有的配对？")
        self._replace_box.show()
        if self.peer_list_lay.indexOf(self._replace_box) < 0:
            self.peer_list_lay.insertWidget(0, self._replace_box)

    def _hide_replace_confirm(self) -> None:
        if self._replace_box is not None:
            self._replace_box.hide()
        self._pending_peer = None

    def _confirm_replace(self) -> None:
        peer = self._pending_peer
        self._hide_replace_confirm()
        if peer is not None:
            self._select_peer(peer)

    # ------------------------------------------------------------------ 显示 / 输入码
    def _start_show_code(self) -> None:
        if self._engine is None:
            self.toastRequested.emit("剪贴板同步未启用，无法配对")
            return
        try:
            pin = self._engine.begin_pairing()
        except Exception as exc:  # noqa: BLE001
            self.toastRequested.emit(f"无法显示配对码：{exc}")
            return
        self._show_show_code(pin)

    def _renew_code(self) -> None:
        if self._engine is None:
            return
        try:
            self._engine.cancel_pairing()
            pin = self._engine.begin_pairing()
        except Exception as exc:  # noqa: BLE001
            self.toastRequested.emit(f"无法生成配对码：{exc}")
            return
        self._show_show_code(pin)
        self.toastRequested.emit("已生成新的配对码")

    def _render_pin(self, pin: str) -> None:
        for i, block in enumerate(self._pin_blocks):
            block.setText(pin[i] if i < len(pin) else "—")

    def _cancel_to_discover(self) -> None:
        if self._engine is not None:
            try:
                self._engine.cancel_pairing()
            except Exception:  # noqa: BLE001
                pass
        self._pair_started = 0.0
        self._show_discover()

    def _on_pin_changed(self) -> None:
        pin = self.pin_group.pin()
        self.btn_pair.setEnabled(len(pin) == PIN_LEN and pin.isdigit())
        if self.btn_pair.isEnabled():
            self.pin_error.hide()

    def _update_attempts(self) -> None:
        left = int(self._status().get("attempts_left", 5))
        self.pin_hint.setText(
            f"配对码在另一台电脑的「配对电脑」页显示，5 分钟内有效 · 还可以试 {left} 次"
        )

    def _do_pair(self) -> None:
        if self._engine is None:
            self.toastRequested.emit("剪贴板同步未启用，无法配对")
            return
        pin = self.pin_group.pin()
        if len(pin) != PIN_LEN or not pin.isdigit():
            return
        left = int(self._status().get("attempts_left", 5))
        if left <= 0:
            self._show_invalid_code()
            return
        peer = dict(self._selected)
        ip = str(peer.get("ip") or "")
        try:
            port = int(peer.get("port") or self._default_port)
        except (TypeError, ValueError):
            port = self._default_port
        if not ip:
            try:
                parsed = parse_peer(str(peer.get("host") or ""), self._default_port)
            except Exception:  # noqa: BLE001
                parsed = None
            if parsed is None:
                self.toastRequested.emit("请选择附近设备或手动输入 IP")
                return
            ip, port = parsed
        self._last_pin = pin
        self._show_connecting()
        self._worker = threading.Thread(
            target=self._pair_worker, args=((ip, port), pin), name="pair-with-peer", daemon=True
        )
        self._worker.start()

    def _pair_worker(self, addr, pin: str) -> None:  # noqa: ANN001
        try:
            result = self._engine.pair_with_peer(addr, pin)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "kind": "error", "reason": str(exc)}
        self._worker_result = result

    def _handle_result(self, result: dict[str, Any]) -> None:
        st = self._status()
        if result.get("ok"):
            name = result.get("peer_name") or st.get("peer_name") or self._selected.get("host") or ""
            device = result.get("peer_device") or st.get("peer_device") or ""
            addr = result.get("addr") or st.get("addr") or ""
            self._show_success(str(name), str(device), str(addr))
            self.toastRequested.emit(f"已和 {name or '对端'} 配对")
            return
        kind = str(result.get("kind") or "")
        left = int(st.get("attempts_left", 0))
        if kind in ("auth", "bad_pin") and left > 0:
            self._show_enter()
            self._show_code_error(left, result.get("reason") or "")
            return
        if kind == "max_attempts" or left <= 0:
            self._show_enter()
            self._show_invalid_code()
            return
        reason = str(result.get("reason") or "配对失败")
        name = self._selected.get("host") or "对端"
        addr = result.get("addr") or _addr(self._selected)
        self._show_failed(
            f"没能连上 {name}",
            f"{addr} 没有响应。\n请确认两台电脑在同一个局域网，且没有被防火墙拦截。",
        )
        self.toastRequested.emit(reason)

    def _show_code_error(self, left: int, reason: str) -> None:
        extra = f"\n还可以试 {left} 次；输错 5 次后需要让对方重新生成。"
        self.pin_error_text.setText(f"配对码不正确{extra}")
        self.pin_error.show()
        self.pin_group.set_editable(True)
        self.pin_group.clear()
        self.pin_group.focus_first()
        self._update_attempts()

    def _show_invalid_code(self) -> None:
        self.pin_error_text.setText("配对码已失效，请让对方重新显示配对码")
        self.pin_error.show()
        self.pin_group.clear()
        self.pin_group.set_editable(False)
        self.btn_pair.setEnabled(False)
        self.pin_hint.setText("请让对方重新显示配对码后，返回设备列表重新选择")

    def _retry_pair(self) -> None:
        if not self._last_pin or not self._selected:
            self._show_enter()
            return
        self.btn_pair.setEnabled(True)
        self.pin_group.set_editable(True)
        self.pin_group.fill(0, self._last_pin)
        self._do_pair()

    # ------------------------------------------------------------------ 已配对
    def _begin_repair(self) -> None:
        self._repairing = True
        self._show_discover(notice="重新配对会先断开现有连接，再走一次 6 位码流程；两台电脑都要在 5 分钟内完成。")
        self._refresh_peers(force=True)

    def _ask_unpair(self) -> None:
        st = self._status()
        name = st.get("peer_name") or st.get("peer_device") or "对端"
        self.confirm_title.setText(f"解除和 {name} 的配对？")
        self.confirm_box.show()

    def _hide_unpair(self) -> None:
        self.confirm_box.hide()

    def _do_unpair(self) -> None:
        if self._engine is not None:
            try:
                self._engine.unpair()
            except Exception as exc:  # noqa: BLE001
                self.toastRequested.emit(f"解除配对失败：{exc}")
                return
        self.pairedChanged.emit(False)
        self._show_discover()
        self.toastRequested.emit("已解除配对")

    def _copy_test_text(self) -> None:
        QApplication.clipboard().setText("配对测试 · 来自 Mac")
        self.toastRequested.emit("已复制测试文本，去另一台电脑粘贴试试")

    # ------------------------------------------------------------------ 轮询
    def _tick(self) -> None:
        if not self._active:
            return
        state = self._state
        if state == "discover":
            self._refresh_peers()
        elif state == "showcode":
            self._tick_show_code()
        elif state == "connecting":
            self._tick_connecting()
        elif state == "paired":
            self._show_paired()

    def _tick_show_code(self) -> None:
        st = self._status()
        if st.get("state") == "showing" and st.get("pin"):
            pin = str(st["pin"])
            if pin != "".join(b.text() for b in self._pin_blocks):
                self._render_pin(pin)
            self.pin_count.setText(
                f"{_fmt_left(float(st.get('expires_in') or 0))} 后失效 · 只能使用一次"
            )
            self._update_pill()
            return
        if st.get("outcome") == "ok":
            self._show_success(
                str(st.get("peer_name") or ""),
                str(st.get("peer_device") or ""),
                str(st.get("addr") or ""),
            )
            self.toastRequested.emit("配对成功")
            return
        if st.get("outcome") == "failed":
            self.toastRequested.emit(str(st.get("error") or "配对码已失效"))
            self._show_discover()
            return

    def _tick_connecting(self) -> None:
        st = self._status()
        result = self._worker_result
        if result is not None:
            self._worker_result = None
            self._handle_result(result)
            return
        if st.get("outcome") == "ok" and st.get("paired"):
            self._show_success(
                str(st.get("peer_name") or self._selected.get("host") or ""),
                str(st.get("peer_device") or ""),
                str(st.get("addr") or ""),
            )
            return
        if st.get("outcome") == "failed":
            left = int(st.get("attempts_left", 0))
            kind = str(st.get("error_kind") or "")
            if kind in ("auth", "bad_pin") and left > 0:
                self._show_enter()
                self._show_code_error(left, str(st.get("error") or ""))
            elif kind == "max_attempts" or left <= 0:
                self._show_enter()
                self._show_invalid_code()
            else:
                reason = str(st.get("error") or "连接超时")
                self._show_failed(
                    f"没能连上 {self._selected.get('host') or '对端'}",
                    f"{st.get('addr') or _addr(self._selected)} 没有响应。\n{reason}",
                )
            return
        if st.get("state") != "connecting" and self._pair_started and \
                time.monotonic() - self._pair_started > 16.0:
            self._show_failed(
                f"没能连上 {self._selected.get('host') or '对端'}",
                f"{_addr(self._selected)} 没有响应。请确认两台电脑在同一个局域网，且没有被防火墙拦截。",
            )

    # ------------------------------------------------------------------ 状态读取
    def _status(self) -> dict[str, Any]:
        if self._engine is None:
            return {
                "state": "idle", "outcome": "", "error": "", "error_kind": "",
                "pin": "", "expires_in": 0.0, "attempts_left": 5,
                "paired": False, "legacy": False, "mismatch": False,
                "connected": False, "peer_device": "", "peer_name": "",
                "paired_at": 0.0, "addr": "", "last_sync_at": 0.0,
            }
        try:
            return dict(self._engine.pairing_status() or {})
        except Exception:  # noqa: BLE001
            return {
                "state": "idle", "outcome": "", "error": "", "error_kind": "",
                "pin": "", "expires_in": 0.0, "attempts_left": 5,
                "paired": False, "legacy": False, "mismatch": False,
                "connected": False, "peer_device": "", "peer_name": "",
                "paired_at": 0.0, "addr": "", "last_sync_at": 0.0,
            }


def _platform_name(peer: dict[str, Any]) -> str:
    plat = str(peer.get("plat") or "")
    if plat == "windows":
        return "Windows"
    if plat in ("macos", "mac"):
        return "Mac"
    return "未知平台"


def _peer_meta(peer: dict[str, Any], with_state: bool = True) -> str:
    parts = []
    ip = str(peer.get("ip") or "")
    if ip:
        port = peer.get("port")
        parts.append(f"{ip}:{port}" if port else ip)
    plat = str(peer.get("plat") or "")
    if plat == "windows":
        parts.append("Windows")
    elif plat in ("macos", "mac"):
        parts.append("macOS")
    if with_state:
        if peer.get("paired"):
            parts.append("已与别的电脑配对")
        elif peer.get("pairing"):
            parts.append("正在等待配对")
        else:
            parts.append("未配对")
    return " · ".join(parts) or "—"


def _addr(peer: dict[str, Any]) -> str:
    ip = str(peer.get("ip") or "")
    port = peer.get("port")
    if not ip:
        return str(peer.get("host") or "—")
    return f"{ip}:{port}" if port else ip


def _facts_grid(items: list[tuple[str, str]]) -> tuple[QWidget, dict[str, QLabel]]:
    """2 列信息栅格；返回 (容器, {键: 值标签})。"""
    grid = QFrame()
    grid.setObjectName("pairFacts")
    from PySide6.QtWidgets import QGridLayout

    lay = QGridLayout(grid)
    lay.setContentsMargins(1, 1, 1, 1)
    lay.setSpacing(1)
    values: dict[str, QLabel] = {}
    for index, (key, value) in enumerate(items):
        cell = QFrame()
        cell.setObjectName("pairFact")
        cl = QVBoxLayout(cell)
        cl.setContentsMargins(11, 9, 11, 9)
        cl.setSpacing(3)
        cl.addWidget(label(key, "pairFactK"))
        val = label(value or "—", "pairFactV")
        val.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        cl.addWidget(val)
        lay.addWidget(cell, index // 2, index % 2)
        values[key] = val
    return grid, values


def _ring_label(glyph: str, ok: bool = True) -> QLabel:
    ring = QLabel()
    ring.setObjectName("pairRingOk" if ok else "pairRingBad")
    ring.setFixedSize(56, 56)
    ring.setAlignment(Qt.AlignCenter)
    color = theme.tokens().ok if ok else theme.tokens().danger
    ring.setPixmap(icons.svg_pixmap(glyph, 27, color, 2.4))
    return ring


__all__ = ["PairingPage", "PinGroup", "Spinner"]
