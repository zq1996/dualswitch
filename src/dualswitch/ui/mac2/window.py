"""v2 主窗口：780×580 宽窗口 + 左侧栏导航 + 页面栈 + 底栏（INTERACTIONS §0.1）。

**一个窗口铁律**：切换/剪贴板/设置都是窗口内换页（QStackedWidget），
绝不弹独立面板。预览窗是临时悬浮层、菜单栏菜单是系统菜单，都不算窗口。

布局：

    ┌───────────────────────────────────────────────┐
    │ 系统标题栏（红绿灯+居中「屏幕切换」）   [剪贴板][设置] │
    ├──────────┬────────────────────────────────────┤
    │ 侧栏188px │ 内容区（切换页/剪贴板页/设置页）      │
    │ 核心      │                                    │
    │  切换     │                                    │
    │  剪贴板 12│                                    │
    │ 系统      │                                    │
    │  设置     │                                    │
    │ ──────── │                                    │
    │ ● 已连接  │                                    │
    │ ⌥Space   │                                    │
    ├──────────┴────────────────────────────────────┤
    │ 底栏 30px：对端连接状态 · 版本号                 │
    └───────────────────────────────────────────────┘

标题栏右侧工具钮：macOS 原生标题栏内放不了自绘控件 —— 先尝试
`NSTitlebarAccessory`（全尺寸内容 + 透明标题栏），失败时退回内容区右上角。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import IconButton, KeyCaps, NavButton, dot, label

PAGE_SWITCH, PAGE_CLIP, PAGE_SETTINGS = 0, 1, 2

ToolButton = IconButton          # 兼容旧名（标题栏工具钮）


class MainWindow(QWidget):
    switchRequested = Signal()
    forceRequested = Signal()
    deviceClicked = Signal(str)
    hiddenToBackground = Signal()

    def __init__(self, cfg, hotkey_label: str, version: str, monitor_name: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.hotkey_label = hotkey_label
        self.version = version
        self.monitor_name = monitor_name
        self._allow_close = False
        self.withdraw_to_tray = True

        self.setWindowTitle("屏幕切换")
        self.setAttribute(Qt.WA_MacShowFocusRect, False)
        self.setFixedSize(theme.WIN_W, theme.WIN_H)
        self._transparent_titlebar = False   # showEvent 里再尝试透明标题栏

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.root = QFrame()
        self.root.setObjectName("root")
        outer.addWidget(self.root)

        root_lay = QVBoxLayout(self.root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ---- 标题栏右侧工具条（系统标题栏覆盖其上；红绿灯在左侧）----
        tools_bar = QWidget()
        tools_bar.setFixedHeight(theme.TOOLBAR_H)
        tl = QHBoxLayout(tools_bar)
        tl.setContentsMargins(12, 0, 12, 0)
        tl.setSpacing(4)
        tl.addStretch(1)
        self.tool_clip = IconButton("clip", "剪贴板", "ghost")
        self.tool_set = IconButton("settings", "设置", "ghost")
        tl.addWidget(self.tool_clip)
        tl.addWidget(self.tool_set)
        root_lay.addWidget(tools_bar)

        # ---- 侧栏 + 内容 ----
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_W)
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(10, 14, 10, 12)
        side_lay.setSpacing(2)

        side_lay.addWidget(label("核心", "navSec"))
        self.nav_switch = NavButton("switch", "切换")
        self.nav_clip = NavButton("clip", "剪贴板")
        side_lay.addWidget(self.nav_switch)
        side_lay.addWidget(self.nav_clip)
        side_lay.addWidget(label("系统", "navSec"))
        self.nav_settings = NavButton("settings", "设置")
        side_lay.addWidget(self.nav_settings)

        side_lay.addStretch(1)
        foot = QFrame()
        foot.setObjectName("navFoot")
        foot_lay = QVBoxLayout(foot)
        foot_lay.setContentsMargins(10, 10, 10, 2)
        foot_lay.setSpacing(6)
        conn_row = QHBoxLayout()
        conn_row.setContentsMargins(0, 0, 0, 0)
        conn_row.setSpacing(6)
        self.side_dot = dot(theme.tokens().ok, 6)
        self.side_conn = label("未连接对端", "navFootText")
        conn_row.addWidget(self.side_dot, 0, Qt.AlignVCenter)
        conn_row.addWidget(self.side_conn, 1)
        foot_lay.addLayout(conn_row)

        hk_row = QHBoxLayout()
        hk_row.setContentsMargins(0, 0, 0, 0)
        hk_row.setSpacing(5)
        self.side_hotkey_text = label("热键", "navFootText")
        self.side_hotkey = KeyCaps(theme.keycap_labels(hotkey_label))
        hk_row.addWidget(self.side_hotkey_text, 0, Qt.AlignVCenter)
        hk_row.addWidget(self.side_hotkey, 0, Qt.AlignVCenter)
        hk_row.addStretch(1)
        foot_lay.addLayout(hk_row)
        side_lay.addWidget(foot)
        body_lay.addWidget(sidebar)

        self.pages = QStackedWidget()
        # 注意：容器上不能裸 setStyleSheet（无选择器）—— 会级联盖掉子控件的
        # objectName 背景（#primary 主按钮会被刷成透明，白底白字）。
        self.pages.setObjectName("pageStack")
        body_lay.addWidget(self.pages, 1)
        root_lay.addWidget(body, 1)

        # ---- 底栏 ----
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(theme.FOOTER_H)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 0, 16, 0)
        fl.setSpacing(7)
        self.foot_dot = dot(theme.tokens().ok, 6)
        self.foot_text = label("对端未连接", "footerText")
        self.foot_ver = label(version, "footerText")
        fl.addWidget(self.foot_dot, 0, Qt.AlignVCenter)
        fl.addWidget(self.foot_text, 1)
        fl.addWidget(self.foot_ver, 0)
        root_lay.addWidget(footer)

        # 导航
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for btn in (self.nav_switch, self.nav_clip, self.nav_settings):
            self._nav_group.addButton(btn)
        self.nav_switch.setChecked(True)
        self.nav_switch.clicked.connect(lambda: self.show_page(PAGE_SWITCH))
        self.nav_clip.clicked.connect(lambda: self.show_page(PAGE_CLIP))
        self.nav_settings.clicked.connect(lambda: self.show_page(PAGE_SETTINGS))
        self.tool_clip.clicked.connect(lambda: self.show_page(PAGE_CLIP))
        self.tool_set.clicked.connect(lambda: self.show_page(PAGE_SETTINGS))
        self._page = PAGE_SWITCH

    # ------------------------------------------------------------------ 页面
    def attach_pages(self, switch_page, clip_page, settings_page) -> None:
        self.switch_page = switch_page
        self.clip_page = clip_page
        self.settings_page = settings_page
        self.pages.addWidget(switch_page)
        self.pages.addWidget(clip_page)
        self.pages.addWidget(settings_page)
        self.switch_page.switchRequested.connect(self.switchRequested)
        self.switch_page.forceRequested.connect(self.forceRequested)
        self.switch_page.deviceClicked.connect(self.deviceClicked)

    def show_page(self, index: int) -> None:
        self._page = index
        self.pages.setCurrentIndex(index)
        self.nav_switch.setChecked(index == PAGE_SWITCH)
        self.nav_clip.setChecked(index == PAGE_CLIP)
        self.nav_settings.setChecked(index == PAGE_SETTINGS)
        for btn in (self.nav_switch, self.nav_clip, self.nav_settings):
            btn.update()
        self.tool_clip.setChecked(index == PAGE_CLIP)
        self.tool_set.setChecked(index == PAGE_SETTINGS)
        self.tool_clip.update()
        self.tool_set.update()
        title = {PAGE_SWITCH: "屏幕切换", PAGE_CLIP: "剪贴板", PAGE_SETTINGS: "设置"}[index]
        self.setWindowTitle(f"屏幕切换 · {title}" if index != PAGE_SWITCH else "屏幕切换")
        if index != PAGE_CLIP:
            clip = getattr(self, "clip_page", None)
            if clip is not None:
                clip.preview_controller.hide_now()

    def current_page(self) -> int:
        return self._page

    def on_clipboard_page(self) -> bool:
        return self._page == PAGE_CLIP

    def set_nav_count(self, count: int) -> None:
        self.nav_clip.set_count(str(count) if count else "")

    def set_side_status(self, conn_text: str, ok: bool = True) -> None:
        t = theme.tokens()
        self.side_conn.setText(conn_text)
        self.side_conn.setStyleSheet(f"color:{t.ok if ok else t.ink3};font-size:10px;")
        self.side_dot.setStyleSheet(
            f"background:{t.ok if ok else t.idle};border-radius:3px;border:0;"
        )

    def set_side_hotkey(self, text: str) -> None:
        """`text` = hotkey describe 串；侧栏改成键帽展示（§0.3 第 4 处）。"""
        self.hotkey_label = text
        keys = theme.keycap_labels(text)
        self.side_hotkey.set_keys(keys)

    def set_footer(self, text: str, ok: bool = True) -> None:
        t = theme.tokens()
        self.foot_text.setText(text)
        self.foot_dot.setStyleSheet(
            f"background:{t.ok if ok else t.idle};border-radius:3px;border:0;"
        )

    # ------------------------------------------------------------------ 平台外观
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_transparent_titlebar", False):
            self._try_transparent_titlebar()

    def _try_transparent_titlebar(self) -> None:
        """标题栏透明 + 全尺寸内容区（工具钮贴到标题栏高度内）。失败就当普通窗口。

        必须等真正 show 过（NSWindow 已建）再做；offscreen 测试平台 winId 无效，
        硬包指针会直接段错误，所以先判平台。
        """
        self._transparent_titlebar = False
        import sys

        if sys.platform != "darwin":
            return
        try:
            from PySide6.QtGui import QGuiApplication

            if QGuiApplication.platformName() == "offscreen":
                return
        except Exception:  # noqa: BLE001
            return
        try:
            import objc
            from AppKit import NSWindowStyleMaskFullSizeContentView

            wid = int(self.winId())
            if wid == 0:
                return
            ns_view = objc.objc_object(c_void_p=wid)
            ns_window = ns_view.window()
            if ns_window is None:
                return
            ns_window.setTitlebarAppearsTransparent_(True)
            ns_window.setStyleMask_(
                ns_window.styleMask() | NSWindowStyleMaskFullSizeContentView
            )
            self._transparent_titlebar = True
        except Exception:  # noqa: BLE001 - 不同 Qt/pyobjc 版本路径不同，失败就退回
            self._transparent_titlebar = False

    # ------------------------------------------------------------------ 事件
    def closeEvent(self, event: QCloseEvent):  # noqa: N802
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        if not self.withdraw_to_tray:
            self.showMinimized()
            return
        self.hide()
        self.hiddenToBackground.emit()

    def quit_completely(self) -> None:
        self._allow_close = True
        self.close()
