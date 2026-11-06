"""切换页「跟随画面」卡片：键盘转发 / 鼠标跟随，各带一行实时状态。

版式真源 `design/redesign/INTERACTIONS-v2.md` §14；Windows 照做版见
`docs/UI-WINDOWS2-DESIGN.md` §5.4。与 mac2 的 `follow_card.py` 同源，只改文案
口径（mac2 是注入端、Windows 是吞键端，说法不一样）：

| | mac2（注入端） | Windows（吞键端） |
|---|---|---|
| 键盘标题 | 键盘跟随 | **键盘转发** |
| 副文案 | 键盘接在 Windows 上，切到 Mac 时按键转发 | **允许把本机键盘转发给 Mac** |
| 关闭后状态 | 已暂停 · 按键留在 Windows | **已暂停 · 按键留在本机** |
| 槽位显示 | 本机槽 2 ⇄ 对端槽 1 | **本机槽 1 ⇄ 对端槽 2**（两边相反） |

几条刻意的约束（两端一致）：

* **开关是「一个状态」，不是两个独立开关。** 键盘这条链路两端各管一半：
  Windows 吞键、Mac 注入。Mac 单方面关掉接收而 Windows 照吞，按键就是黑洞。
* **槽位没配时不给开关**（不是置灰）—— 打了也没效果，置灰会被当成"待会儿能开"。
* 开关只有开 / 关两种视觉，**没有中间态、没有动画**：一按就生效，动画会让人
  以为还在过渡。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import icons, theme
from .widgets import label

#: 状态行配色 → QSS objectName（`theme.qss()` 里对应 #followStateXxx）
_TONE_NAME = {
    "run": "followStateRun",
    "ok": "followStateOk",
    "idle": "followStateIdle",
    "warn": "followStateWarn",
    "bad": "followStateBad",
}


def _tone_color(tone: str) -> str:
    t = theme.tokens()
    return {
        "run": t.run,
        "ok": t.ok,
        "idle": t.ink3,
        "warn": t.warn,
        "bad": t.danger,
    }.get(tone, t.ink3)


class ToggleSwitch(QWidget):
    """36×20 的开关。**手绘不用 QSS**：QSS 圆角一旦超过控件高度一半就退化成直角
    （项目里踩过），而轨道半径正好是高度的一半，用 QSS 必翻车。"""

    W = 36
    H = 20
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._on = False
        self._hover = False

    def isChecked(self) -> bool:  # noqa: N802 - 跟 Qt 习惯对齐
        return self._on

    def setChecked(self, on: bool) -> None:  # noqa: N802
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self.update()

    # ------------------------------------------------------------------ 交互
    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if not self.isEnabled() or event.button() != Qt.LeftButton:
            return
        self._on = not self._on
        self.update()
        self.toggled.emit(self._on)

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event):  # noqa: N802, ARG002
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)

        if not self.isEnabled():
            track = theme.qcolor(t.line_soft)
            knob = theme.qcolor(t.card3)
        elif self._on:
            track = theme.qcolor(t.accent_deep if self._hover else t.accent)
            knob = theme.qcolor("#FFFFFF")
        else:
            track = theme.qcolor(t.ink3 if self._hover else t.line)
            knob = theme.qcolor("#FFFFFF")

        radius = self.H / 2.0
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.W, self.H), radius, radius)

        d = self.H - 4
        x = (self.W - d - 2) if self._on else 2
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, 2, d, d))
        p.end()


class FollowRow(QWidget):
    """一行：图标 + 标题/副文案 + 右侧开关；下面一行小字是实时状态。"""

    toggled = Signal(bool)

    #: 状态小字左缩进 = 图标 22 + 间距 10，让状态与标题左对齐
    STATE_INDENT = 32

    def __init__(self, icon_name: str, title: str, sub: str, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 10, 0, 8)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)

        self._icon = QLabel()
        self._icon.setFixedSize(22, 22)
        self._icon.setPixmap(icons.svg_pixmap(icon_name, 22, theme.tokens().accent, 1.9))
        self._icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        top.addWidget(self._icon, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self.title = label(title, "followTitle")
        self.sub = label(sub, "followSub")
        self.sub.setWordWrap(True)      # 装不下换行是正解，不许压开关
        for w in (self.title, self.sub):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        top.addLayout(col, 1)

        self.switch = ToggleSwitch()
        self.switch.toggled.connect(self.toggled)
        top.addWidget(self.switch, 0, Qt.AlignVCenter)

        outer.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(self.STATE_INDENT, 0, 0, 0)
        bottom.setSpacing(5)
        self._dot = QLabel()
        self._dot.setFixedSize(6, 6)
        self._dot.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.state = label("", "followStateIdle")
        self.state.setAttribute(Qt.WA_TransparentForMouseEvents)
        bottom.addWidget(self._dot, 0, Qt.AlignVCenter)
        bottom.addWidget(self.state, 1)
        outer.addLayout(bottom)

        self.set_state("", "idle")

    # ------------------------------------------------------------------ 状态
    def set_checked(self, on: bool) -> None:
        self.switch.setChecked(bool(on))

    def set_usable(self, usable: bool) -> None:
        """能不能操作。不可用时开关隐藏（不是置灰）—— 置灰的开关会被当成"待会儿能开"。"""
        self.switch.setVisible(bool(usable))
        self.switch.setEnabled(bool(usable))

    def set_state(self, text: str, tone: str = "idle") -> None:
        # 空文本时整行隐藏：首帧还没拿到状态时别留一个孤零零的灰点
        has_text = bool(text)
        self._dot.setVisible(has_text)
        self.state.setVisible(has_text)
        color = _tone_color(tone)
        self._dot.setStyleSheet(f"background:{color};border-radius:3px;")
        name = _TONE_NAME.get(tone, "followStateIdle")
        if self.state.objectName() != name:
            self.state.setObjectName(name)
            self.state.style().unpolish(self.state)
            self.state.style().polish(self.state)
        self.state.setText(text)

    @property
    def checked(self) -> bool:
        return self.switch.isChecked()


class FollowCard(QFrame):
    """「跟随画面」小节：键盘在上、鼠标在下，中间一条发丝线。"""

    keyboardToggled = Signal(bool)
    mouseToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("followCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 2, 14, 10)
        lay.setSpacing(0)

        # 文案按 Windows 视角写（本机是吞键端），见模块头部的对照表
        self.keyboard = FollowRow(
            "keyboard", "键盘转发", "允许把本机键盘转发给 Mac"
        )
        self.keyboard.toggled.connect(self.keyboardToggled)
        lay.addWidget(self.keyboard)

        hair = QFrame()
        hair.setObjectName("followHair")
        hair.setFixedHeight(1)
        lay.addWidget(hair)

        self.mouse = FollowRow(
            "mouse", "鼠标跟随（罗技 Easy-Switch）", "切屏时把鼠标推到画面那一台"
        )
        self.mouse.toggled.connect(self.mouseToggled)
        lay.addWidget(self.mouse)


__all__ = ["FollowCard", "FollowRow", "ToggleSwitch"]
