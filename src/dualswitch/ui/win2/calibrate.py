"""标定向导：按 `design/v3/index-v3.html` ① 屏实现。

三段：端口清单 → 逐口标定（读到值自动切回起始口）→ 确认码表。
窗口是无边框圆角卡片，顶部有步骤圆点，中间是蓝色指令框和端口状态行，
不再是原生 QCheckBox / 原生对话框。
"""

from __future__ import annotations

import copy
import threading
import time
from datetime import datetime

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import ConfigError
from ...ddc.calibration import CalibrationPlan
from ...ddc.profiles import PORT_ORDER, MonitorProfile, port_label
from . import theme
from .widgets import label


def _btn(text: str, primary: bool = False, parent=None) -> QPushButton:
    t = theme.tokens()
    btn = QPushButton(text, parent)
    btn.setFixedHeight(30)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setFont(theme.qfont(11.5, 650 if primary else 550))
    if primary:
        style = (
            f"QPushButton{{background:{t.accent};border:0;border-radius:9px;color:#fff;"
            f"font-size:11.5px;font-weight:650;padding:0 15px;}}"
            f"QPushButton:hover{{background:{t.accent_deep};}}"
        )
    else:
        style = (
            f"QPushButton{{background:#fff;border:1px solid {t.line};border-radius:9px;"
            f"color:{t.ink2};font-size:11.5px;font-weight:550;padding:0 13px;}}"
            f"QPushButton:hover{{border-color:{t.accent};color:{t.accent_deep};}}"
        )
    btn.setStyleSheet(style)
    return btn


def _pill(text: str = "") -> QLabel:
    t = theme.tokens()
    lb = QLabel(text)
    lb.setFixedHeight(20)
    lb.setAlignment(Qt.AlignCenter)
    lb.setFont(theme.qfont(10, 650))
    lb.setStyleSheet(
        f"background:{t.ok_bg};color:{t.ok};border-radius:10px;padding:0 8px;"
    )
    return lb


