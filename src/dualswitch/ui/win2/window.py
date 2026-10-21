"""Windows v2 主窗口：自绘无边框标题栏 + 侧栏 + 页面栈 + 底栏（设计 §3）。

**一个窗口铁律**：切换/剪贴板/设置都是窗口内换页（QStackedWidget），绝不弹独立面板。
预览气泡是临时悬浮层（顶层窗口，不算主窗口）；清空确认是**窗口内遮罩**（§8），
比 mac2 的 QDialog 更彻底。

    ┌───────────────────────────────────────────────┐  ← 自绘标题栏 40
    │ [icon] 屏幕切换                        ─   ✕   │     46×32，Win11 hover
    ├──────────┬────────────────────────────────────┤
    │ 核心      │                                    │
    │  切换     │  内容区（切换页/剪贴板页/设置页）      │
    │  剪贴板 12│                                    │
    │ 系统      │                                    │
    │  设置     │                                    │
    │ ──────── │                                    │
    │ ● 已连接  │                                    │
    │ 热键 Alt Space                                │
    ├──────────┴────────────────────────────────────┤
    │ ● 已连接 Mac · 剪贴板已开启            v0.9.0  │  ← 底栏 30
    └───────────────────────────────────────────────┘

标题栏右上角**只有最小化 / 关闭**（设计 §2：不放剪贴板/齿轮，功能入口统一走侧栏）。

关于阴影：设计 §3.1 给了 `0 2px 6px …` 的窗口投影，但本窗口是 780×580 固定尺寸、
内容铺满整窗（没有留给投影出血的边距），QGraphicsDropShadowEffect 会被窗口矩形裁掉，
所以这里用 1px 描边 `rgba(16,24,40,.07)` 立住边界 —— 与设计同色，只是不画外投影。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import KeyCaps, NavButton, dot, label

PAGE_SWITCH, PAGE_CLIP, PAGE_SETTINGS = 0, 1, 2

PAGE_TITLES = {PAGE_SWITCH: "屏幕切换", PAGE_CLIP: "剪贴板", PAGE_SETTINGS: "设置"}


# --------------------------------------------------------------------------- #
# caption 按钮（最小化 / 关闭，46×32 r6）
# --------------------------------------------------------------------------- #
class CaptionButton(QAbstractButton):
    """标题栏按钮：自绘线性字形，避免 Unicode 字形大小与基线不一致。

    hover：最小化 `#F0F0F0` 底；关闭 `#C42B1C` 底 + 白叉（设计 §3.1）。
    """

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(theme.CAPTION_W, theme.CAPTION_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("最小化" if kind == "min" else "关闭")

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        hover = self.underMouse()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if hover:
            p.setPen(Qt.NoPen)
            p.setBrush(
                QColor(t.caption_close if self.kind == "close" else t.caption_hover)
            )
            p.drawRoundedRect(rect, theme.CAPTION_R, theme.CAPTION_R)

        white = hover and self.kind == "close"
        color = QColor(t.on_accent) if white else QColor(t.ink2)
        c = QPointF(self.width() / 2, self.height() / 2)
        pen = QPen(color, 1.25)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.kind == "min":
            p.drawLine(QPointF(c.x() - 5, c.y() + 0.5), QPointF(c.x() + 5, c.y() + 0.5))
        else:
            p.drawLine(QPointF(c.x() - 4.2, c.y() - 4.2), QPointF(c.x() + 4.2, c.y() + 4.2))
            p.drawLine(QPointF(c.x() + 4.2, c.y() - 4.2), QPointF(c.x() - 4.2, c.y() + 4.2))
        p.end()


# --------------------------------------------------------------------------- #
# 自绘标题栏
# --------------------------------------------------------------------------- #
class TitleBar(QWidget):
    """40px 自绘标题栏：左「图标 + 标题」，右 最小化 / 关闭；整条可拖动窗口。"""

    minimizeClicked = Signal()
    closeClicked = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("titlebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(theme.TITLEBAR_H)
        self._drag_pos: QPointF | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(8)

        self.icon = QLabel()
        try:
            self.icon.setPixmap(QIcon(str(theme.asset("screen-switch.ico"))).pixmap(QSize(16, 16)))
        except Exception:  # noqa: BLE001 - 缺资源不影响开窗
            pass
        self.icon.setFixedSize(16, 16)
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)

        self.title_label = label(title, "tbTitle")
        lay.addWidget(self.title_label, 0, Qt.AlignVCenter)
        lay.addStretch(1)

        self.btn_min = CaptionButton("min")
        self.btn_close = CaptionButton("close")
        lay.addWidget(self.btn_min, 0, Qt.AlignVCenter)
        lay.addWidget(self.btn_close, 0, Qt.AlignVCenter)
        self.btn_min.clicked.connect(self.minimizeClicked)
        self.btn_close.clicked.connect(self.closeClicked)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    # ---- 无边框窗口拖动 ----
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


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class MainWindow(QWidget):
    switchRequested = Signal()
    forceRequested = Signal()
    deviceClicked = Signal(str)
    hiddenToBackground = Signal()
    quitRequested = Signal()          # 设置里关掉「关闭窗口后仍在托盘运行」→ 关窗即退出

    def __init__(self, cfg, hotkey_label: str, version: str, monitor_name: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.hotkey_label = hotkey_label
        self.version = version
        self.monitor_name = monitor_name
        self._allow_close = False
        self.withdraw_to_tray = True
        self.quit_on_close = False

        self.setWindowTitle("屏幕切换")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(theme.WIN_W, theme.WIN_H)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.root = QFrame()
        self.root.setObjectName("root")
        outer.addWidget(self.root)

        root_lay = QVBoxLayout(self.root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ---- 标题栏 ----
        self.titlebar = TitleBar("屏幕切换")
        self.titlebar.minimizeClicked.connect(self.showMinimized)
        self.titlebar.closeClicked.connect(self.close)
        root_lay.addWidget(self.titlebar)

        # ---- 侧栏 + 内容 ----
        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_W)
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(10, 6, 10, 12)
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
        # 容器上不能裸 setStyleSheet（无选择器）—— 会级联盖掉子控件 objectName 背景
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
        title = PAGE_TITLES.get(index, "屏幕切换")
        self.setWindowTitle(f"屏幕切换 · {title}" if index != PAGE_SWITCH else "屏幕切换")
        self.titlebar.set_title("屏幕切换")
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
        """`text` = hotkey describe 串；侧栏改成键帽展示（§3.4 第 4 处）。"""
        self.hotkey_label = text
        self.side_hotkey.set_keys(theme.keycap_labels(text))

    def set_footer(self, text: str, ok: bool = True) -> None:
        t = theme.tokens()
        self.foot_text.setText(text)
        self.foot_dot.setStyleSheet(
            f"background:{t.ok if ok else t.idle};border-radius:3px;border:0;"
        )

    # ------------------------------------------------------------------ 事件
    def closeEvent(self, event: QCloseEvent):  # noqa: N802
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        if self.quit_on_close:
            # 设置里关掉「关闭窗口后仍在托盘运行」：关窗就是退出（§7.2）
            self.quitRequested.emit()
            return
        if not self.withdraw_to_tray:
            # 托盘不可用：关窗 = 最小化，否则窗口再也叫不回来（§4.1）
            self.showMinimized()
            return
        self.hide()
        self.hiddenToBackground.emit()

    def quit_completely(self) -> None:
        self._allow_close = True
        self.close()
