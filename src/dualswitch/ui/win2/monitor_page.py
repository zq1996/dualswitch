"""显示器配置子页（Windows 端，与 mac2 同构）。

入口在设置页「显示器」组的第一行；同一窗口内切页，左侧栏仍高亮「设置」。
默认收起「已保存的显示器」，展开后才出现码值与操作。
"""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...config import Config, ConfigError, save as save_config
from ...ddc import profiles as profile_mod
from ...ddc.profiles import MonitorProfile, normalize_port, port_label
from . import theme
from .widgets import Pill, label, settings_row


def _small_button(text: str, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("ghostBtn")
    btn.setFixedHeight(28)
    btn.setMinimumWidth(56)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    return btn


def _port_combo(ports: list[str], current: str, parent=None) -> QComboBox:
    combo = QComboBox(parent)
    combo.setObjectName("portCombo")
    combo.setFixedHeight(28)
    combo.setCursor(Qt.PointingHandCursor)
    combo.setFocusPolicy(Qt.NoFocus)
    t = theme.tokens()
    combo.setStyleSheet(
        f"QComboBox{{background:{t.card};border:1px solid {t.line};"
        f"border-radius:8px;padding:0 8px;color:{t.ink};font-size:12px;}}"
        f"QComboBox::drop-down{{border:0;width:18px;}}"
    )
    for port in ports:
        combo.addItem(port_label(port), port)
    index = combo.findData(normalize_port(current))
    combo.setCurrentIndex(max(0, index))
    return combo


class MonitorPage(QWidget):
    backRequested = Signal()
    saved = Signal(object)              # Config
    calibrateRequested = Signal(object)  # MonitorProfile
    reidentifyRequested = Signal()
    toastRequested = Signal(str)

    def __init__(self, cfg: Config, config_file: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.config_file = Path(config_file)
        self._expanded = False
        self._build()
        self.refresh()

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 12)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.btn_back = _small_button("‹ 返回设置")
        self.btn_back.clicked.connect(self.backRequested)
        top.addWidget(self.btn_back, 0, Qt.AlignVCenter)
        top.addStretch(1)
        top.addWidget(label("显示器配置", "heroLabel"))
        lay.addLayout(top)

        card = QFrame()
        card.setObjectName("hero")
        card_lay = QHBoxLayout(card)
        card_lay.setContentsMargins(18, 14, 18, 14)
        card_lay.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.name = label("外接显示器", "heroName")
        self.identity = label("", "heroSub")
        self.identity.setWordWrap(True)
        col.addWidget(self.name)
        col.addWidget(self.identity)
        card_lay.addLayout(col, 1)
        self.pill = Pill("未标定")
        card_lay.addWidget(self.pill, 0, Qt.AlignVCenter)
        self.btn_reidentify = _small_button("重新识别")
        self.btn_reidentify.clicked.connect(self.reidentifyRequested)
        card_lay.addWidget(self.btn_reidentify, 0, Qt.AlignVCenter)
        self.btn_calibrate = _small_button("重新标定")
        self.btn_calibrate.clicked.connect(
            lambda: self.calibrateRequested.emit(self._editable_profile())
        )
        card_lay.addWidget(self.btn_calibrate, 0, Qt.AlignVCenter)
        lay.addWidget(card)

        self.card_wiring = QFrame()
        self.card_wiring.setObjectName("setCard")
        wiring_lay = QVBoxLayout(self.card_wiring)
        wiring_lay.setContentsMargins(0, 0, 0, 0)
        wiring_lay.setSpacing(0)
        self.combo_local = _port_combo(["usb_c"], "usb_c")
        self.combo_peer = _port_combo(["hdmi1"], "hdmi1")
        self.combo_local.currentIndexChanged.connect(self._save_wiring)
        self.combo_peer.currentIndexChanged.connect(self._save_wiring)
        wiring_lay.addWidget(settings_row("这台接哪个口", "画面回到本机时切到这个输入口", self.combo_local))
        sep = QFrame()
        sep.setObjectName("setSep")
        sep.setFixedHeight(1)
        wiring_lay.addWidget(sep)
        wiring_lay.addWidget(settings_row("对方接哪个口", "热键「切到另一台」时切到这个输入口", self.combo_peer))
        lay.addWidget(self.card_wiring)

        self.card_ports = QFrame()
        self.card_ports.setObjectName("setCard")
        ports_lay = QVBoxLayout(self.card_ports)
        ports_lay.setContentsMargins(0, 0, 0, 0)
        ports_lay.setSpacing(0)
        head = QWidget()
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(15, 10, 15, 8)
        head_lay.addWidget(label("端口码表", "setTitle"), 1)
        self.btn_save_ports = _small_button("保存码表")
        self.btn_save_ports.clicked.connect(self._save_ports)
        head_lay.addWidget(self.btn_save_ports)
        ports_lay.addWidget(head)
        self.port_scroll = QScrollArea()
        self.port_scroll.setWidgetResizable(True)
        self.port_scroll.setFrameShape(QFrame.NoFrame)
        self.port_scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        self.port_scroll.setMaximumHeight(150)
        self.port_grid_host = QWidget()
        self.port_grid = QGridLayout(self.port_grid_host)
        self.port_grid.setContentsMargins(15, 0, 15, 12)
        self.port_grid.setHorizontalSpacing(12)
        self.port_grid.setVerticalSpacing(8)
        self.port_scroll.setWidget(self.port_grid_host)
        ports_lay.addWidget(self.port_scroll)
        lay.addWidget(self.card_ports)

        self.btn_saved = _small_button("已保存的显示器 ▸")
        self.btn_saved.clicked.connect(self._toggle_saved)
        lay.addWidget(self.btn_saved, 0, Qt.AlignLeft)
        self.saved_host = QWidget()
        self.saved_lay = QVBoxLayout(self.saved_host)
        self.saved_lay.setContentsMargins(0, 0, 0, 0)
        self.saved_lay.setSpacing(6)
        self.saved_host.setVisible(False)
        lay.addWidget(self.saved_host)
        lay.addStretch(1)

    # ------------------------------------------------------------------ 数据
    def set_config(self, cfg: Config) -> None:
        self.cfg = cfg
        self.refresh()

    def _identity(self):
        return getattr(self.cfg, "_active_identity", None)

    def _editable_profile(self) -> MonitorProfile:
        profile = copy.deepcopy(self.cfg.active_profile())
        ident = self._identity()
        if ident is not None and profile.key != ident.key():
            profile.key = ident.key()
            profile.name = profile.name or getattr(ident, "name", "")
            profile.serial = profile.serial or getattr(ident, "serial", "")
        return profile

    def refresh(self) -> None:
        profile = self._editable_profile()
        self.name.setText(profile.name or "外接显示器")
        ident = self._identity()
        bits = [profile.key]
        if ident is not None:
            bits.append(f"slot={ident.slot}")
        if profile.serial:
            bits.append(f"SN {profile.serial}")
        bits.append(f"{len(profile.ports)} 个端口")
        self.identity.setText(" · ".join(b for b in bits if b))

        calibrated = profile.is_calibrated() or profile.key in self.cfg.monitors
        t = theme.tokens()
        self.pill.setText("已标定" if calibrated else "未标定")
        self.pill.set_colors(t.ok_bg if calibrated else t.warn_bg,
                             t.ok if calibrated else t.warn)

        self._rebuild_combos(profile)
        self._rebuild_port_grid(profile)
        self._rebuild_saved()

    def _rebuild_combos(self, profile: MonitorProfile) -> None:
        ports = profile.ordered_ports()
        for combo, role in ((self.combo_local, "local"), (self.combo_peer, "peer")):
            current = self.cfg.port_for_role(role)
            combo.blockSignals(True)
            combo.clear()
            for port in ports:
                combo.addItem(port_label(port), port)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.setEnabled(len(ports) > 0)
            combo.blockSignals(False)

    def _rebuild_port_grid(self, profile: MonitorProfile) -> None:
        while self.port_grid.count():
            item = self.port_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._port_edits: dict[str, QLineEdit] = {}
        ports = profile.ordered_ports()
        for i, port in enumerate(ports):
            cell = QFrame()
            cell.setObjectName("actRow")
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(10, 0, 10, 0)
            cell_lay.setSpacing(8)
            chip = label(str(profile.value(port) or "—"), "valChip")
            chip.setFixedSize(42, 26)
            chip.setAlignment(Qt.AlignCenter)
            cell_lay.addWidget(chip, 0, Qt.AlignVCenter)
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(label(port_label(port), "actPort"))
            edit = QLineEdit("" if profile.value(port) is None else str(profile.value(port)))
            edit.setObjectName("numEdit")
            edit.setFixedSize(54, 26)
            edit.setAlignment(Qt.AlignCenter)
            edit.setValidator(QIntValidator(0, 255, self))
            col.addWidget(edit)
            cell_lay.addLayout(col, 1)
            self._port_edits[port] = edit
            self.port_grid.addWidget(cell, i // 2, i % 2)
        if not ports:
            self.port_grid.addWidget(label("这块屏还没有可用的端口", "setSub"), 0, 0, 1, 2)

    def _rebuild_saved(self) -> None:
        while self.saved_lay.count():
            item = self.saved_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        keys = [k for k in self.cfg.monitors if k != profile_mod.DEFAULT_KEY]
        self.btn_saved.setText(
            f"已保存的显示器（{len(keys)}）{'▾' if self._expanded else '▸'}"
        )
        for key in keys:
            profile = self.cfg.monitors[key]
            row = QFrame()
            row.setObjectName("setRow")
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(15, 8, 15, 8)
            row_lay.setSpacing(8)
            name = label(profile.name or key, "setTitle")
            name.setToolTip(key)
            row_lay.addWidget(name, 1)
            row_lay.addWidget(label(f"{len(profile.ports)} 口", "setSub"))
            btn_use = _small_button("设为当前")
            btn_use.clicked.connect(lambda _=False, k=key: self._use_saved(k))
            row_lay.addWidget(btn_use)
            btn_del = _small_button("删除")
            btn_del.clicked.connect(lambda _=False, k=key: self._delete_saved(k))
            row_lay.addWidget(btn_del)
            self.saved_lay.addWidget(row)
        if not keys:
            self.saved_lay.addWidget(label("还没有保存过其它显示器", "setSub"))
        self.saved_host.setVisible(self._expanded)

    def _toggle_saved(self) -> None:
        self._expanded = not self._expanded
        self.saved_host.setVisible(self._expanded)
        self._rebuild_saved()

    # ------------------------------------------------------------------ 保存
    def _save_wiring(self, *_args) -> None:
        local = self.combo_local.currentData()
        peer = self.combo_peer.currentData()
        if not local or not peer:
            return
        if local == peer:
            self.toastRequested.emit("这台和对方不能接同一个输入口")
            self._rebuild_combos(self._editable_profile())
            return
        self.cfg.wiring = {"local": local, "peer": peer}
        try:
            self.cfg.sync_sources()
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.saved.emit(self.cfg)

    def _save_ports(self) -> None:
        profile = self._editable_profile()
        ports: dict[str, int] = {}
        for port, edit in getattr(self, "_port_edits", {}).items():
            text = str(edit.text()).strip()
            if not text:
                continue
            try:
                value = int(text)
            except ValueError:
                self.toastRequested.emit(f"{port_label(port)} 的码值不是数字")
                return
            if not 0 <= value <= 0xFF:
                self.toastRequested.emit(f"{port_label(port)} 的码值必须在 0..255")
                return
            ports[port] = value
        if not ports:
            self.toastRequested.emit("至少保留一个端口的码值")
            return
        profile.ports = ports
        profile.calibrated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._persist(profile)

    def _persist(self, profile: MonitorProfile) -> None:
        profile.builtin = False
        self.cfg.monitors[profile.key] = profile
        self.cfg.active_monitor = profile.key
        self.cfg.set_active_profile(profile)
        try:
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.refresh()
        self.saved.emit(self.cfg)

    def _use_saved(self, key: str) -> None:
        profile = copy.deepcopy(self.cfg.monitors[key])
        self.cfg.active_monitor = key
        self.cfg.set_active_profile(profile)
        try:
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.refresh()
        self.saved.emit(self.cfg)

    def _delete_saved(self, key: str) -> None:
        self.cfg.monitors.pop(key, None)
        if self.cfg.active_monitor == key:
            self.cfg.active_monitor = "auto"
        try:
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.refresh()
        self.saved.emit(self.cfg)
