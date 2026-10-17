"""主窗口：状态卡 + 两台设备（亮灯/灭灯）+ 排障列表。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import (
    DeviceCard,
    HeroCard,
    ListRow,
    StatusBar,
    TitleBar,
    label,
    soft_shadow,
)

PORT = {"windows": "HDMI", "mac": "USB-C"}
BANNER_EXTRA = 54   # 横幅出现时窗口额外高度


class Banner(QFrame):
    """失败 / 警告横幅，默认隐藏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 9, 11, 9)
        lay.setSpacing(9)
        self.icon = QLabel("\u26a0")
        self.text = QLabel("")
        self.text.setWordWrap(True)
        for w in (self.icon, self.text):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1)

    def show_message(self, kind: str, message: str) -> None:
        self.setObjectName("bannerErr" if kind == "error" else "bannerWarn")
        color = theme.DANGER if kind == "error" else theme.WARN
        self.icon.setStyleSheet(f"color:{color};font-size:12px;background:transparent;")
        self.text.setStyleSheet(f"color:{'#8C2222' if kind == 'error' else '#7A4A05'};font-size:11.5px;")
        self.text.setText(message)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(True)

    def clear(self) -> None:
        self.setVisible(False)


class MainWindow(QWidget):
    switchRequested = Signal()
    openSettingsRequested = Signal()
    openLogRequested = Signal()
    probeRequested = Signal()
    doctorRequested = Signal()

    def __init__(self, cfg, hotkey_label: str, version: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.hotkey_label = hotkey_label
        self._allow_close = False
        self._host = cfg.effective_host()

        self.setWindowTitle("屏幕切换")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(theme.WIN_W, theme.WIN_H)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame()
        self.root.setObjectName("root")
        outer.addWidget(self.root)
        soft_shadow(self.root, "#0F172A", blur=38, dy=14, alpha=52)

        root_lay = QVBoxLayout(self.root)
        root_lay.setContentsMargins(1, 1, 1, 1)
        root_lay.setSpacing(0)

        self.titlebar = TitleBar("屏幕切换")
        self.titlebar.closeClicked.connect(self.hide)
        self.titlebar.minimizeClicked.connect(self.showMinimized)
        self.titlebar.settingsClicked.connect(self.openSettingsRequested)
        root_lay.addWidget(self.titlebar)

        body = QWidget()
        body.setObjectName("body")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(18, 16, 18, 14)
        body_lay.setSpacing(14)

        self.hero = HeroCard()
        self.hero.switchRequested.connect(self.switchRequested)
        body_lay.addWidget(self.hero)

        self.banner = Banner()
        body_lay.addWidget(self.banner)

        devices = QHBoxLayout()
        devices.setContentsMargins(0, 0, 0, 0)
        devices.setSpacing(10)
        self.card_win = DeviceCard("windows")
        self.card_mac = DeviceCard("mac")
        for card in (self.card_win, self.card_mac):
            card.set_content(
                "Windows" if card.host == "windows" else "Mac",
                PORT[card.host],
                cfg.sources[card.host],
            )
            card.clicked.connect(self._on_card_clicked)
            devices.addWidget(card)
        body_lay.addLayout(devices)

        self.list_wrap = QFrame()
        self.list_wrap.setObjectName("listWrap")
        list_lay = QVBoxLayout(self.list_wrap)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(0)
        self.row_probe = ListRow("search", "探测显示器", "只读")
        self.row_log = ListRow("doc", "查看日志", "dualswitch.log")
        self.row_doctor = ListRow("pulse", "运行诊断", "doctor", last=True)
        self.row_probe.clicked.connect(self.probeRequested)
        self.row_log.clicked.connect(self.openLogRequested)
        self.row_doctor.clicked.connect(self.doctorRequested)
        for row in (self.row_probe, self.row_log, self.row_doctor):
            list_lay.addWidget(row)
        body_lay.addWidget(self.list_wrap)
        body_lay.addStretch(1)

        root_lay.addWidget(body, 1)

        self.statusbar = StatusBar()
        hk_state = "已启用" if hotkey_label != "未启用" else "未启用"
        self.statusbar.text.setText(f"热键{hk_state} · {hotkey_label}")
        self.statusbar.ver.setText(version)
        root_lay.addWidget(self.statusbar)

        self.set_state(self._host)
        self.hero.set_hotkey(hotkey_label)
        self.hero.set_foot("尚未切换 · 按一下开始", ok=True)

    # ------------------------------------------------------------------ 状态
    def set_state(self, host: str) -> None:
        self._host = host
        other = "mac" if host == "windows" else "windows"
        self.hero.set_state(host, self._monitor_line(host))
        self.card_win.set_active(host == "windows")
        self.card_mac.set_active(host == "mac")
        self._other = other

    def _monitor_line(self, host: str) -> str:
        name = (self.cfg.monitor_match or "").strip().upper() or "显示器"
        return f"{name} · {PORT[host]} · 输入源 {self.cfg.sources[host]}"

    def set_busy(self, busy: bool) -> None:
        target = "Mac" if self._other == "mac" else "Windows"
        self.hero.set_busy(busy, target)
        self.card_win.setEnabled(not busy)
        self.card_mac.setEnabled(not busy)

    def set_foot(self, text: str, ok: bool = True) -> None:
        self.hero.set_foot(text, ok)

    def show_banner(self, kind: str, message: str) -> None:
        self.banner.show_message(kind, message)
        self._apply_height(theme.WIN_H + BANNER_EXTRA)

    def clear_banner(self) -> None:
        if self.banner.isHidden():      # isVisible() 在窗口未 show 时恒为 False，这里只看显式状态
            return
        self.banner.clear()
        self._apply_height(theme.WIN_H)

    def _apply_height(self, height: int) -> None:
        self.setFixedHeight(height)
        self.resize(theme.WIN_W, height)

    # ------------------------------------------------------------------ 事件
    def _on_card_clicked(self, host: str) -> None:
        # 点灭灯那张 = 切过去；点亮的自己 = 无事发生
        if host != self._host:
            self.switchRequested.emit()

    def closeEvent(self, event: QCloseEvent):  # noqa: N802
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        self.hide()

    def quit_completely(self) -> None:
        self._allow_close = True
        self.close()
