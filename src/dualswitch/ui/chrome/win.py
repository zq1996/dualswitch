"""Windows 外观：自绘无边框窗口 + 托盘（QSystemTrayIcon）。

契约说明见 `chrome/base.py`。这个文件是 Windows 端的「外观真源」：
改 Windows 界面只改这里，不会碰到 mac 的文件。
"""

from __future__ import annotations

from typing import Callable

from .base import Palette

NAME = "windows"
PLATFORM_LABEL = "Windows"

NATIVE_CHROME = False
BODY_TOP = 16
INLINE_SETTINGS_ROW = False
SETTINGS_IN_WINDOW = False       # Windows 保持独立设置窗口（自绘标题栏，嵌进来会有两层标题）
CLICK_HINT = "双击图标切换"
REOPEN_ON_ACTIVATE = False
STRICT_BACKEND = "hook"
SETTINGS_ROW_HINT = ""

BACKEND_OPTIONS = ["auto", "dxva2", "command", "mock"]

# 应用图标候选（按顺序找第一个存在的）；mac 用 .icns 更清晰
APP_ICON_NAMES = ("screen-switch.ico",)

MATCH_DESC = "按设备名或 device_id 的片段定位这台屏，匹配不到就用第一台"

HOTKEY_COPY = {
    "strict_title": "严格区分左右 Alt",
    "strict_desc": "改用低级键盘钩子，只认右 Alt",
    "tray_title": "开机后常驻托盘",
    "tray_desc": "关闭窗口时不退出程序",
    "swallow_desc": "阻止 Alt + Space 继续弹出系统菜单",
}

PALETTE = Palette(
    accent="#2F6BFF",
    accent_deep="#1E4FD8",
    accent_soft="#EDF3FF",
    primary_top="#3F7BFF",
    primary_bottom="#2456E0",
    primary_top_hover="#4A83FF",
    primary_bottom_hover="#2A5CE6",
    primary_top_down="#2F6BFF",
    primary_bottom_down="#1B47C4",
    font_families=("Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"),
    body_font_size=12.5,
    app_font_point=9.75,
)

ROOT_QSS = (
    "background: #FFFFFF; border: 1px solid rgba(16,24,40,0.07); border-radius: 14px;"
)
STATUSBAR_QSS = "border-bottom-left-radius: 13px; border-bottom-right-radius: 13px;"
QSS_EXTRA = ""


# --------------------------------------------------------------------------- #
# 自绘标题栏（Windows 无边框窗口）
# --------------------------------------------------------------------------- #
def titlebar(title: str, show_settings: bool = True):  # noqa: ANN201
    """返回自绘标题栏控件（最小化 / 关闭 / 设置齿轮 + 拖动）。"""
    from PySide6.QtCore import QPointF, QRectF, QSize, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QTransform
    from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

    from .. import theme
    from ..widgets import label

    class CaptionButton(QAbstractButton):
        """标题栏按钮：线性图标自绘，避免 Unicode 字形大小与基线不一致。"""

        def __init__(self, kind: str, parent=None):
            super().__init__(parent)
            self.kind = kind
            self.setFixedSize(30 if kind == "gear" else 34, 26)
            self.setCursor(Qt.PointingHandCursor)
            self.setFocusPolicy(Qt.NoFocus)
            self.setToolTip("设置" if kind == "gear" else "")

        def enterEvent(self, event):  # noqa: N802
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event):  # noqa: N802
            self.update()
            super().leaveEvent(event)

        def paintEvent(self, event):  # noqa: N802
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            hover = self.underMouse()
            rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
            if hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#E81123" if self.kind == "close" else "#E4E9F1"))
                p.drawRoundedRect(rect, 6, 6)

            white = hover and self.kind == "close"
            color = QColor("#FFFFFF") if white else QColor("#4B5566")
            c = QPointF(self.width() / 2, self.height() / 2)

            if self.kind == "gear":
                from ..widgets import paint_gear

                paint_gear(p, c, color)
            else:
                pen = QPen(color, 1.3)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                if self.kind == "min":
                    p.drawLine(QPointF(c.x() - 5, c.y() + 0.5), QPointF(c.x() + 5, c.y() + 0.5))
                else:
                    p.drawLine(QPointF(c.x() - 4.2, c.y() - 4.2), QPointF(c.x() + 4.2, c.y() + 4.2))
                    p.drawLine(QPointF(c.x() + 4.2, c.y() - 4.2), QPointF(c.x() - 4.2, c.y() + 4.2))
            p.end()

    # 让 Glyph 复用同一套齿轮路径（widgets.Glyph 会反过来 import 这个函数）
    from PySide6.QtCore import Signal

    class TitleBar(QWidget):
        minimizeClicked = Signal()
        closeClicked = Signal()
        settingsClicked = Signal()

        def __init__(self, title: str, show_settings: bool = True, parent=None):
            super().__init__(parent)
            self.setObjectName("titlebar")
            # QWidget 子类默认不画 QSS 背景，必须显式打开
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.setFixedHeight(44)
            self._drag_pos: QPointF | None = None

            lay = QHBoxLayout(self)
            lay.setContentsMargins(15, 0, 8, 0)
            lay.setSpacing(6)

            icon = QLabel()
            pm = QIcon(str(theme.asset("screen-switch.ico"))).pixmap(QSize(18, 18))
            icon.setPixmap(pm)
            icon.setFixedSize(18, 18)
            lay.addWidget(icon)
            lay.addWidget(label(title, "tbTitle"))
            lay.addStretch(1)

            self.btn_settings = CaptionButton("gear")
            self.btn_settings.setVisible(show_settings)
            self.btn_min = CaptionButton("min")
            self.btn_close = CaptionButton("close")
            for btn in (self.btn_settings, self.btn_min, self.btn_close):
                lay.addWidget(btn, 0, Qt.AlignVCenter)

            self.btn_settings.clicked.connect(self.settingsClicked)
            self.btn_min.clicked.connect(self.minimizeClicked)
            self.btn_close.clicked.connect(self.closeClicked)

        # 无边框窗口的拖动
        def mousePressEvent(self, event):  # noqa: N802
            if event.button() == Qt.LeftButton:
                window = self.window()
                self._drag_pos = event.globalPosition() - QPointF(window.frameGeometry().topLeft())
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):  # noqa: N802
            if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
                self.window().move((event.globalPosition() - self._drag_pos).toPoint())
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):  # noqa: N802
            self._drag_pos = None
            super().mouseReleaseEvent(event)

    return TitleBar(title, show_settings)


