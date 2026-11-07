"""切换页：显示器信息 + 四条显式通道（17/18 × 本机/对端）。

**这一页不再判断「画面在哪」来算方向**（docs/SCREEN-OWNERSHIP.md §3.2/§3.4）。
原因：这台 AOC 在输入切走之后，macOS 仍把它报成「在线 + active + 122 个模式」，
两端都枚举得到 ⇒ 任何「按归属推方向」的逻辑都只能瞎猜（2026-10-24 实测，
1200 个样本跨真实切换、字段完全不变）。

所以把两条 DDC 通道直接摆出来，由用户选：

    17 · HDMI   [本机发送 17]  [请对端发送 17]
    18 · USB-C  [本机发送 18]  [请对端发送 18]

「画面在 X」那行是**读数**（Windows 端读 VCP 0x60 读到什么），带出处与年龄；
拿不到就写「未检测」，既不猜也不影响按钮。

**2026-11-06 版面精简**（用户要求）：
* 原来常驻在 hero 里、占两行的「操作提示」整段移进按钮的长悬停 tip
  （`widgets.HoverTip`，1.2s 才弹）—— 说明文字不该每次都占版面。
* 「快速操作 · 剪贴板」整节删除：剪贴板在侧栏「核心」里已有入口，这里重复。
* 两处删完页面矮了 ~115px，窗口随之收回到 780×580（见 `theme.WIN_H`）。

图标全部来自开源库（Lucide）：本机 = monitor，对端 = arrow-left-right。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...ddc.profiles import ROW_H as PORT_ROW_H, port_label

from . import icons, theme
from .follow_card import FollowCard
from .widgets import attach_hover_tip, label

TITLE = {"windows": "Windows", "mac": "Mac"}
_ROLE_TEXT = {"local": "本机输入口", "peer": "对端输入口", "": "空闲输入口"}


def _title(host: str) -> str:
    return TITLE.get(host, host or "?")


# --------------------------------------------------------------------------- #
# 一行动作：目标标签 + 两个发送通道
# --------------------------------------------------------------------------- #
class ActionRow(QFrame):
    """一行 = 一个目标输入 + 本机发送（主） + 请对端代发（兜底小钮）。

    布局几何（2026-10-24 定案）：

    - **数字章固定 36×26**、**文字列 120px 自动换行**、**行高 58**——两行背景框
      一样大、文字不压按钮。章原先只给 `MinimumWidth(34)`，布局把富余宽度喂给章
      （17 行 34px / 18 行 57px），「背景框不一样大」就是这么来的。
    - **主按钮定宽 BTN_W=150，不许 stretch**。「本机发送 N」内容只需 ~85px
      （实测 sizeHint 107），拉满到 247px 又大又空（用户 2026-10-24 反馈「太大了」）；
      150 = 内容 + 两侧各 ~30 留白。
    - **兜底降级为行尾 28×28 小图标钮**（方案 A，`switch-fallback-btn.html`）：
      本机发送已实测始终可达（SCREEN-OWNERSHIP §1.4），「请对端发送」只剩兜底
      用途，不配再占半个按钮位。文案进 tooltip + accessibleName；对端离线置灰。
    - 主按钮 + 小钮**靠右成簇**（间距 8px），两行按钮列左右对齐。

    两个按钮的说明都走**长悬停 tip**（1.2s 才弹，2026-11-06）：原来那段
    「本机发送直接把 DDC 命令发给显示器…」是常驻在卡片底部的两行灰字，占版面
    又不常读；悬停看一次就够。
    """

    localClicked = Signal(int)
    peerClicked = Signal(int)

    ROW_H = PORT_ROW_H
    CHIP_W, CHIP_H = 36, 26
    #: 文字列宽：「Windows 的输入口」fallback 字体实测渲染 105px，给到 120 免得
    #: 常规文案无谓折行（两行文字块高度不一会让 17/18 的端口名高低错位）。
    #: **wordWrap 仍开着**：超长文案照常折行收进行内，绝不压按钮（用户
    #: 2026-10-24：装不下可以换行，不许硬挤）。
    TEXT_W = 120
    BTN_W = 150          # 主按钮定宽（内容 ~85px + 留白）；stretch 会拉到 247px
    BTN_H = 38
    MINI = 28            # 兜底小钮边长
    MINI_GAP = 8         # 主按钮与小钮间距

    #: 主按钮的长悬停说明（= 原 hero 常驻提示行的前半段）
    TIP_LOCAL = (
        "「本机发送」直接把 DDC 命令发给显示器：\n"
        "画面在哪一边都能切，不需要先判断当前输入。\n"
        "行尾纸飞机是兜底通道——本机 DDC 不可用时（驱动 / 权限 / 线材）"
        "让对端代发。"
    )

    def __init__(self, port: str, value: int, role: str = "", parent=None):
        super().__init__(parent)
        self.port_name = port
        self.role = role
        self.value = int(value)
        self.setObjectName("actRow")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(self.ROW_H)

        t = theme.tokens()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)   # 行高固定 → 垂直居中，不留上下 padding
        lay.setSpacing(10)

        # 左：值 + 端口 + 归属
        self.chip = QLabel(str(self.value))
        self.chip.setObjectName("valChip")
        self.chip.setFont(theme.qfont(12.5, 700))
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setFixedSize(self.CHIP_W, self.CHIP_H)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self.port = label(port_label(port), "actPort")
        self.who = label(_ROLE_TEXT.get(role, "空闲输入口"), "actWho")
        self.who.setWordWrap(True)   # 装不下就换行，不许压按钮
        col.addWidget(self.port)
        col.addWidget(self.who)
        text_wrap = QWidget()
        text_wrap.setLayout(col)
        text_wrap.setFixedWidth(self.TEXT_W)

        lay.addWidget(self.chip, 0, Qt.AlignVCenter)
        lay.addWidget(text_wrap, 0, Qt.AlignVCenter)

        # 右：主按钮 + 兜底小钮靠右成簇（中间 stretch，两行按钮列自然对齐）
        self.btn_local = QPushButton(f"本机发送 {self.value}")
        self.btn_local.setObjectName("primary")
        self.btn_local.setFixedSize(self.BTN_W, self.BTN_H)
        self.btn_local.setCursor(Qt.PointingHandCursor)
        self.btn_local.setFocusPolicy(Qt.NoFocus)
        self.btn_local.setIcon(QIcon(icons.svg_pixmap("monitor", 15, "#FFFFFF", 2.0)))
        self.btn_local.setAccessibleName(f"本机发送 {self.value}")
        self.btn_local.clicked.connect(lambda: self.localClicked.emit(self.value))
        # 长悬停说明（2026-11-06）：原 hero 底部那两行常驻灰字搬到这里
        self.tip_local = attach_hover_tip(self.btn_local, self.TIP_LOCAL)

        self.btn_peer = QPushButton()
        self.btn_peer.setObjectName("miniBtn")
        self.btn_peer.setFixedSize(self.MINI, self.MINI)
        self.btn_peer.setCursor(Qt.PointingHandCursor)
        self.btn_peer.setFocusPolicy(Qt.NoFocus)
        self.btn_peer.setIcon(QIcon(icons.svg_pixmap("send", 13, t.ink2, 2.0)))
        self.btn_peer.setAccessibleName(f"请对端发送 {self.value}")
        self.btn_peer.clicked.connect(lambda: self.peerClicked.emit(self.value))
        self.tip_peer = attach_hover_tip(self.btn_peer, self.peer_tip_text())

        lay.addStretch(1)
        cluster = QHBoxLayout()          # 按钮簇独立布局：间距 MINI_GAP，不受外层 10px 影响
        cluster.setSpacing(self.MINI_GAP)
        cluster.addWidget(self.btn_local, 0, Qt.AlignVCenter)
        cluster.addWidget(self.btn_peer, 0, Qt.AlignVCenter)
        lay.addLayout(cluster)

    def peer_tip_text(self) -> str:
        """兜底小钮的说明。对端离线时补一句「当前不可用」，别让人对着灰钮猜。"""
        text = (
            f"请对端发送 {self.value} · 兜底通道：\n"
            "本机 DDC 不可用时（驱动 / 权限 / 线材）让对端代发这条切屏命令。"
        )
        if not self.btn_peer.isEnabled():
            text += "\n当前对端离线，此通道不可用。"
        return text

    def set_peer_available(self, available: bool) -> None:
        self.btn_peer.setEnabled(bool(available))
        self.tip_peer.set_text(self.peer_tip_text())


# --------------------------------------------------------------------------- #
# 顶部卡片
# --------------------------------------------------------------------------- #
class SwitchCard(QFrame):
    localRequested = Signal(int)
    peerRequested = Signal(int)

    def __init__(self, cfg, monitor_name: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("hero")

        self._busy = False
        self._remote_ok = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(0)

        # ---- 顶部：标签 + 对端状态 ----
        top = QHBoxLayout()
        top.setSpacing(9)
        top.addWidget(label("显示器", "heroLabel"))
        top.addStretch(1)
        self.pill = QLabel("对端离线")
        self.pill.setObjectName("pillWarn")
        self.pill.setFont(theme.qfont(10, 650))
        top.addWidget(self.pill)
        lay.addLayout(top)
        lay.addSpacing(12)

        # ---- 主信息：显示器名（确定的东西）+ 端口事实 + 读数 ----
        main = QHBoxLayout()
        main.setSpacing(16)
        t = theme.tokens()
        plate = QLabel()
        plate.setFixedSize(62, 62)
        plate.setPixmap(icons.plain_plate_pixmap("monitor", 62, t.card3, t.ink3, border=t.line))
        main.addWidget(plate, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self.name = label((monitor_name or "").strip() or "外接显示器", "heroName")
        self.sub = label(self._port_line(), "heroSub")
        src = QHBoxLayout()
        src.setContentsMargins(0, 2, 0, 0)
        src.setSpacing(6)
        self.src_dot = QLabel()
        self.src_dot.setFixedSize(6, 6)
        self.src_dot.setObjectName("srcDotIdle")
        self.src_text = label("画面位置未检测", "srcLine")
        src.addWidget(self.src_dot, 0, Qt.AlignVCenter)
        src.addWidget(self.src_text, 0, Qt.AlignVCenter)
        src.addStretch(1)
        col.addWidget(self.name)
        col.addWidget(self.sub)
        col.addLayout(src)
        main.addLayout(col, 1)
        lay.addLayout(main)
        lay.addSpacing(14)

        # ---- 四条通道 ----
        acts = QVBoxLayout()
        acts.setContentsMargins(0, 0, 0, 0)
        acts.setSpacing(8)
        self.rows: list[ActionRow] = []
        for port, value in cfg.active_ports():
            row = ActionRow(port, int(value), role=cfg.port_role(port))
            row.localClicked.connect(self.localRequested)
            row.peerClicked.connect(self.peerRequested)
            self.rows.append(row)
            acts.addWidget(row)
        lay.addLayout(acts)

        # ---- 上次动作 ----
        # 原来这里上面还有一段常驻「操作提示」两行灰字（占 32px）；2026-11-06
        # 用户要求改成按钮的长悬停 tip，那两行随之删掉（见 ActionRow.TIP_LOCAL）。
        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 12, 0, 0)
        note_row.setSpacing(6)
        self.note_icon = QLabel()
        self.note_icon.setFixedSize(12, 12)
        self.note_icon.setVisible(False)
        self.note = label("还没切过 · 选一个通道", "heroNoteDim")
        self.note.setWordWrap(True)
        note_row.addWidget(self.note_icon, 0, Qt.AlignTop)
        note_row.addWidget(self.note, 1)
        lay.addLayout(note_row)

        self._apply_enabled()

    def _port_line(self) -> str:
        items = self.cfg.active_ports()
        if not items:
            return "未识别到输入口"
        detail = "，".join(f"{port_label(p)}={v}" for p, v in items)
        return f"{len(items)} 个输入口 · {detail}"

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
        grad.setColorAt(1.0, theme.qcolor(t.accent_soft, 0.0))
        p.fillRect(self.rect(), grad)
        p.end()

    # ------------------------------------------------------------------ 状态
    def set_monitor_name(self, name: str) -> None:
        self.name.setText((name or "").strip() or "外接显示器")

    def set_owner(self, host: str | None, detail: str = "") -> None:
        """画面读数：`host` 为 None/空 = 读不到。

        **只做展示**：不改变任何按钮的可用性（§3.5）。
        """
        if host in TITLE:
            self.src_dot.setObjectName("srcDotOk")
            text = f"画面在 {_title(host)}"
            if detail:
                text += f" · {detail}"
        else:
            self.src_dot.setObjectName("srcDotIdle")
            text = "画面位置未检测" + (f" · {detail}" if detail else "")
        self.src_text.setText(text)
        self._polish(self.src_dot)

    def set_remote_available(self, available: bool) -> None:
        self._remote_ok = bool(available)
        self.pill.setText("对端在线" if self._remote_ok else "对端离线")
        self.pill.setObjectName("pillRun" if self._remote_ok else "pillWarn")
        self._polish(self.pill)
        self._apply_enabled()

    def set_busy(self, busy: bool, label_text: str = "") -> None:
        self._busy = bool(busy)
        self._apply_enabled()
        if busy and label_text:
            self.note.setText(f"正在发送 · {label_text}")

    def set_note(self, text: str, ok: bool = True, bad: bool = False) -> None:
        t = theme.tokens()
        name = "heroNoteBad" if bad else ("heroNoteOk" if ok else "heroNoteDim")
        if ok or bad:
            color = t.danger if bad else t.ok
            self.note_icon.setPixmap(icons.svg_pixmap("check" if ok else "x", 12, color, 2.4))
            self.note_icon.setVisible(True)
        else:
            self.note_icon.setVisible(False)
        self.note.setText(text)
        if self.note.objectName() != name:
            self.note.setObjectName(name)
            self._polish(self.note)

    def _apply_enabled(self) -> None:
        for row in self.rows:
            row.btn_local.setEnabled(not self._busy)
            # 兜底钮的可用性 = 不忙 且 对端在线；tip 文案随状态补一句（见 peer_tip_text）
            row.set_peer_available(not self._busy and self._remote_ok)

    @staticmethod
    def _polish(*widgets) -> None:  # noqa: ANN002
        for w in widgets:
            w.style().unpolish(w)
            w.style().polish(w)


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
class SwitchPage(QWidget):
    localRequested = Signal(int)
    peerRequested = Signal(int)
    toastRequested = Signal(str)
    #: 跟随开关（2026-11-06）：状态由链路同步，这里只把用户操作抛出去
    keyboardFollowToggled = Signal(bool)
    mouseFollowToggled = Signal(bool)

    def __init__(self, cfg, hotkey_label: str = "", monitor_name: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.monitor_name = monitor_name
        self._busy = False
        self._remote_ok = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 12)
        lay.setSpacing(12)

        self.hero = SwitchCard(cfg, monitor_name)
        self.hero.localRequested.connect(self.localRequested)
        self.hero.peerRequested.connect(self.peerRequested)
        lay.addWidget(self.hero)

        # 「跟随画面」：键盘 / 鼠标这两个外设是切屏的副作用，不属于 hero 的
        # 「输入通道」语义，所以单开一节。
        lay.addWidget(label("跟随画面", "secLabel"))
        self.follow = FollowCard()
        self.follow.keyboardToggled.connect(self.keyboardFollowToggled)
        self.follow.mouseToggled.connect(self.mouseFollowToggled)
        lay.addWidget(self.follow)

        # 2026-11-06 删除「快速操作 · 剪贴板」：剪贴板在侧栏「核心」里已有入口，
        # 这里再放一行是重复导航，还白占 74px（标题 16 + 卡片 46 + 两处间距）。
        lay.addStretch(1)

    # ------------------------------------------------------------------ 状态
    def set_owner(self, host: str | None, detail: str = "") -> None:
        self.hero.set_owner(host, detail)

    def set_monitor_name(self, name: str) -> None:
        self.monitor_name = name or ""
        self.hero.set_monitor_name(self.monitor_name)

    def set_remote_available(self, available: bool) -> None:
        self._remote_ok = bool(available)
        self.hero.set_remote_available(self._remote_ok)

    def set_busy(self, busy: bool, target_label: str = "") -> None:
        self._busy = bool(busy)
        self.hero.set_busy(self._busy, target_label)

    def set_note(self, text: str, ok: bool = True, bad: bool = False) -> None:
        self.hero.set_note(text, ok=ok, bad=bad)

    # ------------------------------------------------------------------ 跟随
    def set_keyboard_follow(self, on: bool, text: str, tone: str = "idle") -> None:
        self.follow.keyboard.set_checked(on)
        self.follow.keyboard.set_usable(True)
        self.follow.keyboard.set_state(text, tone)

    def set_mouse_follow(self, on: bool, text: str, tone: str = "idle",
                         usable: bool = True) -> None:
        self.follow.mouse.set_checked(on)
        self.follow.mouse.set_usable(usable)
        self.follow.mouse.set_state(text, tone)
