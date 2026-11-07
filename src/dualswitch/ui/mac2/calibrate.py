"""标定向导：端口清单 → 逐口切换 → 自动切回 → 确认保存。

为什么必须先问「有哪些口」：显示器**不会**通过 EDID/DDC 报出接口列表
（`monitor_page` 的说明也写了）。认识的屏用 profile 预选，不认识的屏让用户勾，
只标定勾到的口 —— 3 口屏就不会被硬塞一个 HDMI 2。

关键交互（`docs/V3-PLAN.md` §6.6）：切到目标口后画面离开本机，看不见引导；
所以每抓到一个值就**立刻发两条 Set 切回起始口**，等 2–3 秒再进入下一口。
"""

from __future__ import annotations

import copy
import threading
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import ConfigError
from ...ddc.calibration import CalibrationPlan
from ...ddc.profiles import PORT_ORDER, MonitorProfile, port_label
from . import theme
from .widgets import label


def _button(text: str, primary: bool = False, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("primary" if primary else "ghostBtn")
    btn.setFixedHeight(32 if primary else 28)
    btn.setMinimumWidth(72 if primary else 56)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    return btn


class CalibrationDialog(QDialog):
    started = Signal(object)     # 起始 VCP 读数（int 或 None）
    readingReady = Signal(object)  # 轮询读数（int 或 None）
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

        self.setObjectName("root")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle("标定显示器")
        self.setModal(True)
        self.setMinimumSize(560, 420)

        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_once)
        self.started.connect(self._on_started)
        self.readingReady.connect(self._on_reading)
        self.switchBackDone.connect(self._on_switch_back_done)

        self._build()

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)
        self._pages = QWidget()
        self._pages_lay = QVBoxLayout(self._pages)
        self._pages_lay.setContentsMargins(0, 0, 0, 0)
        self._pages_lay.setSpacing(10)
        lay.addWidget(self._pages, 1)

        self._build_select()
        self._build_run()
        self._build_confirm()
        self._show_only(self.page_select)

    def _show_only(self, page: QWidget) -> None:
        for item in (self.page_select, self.page_run, self.page_confirm):
            item.setVisible(item is page)

    def _build_select(self) -> None:
        self.page_select = QWidget()
        lay = QVBoxLayout(self.page_select)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("这块显示器有哪些输入口？", "heroName"))
        tip = label(
            "显示器不会自动上报接口列表，所以先确认一遍；只标定勾选的口。\n"
            "勾错了可以之后在「显示器配置」里补。",
            "heroSub",
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self._checks: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        if self.profile.key == "default":
            # 未识别屏：默认给最常见的三口组合，用户可加 HDMI 2 / DVI / VGA
            default_ports = {"hdmi1", "dp", "usb_c"}
        else:
            default_ports = set(self.profile.ports)
        for i, port in enumerate(PORT_ORDER):
            box = QCheckBox(port_label(port))
            box.setChecked(port in default_ports)
            box.setStyleSheet(
                f"QCheckBox{{color:{theme.tokens().ink};font-size:12.5px;spacing:8px;}}"
            )
            self._checks[port] = box
            grid.addWidget(box, i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_later = _button("稍后再说")
        btn_later.clicked.connect(self.reject)
        row.addWidget(btn_later)
        self.btn_start = _button("开始标定", primary=True)
        self.btn_start.clicked.connect(self._start)
        row.addWidget(self.btn_start)
        lay.addLayout(row)

    def _build_run(self) -> None:
        self.page_run = QWidget()
        lay = QVBoxLayout(self.page_run)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.run_title = label("准备标定", "heroName")
        lay.addWidget(self.run_title)
        self.run_instruction = label("", "heroSub")
        self.run_instruction.setWordWrap(True)
        lay.addWidget(self.run_instruction)

        card = QFrame()
        card.setObjectName("actRow")
        card_lay = QHBoxLayout(card)
        card_lay.setContentsMargins(14, 10, 14, 10)
        card_lay.setSpacing(12)
        self.live_value = label("—", "heroName")
        card_lay.addWidget(self.live_value, 0, Qt.AlignVCenter)
        self.live_state = label("等待读数…", "setSub")
        self.live_state.setWordWrap(True)
        card_lay.addWidget(self.live_state, 1)
        lay.addWidget(card)

        self.run_note = label("", "setSub")
        self.run_note.setWordWrap(True)
        lay.addWidget(self.run_note)
        lay.addStretch(1)

        row = QHBoxLayout()
        self.btn_cancel = _button("取消")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        row.addStretch(1)
        self.btn_skip = _button("这个口没信号 / 跳过")
        self.btn_skip.clicked.connect(self._skip)
        row.addWidget(self.btn_skip)
        self.btn_manual = _button("已切到（读数不变）")
        self.btn_manual.clicked.connect(self._manual_catch)
        row.addWidget(self.btn_manual)
        self.btn_retry = _button("重试")
        self.btn_retry.clicked.connect(self._retry)
        row.addWidget(self.btn_retry)
        self.btn_back_done = _button("已切回，继续", primary=True)
        self.btn_back_done.clicked.connect(self._manual_switched_back)
        self.btn_back_done.setVisible(False)
        row.addWidget(self.btn_back_done)
        lay.addLayout(row)

    def _build_confirm(self) -> None:
        self.page_confirm = QWidget()
        lay = QVBoxLayout(self.page_confirm)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("确认端口码表", "heroName"))
        tip = label("读通道可能抓到上一口的残留值；确认前可以直接改。", "heroSub")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.confirm_host = QWidget()
        self.confirm_grid = QGridLayout(self.confirm_host)
        self.confirm_grid.setContentsMargins(0, 0, 0, 0)
        self.confirm_grid.setHorizontalSpacing(12)
        self.confirm_grid.setVerticalSpacing(8)
        lay.addWidget(self.confirm_host)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = _button("放弃")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_cancel)
        btn_save = _button("保存标定", primary=True)
        btn_save.clicked.connect(self._save)
        row.addWidget(btn_save)
        lay.addLayout(row)
        self._confirm_edits: dict[str, QLineEdit] = {}

    # ------------------------------------------------------------------ 启动
    def _selected_ports(self) -> list[str]:
        return [port for port, box in self._checks.items() if box.isChecked()]

    def _start(self) -> None:
        selected = self._selected_ports()
        if not selected:
            self.run_note.setText("至少勾选一个输入口")
            return
        self.btn_start.setEnabled(False)
        self._selected = selected
        self._show_only(self.page_run)
        self.run_title.setText("正在读取当前输入口…")
        self.run_instruction.setText("请稍候")

        def work() -> None:
            value = None
            try:
                value = int(self.backend.get_input())
            except Exception:  # noqa: BLE001 - 读不到就按未知起始口处理
                value = None
            self.started.emit(value)

        threading.Thread(target=work, name="calib-start", daemon=True).start()

    def _on_started(self, value) -> None:
        self._plan = CalibrationPlan.create(self.profile, self._selected, current_value=value)
        self._read_failures = 0
        self._plan.begin()
        if self._plan.finished:
            self._show_confirm()
            return
        start = self._plan.start_port
        if start:
            self.run_title.setText(f"已识别起始口：{port_label(start)} = {self._plan.start_value}")
        elif value is None:
            self.run_title.setText("没读到起始口，标定后需手动切回")
        else:
            self.run_title.setText(f"起始口读数 {value}（不在勾选列表里）")
        self._update_run()
        self._poll.start()

    # ------------------------------------------------------------------ 轮询
    def _poll_once(self) -> None:
        if self._poll_busy or self._plan is None:
            return
        if self._plan.state != "waiting":
            return
        self._poll_busy = True

        def work() -> None:
            value = None
            try:
                value = int(self.backend.get_input())
            except Exception:  # noqa: BLE001 - 读失败是常态（成功率约 50%）
                value = None
            self.readingReady.emit(value)

        threading.Thread(target=work, name="calib-poll", daemon=True).start()

    def _on_reading(self, value) -> None:
        self._poll_busy = False
        if self._plan is None:
            return
        event = self._plan.feed(value, time.monotonic())
        if value is None:
            self._read_failures += 1
            self.live_value.setText("—")
            self.live_state.setText(f"读不到（第 {self._read_failures} 次）")
            if self._read_failures >= 6:
                self.run_note.setText("连续读不到，这个口可能没接设备；可以跳过或重试。")
            return
        self._read_failures = 0
        self.live_value.setText(str(value))
        if event == "caught":
            self._poll.stop()
            self.run_note.setText(
                f"已抓到 {port_label(self._plan.current_step.port)} = {value}，正在切回起始口…"
            )
            self._switch_back()
        elif event == "same-as-start":
            self.run_note.setText(
                "读数没有变化（两个口可能同码）。如果画面已经切到这个口，点「已切到（读数不变）」。"
            )
        else:
            self.live_state.setText("已读到新值，等待稳定…")

    def _update_run(self) -> None:
        plan = self._plan
        if plan is None or plan.current_step is None:
            return
        step = plan.current_step
        port = port_label(step.port)
        self.run_title.setText(f"请把显示器切到 {port}（{plan.index + 1}/{plan.total}）")
        self.run_instruction.setText(
            "切过去后画面会离开本机，这是正常的；抓到值后会自动切回来。"
        )
        self.live_value.setText("—")
        self.live_state.setText("等待读数…")
        self.run_note.setText("")
        self.btn_back_done.setVisible(False)
        self.btn_retry.setEnabled(True)
        self.btn_skip.setEnabled(True)
        self.btn_manual.setEnabled(True)

    # ------------------------------------------------------------------ 切回
    def _switch_back(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self.btn_manual.setEnabled(False)
        if not plan.can_auto_switch_back():
            self.run_note.setText("起始口未知，无法自动切回；请手动切回后点「已切回，继续」。")
            self.btn_back_done.setVisible(True)
            return

        def work() -> None:
            try:
                self.controller.set_input_local(int(plan.start_value), source="calibrate")
            except Exception:  # noqa: BLE001 - 切回失败也要让用户手动兜底
                pass
            time.sleep(2.5)          # 等显示器完成切换，别在切换中读
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

    def _manual_switched_back(self) -> None:
        self.btn_back_done.setVisible(False)
        self._on_switch_back_done()

    def _skip(self) -> None:
        if self._plan is None:
            return
        self._poll.stop()
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
        if self._plan is None:
            return
        if self._plan.manual_catch():
            self._poll.stop()
            self.run_note.setText("已按「读数不变」确认，正在切回起始口…")
            self._switch_back()

    # ------------------------------------------------------------------ 确认
    def _show_confirm(self) -> None:
        self._poll.stop()
        self._show_only(self.page_confirm)
        while self.confirm_grid.count():
            item = self.confirm_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._confirm_edits = {}
        values = dict(self.profile.ports)
        if self._plan is not None:
            values.update(self._plan.result())
        ports = self._plan.selected_ports if self._plan is not None else list(values)
        for i, port in enumerate(ports):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(label(port_label(port), "actPort"))
            edit = QLineEdit(str(values.get(port, "") or ""))
            edit.setObjectName("numEdit")
            edit.setFixedSize(64, 28)
            edit.setAlignment(Qt.AlignCenter)
            edit.setValidator(QIntValidator(0, 255, self))
            col.addWidget(edit)
            host = QWidget()
            host.setLayout(col)
            self._confirm_edits[port] = edit
            self.confirm_grid.addWidget(host, i // 2, i % 2)

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

    def closeEvent(self, event):  # noqa: N802
        self._poll.stop()
        # 标定中关闭：尽量把画面切回起始口，别把用户留在别的输入上
        plan = self._plan
        if plan is not None and plan.can_auto_switch_back() and plan.state == "caught":
            try:
                self.controller.set_input_local(int(plan.start_value), source="calibrate")
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)
