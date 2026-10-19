"""Windows 外观：自绘无边框窗口 + 托盘（QSystemTrayIcon）。

契约说明见 `chrome/base.py`。这个文件是 Windows 端的「外观真源」：
改 Windows 界面只改这里，不会碰到 mac 的文件。
"""

from __future__ import annotations

import logging
from typing import Callable

from .base import Palette

log = logging.getLogger("dualswitch.tray")

NAME = "windows"
PLATFORM_LABEL = "Windows"
PLATFORM_LOGO = "windows"        # 本机图标（剪贴板页文本记录的缩略图）
PEER_LOGO = "apple"              # 对端图标：本应用只在 Windows/mac 两端配对，对端就是另一台

NATIVE_CHROME = False
BODY_TOP = 16
INLINE_SETTINGS_ROW = False
# 设置也是主窗口里的一页（齿轮进去、点「‹ 返回」回来）。
# 以前是独立窗口：自绘标题栏 + 固定 484 高，和主页面并排时是两套壳子，
# 现在统一成「一个窗口、几页内容」，和 mac 一致。
SETTINGS_IN_WINDOW = True
CLICK_HINT = "双击图标打开主界面"
# 双击托盘的语义：Windows 上是「把主页面摊开给用户看」，不再是「顺手切一下屏」。
# 双击会误触（切换是改显示器输入源的重操作），而"打开主界面"随时可撤销。
STATUS_ACTIVATE = "show_main"
REOPEN_ON_ACTIVATE = False
DOCK_FOLLOWS_WINDOW = False      # Windows 没有程序坞，任务栏按钮跟着窗口走，无需处理
STRICT_BACKEND = "hook"
SETTINGS_ROW_HINT = ""
INLINE_CLIPBOARD_ROW = False     # 剪贴板入口在标题栏（齿轮左边）
CLIPBOARD_ROW_HINT = ""
EXTRA_H = 0                      # 列表没变长，窗口高度不变

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
def titlebar(title: str, show_settings: bool = True, show_clipboard: bool = False):  # noqa: ANN201
    """返回自绘标题栏控件（最小化 / 关闭 / 剪贴板 / 设置齿轮 + 拖动）。

    `show_clipboard` 只有主窗口传 True：设置窗口是独立窗口，那里放剪贴板入口没有意义。
    """
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
            self.setToolTip({"gear": "设置", "clip": "剪贴板"}.get(kind, ""))

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
            elif self.kind == "clip":
                # 剪贴板：板身 + 顶部夹子（和设计稿同一套 1.15px 线条）
                pen = QPen(color, 1.15)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(c.x() - 4.3, c.y() - 4.4, 8.6, 9.6), 1.7, 1.7)
                p.drawRoundedRect(QRectF(c.x() - 1.9, c.y() - 6.1, 3.8, 2.6), 0.9, 0.9)
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

        def __init__(self, title: str, show_settings: bool = True,
                     show_clipboard: bool = False, parent=None):
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
            self.title_label = label(title, "tbTitle")
            lay.addWidget(self.title_label)
            lay.addStretch(1)

            # 剪贴板在齿轮左边：标题栏本来就是工具条，两者同级
            self.btn_clipboard = CaptionButton("clip")
            self.btn_clipboard.setVisible(show_clipboard)
            self.btn_settings = CaptionButton("gear")
            self.btn_settings.setVisible(show_settings)
            self.btn_min = CaptionButton("min")
            self.btn_close = CaptionButton("close")
            for btn in (self.btn_clipboard, self.btn_settings, self.btn_min, self.btn_close):
                lay.addWidget(btn, 0, Qt.AlignVCenter)

            # 剪贴板按钮不给本类发信号：`window.py` 直接连 `btn_clipboard.clicked`
            # （这个按钮不是契约的一部分，mac 没有自绘标题栏，所以走「按能力接」）
            self.btn_settings.clicked.connect(self.settingsClicked)
            self.btn_min.clicked.connect(self.minimizeClicked)
            self.btn_close.clicked.connect(self.closeClicked)

        def set_title(self, text: str) -> None:
            """标题栏文字跟着页面变（`屏幕切换` / `屏幕切换 · 剪贴板`）。"""
            self.title_label.setText(text)

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

    return TitleBar(title, show_settings, show_clipboard)


