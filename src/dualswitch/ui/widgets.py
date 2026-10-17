"""自绘控件：标题栏、指示灯、设备卡、状态卡、列表行、开关。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def soft_shadow(widget: QWidget, color: str = "#101828", blur: int = 28, dy: int = 10, alpha: int = 46):
    """给控件加一层柔和投影（QSS 没有 box-shadow）。"""
    effect = QGraphicsDropShadowEffect(widget)
    c = QColor(color)
    c.setAlpha(alpha)
    effect.setColor(c)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    widget.setGraphicsEffect(effect)
    return effect


def label(text: str, name: str = "", size: float | None = None, weight=None) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    if size is not None or weight is not None:
        f = lb.font()
        if size is not None:
            f.setPointSizeF(size)
        if weight is not None:
            f.setWeight(weight)
        lb.setFont(f)
    return lb


class ElidedLabel(QLabel):
    """超长文本自动中间省略（状态栏路径、长配置值）。setText 仍收完整文本。"""

    def __init__(self, text: str = "", name: str = "", parent=None):
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._apply()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._apply()

    def full_text(self) -> str:
        return self._full

    def _apply(self) -> None:
        fm = self.fontMetrics()
        width = self.width() - 1
        shown = self._full if width <= 12 else fm.elidedText(self._full, Qt.ElideMiddle, width)
        if shown != super().text():
            super().setText(shown)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply()


def hline(color: str = theme.LINE_SOFT) -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{color};border:0;")
    return line


def logo_pixmap(kind: str, size: int, dim: bool = False) -> QPixmap:
    """加载 Windows / Apple 官方图标（已预渲染成 PNG，含置灰版）。"""
    suffix = "-logo-dim.png" if dim else "-logo.png"
    pm = QPixmap(str(theme.asset(f"{kind}{suffix}")))
    return pm.scaled(
        QSize(size, size),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )


# --------------------------------------------------------------------------- #
# 齿轮路径（Windows 标题栏按钮与列表图标共用）
# --------------------------------------------------------------------------- #
def paint_gear(p: QPainter, c: QPointF, color: QColor, scale: float = 1.0) -> None:
    """在 c 处画一个 8 齿齿轮；scale=1 时直径约 14px。"""
    body = QPainterPath()
    body.addEllipse(QPointF(0, 0), 5.3, 5.3)
    for i in range(8):
        tooth = QPainterPath()
        tooth.addRoundedRect(QRectF(-1.15, -7.0, 2.3, 3.4), 0.85, 0.85)
        spin = QTransform()
        spin.rotate(i * 45)
        body = body.united(spin.map(tooth))
    hole = QPainterPath()
    hole.addEllipse(QPointF(0, 0), 2.55, 2.55)
    place = QTransform()
    place.translate(c.x(), c.y())
    place.scale(scale, scale)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(place.map(body.subtracted(hole)))


# --------------------------------------------------------------------------- #
# 指示灯
# --------------------------------------------------------------------------- #
class Led(QWidget):
    """亮灯 = 绿色发光；灭灯 = 灰色内凹。18px 画布，灯体直径 9。"""

    def __init__(self, on: bool = False, parent=None):
        super().__init__(parent)
        self._on = on
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)

        if self._on:
            glow = QRadialGradient(c, 9)
            glow.setColorAt(0.0, QColor(52, 199, 89, 120))
            glow.setColorAt(0.6, QColor(52, 199, 89, 45))
            glow.setColorAt(1.0, QColor(52, 199, 89, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, 9, 9)

            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(22, 163, 74, 46), 2.4))
            p.drawEllipse(c, 6.2, 6.2)

            body = QRadialGradient(QPointF(c.x() - 1.2, c.y() - 1.4), 5.6)
            body.setColorAt(0.0, QColor("#CDF7DC"))
            body.setColorAt(0.55, QColor(theme.LED_CORE))
            body.setColorAt(1.0, QColor(theme.LED_DEEP))
            p.setPen(Qt.NoPen)
            p.setBrush(body)
            p.drawEllipse(c, 4.6, 4.6)
        else:
            body = QRadialGradient(QPointF(c.x() - 1.0, c.y() - 1.2), 5.6)
            body.setColorAt(0.0, QColor("#EBEFF5"))
            body.setColorAt(0.6, QColor("#C6CDDA"))
            body.setColorAt(1.0, QColor("#AAB3C2"))
            p.setPen(Qt.NoPen)
            p.setBrush(body)
            p.drawEllipse(c, 4.5, 4.5)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(16, 24, 40, 34), 1.1))
            p.drawEllipse(c, 4.5, 4.5)
        p.end()


# --------------------------------------------------------------------------- #
# 设备卡
# --------------------------------------------------------------------------- #
class DeviceCard(QFrame):
    """一台机器：官方图标 + 名称 + 端口 + 输入源 + 指示灯。"""

    clicked = Signal(str)

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.host = host
        self._active = False
        self._kind = "windows" if host == "windows" else "apple"
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 11, 12, 11)
        root.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        self.logo = QLabel()
        self.logo.setFixedSize(26, 26)
        self.led = Led(False)
        top.addWidget(self.logo)
        top.addStretch(1)
        top.addWidget(self.led)
        root.addLayout(top)

        self.name = label("", "devNameDim")
        self.name.setContentsMargins(0, 8, 0, 0)
        self.port = label("", "devPort")
        self.port.setContentsMargins(0, 3, 0, 0)
        self.state = label("", "devStateOff")
        self.state.setContentsMargins(0, 8, 0, 0)
        root.addWidget(self.name)
        root.addWidget(self.port)
        root.addWidget(self.state)

        self.set_active(False)

    def set_content(self, name: str, port: str, value: int) -> None:
        self.name.setText(name)
        self.port.setText(f"{port} · 输入源 {value}")
        self._kind = "windows" if self.host == "windows" else "apple"

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setObjectName("devLit" if active else "devDim")
        self.logo.setPixmap(logo_pixmap(self._kind, 26, dim=not active))
        self.name.setObjectName("devName" if active else "devNameDim")
        self.state.setObjectName("devStateOn" if active else "devStateOff")
        self.state.setText("正在使用" if active else "未使用 · 点击切换")
        self.led.set_on(active)
        # 重新应用样式（objectName 变了）
        for w in (self, self.name, self.state):
            w.style().unpolish(w)
            w.style().polish(w)
        if active:
            soft_shadow(self, "#16A34A", blur=22, dy=8, alpha=40)
        else:
            self.setGraphicsEffect(None)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and not self._active:
            self.clicked.emit(self.host)
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------- #
# 状态药丸
# --------------------------------------------------------------------------- #
class Pill(QFrame):
    """状态药丸：浅绿底 + 绿字，无描边（描边会和卡片渐变打架）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.setFixedHeight(22)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 10, 0)
        lay.setSpacing(5)
        dot = QLabel()
        dot.setObjectName("pillDot")
        dot.setFixedSize(6, 6)
        self.text = QLabel(text)
        self.text.setObjectName("pillText")
        lay.addWidget(dot)
        lay.addWidget(self.text)


