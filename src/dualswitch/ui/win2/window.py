"""Windows v2 主窗口：自绘无边框标题栏 + 侧栏 + 页面栈 + 底栏（设计 §3）。

**一个窗口铁律**：切换/剪贴板/配对电脑/设置都是窗口内换页（QStackedWidget），绝不弹独立面板。
预览气泡是临时悬浮层（顶层窗口，不算主窗口）；清空/解除配对确认是**窗口内遮罩**（§8），
比 mac2 的 QDialog / 页内确认块更彻底。

    ┌───────────────────────────────────────────────┐  ← 自绘标题栏 40
    │ [icon] 屏幕切换                        ─   ✕   │     46×32，Win11 hover
    ├──────────┬────────────────────────────────────┤
    │ 核心      │                                    │
    │  切换     │  内容区（切换/剪贴板/配对电脑/设置）   │
    │  剪贴板 12│                                    │
    │ 系统      │                                    │
    │  配对电脑 ●│                                   │
    │  设置     │                                    │
    │ ──────── │                                    │
    │ ● 已连接  │                                    │
    │ 热键 Alt Space                                │
    │ 退出应用  │                                    │
    ├──────────┴────────────────────────────────────┤
    │ ● 已连接 Mac · 剪贴板已开启            v0.9.0  │  ← 底栏 30
    └───────────────────────────────────────────────┘

2026-10-22 用户拍板「侧栏跟 mac2」（`docs/UI-WINDOWS2-DESIGN.md` §14.2 从待拍板变已拍板）：
  · 侧栏 188 → **156**，边缘 15×46 把手可收到 **56**（收起只留图标，tooltip 补文字）；
  · footer 加「退出应用」——Windows 关窗=最小化，这个入口比 mac 上更有价值；
  · **底栏 30px 保留**（§2 差异表：托盘不可用时它是唯一的状态出口，mac2 删了是因为有菜单栏）。

标题栏右上角**只有最小化 / 关闭**（设计 §2：不放剪贴板/齿轮，功能入口统一走侧栏）。

关于阴影：设计 §3.1 给了 `0 2px 6px …` 的窗口投影，但本窗口是 780×580 固定尺寸、
内容铺满整窗（没有留给投影出血的边距），QGraphicsDropShadowEffect 会被窗口矩形裁掉，
所以这里用 1px 描边 `rgba(16,24,40,.07)` 立住边界 —— 与设计同色，只是不画外投影。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
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

PAGE_TITLES = {
    PAGE_SWITCH: "屏幕切换",
    PAGE_CLIP: "剪贴板",
    PAGE_SETTINGS: "设置",
    PAGE_PAIR: "配对电脑",
}

# footer 对齐规范（8px 栅格，与 mac2 同一套）：
#   [10px 行内边距][16px 图标/灯列][8px 间距][文字列]
# 状态行 / 热键行 / 退出按钮共用同一条文字基线，不再各用各的 padding。
FOOT_PAD_L = 10
FOOT_LEAD_W = 16
FOOT_GAP = 8
FOOT_TEXT_X = FOOT_PAD_L + FOOT_LEAD_W + FOOT_GAP     # 按钮/行内的文字起点



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
# 侧栏 footer 的两个自绘控件（跟 mac2 同一套，2026-10-22 拍板）
# --------------------------------------------------------------------------- #
class QuitButton(QPushButton):
    """侧栏 footer 的退出动作（INTERACTIONS §13）：低存在感，hover 才变危险色。

    自绘而不是 QSS：退出图标要落在和状态灯/热键同一个 16px 引导列里，
    QPushButton 的 icon+text 内边距做不到精确对齐。
    Windows 口径：tooltip 写 `Ctrl+Q`（mac2 是 `⌘Q`）。
    """

    def __init__(self, parent=None):
        super().__init__("退出应用", parent)
        self.setObjectName("quitBtn")
        self.setFixedHeight(31)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("退出应用（Ctrl+Q）")
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
        # 用户指定（§13.4）：hover 只加淡红底，图标和文字保持灰色，不做红字
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
    """侧栏边缘的收起把手（方案 A）：15×46 半浮在分割线上，不占布局。

    `design/redesign/sidebar-collapse-options.html` 三个方案里用户选了 A：
    常态隐藏，鼠标进侧栏或靠近把手才出现（带轻微阴影）。
    """

    W, H = theme.SIDEBAR_HANDLE_W, theme.SIDEBAR_HANDLE_H
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
            # 走 theme.app_icon()：它读的是 ui/assets 里的运行时副本（新图标由
            # design/icon/build_icon.py 自动同步过去），不要在这里写死文件名。
            self.icon.setPixmap(theme.app_icon().pixmap(QSize(16, 16)))
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
    pageChanged = Signal(int)
    quitRequested = Signal()          # 侧栏「退出应用」/ 设置里关掉托盘 → 真退出

    def __init__(self, cfg, hotkey_label: str, version: str, monitor_name: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.hotkey_label = hotkey_label
        self.version = version
        self.monitor_name = monitor_name
        self._allow_close = False
        self.withdraw_to_tray = True
        self.quit_on_close = False
        self._collapsed = False

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
        self.body = body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self.sidebar = sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_W)
        self.sidebar_layout = side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(10, 6, 10, 12)
        side_lay.setSpacing(2)

        self.sec_core = label("核心", "navSec")
        side_lay.addWidget(self.sec_core)
        self.nav_switch = NavButton("switch", "切换")
        self.nav_clip = NavButton("clip", "剪贴板")
        side_lay.addWidget(self.nav_switch)
        side_lay.addWidget(self.nav_clip)
        self.sec_system = label("系统", "navSec")
        side_lay.addWidget(self.sec_system)
        # 配对电脑放在「设置」上方（§12.3）；右侧状态灯由 runner 刷（灰/绿/红）
        self.nav_pair = NavButton("pair", "配对电脑")
        self.nav_settings = NavButton("settings", "设置")
        side_lay.addWidget(self.nav_pair)
        side_lay.addWidget(self.nav_settings)

        side_lay.addStretch(1)
        foot = QFrame()
        foot.setObjectName("navFoot")
        self.foot = foot
        self.foot_layout = foot_lay = QVBoxLayout(foot)
        # footer 左内边距不放在布局上：退出按钮要铺满整行，hover 淡红底才能从
        # 侧栏内沿开始；状态/热键两行各自在行内缩进 10px，文字列仍与按钮对齐。
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
        self.quit_sep.setStyleSheet(f"background:{theme.tokens().line_soft};border:0;")
        self.quit_sep_box = sep_box = QWidget()
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
        # 容器上不能裸 setStyleSheet（无选择器）—— 会级联盖掉子控件 objectName 背景
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

        # ---- 底栏已删除（2026-10-22 用户：「最下边的版本号那行不要了」）----
        # 原来那 30px 放「● 已连接 Mac · 剪贴板已开启 … v1.0.0」，侧栏 footer 已经有
        # 连接状态，版本号对日常使用没价值 —— 与 mac2 一样删掉，窗口高度全给内容。

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
        title = PAGE_TITLES.get(index, "屏幕切换")
        self.setWindowTitle(f"屏幕切换 · {title}" if index != PAGE_SWITCH else "屏幕切换")
        self.titlebar.set_title("屏幕切换")
        if index != PAGE_CLIP:
            clip = getattr(self, "clip_page", None)
            if clip is not None:
                clip.preview_controller.hide_now()
        # 配对页的 activate / deactivate 交给 runner 处理（它才知道引擎与托盘菜单）
        self.pageChanged.emit(index)

    def current_page(self) -> int:
        return self._page

    def on_clipboard_page(self) -> bool:
        return self._page == PAGE_CLIP

    def set_nav_count(self, count: int) -> None:
        self.nav_clip.set_count(str(count) if count else "")

    # ------------------------------------------------------------------ 侧栏收起
    def is_sidebar_collapsed(self) -> bool:
        return self._collapsed

    def toggle_sidebar(self) -> None:
        self.set_sidebar_collapsed(not self._collapsed)

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        """收起 = 只留导航图标；数量徽章/配对状态灯缩到图标右上角（§0.4）。"""
        self._collapsed = bool(collapsed)
        self.sidebar.setFixedWidth(
            theme.SIDEBAR_W_COLLAPSED if self._collapsed else theme.SIDEBAR_W
        )
        pad = 8 if self._collapsed else 10
        self.sidebar_layout.setContentsMargins(pad, 6, pad, 12)
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
        """鼠标在侧栏内或把手附近时露出把手；离开就隐藏（方案 A）。"""
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
        """`text` = hotkey describe 串；侧栏改成键帽展示（§3.4 第 4 处）。

        热键未启用时把「热键」两个字换成警示色 —— 与 mac2 同一处理（§14 跟随项）。
        """
        self.hotkey_label = text
        keys = theme.keycap_labels(text)
        self.side_hotkey.set_keys(keys)
        t = theme.tokens()
        if keys:
            self.side_hotkey_text.setText("热键")
            self.side_hotkey_text.setStyleSheet(f"color:{t.ink3};font-size:10px;")
        else:
            self.side_hotkey_text.setText("热键未启用")
            self.side_hotkey_text.setStyleSheet(f"color:{t.warn};font-size:10px;")

    def set_footer(self, text: str, ok: bool = True) -> None:  # noqa: ARG002
        """底栏已删除（2026-10-22）：保留空实现，免得旧调用点炸。

        连接状态看侧栏 footer 的状态行（`set_side_status`）。
        """

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