# --------------------------------------------------------------------------- #
# 托盘
# --------------------------------------------------------------------------- #
class QtStatusItem:
    """QSystemTrayIcon 封装：单击不响应，双击切换（Windows 端已定的交互）。"""

    kind = "tray"

    def __init__(self, icon, on_left_click: Callable[[], None] | None = None):
        from PySide6.QtWidgets import QSystemTrayIcon

        self._QSystemTrayIcon = QSystemTrayIcon
        self._tray = QSystemTrayIcon(icon)
        self._on_left_click = on_left_click
        self._menu = None                    # 保持引用，否则 QMenu 会被回收
        self._tray.activated.connect(self._activated)
        self._tray.show()

    def _activated(self, reason) -> None:      # noqa: ANN001
        if reason == self._QSystemTrayIcon.DoubleClick and self._on_left_click:
            self._on_left_click()

    def set_tooltip(self, text: str) -> None:
        self._tray.setToolTip(text)

    def set_menu(self, entries) -> None:       # noqa: ANN001 - list[MenuEntry]
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        for entry in entries:
            if entry.kind == "sep":
                menu.addSeparator()
                continue
            action = QAction(entry.title, menu)
            if entry.kind == "label" or entry.action is None:
                action.setEnabled(False)
            else:
                action.triggered.connect(entry.action)
            action.setCheckable(entry.checked)
            action.setChecked(entry.checked)
            menu.addAction(action)
        self._menu = menu
        self._tray.setContextMenu(menu)

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def is_visible(self) -> bool:
        return bool(self._tray.isVisible())

    def notify(self, title: str, message: str, icon=None, msecs: int = 4000) -> None:  # noqa: ANN001
        self._tray.showMessage(title, message, icon, msecs)


def build_status_item(icon, on_click: Callable[[], None]):  # noqa: ANN001
    return QtStatusItem(icon, on_click)


def build_app_menu(runner):  # noqa: ANN001
    """Windows 没有全局菜单栏，菜单都在托盘里。"""
    return None


def activate(window) -> None:  # noqa: ANN001
    return None


def open_path(path) -> None:  # noqa: ANN001
    """用系统默认程序打开文件/目录（Windows：os.startfile）。"""
    import logging
    import os

    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        logging.getLogger("dualswitch.ui").warning("打开路径失败 %s", path, exc_info=True)


def notify(status, title: str, message: str, icon=None) -> None:  # noqa: ANN001
    status.notify(title, message, icon, 4000)


# --------------------------------------------------------------------------- #
# 「辅助功能」权限：Windows 没有这个概念（RegisterHotKey 不需要授权），
# 这几个函数只为满足契约，让共享代码可以无条件调用。
# --------------------------------------------------------------------------- #
def permission_target() -> str:
    return ""


def accessibility_granted(prompt: bool = False) -> bool:  # noqa: ARG001
    return True


def request_accessibility() -> bool:
    return True


def open_accessibility_settings() -> None:
    return None