# --------------------------------------------------------------------------- #
# 主状态卡
# --------------------------------------------------------------------------- #
class HeroCard(QFrame):
    switchRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hero")
        self.setMinimumHeight(196)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(label("当前画面", "eyebrow"))
        top.addStretch(1)
        self.pill = Pill("运行中")
        top.addWidget(self.pill)
        root.addLayout(top)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 8, 0, 0)
        mid.setSpacing(12)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)
        self.title = label("Windows", "heroTitle")
        self.sub = label("AOC Q2790PC · HDMI · 输入源 17", "heroSub")
        text_col.addWidget(self.title)
        text_col.addWidget(self.sub)
        mid.addLayout(text_col)
        mid.addStretch(1)

        self.plate = QFrame()
        self.plate.setObjectName("logoPlate")
        self.plate.setFixedSize(46, 46)
        plate_lay = QVBoxLayout(self.plate)
        plate_lay.setContentsMargins(0, 0, 0, 0)
        self.plate_logo = QLabel()
        self.plate_logo.setAlignment(Qt.AlignCenter)
        plate_lay.addWidget(self.plate_logo)
        mid.addWidget(self.plate)
        root.addLayout(mid)

        root.addSpacing(15)

        # 主按钮（内含图标 / 文案 / 键帽）
        self.button = QPushButton()
        self.button.setObjectName("primary")
        self.button.setFixedHeight(44)
        self.button.setCursor(Qt.PointingHandCursor)
        btn_lay = QHBoxLayout(self.button)
        btn_lay.setContentsMargins(16, 0, 10, 0)
        btn_lay.setSpacing(9)
        self.arrow = QLabel("\u21c4")
        self.arrow.setStyleSheet("color:#fff;font-size:16px;background:transparent;")
        self.btn_text = label("切换到 Mac", "spinnerText")
        btn_lay.addWidget(self.arrow)
        btn_lay.addWidget(self.btn_text)
        btn_lay.addStretch(1)
        # 单个键帽芯片展示当前热键，比拆成两格更像快捷键提示
        self.kbd = self._kbd("Alt + Space")
        self._kbd_enabled = True
        btn_lay.addWidget(self.kbd)
        for child in (self.arrow, self.btn_text, self.kbd):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.button.clicked.connect(self.switchRequested)
        root.addWidget(self.button)

        self.foot = QLabel("\u2713 上次切换 63.9 ms · 回读已确认")
        self.foot.setObjectName("heroFoot")
        self.foot.setContentsMargins(0, 10, 0, 0)
        root.addWidget(self.foot)

    def _kbd(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("kbd")
        lb.setAlignment(Qt.AlignCenter)
        return lb

    # ------------------------------------------------------------------ 状态
    def set_state(self, host: str, monitor_line: str) -> None:
        title = "Windows" if host == "windows" else "Mac"
        self.title.setText(title)
        self.sub.setText(monitor_line)
        self.plate_logo.setPixmap(
            logo_pixmap("windows" if host == "windows" else "apple", 26)
        )
        other = "Mac" if host == "windows" else "Windows"
        self.btn_text.setText(f"切换到 {other}")

    def set_hotkey(self, hotkey_label: str) -> None:
        """主按钮右侧键帽芯片显示当前热键（`Alt+Space` → `Alt + Space`；mac 上 → `⌥ Space`）。"""
        self.kbd.setText(theme.pretty_hotkey(hotkey_label))
        self._kbd_enabled = bool(hotkey_label and hotkey_label != "未启用")
        self.kbd.setVisible(self._kbd_enabled)
        self.kbd.setToolTip(hotkey_label)

    def set_busy(self, busy: bool, target_label: str = "") -> None:
        self.button.setEnabled(not busy)
        if busy:
            self.btn_text.setText(f"正在切换到 {target_label}…")
            self.arrow.setText("\u25cf")
            self.kbd.hide()
        else:
            self.arrow.setText("\u21c4")
            self.kbd.setVisible(self._kbd_enabled)

    def set_foot(self, text: str, ok: bool = True) -> None:
        mark = "\u2713" if ok else "\u26a0"
        self.foot.setText(f"{mark} {text}")
        name = "heroFootOk" if ok else "heroFootErr"
        if self.foot.objectName() != name:
            self.foot.setObjectName(name)
            self.foot.style().unpolish(self.foot)
            self.foot.style().polish(self.foot)


# --------------------------------------------------------------------------- #
# 列表行
# --------------------------------------------------------------------------- #
class Glyph(QWidget):
    """15px 线性小图标（搜索 / 文档 / 波形 / 齿轮），比 Unicode 字形稳定。"""

    def __init__(self, kind: str, size: int = 15, color: str = theme.INK3, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.color = QColor(color)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.color, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        w = self.width()
        if self.kind == "search":
            p.drawEllipse(QRectF(2.2, 2.2, 7.6, 7.6))
            p.drawLine(QPointF(8.8, 8.8), QPointF(12.6, 12.6))
        elif self.kind == "doc":
            p.drawRoundedRect(QRectF(3.2, 1.8, 8.6, 11.4), 1.2, 1.2)
            p.drawLine(QPointF(5.6, 5.6), QPointF(9.4, 5.6))
            p.drawLine(QPointF(5.6, 8.2), QPointF(9.4, 8.2))
            p.drawLine(QPointF(5.6, 10.8), QPointF(8.2, 10.8))
        elif self.kind == "gear":
            paint_gear(p, QPointF(w / 2, w / 2), self.color, scale=0.78)
        else:  # pulse
            path = QPainterPath()
            path.moveTo(1.6, 7.5)
            path.lineTo(4.2, 7.5)
            path.lineTo(6.0, 3.4)
            path.lineTo(8.4, 11.6)
            path.lineTo(10.2, 7.5)
            path.lineTo(13.4, 7.5)
            p.drawPath(path)
        p.end()


class ListRow(QPushButton):
    def __init__(self, kind: str, text: str, value: str = "", last: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("rowLast" if last else "row")
        self.setFixedHeight(42)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)

        lay.addWidget(Glyph(kind))
        lay.addWidget(QLabel(text))
        lay.addStretch(1)
        if value:
            lay.addWidget(label(value, "rowVal"))
        chev = label("\u203a", "chev")
        chev.setStyleSheet(f"color:#B6BFCD;font-size:16px;background:transparent;")
        lay.addWidget(chev)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)


# --------------------------------------------------------------------------- #
# 状态栏
# --------------------------------------------------------------------------- #
class StatusBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusbar")
        self.setFixedHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(7)
        dot = QLabel()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet("background:#0F9D58;border-radius:3px;")
        self.text = ElidedLabel("热键已启用 · Alt + Space", "statusText")
        self.ver = label("", "statusText")
        lay.addWidget(dot)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.ver, 0)