class _Gbar(QWidget):
    """顶部拖动条（无边框窗口也要能拖）。"""

    def __init__(self, dialog: QDialog, parent=None):
        super().__init__(parent)
        self._dialog = dialog
        self._pos: QPoint | None = None
        self.setFixedHeight(34)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pos = event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._pos is not None and event.buttons() & Qt.LeftButton:
            self._dialog.move(event.globalPosition().toPoint() - self._pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._pos = None
        super().mouseReleaseEvent(event)


class CalibrationDialog(QDialog):
    started = Signal(object)
    readingReady = Signal(object)
    switchBackDone = Signal()

    def __init__(self, profile: MonitorProfile, backend, controller, on_saved, parent=None):
        super().__init__(parent)
        self.profile = copy.deepcopy(profile)
        self.backend = backend
        self.controller = controller
        self._on_saved = on_saved
        self._plan: CalibrationPlan | None = None
        self._poll_busy = False
        self._read_failures = 0
        self._selected: list[str] = []
        self._kv_rows: dict[str, tuple[QLabel, QLabel]] = {}

        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedSize(560, 500)

        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_once)
        self.started.connect(self._on_started)
        self.readingReady.connect(self._on_reading)
        self.switchBackDone.connect(self._on_switch_back_done)

        self._build()

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        t = theme.tokens()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        self.root = QFrame()
        self.root.setObjectName("calibRoot")
        self.root.setStyleSheet(
            "QFrame#calibRoot{background:#fff;border-radius:14px;border:0;}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(theme.qcolor(t.shadow))
        self.root.setGraphicsEffect(shadow)
        outer.addWidget(self.root)

        lay = QVBoxLayout(self.root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = _Gbar(self, self.root)
        bar.setStyleSheet(f"background:transparent;border-bottom:1px solid {t.line_soft};")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(12, 0, 8, 0)
        bar_lay.setSpacing(6)
        for color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color};border-radius:5px;")
            bar_lay.addWidget(dot, 0, Qt.AlignVCenter)
        bar_lay.addSpacing(6)
        bar_lay.addWidget(label("标定显示器", "setSub"))
        bar_lay.addStretch(1)
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.setStyleSheet(
            f"QPushButton{{background:transparent;border:0;color:{t.ink3};font-size:12px;}}"
            f"QPushButton:hover{{color:{t.ink};}}"
        )
        btn_close.clicked.connect(self.reject)
        bar_lay.addWidget(btn_close)
        lay.addWidget(bar)

        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(20, 16, 20, 16)
        self._body.setSpacing(10)
        lay.addWidget(body, 1)
        self._build_steps()
        self._build_select()
        self._build_run()
        self._build_confirm()
        self._show_only(self.page_select)

    def _build_steps(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._dots: list[QLabel] = []
        for i in range(3):
            dot = QLabel(str(i + 1))
            dot.setFixedSize(20, 20)
            dot.setAlignment(Qt.AlignCenter)
            dot.setFont(theme.qfont(10.5, 650))
            self._dots.append(dot)
            row.addWidget(dot)
        self.step_label = label("", "setSub")
        row.addSpacing(3)
        row.addWidget(self.step_label)
        row.addStretch(1)
        self._body.addLayout(row)
        self._set_step(1)

    def _set_step(self, active: int) -> None:
        t = theme.tokens()
        for i, dot in enumerate(self._dots, start=1):
            if i < active:
                dot.setStyleSheet(
                    f"background:{t.ok_bg};color:{t.ok};border-radius:10px;"
                    "font-size:10.5px;font-weight:650;"
                )
            elif i == active:
                dot.setStyleSheet(
                    f"background:{t.accent};color:#fff;border-radius:10px;"
                    "font-size:10.5px;font-weight:650;"
                )
            else:
                dot.setStyleSheet(
                    f"background:{t.card3};color:{t.ink3};border-radius:10px;"
                    "font-size:10.5px;font-weight:650;"
                )
        self.step_label.setText(
            {1: "端口清单 · 1 / 3", 2: "正在标定 · 2 / 3", 3: "确认码表 · 3 / 3"}[active]
        )

    def _show_only(self, page: QWidget) -> None:
        for item in (self.page_select, self.page_run, self.page_confirm):
            item.setVisible(item is page)

    def _build_select(self) -> None:
        self.page_select = QWidget()
        lay = QVBoxLayout(self.page_select)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        title = label("这块显示器有哪些输入口？", "setTitle")
        title.setFont(theme.qfont(15, 650))
        title.setStyleSheet(
            f"color:{theme.tokens().ink};background:transparent;"
            "font-size:15px;font-weight:650;"
        )
        lay.addWidget(title)
        tip = label(
            "显示器不会自动上报接口列表，所以先确认一遍；只标定勾选的口。",
            "setSub",
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        card = QFrame()
        t = theme.tokens()
        card.setStyleSheet(
            f"QFrame{{background:{t.card2};border:1px solid {t.line_soft};"
            "border-radius:10px;}"
        )
        grid = QGridLayout(card)
        grid.setContentsMargins(13, 10, 13, 10)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self._checks: dict[str, QCheckBox] = {}
        if self.profile.key == "default":
            defaults = {"hdmi1", "dp", "usb_c"}
        else:
            defaults = set(self.profile.ports)
        for i, port in enumerate(PORT_ORDER):
            box = QCheckBox(port_label(port))
            box.setChecked(port in defaults)
            box.setCursor(Qt.PointingHandCursor)
            box.setStyleSheet(
                f"QCheckBox{{color:{t.ink};font-size:11.5px;spacing:8px;}}"
                f"QCheckBox::indicator{{width:14px;height:14px;border:1px solid {t.line};"
                "border-radius:4px;background:#fff;}"
                f"QCheckBox::indicator:checked{{background:{t.accent};"
                f"border-color:{t.accent};}}"
            )
            self._checks[port] = box
            grid.addWidget(box, i // 2, i % 2)
        lay.addWidget(card)
        lay.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        later = _btn("稍后再说")
        later.clicked.connect(self.reject)
        row.addWidget(later)
        self.btn_start = _btn("开始标定", primary=True)
        self.btn_start.clicked.connect(self._start)
        row.addWidget(self.btn_start)
        lay.addLayout(row)
        self._body.addWidget(self.page_select)

    def _build_run(self) -> None:
        self.page_run = QWidget()
        lay = QVBoxLayout(self.page_run)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)

        mcard = QFrame()
        t = theme.tokens()
        mcard.setStyleSheet(
            f"QFrame{{background:{t.card2};border:1px solid {t.line_soft};"
            "border-radius:10px;}"
        )
        mc = QHBoxLayout(mcard)
        mc.setContentsMargins(13, 9, 13, 9)
        mc.setSpacing(6)
        self.monitor_name = label(self.profile.name or self.profile.key, "setTitle")
        self.monitor_name.setFont(theme.qfont(12.5, 650))
        self.monitor_name.setStyleSheet(
            f"color:{t.ink};background:transparent;font-size:12.5px;font-weight:650;"
        )
        mc.addWidget(self.monitor_name)
        self.start_hint = label("", "setSub")
        mc.addWidget(self.start_hint)
        mc.addStretch(1)
        self.caught_pill = _pill("已抓到 0 个")
        mc.addWidget(self.caught_pill)
        lay.addWidget(mcard)

        self.bluebox = QFrame()
        self.bluebox.setStyleSheet(
            f"QFrame{{background:{t.accent_soft};border-radius:9px;border:0;}}"
        )
        bb = QVBoxLayout(self.bluebox)
        bb.setContentsMargins(12, 9, 12, 9)
        bb.setSpacing(1)
        self.blue_caption = label("现在请切到", "setSub")
        self.blue_caption.setStyleSheet(
            f"color:{t.accent_deep};font-size:10px;background:transparent;"
        )
        self.blue_port = label("—", "setTitle")
        self.blue_port.setFont(theme.qfont(15, 650))
        self.blue_port.setStyleSheet(
            f"color:{t.accent_deep};background:transparent;font-size:15px;font-weight:650;"
        )
        self.blue_hint = label("", "setSub")
        self.blue_hint.setWordWrap(True)
        self.blue_hint.setStyleSheet(
            f"color:{t.accent_deep};font-size:10px;background:transparent;"
        )
        bb.addWidget(self.blue_caption)
        bb.addWidget(self.blue_port)
        bb.addWidget(self.blue_hint)
        lay.addWidget(self.bluebox)

        self.kv_host = QWidget()
        self.kv_lay = QVBoxLayout(self.kv_host)
        self.kv_lay.setContentsMargins(2, 0, 2, 0)
        self.kv_lay.setSpacing(0)
        lay.addWidget(self.kv_host)
        lay.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            "QProgressBar{background:transparent;border:0;}"
            f"QProgressBar::chunk{{background:{t.accent};border-radius:1px;}}"
        )
        lay.addWidget(self.progress)

        row = QHBoxLayout()
        self.btn_skip = _btn("跳过此项")
        self.btn_skip.clicked.connect(self._skip)
        row.addWidget(self.btn_skip)
        self.btn_cancel = _btn("取消")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        row.addStretch(1)
        self.btn_manual = _btn("已切到（读数不变）")
        self.btn_manual.clicked.connect(self._manual_catch)
        self.btn_manual.setVisible(False)
        row.addWidget(self.btn_manual)
        self.btn_retry = _btn("重试")
        self.btn_retry.clicked.connect(self._retry)
        self.btn_retry.setVisible(False)
        row.addWidget(self.btn_retry)
        self.btn_manual_fill = _btn("手工填码")
        self.btn_manual_fill.clicked.connect(self._show_confirm)
        row.addWidget(self.btn_manual_fill)
        lay.addLayout(row)
        self._body.addWidget(self.page_run)

    def _build_confirm(self) -> None:
        self.page_confirm = QWidget()
        lay = QVBoxLayout(self.page_confirm)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        title = label("确认端口码表", "setTitle")
        title.setFont(theme.qfont(15, 650))
        title.setStyleSheet(
            f"color:{theme.tokens().ink};background:transparent;"
            "font-size:15px;font-weight:650;"
        )
        lay.addWidget(title)
        tip = label("读通道可能抓到上一口的残留值；确认前可以直接改。", "setSub")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.confirm_host = QWidget()
        self.confirm_lay = QVBoxLayout(self.confirm_host)
        self.confirm_lay.setContentsMargins(0, 0, 0, 0)
        self.confirm_lay.setSpacing(0)
        lay.addWidget(self.confirm_host)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        again = _btn("重来")
        again.clicked.connect(self._restart)
        row.addWidget(again)
        save = _btn("保存并启用", primary=True)
        save.clicked.connect(self._save)
        row.addWidget(save)
        lay.addLayout(row)
        self._body.addWidget(self.page_confirm)
        self._confirm_edits: dict[str, QLineEdit] = {}

    # ------------------------------------------------------------------ 启动
    def _selected_ports(self) -> list[str]:
        return [port for port, box in self._checks.items() if box.isChecked()]

    def _start(self) -> None:
        selected = self._selected_ports()
        if not selected:
            return
        self.btn_start.setEnabled(False)
        self._selected = selected
        self._set_step(2)
        self._show_only(self.page_run)
        self._build_kv_rows()
        self.blue_port.setText("正在读取起始口…")
        self.blue_hint.setText("请稍候")

        def work() -> None:
            value = None
            try:
                value = int(self.backend.get_input())
            except Exception:  # noqa: BLE001
                value = None
            self.started.emit(value)

        threading.Thread(target=work, name="calib-start", daemon=True).start()

    def _build_kv_rows(self) -> None:
        while self.kv_lay.count():
            item = self.kv_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._kv_rows = {}
        for port in self._selected:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2, 5, 2, 5)
            name = label(port_label(port), "actPort")
            value = label("未开始", "setSub")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setFont(theme.qfont(11, 400, mono=True))
            rl.addWidget(name)
            rl.addStretch(1)
            rl.addWidget(value)
            self.kv_lay.addWidget(row)
            self._kv_rows[port] = (name, value)

    def _on_started(self, value) -> None:
        self._plan = CalibrationPlan.create(self.profile, self._selected, current_value=value)
        self._read_failures = 0
        self._plan.begin()
        self.start_hint.setText(
            f"从 {port_label(self._plan.start_port)} 开始" if self._plan.start_port else ""
        )
        if self._plan.finished:
            self._show_confirm()
            return
        self._set_step(2)
        self._update_run()
        self._poll.start()

    def _poll_once(self) -> None:
        if self._poll_busy or self._plan is None or self._plan.state != "waiting":
            return
        self._poll_busy = True

        def work() -> None:
            value = None
            try:
                value = int(self.backend.get_input())
            except Exception:  # noqa: BLE001
                value = None
            self.readingReady.emit(value)

        threading.Thread(target=work, name="calib-poll", daemon=True).start()

    def _on_reading(self, value) -> None:
        self._poll_busy = False
        if self._plan is None:
            return
        event = self._plan.feed(value, time.monotonic())
        port = self._plan.current_step.port if self._plan.current_step else ""
        if value is None:
            self._read_failures += 1
            self.blue_hint.setText(f"读不到（第 {self._read_failures} 次），稍等…")
            if self._read_failures >= 6:
                self.btn_retry.setVisible(True)
                self.btn_skip.setVisible(True)
                self.blue_hint.setText("连续读不到，这个口可能没接设备；可以重试或跳过。")
            return
        self._read_failures = 0
        self._update_kv(port, f"{value} · 读到了，等待稳定…", "idle")
        if event == "caught":
            self._poll.stop()
            self._mark_caught(port, value)
            self._switch_back()
        elif event == "same-as-start":
            self.btn_manual.setVisible(True)
            self._update_kv(port, "读数没变 · 若画面已切到这个口请点「已切到」", "warn")

    def _update_kv(self, port: str, text: str, tone: str) -> None:
        row = self._kv_rows.get(port)
        if not row:
            return
        t = theme.tokens()
        _, value = row
        value.setText(text)
        value.setStyleSheet(
            f"color:{t.ok if tone == 'ok' else (t.warn if tone == 'warn' else t.ink3)};"
            "font-size:11px;"
        )

    def _mark_caught(self, port: str, value: int) -> None:
        self._update_kv(port, f"{value} · 已抓到", "ok")
        done = sum(1 for step in self._plan.steps if step.value is not None)
        self.caught_pill.setText(f"已抓到 {done} 个")
        self.blue_caption.setText(f"{port_label(port)} 已抓到")
        self.blue_port.setText(str(value))
        self.blue_hint.setText("正在切回起始口，画面马上回来…")
        self.progress.setValue(min(95, int((done / max(1, self._plan.total)) * 100)))

    def _update_run(self) -> None:
        plan = self._plan
        if plan is None or plan.current_step is None:
            return
        step = plan.current_step
        self.blue_caption.setText("现在请切到")
        self.blue_port.setText(port_label(step.port))
        self.blue_hint.setText("切走后画面不在这台电脑上——读到我就会把画面送回来的")
        self.btn_manual.setVisible(False)
        self.btn_retry.setVisible(False)
        self.btn_skip.setVisible(True)
        self.progress.setValue(int((plan.index / max(1, plan.total)) * 100))
        for port, (name, value) in self._kv_rows.items():
            if port == plan.start_port:
                value.setText(f"{plan.start_value} · 起始口，最后跳过")
                name.setStyleSheet(f"color:{theme.tokens().ink3};font-size:12px;")
            elif port == step.port:
                value.setText("等待中…")
                name.setStyleSheet(f"color:{theme.tokens().ink};font-size:12px;font-weight:650;")
            elif any(s.port == port and s.value is not None for s in plan.steps):
                pass
            else:
                value.setText("未开始")
                name.setStyleSheet(f"color:{theme.tokens().ink2};font-size:12px;")

    # ------------------------------------------------------------------ 切回
    def _switch_back(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self.btn_manual.setVisible(False)
        if not plan.can_auto_switch_back():
            self.blue_hint.setText("起始口未知，无法自动切回；请手动切回后点「已切回，继续」。")
            self.btn_manual.setText("已切回，继续")
            self.btn_manual.setVisible(True)
            self._manual_is_back = True
            return
        self._manual_is_back = False

        def work() -> None:
            try:
                self.controller.set_input_local(int(plan.start_value), source="calibrate")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2.5)
            self.switchBackDone.emit()

        threading.Thread(target=work, name="calib-back", daemon=True).start()

    def _on_switch_back_done(self) -> None:
        if self._plan is None:
            return
        self._plan.mark_switched_back()
        if self._plan.finished:
            self._show_confirm()
            return
        self._update_run()
        self._poll.start()

    def _skip(self) -> None:
        if self._plan is None:
            return
        self._poll.stop()
        port = self._plan.current_step.port if self._plan.current_step else ""
        self._update_kv(port, "已跳过", "idle")
        self._plan.skip()
        if self._plan.finished:
            self._show_confirm()
            return
        self._update_run()
        self._poll.start()

    def _retry(self) -> None:
        if self._plan is None:
            return
        self._read_failures = 0
        self._plan.retry()
        self._update_run()
        self._poll.start()

    def _manual_catch(self) -> None:
        if getattr(self, "_manual_is_back", False):
            self._manual_is_back = False
            self.btn_manual.setText("已切到（读数不变）")
            self._on_switch_back_done()
            return
        if self._plan is None:
            return
        if self._plan.manual_catch():
            self._poll.stop()
            port = self._plan.current_step.port
            self._mark_caught(port, self._plan.current_step.value or 0)
            self._switch_back()

    # ------------------------------------------------------------------ 确认
    def _show_confirm(self) -> None:
        self._poll.stop()
        self._set_step(3)
        self._show_only(self.page_confirm)
        while self.confirm_lay.count():
            item = self.confirm_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._confirm_edits = {}
        values = dict(self.profile.ports)
        if self._plan is not None:
            values.update(self._plan.result())
        ports = self._plan.selected_ports if self._plan is not None else list(values)
        card = QFrame()
        t = theme.tokens()
        card.setStyleSheet(
            f"QFrame{{background:{t.card2};border:1px solid {t.line_soft};"
            "border-radius:10px;}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(13, 4, 13, 4)
        cl.setSpacing(0)
        for i, port in enumerate(ports):
            if i:
                sep = QFrame()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background:{t.line_soft};border:0;")
                cl.addWidget(sep)
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 7, 0, 7)
            rl.addWidget(label(port_label(port), "actPort"))
            rl.addStretch(1)
            edit = QLineEdit(str(values.get(port, "") or ""))
            edit.setFixedWidth(62)
            edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            edit.setValidator(QIntValidator(0, 255, self))
            edit.setFont(theme.qfont(15, 600, mono=True))
            edit.setStyleSheet(
                f"QLineEdit{{background:transparent;border:0;color:{t.ink};"
                "font-size:15px;font-weight:600;}"
            )
            rl.addWidget(edit)
            self._confirm_edits[port] = edit
            cl.addWidget(row)
        self.confirm_lay.addWidget(card)

    def _restart(self) -> None:
        self._plan = None
        self._read_failures = 0
        self._set_step(1)
        self._show_only(self.page_select)

    def _save(self) -> None:
        ports: dict[str, int] = {}
        for port, edit in self._confirm_edits.items():
            text = str(edit.text()).strip()
            if not text:
                continue
            try:
                value = int(text)
            except ValueError:
                return
            if 0 <= value <= 0xFF:
                ports[port] = value
        if ports:
            self.profile.ports = ports
            self.profile.calibrated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.profile.builtin = False
            if self._on_saved is not None:
                try:
                    self._on_saved(self.profile)
                except (ConfigError, OSError):
                    pass
        self.accept()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            geo = parent.window().frameGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )

    def closeEvent(self, event):  # noqa: N802
        self._poll.stop()
        plan = self._plan
        if plan is not None and plan.can_auto_switch_back() and plan.state == "caught":
            try:
                self.controller.set_input_local(int(plan.start_value), source="calibrate")
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)
