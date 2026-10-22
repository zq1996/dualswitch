"""v2 主窗口：780×580 宽窗口 + 左侧栏导航 + 页面栈 + 底栏（INTERACTIONS §0.1）。

**一个窗口铁律**：切换/剪贴板/设置都是窗口内换页（QStackedWidget），
绝不弹独立面板。预览窗是临时悬浮层、菜单栏菜单是系统菜单，都不算窗口。

布局：

    ┌───────────────────────────────────────────────┐
    │ 系统标题栏（红绿灯 + 居中「屏幕切换」）              │
    ├──────────┬────────────────────────────────────┤
    │ 侧栏156px │ 内容区（切换页/剪贴板页/配对/设置）    │
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

标题栏不放自绘工具钮（用户 2026-10-21 明确：右上角两个图标按钮不要了）：窗口直接
用系统标题栏，侧栏从标题栏下沿开始。页面切换只靠侧栏与菜单栏菜单。

**不要再开 full-size content view**：那样内容会铺到标题栏底下，侧栏/内容区会吃掉
标题栏的拖拽区域 → 窗口拖不动（用户 2026-10-21 报的 bug）。标题栏里也不再有自绘
控件，没有理由让内容顶上去。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from .widgets import KeyCaps, NavButton, dot, label

PAGE_SWITCH, PAGE_CLIP, PAGE_SETTINGS, PAGE_PAIR = 0, 1, 2, 3

# footer 对齐规范（8px 栅格）：
#   [10px 行内边距][16px 图标/灯列][8px 间距][文字列]
# 三行（状态 / 热键 / 退出）共用同一条文字基线，不再各用各的 padding。
# 退出按钮自己铺满 footer 宽度，10px 行内边距画在按钮内部，
# hover 粉底因此从侧栏内沿开始，图标与粉底左边缘之间还有 10px 呼吸。
FOOT_PAD_L = 10
FOOT_LEAD_W = 16
FOOT_GAP = 8
FOOT_TEXT_X = FOOT_PAD_L + FOOT_LEAD_W + FOOT_GAP     # 按钮/行内的文字起点


class QuitButton(QPushButton):
    """侧栏 footer 的退出动作（INTERACTIONS §13）：低存在感，hover 才变危险色。

    自绘而不是 QSS：退出图标要落在和状态灯/热键同一个 16px 引导列里，
    QPushButton 的 icon+text 内边距做不到精确对齐。
    """

    def __init__(self, parent=None):
        super().__init__("退出应用", parent)
        self.setObjectName("quitBtn")
        self.setFixedHeight(31)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("退出应用（⌘Q）")
        self.setFlat(True)
        self._collapsed = False
        # 自己记 hover 状态：自绘按钮不经过 QStyle，不能指望 style 帮我们触发重绘
        self._hovered = False
        self.setAttribute(Qt.WA_Hover, True)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.update()

    def enterEvent(self, event):  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        active = self._hovered or self.isDown()
        # 用户指定：hover 只加淡红底，图标和文字保持灰色，不做红字
        color = t.ink3
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if active:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor("#FEF2F2"))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 7, 7)
        glyph = icons.svg_pixmap("power", 14, color, 1.7)
        gw = glyph.width() / (glyph.devicePixelRatio() or 1)
        if self._collapsed:
            p.drawPixmap(
                int((self.width() - gw) / 2), int((self.height() - gw) / 2), glyph
            )
        else:
            ix = int(FOOT_PAD_L + (FOOT_LEAD_W - gw) / 2)
            p.drawPixmap(ix, int((self.height() - gw) / 2), glyph)
            p.setFont(theme.qfont(11, 600))
            p.setPen(theme.qcolor(color))
            p.drawText(
                FOOT_TEXT_X, 0, self.width() - FOOT_TEXT_X - 9, self.height(),
                Qt.AlignLeft | Qt.AlignVCenter, "退出应用",
            )
        p.end()


class SidebarHandle(QPushButton):
    """侧栏边缘的收起把手（方案 A）：15×46 半浮在分割线上，不占布局。"""

    W, H = 15, 46
    TOP_RATIO = 0.44

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("收起侧栏")
        self._collapsed = False
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(theme.qcolor(theme.tokens().shadow))
        self.setGraphicsEffect(shadow)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.setToolTip("展开侧栏" if self._collapsed else "收起侧栏")
        self.update()

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        hovered = self.underMouse()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(theme.qcolor(t.accent if hovered else t.line))
        p.setBrush(theme.qcolor(t.card))
        p.drawRoundedRect(rect, 8, 8)
        # 箭头直接用 QPainter 画：不依赖图标库，任何环境下都不会出现空白把手
        color = t.accent_deep if hovered else t.ink2
        p.setPen(QPen(theme.qcolor(color), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx, cy = self.width() / 2, self.height() / 2
        if self._collapsed:
            p.drawLine(QPointF(cx - 2.5, cy - 5), QPointF(cx + 2.5, cy))
            p.drawLine(QPointF(cx + 2.5, cy), QPointF(cx - 2.5, cy + 5))
        else:
            p.drawLine(QPointF(cx + 2.5, cy - 5), QPointF(cx - 2.5, cy))
            p.drawLine(QPointF(cx - 2.5, cy), QPointF(cx + 2.5, cy + 5))
        p.end()

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)


def _lead_slot(widget: QWidget | None = None) -> QWidget:
    """footer 的 16px 引导列：状态灯/图标都居中在这一列，文字因此能对齐。"""
    slot = QWidget()
    slot.setFixedWidth(FOOT_LEAD_W)
    lay = QHBoxLayout(slot)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    if widget is not None:
        lay.addWidget(widget, 0, Qt.AlignCenter)
    return slot


class MainWindow(QWidget):
    switchRequested = Signal()
    forceRequested = Signal()
    deviceClicked = Signal(str)
    hiddenToBackground = Signal()
    pageChanged = Signal(int)
    quitRequested = Signal()

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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.root = QFrame()
        self.root.setObjectName("root")
        outer.addWidget(self.root)

        root_lay = QVBoxLayout(self.root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ---- 侧栏 + 内容（从标题栏下沿直接开始，不再占一条工具条）----
        self.body = body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self.sidebar = sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_W)
        self._collapsed = False
        self.sidebar_layout = side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(10, 14, 10, 12)
        side_lay.setSpacing(2)

        # 收起按钮是浮在侧栏右边缘的把手（方案 A），不占顶部布局
        self.sec_core = label("核心", "navSec")
        side_lay.addWidget(self.sec_core)
        self.nav_switch = NavButton("switch", "切换")
        self.nav_clip = NavButton("clip", "剪贴板")
        side_lay.addWidget(self.nav_switch)
        side_lay.addWidget(self.nav_clip)
        self.sec_system = label("系统", "navSec")
        side_lay.addWidget(self.sec_system)
        self.nav_pair = NavButton("pair", "配对电脑")
        self.nav_settings = NavButton("settings", "设置")
        side_lay.addWidget(self.nav_pair)
        side_lay.addWidget(self.nav_settings)

        side_lay.addStretch(1)
        foot = QFrame()
        foot.setObjectName("navFoot")
        self.foot = foot
        self.foot_layout = foot_lay = QVBoxLayout(foot)
        # footer 左内边距不放在布局上：退出按钮要铺满整行，粉底才能从侧栏内沿开始；
        # 状态/热键两行各自在行内缩进 10px，文字列仍与退出按钮对齐。
        foot_lay.setContentsMargins(0, 10, 0, 2)
        foot_lay.setSpacing(6)

        self.conn_box = QWidget()
        conn_row = QHBoxLayout(self.conn_box)
        conn_row.setContentsMargins(FOOT_PAD_L, 0, 0, 0)
        conn_row.setSpacing(FOOT_GAP)
        self.side_dot = dot(theme.tokens().ok, 6)
        self.side_conn = label("未连接对端", "navFootText")
        conn_row.addWidget(_lead_slot(self.side_dot), 0, Qt.AlignVCenter)
        conn_row.addWidget(self.side_conn, 1)
        foot_lay.addWidget(self.conn_box)

        self.hk_box = QWidget()
        hk_row = QHBoxLayout(self.hk_box)
        hk_row.setContentsMargins(FOOT_PAD_L, 0, 0, 0)
        hk_row.setSpacing(FOOT_GAP)
        self.side_hotkey_text = label("热键", "navFootText")
        self.side_hotkey = KeyCaps(theme.keycap_labels(hotkey_label))
        hk_row.addWidget(_lead_slot(), 0, Qt.AlignVCenter)
        hk_row.addWidget(self.side_hotkey_text, 0, Qt.AlignVCenter)
        hk_row.addWidget(self.side_hotkey, 0, Qt.AlignVCenter)
        hk_row.addStretch(1)
        foot_lay.addWidget(self.hk_box)

        # 退出应用（§13）：状态行下方，分隔线 + 整行按钮，所有页面都在
        foot_lay.addSpacing(3)
        self.quit_sep = QFrame()
        self.quit_sep.setFixedHeight(1)
        self.quit_sep.setStyleSheet(
            f"background:{theme.tokens().line_soft};border:0;"
        )
        sep_box = QWidget()
        self.quit_sep_box = sep_box
        sep_row = QHBoxLayout(sep_box)
        sep_row.setContentsMargins(FOOT_PAD_L, 0, 0, 0)
        sep_row.addWidget(self.quit_sep)
        foot_lay.addWidget(sep_box)
        foot_lay.addSpacing(1)
        self.btn_quit = QuitButton(self)
        self.btn_quit.clicked.connect(lambda: self.quitRequested.emit())
        foot_lay.addWidget(self.btn_quit)
        side_lay.addWidget(foot)
        body_lay.addWidget(sidebar)

        self.pages = QStackedWidget()
        # 注意：容器上不能裸 setStyleSheet（无选择器）—— 会级联盖掉子控件的
        # objectName 背景（#primary 主按钮会被刷成透明，白底白字）。
        self.pages.setObjectName("pageStack")
        body_lay.addWidget(self.pages, 1)
        root_lay.addWidget(body, 1)

        # 边缘把手：不参与布局，浮在侧栏和内容的分割线上
        self.side_handle = SidebarHandle(body)
        self.side_handle.clicked.connect(self.toggle_sidebar)
        self.side_handle.hide()
        self.side_handle.raise_()
        self._handle_timer = QTimer(self)
        self._handle_timer.setInterval(150)
        self._handle_timer.timeout.connect(self._sync_sidebar_handle)
        self._handle_timer.start()

        # 导航
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for btn in (self.nav_switch, self.nav_clip, self.nav_pair, self.nav_settings):
            self._nav_group.addButton(btn)
        self.nav_switch.setChecked(True)
        self.nav_switch.clicked.connect(lambda: self.show_page(PAGE_SWITCH))
        self.nav_clip.clicked.connect(lambda: self.show_page(PAGE_CLIP))
        self.nav_pair.clicked.connect(lambda: self.show_page(PAGE_PAIR))
        self.nav_settings.clicked.connect(lambda: self.show_page(PAGE_SETTINGS))
        self._page = PAGE_SWITCH

    # ------------------------------------------------------------------ 页面
    def attach_pages(self, switch_page, clip_page, settings_page, pairing_page=None) -> None:
        self.switch_page = switch_page
        self.clip_page = clip_page
        self.settings_page = settings_page
        self.pairing_page = pairing_page
        self.pages.addWidget(switch_page)
        self.pages.addWidget(clip_page)
        self.pages.addWidget(settings_page)
        if pairing_page is not None:
            self.pages.addWidget(pairing_page)
        self.switch_page.switchRequested.connect(self.switchRequested)
        self.switch_page.forceRequested.connect(self.forceRequested)
        self.switch_page.deviceClicked.connect(self.deviceClicked)

    def show_page(self, index: int) -> None:
        self._page = index
        self.pages.setCurrentIndex(index)
        self.nav_switch.setChecked(index == PAGE_SWITCH)
        self.nav_clip.setChecked(index == PAGE_CLIP)
        self.nav_pair.setChecked(index == PAGE_PAIR)
        self.nav_settings.setChecked(index == PAGE_SETTINGS)
        for btn in (self.nav_switch, self.nav_clip, self.nav_pair, self.nav_settings):
            btn.update()
        title = {
            PAGE_SWITCH: "屏幕切换",
            PAGE_CLIP: "剪贴板",
            PAGE_PAIR: "配对电脑",
            PAGE_SETTINGS: "设置",
        }[index]
        self.setWindowTitle(f"屏幕切换 · {title}" if index != PAGE_SWITCH else "屏幕切换")
        if index != PAGE_CLIP:
            clip = getattr(self, "clip_page", None)
            if clip is not None:
                clip.preview_controller.hide_now()
        self.pageChanged.emit(index)

    def current_page(self) -> int:
        return self._page

    def on_clipboard_page(self) -> bool:
        return self._page == PAGE_CLIP

    def set_nav_count(self, count: int) -> None:
        self.nav_clip.set_count(str(count) if count else "")

    def is_sidebar_collapsed(self) -> bool:
        return self._collapsed

    def toggle_sidebar(self) -> None:
        self.set_sidebar_collapsed(not self._collapsed)

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        """收起 = 只留导航图标；数量徽章/配对状态灯缩到图标右上角。"""
        self._collapsed = bool(collapsed)
        self.sidebar.setFixedWidth(
            theme.SIDEBAR_W_COLLAPSED if self._collapsed else theme.SIDEBAR_W
        )
        pad = 8 if self._collapsed else 10
        self.sidebar_layout.setContentsMargins(pad, 14, pad, 12)
        self.sec_core.setVisible(not self._collapsed)
        self.sec_system.setVisible(not self._collapsed)
        for nav in (self.nav_switch, self.nav_clip, self.nav_pair, self.nav_settings):
            nav.set_collapsed(self._collapsed)
        self.conn_box.setVisible(not self._collapsed)
        self.hk_box.setVisible(not self._collapsed)
        self.quit_sep_box.setVisible(not self._collapsed)
        self.quit_sep.setVisible(not self._collapsed)
        self.btn_quit.set_collapsed(self._collapsed)
        self.side_handle.set_collapsed(self._collapsed)
        self._place_sidebar_handle()

    def _place_sidebar_handle(self) -> None:
        if not hasattr(self, "side_handle"):
            return
        x = self.sidebar.width() - self.side_handle.W // 2
        y = int((self.body.height() - self.side_handle.H) * SidebarHandle.TOP_RATIO)
        self.side_handle.move(x, max(8, y))
        self.side_handle.raise_()

    def _sync_sidebar_handle(self) -> None:
        """鼠标在侧栏内或把手附近时露出把手；离开就隐藏。"""
        if not self.isVisible() or self.sidebar.width() <= 0:
            self.side_handle.hide()
            return
        self._place_sidebar_handle()       # 布局稳定后再定位，避免首次 show 时算在 0 高
        local = self.body.mapFromGlobal(QCursor.pos())
        if not self.body.rect().contains(local):
            self.side_handle.hide()
            return
        near = (
            local.x() <= self.sidebar.width() - 2
            or self.side_handle.geometry().adjusted(-10, -24, 10, 24).contains(local)
        )
        if near:
            self.side_handle.show()
            self.side_handle.raise_()
        else:
            self.side_handle.hide()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_sidebar_handle()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._place_sidebar_handle()

    def leaveEvent(self, event):  # noqa: N802
        self.side_handle.hide()
        super().leaveEvent(event)

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
        if keys:
            self.side_hotkey_text.setText("热键")
            self.side_hotkey_text.setStyleSheet(
                f"color:{theme.tokens().ink3};font-size:10px;"
            )
        else:
            self.side_hotkey_text.setText("热键未启用")
            self.side_hotkey_text.setStyleSheet(
                f"color:{theme.tokens().warn};font-size:10px;"
            )

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
