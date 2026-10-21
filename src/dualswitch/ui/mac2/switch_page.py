"""切换页（INTERACTIONS §2）：hero 状态卡 + 两台设备 + 快速操作。

三态逻辑照搬 ISSUE-001（只换视觉）：

| 归属 | 名称 | 副标题 | 主按钮 | 注脚 | 设备卡 |
|---|---|---|---|---|---|
| windows | Windows | … · HDMI · 输入源 17 | 切换到 Mac | 绿勾+耗时 | win 高亮 |
| mac | Mac | … · USB-C · 输入源 18 | 切换到 Windows | 绿勾+耗时 | mac 高亮 |
| unknown | 未检测到外接显示器 | 没接线，或画面在另一台机器上 | 置灰/「从 X 切过来」 | 灰字恢复提示 | 两张置灰 |

unknown 态下对端在线时保留「从 X 切过来」（ISSUE-002 远端代发是双向能力，
本地看不到屏时这是唯一能召回画面的入口），对端离线才整体置灰。

图标全部来自开源图标库（Lucide + Simple Icons）：平台牌是「渐变圆角方块 + 白 logo」，
不再用 19px 角标放大当 32px 图标（会糊）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...config import SOURCE_UNKNOWN
from . import icons, theme
from .widgets import KeyCaps, StatusDot, label, plate_pixmap

PORT = {"windows": "HDMI", "mac": "USB-C"}
SYSTEM_OF = {"windows": "windows", "mac": "apple"}


def _plate(host: str, size: int, dim: float = 1.0) -> QLabel:
    """平台牌 QLabel（hero 62 / 设备卡 32 共用）。"""
    lb = QLabel()
    lb.setFixedSize(size, size)
    lb.setPixmap(plate_pixmap(SYSTEM_OF[host], size, dim=dim))
    return lb


# --------------------------------------------------------------------------- #
# 设备卡
# --------------------------------------------------------------------------- #
class DeviceCard(QFrame):
    clicked = Signal(str)

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.host = host
        self._active = False
        self.setObjectName("devOff")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 15, 15, 15)
        lay.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.logo = _plate(host, 32)
        self.name = label("Windows" if host == "windows" else "Mac", "devNameDim")
        self.led = StatusDot(8, 3)
        top.addWidget(self.logo, 0, Qt.AlignVCenter)
        top.addWidget(self.name, 0, Qt.AlignVCenter)
        top.addStretch(1)
        top.addWidget(self.led, 0, Qt.AlignTop)
        lay.addLayout(top)

        self.sub = label("", "devSub")
        self.sub.setContentsMargins(0, 9, 0, 0)
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)

        lay.addSpacing(10)
        self.tag = QLabel()
        self.tag.setFont(theme.qfont(10, 650))
        lay.addWidget(self.tag, 0, Qt.AlignLeft)   # 药丸 hug 内容，不摊满整行
        self.set_active(False)

    def set_content(self, name: str, port: str, value: int) -> None:
        self.name.setText(name)
        self.sub.setText(f"{port} · 输入源 {value}")

    def _polish(self, *widgets) -> None:  # noqa: ANN002
        for w in widgets:
            w.style().unpolish(w)
            w.style().polish(w)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setObjectName("devActive" if active else "devOff")
        self.name.setObjectName("devName" if active else "devNameDim")
        self.logo.setPixmap(plate_pixmap(SYSTEM_OF[self.host], 32))
        self.led.setVisible(True)
        self.led.set_active(active)
        self.tag.setText("当前画面" if active else "未使用")
        self.tag.setObjectName("devTagOn" if active else "devTagOff")
        self._polish(self, self.name, self.tag)

    def set_dead(self, hint: str = "未连接") -> None:
        """未知态：置灰不可点，文案别承诺「点击切换」。"""
        self._active = False
        self.setObjectName("devDead")
        self.name.setObjectName("devNameDim")
        self.logo.setPixmap(plate_pixmap(SYSTEM_OF[self.host], 32, dim=0.30))
        self.led.setVisible(True)
        self.led.set_active(False)
        self.tag.setText(hint)
        self.tag.setObjectName("devTagOff")
        self.setEnabled(False)
        self._polish(self, self.name, self.tag)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and not self._active:
            self.clicked.emit(self.host)
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------- #
# hero 状态卡
# --------------------------------------------------------------------------- #
class HeroCard(QFrame):
    switchRequested = Signal()
    forceRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hero")
        self._busy = False
        self._unknown = False
        self._remote_ok = False
        self._other_title = "Windows"
        self._host = "windows"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(0)

        top = QHBoxLayout()
        top.addWidget(label("当前画面", "heroLabel"))
        top.addStretch(1)
        self.pill = QLabel("运行中")
        self.pill.setObjectName("pillRun")
        self.pill.setFont(theme.qfont(10, 650))
        top.addWidget(self.pill)
        lay.addLayout(top)
        lay.addSpacing(14)

        main = QHBoxLayout()
        main.setSpacing(18)
        self.plate = _plate("windows", 62)
        main.addWidget(self.plate, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(5)
        self.name = label("Windows", "heroName")
        self.sub = label("", "heroSub")
        col.addWidget(self.name)
        col.addWidget(self.sub)
        col.addStretch(1)
        main.addLayout(col, 1)
        lay.addLayout(main)
        lay.addSpacing(14)

        # 主按钮组：主按钮（图标 + 文案 + 键帽）+ 强制切
        actions = QHBoxLayout()
        actions.setSpacing(9)
        self.button = QPushButton()
        self.button.setObjectName("primary")
        self.button.setFixedHeight(42)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setFocusPolicy(Qt.NoFocus)
        btn_lay = QHBoxLayout(self.button)
        btn_lay.setContentsMargins(16, 0, 12, 0)
        btn_lay.setSpacing(8)
        self.arrow = QLabel()
        self.arrow.setFixedSize(15, 15)
        self.arrow.setPixmap(icons.svg_pixmap("arrow-left-right", 15, "#FFFFFF", 2.2))
        self.btn_text = label("切换到 Mac")
        self.btn_text.setFont(theme.qfont(13.5, 650))
        self.btn_text.setStyleSheet("color:#FFFFFF;background:transparent;")
        self.keys = KeyCaps(["⌥", "Space"], on_accent=True)
        btn_lay.addWidget(self.arrow)
        btn_lay.addWidget(self.btn_text)
        btn_lay.addSpacing(2)
        btn_lay.addStretch(1)
        btn_lay.addWidget(self.keys)
        for child in (self.arrow, self.btn_text, self.keys):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.button.clicked.connect(self.switchRequested)
        actions.addWidget(self.button, 1)

        self.force_btn = QPushButton("强制切")
        self.force_btn.setObjectName("ghostBtn")
        self.force_btn.setFont(theme.qfont(12.5, 600))
        self.force_btn.setFixedHeight(42)
        self.force_btn.setCursor(Qt.PointingHandCursor)
        self.force_btn.setFocusPolicy(Qt.NoFocus)
        self.force_btn.setToolTip("跳过「本地优先、失败才请对端」策略，直接发 DDC Set")
        self.force_btn.clicked.connect(self.forceRequested)
        actions.addWidget(self.force_btn)
        lay.addLayout(actions)

        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 8, 0, 0)
        note_row.setSpacing(6)
        self.note_icon = QLabel()
        self.note_icon.setFixedSize(12, 12)
        self.note = label("尚未切换 · 按一下开始", "heroNoteOk")
        self.note_icon.setVisible(False)
        note_row.addWidget(self.note_icon, 0, Qt.AlignVCenter)
        note_row.addWidget(self.note, 0, Qt.AlignVCenter)
        note_row.addStretch(1)
        lay.addLayout(note_row)

    def paintEvent(self, event):  # noqa: N802
        """设计稿 `.hero::after`：右上角一团主色柔光（沿用 QSS 底色，只叠光晕）。"""
        super().paintEvent(event)
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(self.rect(), theme.RADIUS_CARD, theme.RADIUS_CARD)
        p.setClipPath(clip)
        grad = QRadialGradient(QPointF(self.width() - 20, 0), 130)
        soft = theme.qcolor(t.accent_soft)
        grad.setColorAt(0.0, soft)
        clear = theme.qcolor(t.accent_soft, 0.0)
        grad.setColorAt(1.0, clear)
        p.fillRect(self.rect(), grad)
        p.end()

    # ------------------------------------------------------------------ 状态
    def set_state(self, host: str, monitor_line: str) -> None:
        if host == SOURCE_UNKNOWN:
            self.set_unknown()
            return
        self._unknown = False
        self._host = host
        self.plate.setVisible(True)
        self.plate.setPixmap(plate_pixmap(SYSTEM_OF[host], 62))
        self.name.setText("Windows" if host == "windows" else "Mac")
        self.sub.setText(monitor_line)
        other = "Mac" if host == "windows" else "Windows"
        self._other_title = other
        self.btn_text.setText(f"切换到 {other}")
        self.button.setEnabled(not self._busy)
        self.force_btn.setEnabled(not self._busy)
        self.pill.setText("运行中")
        self.pill.setObjectName("pillRun")
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)
        self.keys.setVisible(not self._busy)

    def set_unknown(self, other_label: str = "") -> None:
        self._unknown = True
        if other_label:
            self._other_title = other_label
        self._render_unknown()

    def set_remote_available(self, available: bool) -> None:
        if available == self._remote_ok:
            return
        self._remote_ok = bool(available)
        if self._unknown:
            self._render_unknown()

    @property
    def remote_ok(self) -> bool:
        return self._remote_ok

    def _render_unknown(self) -> None:
        t = theme.tokens()
        self.plate.setVisible(True)
        self.plate.setPixmap(
            icons.plain_plate_pixmap("monitor", 62, t.idle_bg, t.ink3)
        )
        self.name.setText("未检测到外接显示器")
        self.pill.setText("待确认")
        self.pill.setObjectName("pillWarn")
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)
        if self._remote_ok:
            self.sub.setText(f"画面可能在 {self._other_title} · 可以请它代发切换")
            self.btn_text.setText(f"从 {self._other_title} 切过来")
            self.button.setEnabled(not self._busy)
            self.set_note("对端在线，可以直接把画面叫回来", ok=False)
        else:
            self.sub.setText("没接线，或画面在另一台机器上")
            self.btn_text.setText("未检测到显示器")
            self.button.setEnabled(False)
            self.set_note("插入显示器后 2–4 秒内自动恢复", ok=False)
        self.force_btn.setEnabled(False)
        self.keys.setVisible(False)      # 这时候按热键也切不动，别给键帽暗示

    def set_hotkey(self, hotkey_label: str) -> None:
        self.keys.set_keys(theme.keycap_labels(hotkey_label))
        self.keys.setToolTip(hotkey_label)

    def set_busy(self, busy: bool, target_label: str = "") -> None:
        self._busy = bool(busy)
        self.button.setEnabled(not busy)
        self.force_btn.setEnabled(not busy and not self._unknown)
        if busy:
            self.btn_text.setText("切换中…")
            self.arrow.setPixmap(icons.svg_pixmap("arrow-left-right", 15, "#FFFFFF", 2.2))
            self.keys.setVisible(False)
        else:
            self.arrow.setPixmap(icons.svg_pixmap("arrow-left-right", 15, "#FFFFFF", 2.2))
            if not self._unknown:
                self.keys.setVisible(True)

    def set_note(self, text: str, ok: bool = True, bad: bool = False) -> None:
        t = theme.tokens()
        name = "heroNoteBad" if bad else ("heroNoteOk" if ok else "heroNoteDim")
        if ok or bad:
            color = t.danger if bad else t.ok
            self.note_icon.setPixmap(
                icons.svg_pixmap("check" if ok else "x", 12, color, 2.4)
            )
            self.note_icon.setVisible(True)
        else:
            self.note_icon.setVisible(False)
        self.note.setText(text)
        if self.note.objectName() != name:
            self.note.setObjectName(name)
            self.note.style().unpolish(self.note)
            self.note.style().polish(self.note)


# --------------------------------------------------------------------------- #
# 快速操作行
# --------------------------------------------------------------------------- #
class QuickRow(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("quickRow")
        self.setFixedHeight(theme.TOOL_ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(11)
        icon = QLabel()
        icon.setFixedSize(20, 20)
        icon.setPixmap(icons.svg_pixmap("clipboard", 20, theme.tokens().accent, 1.9))
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(icon)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.title = label("剪贴板", "rowTitle")
        self.sub = label("跨端同步文字与图片 · 来源系统可见", "rowSub")
        for w in (self.title, self.sub):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.hint = label("0 条", "rowHint")
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.hint)
        chev = QLabel()
        chev.setFixedSize(14, 14)
        chev.setPixmap(icons.svg_pixmap("chevron-right", 14, theme.tokens().ink3, 2.0))
        chev.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(chev)

    def set_count(self, count: int, abnormal: int = 0) -> None:
        text = f"{count} 条"
        if abnormal:
            text = f"{count} 条 · {abnormal} 异常"
        self.hint.setText(text)


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
class SwitchPage(QWidget):
    switchRequested = Signal()
    forceRequested = Signal()
    deviceClicked = Signal(str)
    clipboardRequested = Signal()
    toastRequested = Signal(str)

    def __init__(self, cfg, hotkey_label: str = "", monitor_name: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.monitor_name = monitor_name
        self._host = cfg.effective_host()
        self._busy = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 12)
        lay.setSpacing(12)

        self.hero = HeroCard()
        self.hero.switchRequested.connect(self.switchRequested)
        self.hero.forceRequested.connect(self._on_force)
        lay.addWidget(self.hero)

        devs = QHBoxLayout()
        devs.setSpacing(11)
        self.card_win = DeviceCard("windows")
        self.card_mac = DeviceCard("mac")
        for card in (self.card_win, self.card_mac):
            card.set_content(
                "Windows" if card.host == "windows" else "Mac",
                PORT[card.host],
                cfg.sources[card.host],
            )
            card.clicked.connect(self._on_device)
            devs.addWidget(card, 1)
        lay.addLayout(devs)

        lay.addWidget(label("快速操作", "secLabel"))
        rows = QFrame()
        rows.setObjectName("rowsCard")
        rows_lay = QVBoxLayout(rows)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(0)
        self.row_clip = QuickRow()
        self.row_clip.clicked.connect(self.clipboardRequested)
        rows_lay.addWidget(self.row_clip)
        lay.addWidget(rows)

        lay.addStretch(1)
        self.hero.set_hotkey(hotkey_label)
        self.set_state(self._host)

    # ------------------------------------------------------------------ 状态
    def set_state(self, host: str) -> None:
        self._host = host
        if host == SOURCE_UNKNOWN:
            self.hero.set_unknown("Windows" if self.cfg.other_host() == "windows" else "Mac")
            self.card_win.set_dead("未连接")
            self.card_mac.set_dead("未连接")
            return
        self.hero.set_state(host, self._monitor_line(host))
        self.card_win.setEnabled(not self._busy)
        self.card_mac.setEnabled(not self._busy)
        self.card_win.set_active(host == "windows")
        self.card_mac.set_active(host == "mac")

    def _monitor_line(self, host: str) -> str:
        name = (self.monitor_name or self.cfg.monitor_match or "").strip() or "显示器"
        return f"{name} · {PORT[host]} · 输入源 {self.cfg.sources[host]}"

    def set_busy(self, busy: bool, target_label: str = "") -> None:
        self._busy = bool(busy)
        self.hero.set_busy(busy, target_label)
        if self._host != SOURCE_UNKNOWN:
            self.card_win.setEnabled(not busy)
            self.card_mac.setEnabled(not busy)

    def set_remote_available(self, available: bool) -> None:
        self.hero.set_remote_available(available)

    def set_note(self, text: str, ok: bool = True, bad: bool = False) -> None:
        self.hero.set_note(text, ok=ok, bad=bad)

    def set_clip_count(self, count: int, abnormal: int = 0) -> None:
        self.row_clip.set_count(count, abnormal)

    def set_hotkey(self, hotkey_label: str) -> None:
        self.hero.set_hotkey(hotkey_label)

    # ------------------------------------------------------------------ 动作
    def _on_force(self) -> None:
        if self._host == SOURCE_UNKNOWN:
            self.toastRequested.emit("未检测到显示器，无法强制切换")
            return
        self.forceRequested.emit()

    def _on_device(self, host: str) -> None:
        if host == self._host:
            self.toastRequested.emit("画面已经在这台机器上")
            return
        self.deviceClicked.emit(host)
