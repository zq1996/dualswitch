"""主窗口：状态卡 + 两台设备（亮灯/灭灯）+ 排障列表。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .chrome import current as chrome
from .widgets import (
    DeviceCard,
    HeroCard,
    ListRow,
    StatusBar,
    label,
    soft_shadow,
)

PORT = {"windows": "HDMI", "mac": "USB-C"}
BANNER_EXTRA = 54   # 横幅出现时窗口额外高度


class Banner(QFrame):
    """失败 / 警告横幅，默认隐藏。可带一个可点击的动作（如「去授权」）。"""

    actionClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 9, 11, 9)
        lay.setSpacing(9)
        self.icon = QLabel("\u26a0")
        self.text = QLabel("")
        self.text.setWordWrap(True)
        self.action = QPushButton("")
        self.action.setObjectName("bannerAction")
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.setVisible(False)
        self.action.clicked.connect(self.actionClicked)
        self.icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.action, 0, Qt.AlignTop)

    def show_message(self, kind: str, message: str, action_text: str = "") -> None:
        self.setObjectName("bannerErr" if kind == "error" else "bannerWarn")
        color = theme.DANGER if kind == "error" else theme.WARN
        self.icon.setStyleSheet(f"color:{color};font-size:12px;background:transparent;")
        self.text.setStyleSheet(f"color:{'#8C2222' if kind == 'error' else '#7A4A05'};font-size:11.5px;")
        self.text.setText(message)
        self.action.setText(action_text)
        self.action.setVisible(bool(action_text))
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(True)

    def clear(self) -> None:
        self.setVisible(False)
        self.action.setVisible(False)


class MainWindow(QWidget):
    switchRequested = Signal()
    openSettingsRequested = Signal()
    openClipboardRequested = Signal()
    openLogRequested = Signal()
    probeRequested = Signal()
    doctorRequested = Signal()
    hiddenToBackground = Signal()      # 窗口收起（不退出程序）—— mac 借此收掉程序坞图标

    def __init__(
        self,
        cfg,
        hotkey_label: str,
        version: str,
        parent=None,
        monitor_name: str = "",
    ):
        super().__init__(parent)
        self.cfg = cfg
        self.hotkey_label = hotkey_label
        self.monitor_name = monitor_name
        self._allow_close = False
        self.withdraw_to_tray = True       # 托盘真的可用时，关窗口 = 收进托盘
        self._host = cfg.effective_host()

        # 平台差异（谁画窗口、要不要自绘标题栏）全部来自 chrome，这里不做判断
        self.chrome = chrome()
        # 窗口基准高度：mac 的列表多一行剪贴板，靠 chrome.EXTRA_H 加高（Windows 是 0）
        self.win_h = theme.WIN_H + self.chrome.EXTRA_H

        self.setWindowTitle("屏幕切换")
        if self.chrome.NATIVE_CHROME:
            # macOS：用系统标题栏（红绿灯 + 居中标题），窗口外框、圆角、投影都交给系统，
            # 自绘一套反而显得不像 mac 应用。这里只保证内容区能正常撑满。
            self.setAttribute(Qt.WA_MacShowFocusRect, False)
            self.setFixedSize(theme.WIN_W, self.win_h)
        else:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setFixedSize(theme.WIN_W, self.win_h)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.root = QFrame()
        self.root.setObjectName("root")
        outer.addWidget(self.root)
        if not self.chrome.NATIVE_CHROME:
            soft_shadow(self.root, "#0F172A", blur=38, dy=14, alpha=52)

        root_lay = QVBoxLayout(self.root)
        m = 0 if self.chrome.NATIVE_CHROME else 1      # Windows 的 1px 描边内缩
        root_lay.setContentsMargins(m, m, m, m)
        root_lay.setSpacing(0)

        # 标题栏由平台决定：Windows 返回自绘控件，macOS 返回 None（系统给）
        # 剪贴板入口只有主窗口要（设置窗口是独立窗口，那里放了没意义）
        self.titlebar = self.chrome.titlebar("屏幕切换", show_clipboard=True)
        if self.titlebar is not None:
            # 走 close() 而不是 hide()：所有「关窗口」路径都汇到 closeEvent 一处，
            # 托盘不可用时才有机会改成最小化（hide 是绕过去，会留下无入口的进程）
            self.titlebar.closeClicked.connect(self.close)
            self.titlebar.minimizeClicked.connect(self.showMinimized)
            self.titlebar.settingsClicked.connect(self.openSettingsRequested)
            # 标题栏按钮不是契约的一部分（mac 没有自绘标题栏），按能力接
            clipboard_btn = getattr(self.titlebar, "btn_clipboard", None)
            if clipboard_btn is not None:
                clipboard_btn.clicked.connect(self.openClipboardRequested)
            root_lay.addWidget(self.titlebar)

        # 页面栈：主页面 + 剪贴板页 +（mac 上）设置页。都不另开窗口，点「返回」就回主页面。
        # Windows 的设置仍是独立窗口（自绘标题栏，嵌进来会变成两层标题栏）。
        self.pages = QStackedWidget()
        self.settings_page = None
        self.clipboard_page = None
        self._on_settings = False
        self._on_clipboard = False

        body = QWidget()
        body.setObjectName("body")
        body_lay = QVBoxLayout(body)
        top = self.chrome.BODY_TOP
        body_lay.setContentsMargins(18, top, 18, 14)
        body_lay.setSpacing(14)

        self.hero = HeroCard()
        self.hero.switchRequested.connect(self.switchRequested)
        body_lay.addWidget(self.hero)

        self.banner = Banner()
        self._banner_action = None
        self.banner.actionClicked.connect(self._on_banner_action)
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
        self.row_doctor = ListRow(
            "pulse", "运行诊断", "doctor",
            last=not (self.chrome.INLINE_CLIPBOARD_ROW or self.chrome.INLINE_SETTINGS_ROW),
        )
        self.row_probe.clicked.connect(self.probeRequested)
        self.row_log.clicked.connect(self.openLogRequested)
        self.row_doctor.clicked.connect(self.doctorRequested)
        rows = [self.row_probe, self.row_log, self.row_doctor]

        # 剪贴板入口按平台习惯分开：mac 收进这张列表（设置行上方），Windows 在标题栏
        self.row_clipboard = None
        if self.chrome.INLINE_CLIPBOARD_ROW:
            self.row_clipboard = ListRow("clip", "剪贴板", last=not self.chrome.INLINE_SETTINGS_ROW)
            self.row_clipboard.clicked.connect(self.openClipboardRequested)
            rows.append(self.row_clipboard)

        # macOS 没有自绘标题栏，设置入口收进这张列表（⌘, 同时可用）
        self.row_settings = None
        if self.chrome.INLINE_SETTINGS_ROW:
            self.row_settings = ListRow("gear", "设置…", self.chrome.SETTINGS_ROW_HINT, last=True)
            self.row_settings.clicked.connect(self.openSettingsRequested)
            rows.append(self.row_settings)
        for row in rows:
            list_lay.addWidget(row)
        body_lay.addWidget(self.list_wrap)
        body_lay.addStretch(1)

        self.statusbar = StatusBar()
        hk_state = "已启用" if hotkey_label != "未启用" else "未启用"
        self.statusbar.text.setText(f"热键{hk_state} · {hotkey_label}")
        self.statusbar.ver.setText(version)

        self.main_page = QWidget()
        main_lay = QVBoxLayout(self.main_page)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)
        main_lay.addWidget(body, 1)
        main_lay.addWidget(self.statusbar)

        self.pages.addWidget(self.main_page)
        root_lay.addWidget(self.pages, 1)

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
        name = (self.monitor_name or self.cfg.monitor_match or "").strip() or "显示器"
        return f"{name} · {PORT[host]} · 输入源 {self.cfg.sources[host]}"

    def set_busy(self, busy: bool) -> None:
        target = "Mac" if self._other == "mac" else "Windows"
        self.hero.set_busy(busy, target)
        self.card_win.setEnabled(not busy)
        self.card_mac.setEnabled(not busy)

    def set_foot(self, text: str, ok: bool = True) -> None:
        self.hero.set_foot(text, ok)

    def show_banner(self, kind: str, message: str, action_text: str = "", action=None) -> None:
        """横幅。action_text + action 用于「去授权」「重试」这类需要用户动手的提示。"""
        self._banner_action = action
        self.banner.show_message(kind, message, action_text)
        self._apply_height(self.win_h + BANNER_EXTRA)

    def _on_banner_action(self) -> None:
        if callable(self._banner_action):
            try:
                self._banner_action()
            except Exception:  # pragma: no cover - 动作不能把界面打挂
                pass

    def clear_banner(self) -> None:
        if self.banner.isHidden():      # isVisible() 在窗口未 show 时恒为 False，这里只看显式状态
            return
        self.banner.clear()
        self._apply_height(self.win_h)

    def _apply_height(self, height: int) -> None:
        self.setFixedHeight(height)
        self.resize(theme.WIN_W, height)

    def _set_title(self, text: str) -> None:
        """窗口标题跟着页面变。Windows 还要同步自绘标题栏里的那行字。"""
        self.setWindowTitle(text)
        setter = getattr(self.titlebar, "set_title", None)
        if callable(setter):
            setter(text)

    # ------------------------------------------------------------------ 页面
    def attach_settings(self, widget) -> None:
        """把设置页挂进本窗口（mac：同一个窗口里翻页，而不是再弹一个窗口）。"""
        self.settings_page = widget
        self.pages.addWidget(widget)

    def attach_clipboard(self, widget) -> None:
        """把剪贴板页挂进本窗口（两端都走这里，和设置页同一套）。"""
        self.clipboard_page = widget
        self.pages.addWidget(widget)

    def show_settings_page(self) -> None:
        if self.settings_page is None:      # 独立窗口模式（Windows）没有这一页
            return
        self._on_settings = True
        self._on_clipboard = False
        self.pages.setCurrentWidget(self.settings_page)
        self._set_title("屏幕切换 · 设置")
        self._apply_height(theme.SETTINGS_H)

    def show_clipboard_page(self) -> None:
        """翻到剪贴板页。它占满主窗口高度（预览区要吃掉剩余空间），不用 SETTINGS_H。"""
        if self.clipboard_page is None:
            return
        self._on_clipboard = True
        self._on_settings = False
        self.pages.setCurrentWidget(self.clipboard_page)
        self._set_title("屏幕切换 · 剪贴板")
        self._apply_height(self.win_h)
        refresh = getattr(self.clipboard_page, "refresh", None)
        if callable(refresh):
            refresh()

    def show_main_page(self) -> None:
        self._on_settings = False
        self._on_clipboard = False
        self.pages.setCurrentWidget(self.main_page)
        self._set_title("屏幕切换")
        self._apply_height(self.win_h + (0 if self.banner.isHidden() else BANNER_EXTRA))

    def on_settings_page(self) -> bool:
        return self._on_settings

    def on_clipboard_page(self) -> bool:
        return self._on_clipboard

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
        if not self.withdraw_to_tray:
            # 没有可用的托盘：藏起来就再也叫不回来了，至少把窗口留在任务栏
            self.showMinimized()
            return
        self.hide()
        self.hiddenToBackground.emit()

    def quit_completely(self) -> None:
        self._allow_close = True
        self.close()
