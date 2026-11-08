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

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
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


class _PortChoice(QFrame):
    """端口选择卡：整卡可点，右侧是勾选圆点（设计稿的格子，而不是原生 QCheckBox）。"""

    toggled = Signal()

    def __init__(self, port: str, checked: bool, parent=None):
        super().__init__(parent)
        self.port = port
        self._checked = bool(checked)
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip(f"只标定勾选的口：{port_label(port)}")

    def isChecked(self) -> bool:  # noqa: N802 - 与 QCheckBox 保持同名，测试/调用方不用改
        return self._checked

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self.update()
        self.toggled.emit()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 0.5px 内缩把 1px 描边对齐到设备像素栅格。否则 Retina 屏上圆角处的
        # 抗锯齿会把边线叠成 2–3 个像素，看起来就是「四个角特别重」。
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg = t.accent_soft if self._checked else t.card
        if self._hover and not self._checked:
            bg = t.card2
        border = t.accent if self._checked else t.line
        pen = QPen(theme.qcolor(border), 1)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(theme.qcolor(bg))
        p.drawRoundedRect(rect, 9, 9)

        p.setFont(theme.qfont(11.5, 650 if self._checked else 550))
        p.setPen(theme.qcolor(t.accent_deep if self._checked else t.ink2))
        p.drawText(
            rect.adjusted(12, 0, -34, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            port_label(self.port),
        )

        # 右侧勾选圆点：选中 = 蓝底白勾；未选 = 白底灰圈
        box = 16
        x = rect.right() - 12 - box
        y = rect.center().y() - box / 2
        p.setPen(QPen(theme.qcolor(t.accent if self._checked else t.line), 1))
        p.setBrush(theme.qcolor(t.accent if self._checked else t.card))
        p.drawEllipse(QRectF(x + 0.5, y + 0.5, box - 1, box - 1))
        if self._checked:
            p.setPen(QPen(QColor("#FFFFFF"), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(int(x + 4), int(y + 8), int(x + 7), int(y + 11))
            p.drawLine(int(x + 7), int(y + 11), int(x + 12), int(y + 5))
        p.end()


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

        # 用系统标题栏：macOS 左上角是真正的红黄绿三个窗口键，Windows 是系统标题栏。
        # 不再手绘红绿灯，也不再在右上角额外放一个 ✕ —— 关闭由系统窗口按钮负责。
        self.setWindowFlags(Qt.Dialog)
        self.setWindowTitle("标定显示器")
        self.setModal(True)
        self.setFixedSize(560, 420)

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
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.root = QFrame()
        self.root.setObjectName("calibRoot")
        self.root.setStyleSheet(
            f"QFrame#calibRoot{{background:{t.card};border-radius:0;border:0;}}"
        )
        outer.addWidget(self.root)

        lay = QVBoxLayout(self.root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(22, 18, 22, 18)
        self._body.setSpacing(12)
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

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self._checks: dict[str, _PortChoice] = {}
        if self.profile.key == "default":
            defaults = {"hdmi1", "dp", "usb_c"}
        else:
            defaults = set(self.profile.ports)
        for i, port in enumerate(PORT_ORDER):
            choice = _PortChoice(port, port in defaults)
            choice.toggled.connect(self._update_select_state)
            self._checks[port] = choice
            grid.addWidget(choice, i // 2, i % 2)
        lay.addWidget(grid_host)

        self.select_hint = label("", "setSub")
        lay.addWidget(self.select_hint)
        self.select_error = label("", "setSub")
        self.select_error.setStyleSheet(
            f"color:{theme.tokens().danger};font-size:10.5px;background:transparent;"
        )
        self.select_error.setVisible(False)
        lay.addWidget(self.select_error)
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
        self._update_select_state()

    def _build_run(self) -> None:
        self.page_run = QWidget()
        lay = QVBoxLayout(self.page_run)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)

        mcard = QFrame()
        mcard.setObjectName("calibMonitorCard")
        t = theme.tokens()
        mcard.setStyleSheet(
            f"QFrame#calibMonitorCard{{background:{t.card2};"
            f"border:1px solid {t.line_soft};border-radius:10px;}}"
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
        self.bluebox.setObjectName("calibBlue")
        self.bluebox.setStyleSheet(
            f"QFrame#calibBlue{{background:{t.accent_soft};"
            "border-radius:9px;border:0;}"
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
        self.confirm_error = label("", "setSub")
        self.confirm_error.setWordWrap(True)
        self.confirm_error.setStyleSheet(
            f"color:{theme.tokens().danger};font-size:10.5px;background:transparent;"
        )
        self.confirm_error.setVisible(False)
        lay.addWidget(self.confirm_error)
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

    def _update_select_state(self) -> None:
        count = len(self._selected_ports())
        if hasattr(self, "select_hint"):
            self.select_hint.setText(
                f"已选 {count} 个输入口 · 只标定勾选的口" if count else "至少勾选一个输入口"
            )
        if hasattr(self, "select_error"):
            self.select_error.setVisible(False)
        if hasattr(self, "btn_start"):
            self.btn_start.setEnabled(count > 0)

    def _start(self) -> None:
        selected = self._selected_ports()
        if not selected:
            self.select_error.setText("请先勾选至少一个输入口，再开始标定。")
            self.select_error.setVisible(True)
            self.btn_start.setEnabled(False)
            return
        self.select_error.setVisible(False)
        self.btn_start.setEnabled(False)
        self._selected = selected
        self._set_step(2)
        self._show_only(self.page_run)
        self._build_kv_rows()
        self.blue_port.setText("正在读取起始口…")
        self.blue_hint.setText("请稍候")

        def work() -> None:
            # 这块屏的读成功率大约一半，起始口只读一次经常是空的；空着会让
            # 整个流程失去「起始口」（不能自动切回、读数全被判成 same-as-start），
            # 所以先重试几次再认命。
            value = None
            for _ in range(3):
                try:
                    value = int(self.backend.get_input())
                except Exception:  # noqa: BLE001
                    value = None
                if value is not None:
                    break
                time.sleep(0.2)
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
            # 读数/状态文案会变长（「18 · 起始口，最后跳过」）；不给最小宽度
            # 时 QLabel 会沿用初始 33px，文字直接被裁在右边缘。
            value.setMinimumWidth(150)
            value.setFont(theme.qfont(11, 400, mono=True))
            rl.addWidget(name)
            rl.addStretch(1)
            rl.addWidget(value)
            self.kv_lay.addWidget(row)
            self._kv_rows[port] = (name, value)

    def _local_port(self) -> str:
        """本机接的那个口（`wiring.local`）—— 码表对不上时用来认起始口。"""
        cfg = getattr(self.controller, "cfg", None)
        fn = getattr(cfg, "port_for_role", None)
        if not callable(fn):
            return ""
        try:
            return str(fn("local") or "")
        except Exception:  # noqa: BLE001 - 认不出就退回旧行为
            return ""

    def _on_started(self, value) -> None:
        self._plan = CalibrationPlan.create(
            self.profile,
            self._selected,
            current_value=value,
            fallback_port=self._local_port(),
        )
        self._read_failures = 0
        self._plan.begin()
        if self._plan.start_port and self._plan.start_inferred:
            # 码表里没有起始口这个值 —— 说明表过时了，起始口是按接线认的
            self.start_hint.setText(
                f"{port_label(self._plan.start_port)} = {self._plan.start_value}（按接线认定）"
            )
        else:
            self.start_hint.setText(
                f"从 {port_label(self._plan.start_port)} 开始" if self._plan.start_port else ""
            )
        if self._plan.finished:
            self._show_confirm()
            return
        self._set_step(2)
        self._update_run()
        self._poll.start()

    def _invalidate_ddc(self) -> None:
        """作废 DDC 句柄缓存。

        **标定期必须每次读之前都作废一次**：显示器切到别的输入口之后，macOS 侧
        那个 `DCPAVServiceProxy` 会变成哑句柄——读回来的是切换前那份快照
        （2026-11-08 实测：切走后一直读 18，写也返回成功但不生效），必须重新
        枚举句柄才能拿到真值。旧实现只在「外接屏停用/启用」时作废，标定时永远
        拿不到新读数，看到的就是「读到 18，一直不稳定」。
        """
        fn = getattr(self.backend, "invalidate_ddc_cache", None)
        if not callable(fn):
            return
        try:
            fn()
        except Exception:  # noqa: BLE001 - 作废失败不该中断标定
            pass

    def _poll_once(self) -> None:
        if self._poll_busy or self._plan is None or self._plan.state != "waiting":
            return
        self._poll_busy = True

        def work() -> None:
            value = None
            try:
                self._invalidate_ddc()
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
            # 行内也要写「读不到」：只改蓝色提示的话，行里会一直挂着上一次的
            # 「18 · 读到了，等待稳定…」，看起来就像「读到了 18 但不稳定」。
            self._update_kv(port, f"读不到（第 {self._read_failures} 次）", "warn")
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
        elif event == "same-as-known":
            self.btn_manual.setVisible(True)
            self._update_kv(port, "与上一个口同码 · 切过去后请点「已切到」", "warn")

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
        # 「已切到」常驻：同码口、读通道抽风、读数抖成瞬时值（实测见过 50）时，
        # 都得有一条不用等状态机点头的出路，否则只能取消重来。
        self.btn_manual.setVisible(True)
        self.btn_retry.setVisible(False)
        self.btn_skip.setVisible(True)
        self.progress.setValue(int((plan.index / max(1, plan.total)) * 100))
        for port, (name, value) in self._kv_rows.items():
            if port == plan.start_port:
                tag = "起始口（按接线认定）" if plan.start_inferred else "起始口，最后跳过"
                value.setText(f"{plan.start_value} · {tag}")
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
    def _read_back(self) -> int | None:
        """重建句柄后读一次当前输入（切回校验用）。"""
        try:
            self._invalidate_ddc()
            return int(self.backend.get_input())
        except Exception:  # noqa: BLE001
            return None

    def _wait_until(self, target: int, timeout: float) -> bool:
        """等到显示器真的报 `target`。切走后旧句柄会一直回快照，所以每次重读。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.4)
            if self._read_back() == target:
                return True
        return False

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
        target = int(plan.start_value)

        def work() -> None:
            # 只发一次很容易「返回成功但没生效」（切走后的哑句柄 / 显示器还在
            # 扫描输入），所以发完必须**读回来验证**，不对就再发一次。
            for attempt in range(2):
                try:
                    self._invalidate_ddc()
                    self.controller.set_input_local(target, source="calibrate")
                except Exception:  # noqa: BLE001
                    pass
                if self._wait_until(target, 2.6):
                    break
                if attempt == 0:
                    time.sleep(0.5)
            time.sleep(0.6)          # 让画面真正回来再进下一步
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
        else:
            self.blue_hint.setText("这个口还没读到过值，请点「手工填码」直接填。")

    # ------------------------------------------------------------------ 确认
    def _show_confirm(self) -> None:
        self._poll.stop()
        self._set_step(3)
        self._show_only(self.page_confirm)
        self.confirm_error.setVisible(False)
        while self.confirm_lay.count():
            item = self.confirm_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._confirm_edits = {}
        # 只认这一次真正读到的值：没抓到的口不能把码表里的旧值当成实测值显示，
        # 否则会像 HDMI2 那样把「上一口的 18」当成自己的码存下去。
        caught = dict(self._plan.result()) if self._plan is not None else {}
        if self._plan is not None:
            ports = self._plan.selected_ports
        else:
            # 还没跑过标定就点「手工填码」：把码表原样摆出来给用户改
            ports = list(self.profile.ports)
            caught = dict(self.profile.ports)
        card = QFrame()
        card.setObjectName("calibConfirmCard")
        t = theme.tokens()
        card.setStyleSheet(
            f"QFrame#calibConfirmCard{{background:{t.card2};"
            f"border:1px solid {t.line_soft};border-radius:10px;}}"
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
            got = port in caught
            name = label(port_label(port), "actPort")
            if not got:
                name.setText(f"{port_label(port)} · 未抓到")
                name.setStyleSheet(
                    f"color:{t.warn};font-size:12px;font-weight:600;background:transparent;"
                )
            rl.addWidget(name)
            rl.addStretch(1)
            old = self.profile.value(port)
            edit = QLineEdit(str(caught[port]) if got else "")
            edit.setFixedWidth(62)
            edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            edit.setValidator(QIntValidator(0, 255, self))
            edit.setFont(theme.qfont(15, 600, mono=True))
            if not got and old is not None:
                # 留空 = 沿用旧值；灰字只是提示旧值是什么，不会被当成实测结果
                edit.setPlaceholderText(str(old))
            edit.setStyleSheet(
                f"QLineEdit{{background:transparent;border:0;color:{t.ink};"
                "font-size:15px;font-weight:600;}"
            )
            rl.addWidget(edit)
            self._confirm_edits[port] = edit
            cl.addWidget(row)
        self.confirm_lay.addWidget(card)

    def _restart(self) -> None:
        """回到第一步：必须把「开始标定」重新放开，否则重来一次就再也点不动了。"""
        self._poll.stop()
        self._plan = None
        self._read_failures = 0
        self.caught_pill.setText("已抓到 0 个")
        self.progress.setValue(0)
        self.confirm_error.setVisible(False)
        self._set_step(1)
        self._show_only(self.page_select)
        self._update_select_state()

    def _save(self) -> None:
        self.confirm_error.setVisible(False)
        ports: dict[str, int] = {}
        for port, edit in self._confirm_edits.items():
            text = str(edit.text()).strip()
            if not text:
                # 没抓到又没手填：沿用码表里的旧值，但绝不把「别的口的读数」写进来
                old = self.profile.value(port)
                if old is not None:
                    ports[port] = int(old)
                continue
            try:
                value = int(text)
            except ValueError:
                self._show_save_error(f"{port_label(port)} 的码值不是数字")
                return
            if not 0 <= value <= 0xFF:
                self._show_save_error(f"{port_label(port)} 的码值必须在 0..255")
                return
            ports[port] = value
        if not ports:
            self._show_save_error("至少保留一个端口的码值")
            return
        self.profile.ports = ports
        self.profile.calibrated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.profile.builtin = False
        if self._on_saved is not None:
            try:
                result = self._on_saved(self.profile)
            except (ConfigError, OSError) as exc:
                self._show_save_error(f"保存失败：{exc}")
                return
            except Exception as exc:  # noqa: BLE001 - 别把异常吞成「看起来没事」
                self._show_save_error(f"保存失败：{exc}")
                return
            if result is False:
                self._show_save_error("配置没有保存成功，本次只在内存生效；请检查配置文件权限。")
                return
        self.accept()

    def _show_save_error(self, text: str) -> None:
        self.confirm_error.setText(text)
        self.confirm_error.setVisible(True)

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