# --------------------------------------------------------------------------- #
# 托盘
# --------------------------------------------------------------------------- #
class QtStatusItem:
    """QSystemTrayIcon 封装：单击不响应，双击摊开主页面（Windows 端已定的交互）。

    **为什么不双击切换**：切换会改显示器输入源，是重操作且不可撤销；
    双击托盘在 Windows 上是"把程序叫回来看看"的习惯性动作，误触成本很高。
    真要切换，切屏热键 / 托盘菜单 / 主按钮三条路都在。

    托盘是「关窗口收进后台」之后**唯一**的入口，所以这里每一步都要留日志：
    图标注册失败时 Qt 不抛异常、不报警，只是托盘里少一个图标、
    而任务管理器里进程照常在跑 —— 用户看到的症状就是「程序没了，但又没退出」。
    """
    kind = "tray"

    def __init__(self, icon, on_left_click: Callable[[], None] | None = None):
        from PySide6.QtWidgets import QSystemTrayIcon

        self._QSystemTrayIcon = QSystemTrayIcon
        self._tray = QSystemTrayIcon(icon)
        self._on_left_click = on_left_click
        self._menu = None                    # 保持引用，否则 QMenu 会被回收
        self._tray.activated.connect(self._activated)
        self._tray.show()
        self._log_state("首次注册")

    def _activated(self, reason) -> None:      # noqa: ANN001
        if reason == self._QSystemTrayIcon.DoubleClick and self._on_left_click:
            self._on_left_click()

    # ------------------------------------------------------------------ 诊断
    def _log_state(self, when: str) -> None:
        """把「系统认没认这个托盘图标」写进日志。

        打包后「托盘里没图标但进程还在」只有三种可能，这行日志能一次分清：
          · `图标为空=True`      → .ico 没读出来（imageformats/qico 插件或资源路径问题）
          · `可见=False`         → 压根没注册成功
          · 两个都正常           → 注册成功了，只是被 Windows 收进了「隐藏的图标」溢出面板
                                   （Win11 对从没出现过的新 exe 默认就藏起来，属于系统行为）
        """
        try:
            icon = self._tray.icon()
            log.info(
                "托盘图标（%s）：托盘可用=%s 图标可见=%s 图标为空=%s 尺寸=%s 位置=%s",
                when,
                self.is_available(),
                bool(self._tray.isVisible()),
                icon.isNull(),
                [f"{s.width()}x{s.height()}" for s in icon.availableSizes()] or "无",
                self.geometry() or "未知",
            )
            if icon.isNull():
                log.error("托盘图标是空图标：检查包内 assets/ 里的 .ico 与 PySide6/plugins/imageformats/qico")
        except Exception:
            log.exception("读取托盘状态失败（%s）", when)

    def is_available(self) -> bool:
        """系统托盘是否可用（Windows 上基本恒真，远程桌面 / 精简系统可能为假）。"""
        try:
            return bool(self._QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:  # pragma: no cover
            return False

    def geometry(self) -> str:
        """图标在通知区域里的位置；拿不到（没注册上 / 被藏进溢出面板）返回空串。"""
        try:
            rect = self._tray.geometry()
            if rect.isNull() or rect.width() <= 0:
                return ""
            return f"{rect.left()},{rect.top()} {rect.width()}x{rect.height()}"
        except Exception:  # pragma: no cover
            return ""

    def reassert(self) -> None:
        """重新向系统注册一次图标（Explorer 重启、图标被系统吞掉时用）。"""
        try:
            self._tray.hide()
            self._tray.show()
        except Exception:  # pragma: no cover
            log.exception("重新注册托盘图标失败")
            return
        self._log_state("重新注册")

    def recreate(self) -> None:
        """整只重建 QSystemTrayIcon。

        `hide()` / `show()` 只是在同一个已注册的图标上改状态；如果第一次
        `Shell_NotifyIcon(NIM_ADD)` 就没成功（系统里连条目都没有），
        再怎么 show 也不会出现，只能扔掉重来。
        """
        from PySide6.QtGui import QIcon

        icon = self._tray.icon()
        tooltip = self._tray.toolTip()
        menu = self._menu

        try:
            self._tray.activated.disconnect(self._activated)
        except Exception:  # pragma: no cover - 没连上也无所谓
            pass
        try:
            self._tray.hide()
            self._tray.deleteLater()
        except Exception:  # pragma: no cover
            log.exception("销毁旧的托盘图标失败")

        self._tray = self._QSystemTrayIcon(QIcon(icon))
        self._tray.activated.connect(self._activated)
        if menu is not None:
            self._tray.setContextMenu(menu)
        self._tray.setToolTip(tooltip)
        self._tray.show()
        self._log_state("重建")

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


def pystray_menu(entries):  # noqa: ANN001, ANN201 - list[MenuEntry] -> pystray.Menu
    """把共享的 `MenuEntry` 列表翻译成 pystray 的菜单（纯转换，可单测）。"""
    import pystray

    items = []
    for entry in entries:
        if entry.kind == "sep":
            items.append(pystray.Menu.SEPARATOR)
            continue
        action = None
        if entry.kind != "label" and entry.action is not None:
            action = (lambda fn: lambda icon, item: fn())(entry.action)
        items.append(
            pystray.MenuItem(
                entry.title,
                action,
                checked=(lambda _i: entry.checked) if entry.checked else None,
                enabled=entry.enabled and action is not None,
                default=entry.default,
            )
        )
    return pystray.Menu(*items)


class PystrayStatusItem:
    """pystray 版托盘：Qt 托盘注册不上时的第二实现。

    为什么留这一份：打包版里 `QSystemTrayIcon` 会出现「可见=True、图标非空，但通知
    区域既没有条目也没有位置」，而同一个 exe 用 pystray（`dualswitch run` 那条老路）
    在本机是出了图标的 —— 两条实现互不依赖，Qt 那条失灵就换这条顶上。
    菜单条目仍然是共享的 `MenuEntry`，图标仍然用包里的 .ico。
    """

    kind = "tray"

    def __init__(self, on_activate: Callable[[], None]):
        import threading
        import time

        import pystray
        from PIL import Image

        from .. import theme

        self._pystray = pystray
        self._on_activate = on_activate
        self._entries: list = []
        self._title = "屏幕切换"
        self._error = ""                     # 起不来时留原因，供 runner 判断
        self._thread: threading.Thread | None = None

        image = Image.open(theme.app_icon())
        image.load()
        self._icon = pystray.Icon("dualswitch", icon=image, title=self._title)
        # pystray 自带消息循环，必须放在自己的线程里跑（Qt 主线程不能让它阻塞）
        self._thread = threading.Thread(target=self._run, name="pystray-tray", daemon=True)
        self._thread.start()

        # 等它真的把窗口建起来（`_mark_ready` 之后 `_running` 为 True）。
        # 不等的话「pystray 托盘已启动」就是一句谎话 —— 打包版实测过：线程里
        # `ChangeWindowMessageFilterEx` 抛 WinError 5，图标一个都没出现，日志却写着已启动。
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self._error or getattr(self._icon, "_running", False):
                break
            time.sleep(0.02)

        if self._error:
            log.error("pystray 托盘启动失败：%s", self._error)
        elif getattr(self._icon, "_running", False):
            log.info("pystray 托盘已启动（兜底实现）")
        else:
            log.warning("pystray 托盘 2 秒内没就绪，继续观察")

    def _run(self) -> None:
        """线程体：把 pystray 的异常接住并记下来（否则 windowed 版连堆栈都看不到）。"""
        try:
            self._icon.run()
        except Exception as exc:             # pragma: no cover - 依赖系统环境
            self._error = f"{exc.__class__.__name__}: {exc}"
            log.error("pystray 托盘线程异常退出：%s", self._error, exc_info=True)

    @property
    def failed(self) -> bool:
        return bool(self._error)

    # ------------------------------------------------------------------ 菜单
    def _build_menu(self):
        return pystray_menu(self._entries)

    def set_menu(self, entries) -> None:      # noqa: ANN001 - list[MenuEntry]
        self._entries = list(entries)
        menu = self._build_menu()
        self._icon.menu = menu
        try:
            self._icon.update_menu()
        except Exception:  # pragma: no cover - 图标还没起来时 update 会抛
            log.debug("更新 pystray 菜单失败（图标可能尚未就绪）", exc_info=True)

    # ------------------------------------------------------------------ 接口
    def set_tooltip(self, text: str) -> None:
        self._title = text
        self._icon.title = text

    def show(self) -> None:
        self._icon.visible = True

    def hide(self) -> None:
        try:
            self._icon.visible = False
            self._icon.stop()
        except Exception:  # pragma: no cover
            log.exception("停止 pystray 托盘失败")

    def is_visible(self) -> bool:
        if self._error:
            return False
        return bool(getattr(self._icon, "visible", False))

    def is_available(self) -> bool:
        return True

    def geometry(self) -> str:
        """pystray 不暴露图标位置；返回空串表示「查不到」，调用方据此不再重试。"""
        return ""

    def reassert(self) -> None:
        self._icon.visible = True

    def recreate(self) -> None:
        self._icon.visible = True

    def notify(self, title: str, message: str, icon=None, msecs: int = 4000) -> None:  # noqa: ANN001
        try:
            self._icon.notify(message, title)
        except Exception:  # pragma: no cover
            log.debug("pystray 通知失败", exc_info=True)


def build_fallback_status_item(on_click: Callable[[], None]):  # noqa: ANN001
    """Qt 托盘失灵时的备选实现，按「离系统越近越优先」的顺序试：

    1. 自己调 `Shell_NotifyIconW`（`win32tray`）—— 每次调用都返回错误码，能说清原因；
    2. pystray（`dualswitch run` 那条老路），最后一道。
    """
    try:
        from .. import theme

        icon_path = theme.app_icon()
    except Exception:
        icon_path = None

    if icon_path is not None:
        from . import win32tray

        item = win32tray.try_create(icon_path, on_click)
        if item is not None:
            return item
        log.warning("自己的 Win32 托盘没成功，改试 pystray")

    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except Exception:
        log.warning("未安装 pystray / Pillow，无法启用兜底托盘")
        return None
    try:
        return PystrayStatusItem(on_click)
    except Exception:
        log.exception("创建 pystray 托盘失败")
        return None


def build_app_menu(runner):  # noqa: ANN001
    """Windows 没有全局菜单栏，菜单都在托盘里。"""
    return None


def activate(window) -> None:  # noqa: ANN001
    return None


def set_dock_visible(visible: bool) -> None:  # noqa: ARG001
    """Windows 没有程序坞，任务栏按钮由窗口自己管理，这里什么都不用做。"""
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