# --------------------------------------------------------------------------- #
# 下拉框
# --------------------------------------------------------------------------- #
class Combo(QComboBox):
    """自绘下拉框：QSS 的 ::down-arrow 画不出三角形，只能自己画边框和箭头。"""

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self.addItems(items)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.setAttribute(Qt.WA_Hover)

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        active = self.underMouse() or self.hasFocus()
        p.setPen(QPen(QColor(theme.ACCENT if active else theme.LINE), 1))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(rect, 7, 7)

        p.setPen(QColor(theme.INK))
        p.setFont(self.font())
        p.drawText(
            rect.adjusted(11, 0, -27, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.currentText(),
        )

        pen = QPen(QColor(theme.INK3), 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        cx = rect.right() - 12
        cy = rect.center().y()
        p.drawLine(QPointF(cx - 3.6, cy - 1.7), QPointF(cx, cy + 1.8))
        p.drawLine(QPointF(cx + 3.6, cy - 1.7), QPointF(cx, cy + 1.8))
        p.end()


# --------------------------------------------------------------------------- #
# 开关
# --------------------------------------------------------------------------- #
class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(36, 20)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QRectF(0, 0, 36, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT if on else "#D8DEE9"))
        p.drawRoundedRect(track, 10, 10)
        knob_x = 18 if on else 2
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(knob_x, 2, 16, 16))
        p.end()

    def sizeHint(self):  # noqa: N802
        return QSize(36, 20)
