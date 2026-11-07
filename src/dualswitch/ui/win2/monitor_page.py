"""显示器配置子页：按 `design/v3/index-v3.html` ⑤ 屏实现。

结构：返回链接 + 当前屏卡片 → 接线（两张并排小卡）→ 端口码表（2×2 格子）
→ 已保存的显示器（默认收起）。默认状态在 570 高内不滚动。
"""

from __future__ import annotations

import copy
import socket
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ...config import Config, ConfigError, save as save_config
from ...ddc import profiles as profile_mod
from ...ddc.profiles import MonitorProfile, normalize_port, port_label
from . import icons, theme
from .widgets import ElidedLabel, label


def _ghost(text: str, parent=None) -> QPushButton:
    """设计稿 `.ghost`：26 高、白底 1px line、11px/550。"""
    t = theme.tokens()
    btn = QPushButton(text, parent)
    btn.setFixedHeight(26)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setFont(theme.qfont(11, 550))
    btn.setStyleSheet(
        f"QPushButton{{background:#fff;border:1px solid {t.line};border-radius:8px;"
        f"color:{t.ink2};font-size:11px;font-weight:550;padding:0 11px;}}"
        f"QPushButton:hover{{border-color:{t.accent};color:{t.accent_deep};}}"
    )
    return btn


def _card(parent=None, name: str = "monitorCard") -> QFrame:
    """**选择器必须带 #objectName**：`QFrame{...}` 会级联到所有子 QLabel
    （QLabel 继承自 QFrame），表现就是每个文字外面都被画了一圈框线。"""
    t = theme.tokens()
    frame = QFrame(parent)
    frame.setObjectName(name)
    frame.setStyleSheet(
        f"QFrame#{name}{{background:{t.card2};border:1px solid {t.line_soft};"
        "border-radius:10px;}"
    )
    return frame


class _PortItemDelegate(QStyledItemDelegate):
    """弹出列表的每一行自己画背景：Qt 的 QComboBox popup 不吃 QSS 的
    `::item:hover`，所以这里显式处理 hover / selected。"""

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(0, 26)

    def paint(self, painter, option, index):  # noqa: N802
        t = theme.tokens()
        p = painter
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        # hover 优先：QComboBox 的 popup 会把鼠标所在行同时标记成 Selected，
        # 若先判断 selected，鼠标划过去永远是蓝底。用户要的是「悬浮变灰」。
        bg = t.card3 if hover else (t.accent_soft if selected else "")
        rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        if bg:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(bg))
            p.drawRoundedRect(rect, 5, 5)
        p.setFont(theme.qfont(11.5, 600 if selected else 550))
        p.setPen(theme.qcolor(t.accent_deep if selected else t.ink2))
        p.drawText(
            option.rect.adjusted(9, 0, -9, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            str(index.data() or ""),
        )
        p.restore()


class _PortCombo(QComboBox):
    """「输入口 USB-C ⌄」：右侧必须画出下拉箭头。

    原生 QComboBox 在无边框样式下只剩一行字，用户根本认不出这是下拉；
    HIG 对 pop-up button 的要求也是「当前值 + 明确的展开指示」。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(108, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover = False
        t = theme.tokens()
        self.setStyleSheet(
            f"QComboBox{{background:transparent;border:0;padding:0;}}"
            f"QComboBox::drop-down{{border:0;width:0;}}"
        )
        # 弹出层是独立的 popup 窗口，不继承 QComboBox 自己的样式；必须把
        # item 的 hover / selected 样式直接写给 view，否则鼠标划过去没反应。
        view = self.view()
        view.setMouseTracking(True)
        view.viewport().setMouseTracking(True)
        # 弹出层用**直角**。2026-11-07 实测：给 view 加 border-radius 后，圆角外面
        # 那块没人绘制，而 popup 是独立顶层窗口，屏幕上就是四个黑角；QSS / 调色板 /
        # viewport 底色 / WA_NoSystemBackground 全都压不住（Qt 的 combo popup 不按
        # view 的圆角裁剪）。真圆角得自己写 popup，代价不值 —— 直角 + 1px 边框更接近
        # Windows 原生下拉。条目自己的 5px 圆角保留（hover 高亮才是用户天天看的）。
        view.setStyleSheet(
            f"QAbstractItemView{{background:#fff;border:1px solid {t.line};"
            "border-radius:0;padding:4px;outline:0;"
            f"color:{t.ink};font-size:11.5px;}}"
            "QAbstractItemView::item{min-height:24px;padding:0 8px;"
            "border-radius:5px;background:transparent;}"
            # 悬浮必须有反馈：HIG / 系统菜单项都靠底色变化表达可点
            f"QAbstractItemView::item:hover{{background:{t.card3};color:{t.ink};}}"
            f"QAbstractItemView::item:selected{{background:{t.accent_soft};"
            f"color:{t.accent_deep};}}"
        )
        self._prepare_popup()

    def _prepare_popup(self) -> None:
        """给弹出层开透明底，兜住「view 没铺满窗口」的那几个像素。

        popup 窗口默认不透明，没被绘制的区域在屏幕上就是纯黑。设成半透明后，
        露出来的是后面的界面而不是黑块。
        """
        popup = self.view().window()
        if popup is self:
            return
        popup.setAttribute(Qt.WA_TranslucentBackground, True)
        popup.setObjectName("comboPopup")
        # 用 objectName 选择器：裸 setStyleSheet 会连子控件（QListView 也是 QFrame）
        # 的白底一起吃掉。
        popup.setStyleSheet("QFrame#comboPopup{background:transparent;border:0;}")

    def showPopup(self) -> None:  # noqa: N802
        self._prepare_popup()
        super().showPopup()

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(t.card3))
            p.drawRoundedRect(QRectF(rect), 6, 6)
        p.setFont(theme.qfont(11, 600))
        p.setPen(theme.qcolor(t.ink))
        p.drawText(
            rect.adjusted(0, 0, -18, 0),
            Qt.AlignRight | Qt.AlignVCenter,
            self.currentText(),
        )
        # 右侧的小折线箭头：下拉必须有可见的 affordance
        cx = rect.right() - 8
        cy = rect.center().y() + 1
        p.setPen(QPen(theme.qcolor(t.ink2 if self._hover else t.ink3), 1.4,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(cx - 4, cy - 2, cx, cy + 2)
        p.drawLine(cx, cy + 2, cx + 4, cy - 2)
        p.end()


def _port_combo(parent=None) -> QComboBox:
    return _PortCombo(parent)


class _Clickable(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SavedRow(_Clickable):
    """一条已保存的显示器：头部两行文字 + 展开/收起 + 折叠详情。"""

    toggled = Signal(bool)   # True = 刚展开；页面据此把详情滚进可视区

    def __init__(self, profile: MonitorProfile, *, current: bool, removable: bool,
                 on_use, on_delete, parent=None):
        super().__init__(parent)
        self._open = False
        self.setObjectName("savedRow")
        self.setStyleSheet("QFrame#savedRow{background:transparent;border:0;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = _Clickable()
        head.setObjectName("savedHead")
        head.setStyleSheet("QFrame#savedHead{background:transparent;border:0;}")
        head.clicked.connect(self._toggle)
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(15, 8, 15, 8)
        head_lay.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = profile.name or profile.key
        if current:
            title += " · 当前"
        col.addWidget(label(title, "setTitle"))
        bits = [f"{len(profile.ports)} 个输入口"]
        if profile.calibrated_at:
            bits.append(f"{profile.calibrated_at} 标定")
        col.addWidget(label(" · ".join(bits), "setSub"))
        head_lay.addLayout(col, 1)
        self._hint = label("展开", "setSub")
        head_lay.addWidget(self._hint, 0, Qt.AlignVCenter)
        self._chev = label("▸", "setSub")
        head_lay.addWidget(self._chev, 0, Qt.AlignVCenter)
        lay.addWidget(head)

        self.detail = QWidget()
        detail_lay = QVBoxLayout(self.detail)
        detail_lay.setContentsMargins(15, 0, 15, 10)
        detail_lay.setSpacing(2)
        for port, value in profile.items():
            line = QWidget()
            line_lay = QHBoxLayout(line)
            line_lay.setContentsMargins(0, 3, 0, 3)
            line_lay.addWidget(label(port_label(port), "actPort"), 0)
            line_lay.addStretch(1)
            code = label(str(value), "setVal")
            code.setFont(theme.qfont(13, 600, mono=True))
            line_lay.addWidget(code, 0)
            detail_lay.addWidget(line)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 0)
        if removable:
            use = _ghost("设为当前")
            use.clicked.connect(lambda: on_use(profile.key))
            btn_row.addWidget(use)
            delete = _ghost("删除")
            delete.clicked.connect(lambda: on_delete(profile.key))
            btn_row.addWidget(delete)
        else:
            note = label("识别不到配置时使用。新屏切不动就先「重新标定」。", "setSub")
            note.setWordWrap(True)
            btn_row.addWidget(note, 1)
        btn_row.addStretch(1)
        detail_lay.addLayout(btn_row)
        self.detail.setVisible(False)
        lay.addWidget(self.detail)

    def _toggle(self, _event=None) -> None:  # noqa: ANN001
        self._open = not self._open
        self.detail.setVisible(self._open)
        self._hint.setText("收起" if self._open else "展开")
        self._chev.setText("▾" if self._open else "▸")
        self.toggled.emit(self._open)


class MonitorPage(QWidget):
    backRequested = Signal()
    saved = Signal(object)              # Config
    calibrateRequested = Signal(object)  # MonitorProfile
    reidentifyRequested = Signal()
    toastRequested = Signal(str)

    def __init__(self, cfg: Config, config_file: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.config_file = Path(config_file)
        self._build()
        self.refresh()

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        # 内容整体放进滚动区：展开「已保存的显示器」时卡片会长高，
        # 页面高度是固定的（窗口尺寸写死），没有滚动区就只能把卡片压扁 ——
        # 表现就是展开的详情被卡片底边裁掉一截。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        self._scroll = scroll

        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(20, 14, 20, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        t0 = theme.tokens()
        self.btn_back = QPushButton("‹ 返回设置")
        self.btn_back.setFixedHeight(26)
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.setFocusPolicy(Qt.NoFocus)
        self.btn_back.setFont(theme.qfont(11, 550))
        self.btn_back.setStyleSheet(
            f"QPushButton{{background:transparent;border:0;color:{t0.ink2};"
            "font-size:11px;font-weight:550;padding:0 2px;}"
            f"QPushButton:hover{{color:{t0.accent_deep};}}"
        )
        self.btn_back.clicked.connect(self.backRequested)
        top.addWidget(self.btn_back, 0, Qt.AlignVCenter)
        top.addStretch(1)
        top.addWidget(label("设置 · 显示器配置", "setSub"))
        lay.addLayout(top)

        # ---- 当前屏 ----
        self.card = _card()
        card_lay = QHBoxLayout(self.card)
        card_lay.setContentsMargins(11, 10, 11, 10)
        card_lay.setSpacing(11)
        t = theme.tokens()
        self.plate = QLabel()
        self.plate.setFixedSize(44, 44)
        self.plate.setPixmap(
            icons.plain_plate_pixmap("monitor", 44, t.card3, t.ink3, border=t.line)
        )
        card_lay.addWidget(self.plate, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.name = label("外接显示器", "setTitle")
        self.name.setFont(theme.qfont(13.5, 650))
        self.name.setStyleSheet(
            f"color:{t.ink};background:transparent;font-size:13.5px;font-weight:650;"
        )
        name_row.addWidget(self.name)
        self.pill = label("未标定", "pillWarn")
        self.pill.setFont(theme.qfont(10, 650))
        name_row.addWidget(self.pill)
        name_row.addStretch(1)
        col.addLayout(name_row)
        self.identity = ElidedLabel("", "setSub")
        col.addWidget(self.identity)
        card_lay.addLayout(col, 1)
        self.btn_reidentify = _ghost("重新识别")
        self.btn_reidentify.clicked.connect(self.reidentifyRequested)
        card_lay.addWidget(self.btn_reidentify, 0, Qt.AlignVCenter)
        self.btn_calibrate = _ghost("重新标定")
        self.btn_calibrate.clicked.connect(
            lambda: self.calibrateRequested.emit(self._editable_profile())
        )
        card_lay.addWidget(self.btn_calibrate, 0, Qt.AlignVCenter)
        lay.addWidget(self.card)

        # ---- 接线 ----
        lay.addWidget(label("接线 · 哪台机器接哪个口", "setSec"))
        wire_row = QHBoxLayout()
        wire_row.setSpacing(8)
        local_name = socket.gethostname().split(".")[0] or "本机"
        self.wire_local = self._wire_card("这台机器", local_name, "local")
        self.wire_peer = self._wire_card("对方机器", "对端", "peer")
        wire_row.addWidget(self.wire_local[0])
        wire_row.addWidget(self.wire_peer[0])
        lay.addLayout(wire_row)
        self.combo_local = self.wire_local[1]
        self.combo_peer = self.wire_peer[1]
        self.peer_name_label = self.wire_peer[2]
        lay.addWidget(label("改完立即生效，不用重启程序。", "setSub"))

        # ---- 端口码表 ----
        head = QHBoxLayout()
        head.setContentsMargins(0, 5, 0, 2)
        head.addWidget(label("端口码表", "setSec"))
        head.addStretch(1)
        head.addWidget(label("成功率约 50% · 数字可直接点击修改", "setSub"))
        lay.addLayout(head)
        self.port_host = QWidget()
        self.port_grid = QGridLayout(self.port_host)
        self.port_grid.setContentsMargins(0, 0, 0, 0)
        self.port_grid.setHorizontalSpacing(8)
        self.port_grid.setVerticalSpacing(8)
        lay.addWidget(self.port_host)

        # ---- 已保存 ----
        lay.addWidget(label("已保存的显示器", "setSec"))
        self.saved_card = _card()
        self.saved_lay = QVBoxLayout(self.saved_card)
        self.saved_lay.setContentsMargins(0, 0, 0, 0)
        self.saved_lay.setSpacing(0)
        lay.addWidget(self.saved_card)
        lay.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

    def _wire_card(self, caption: str, name: str, role: str):
        t = theme.tokens()
        frame = QFrame()
        frame.setObjectName("wireCard")
        frame.setStyleSheet(
            f"QFrame#wireCard{{background:#fff;border:1px solid {t.line_soft};"
            "border-radius:10px;}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(11, 9, 11, 9)
        lay.setSpacing(3)
        lay.addWidget(label(caption, "setSub"))
        name_label = ElidedLabel(name, "setTitle")
        name_label.setFont(theme.qfont(11.5, 550))
        lay.addWidget(name_label)
        combo = _port_combo()
        combo.currentIndexChanged.connect(self._save_wiring)
        row = QFrame()
        row.setObjectName("wireValue")
        row.setStyleSheet(
            f"QFrame#wireValue{{background:#fff;border:1px solid {t.line};"
            "border-radius:7px;}"
        )
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(8, 3, 5, 3)
        row_lay.setSpacing(4)
        row_lay.addWidget(label("输入口", "setSub"), 0, Qt.AlignVCenter)
        row_lay.addStretch(1)
        row_lay.addWidget(combo, 0, Qt.AlignVCenter)
        lay.addWidget(row)
        return frame, combo, name_label

    # ------------------------------------------------------------------ 数据
    def set_config(self, cfg: Config) -> None:
        self.cfg = cfg
        self.refresh()

    def _identity(self):
        return getattr(self.cfg, "_active_identity", None)

    def _editable_profile(self) -> MonitorProfile:
        profile = copy.deepcopy(self.cfg.active_profile())
        ident = self._identity()
        if ident is not None and profile.key != ident.key():
            profile.key = ident.key()
            profile.name = profile.name or getattr(ident, "name", "")
            profile.serial = profile.serial or getattr(ident, "serial", "")
        return profile

    def refresh(self) -> None:
        profile = self._editable_profile()
        self.name.setText(profile.name or "外接显示器")
        ident = self._identity()
        bits = []
        if ident is not None:
            bits.append(f"产品号 {ident.product_code}")
        if profile.serial:
            bits.append(f"序列号 {profile.serial}")
        bits.append(f"{len(profile.ports)} 个输入口")
        self.identity.setText(" · ".join(bits))

        calibrated = profile.is_calibrated() or profile.key in self.cfg.monitors
        self.pill.setText("已标定" if calibrated else "未标定")
        self.pill.setObjectName("pillRun" if calibrated else "pillWarn")
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)

        peer = getattr(self.cfg.clipboard, "peer_name", "") or "对端"
        self.peer_name_label.setText(peer)
        self._rebuild_combos(profile)
        self._rebuild_port_grid(profile)
        self._rebuild_saved(profile)

    def _rebuild_combos(self, profile: MonitorProfile) -> None:
        ports = profile.ordered_ports()
        for combo, role in ((self.combo_local, "local"), (self.combo_peer, "peer")):
            current = self.cfg.port_for_role(role)
            combo.blockSignals(True)
            combo.clear()
            for port in ports:
                combo.addItem(port_label(port), port)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.setEnabled(len(ports) > 0)
            combo.blockSignals(False)

    def _rebuild_port_grid(self, profile: MonitorProfile) -> None:
        while self.port_grid.count():
            item = self.port_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._port_edits: dict[str, QLineEdit] = {}
        t = theme.tokens()
        ports = profile.ordered_ports()
        current = self.cfg.port_for_role("local")
        for i, port in enumerate(ports):
            cell = QFrame()
            cell.setObjectName("portCell")
            selected = port == current
            bg = "rgba(78,135,255,0.05)" if selected else "#fff"
            border = "rgba(78,135,255,0.5)" if selected else t.line_soft
            cell.setStyleSheet(
                f"QFrame#portCell{{background:{bg};border:1px solid {border};"
                "border-radius:9px;}"
            )
            cell.setFixedHeight(38)
            cell_lay = QHBoxLayout(cell)
            cell_lay.setContentsMargins(11, 0, 11, 0)
            cell_lay.setSpacing(6)
            cell_lay.addWidget(label(port_label(port), "actPort"), 0, Qt.AlignVCenter)
            cell_lay.addStretch(1)
            edit = QLineEdit("" if profile.value(port) is None else str(profile.value(port)))
            edit.setFixedWidth(64)
            edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            edit.setValidator(QIntValidator(0, 255, self))
            edit.setFont(theme.qfont(15, 600, mono=True))
            edit.setToolTip("点击数字可直接修改这个端口的码值")
            # 可编辑必须有视觉线索：虚线底边 = 可改；悬浮/聚焦变成实线高亮
            edit.setStyleSheet(
                f"QLineEdit{{background:transparent;border:0;"
                f"border-bottom:1px dashed {t.ink3};border-radius:0;"
                f"color:{t.ink};font-family:'{theme.MONO_FAMILIES[0]}';"
                "font-size:15px;font-weight:600;padding:0 4px;}"
                f"QLineEdit:hover{{background:{t.card2};border-bottom-color:{t.accent};}}"
                f"QLineEdit:focus{{background:{t.accent_soft};"
                f"border-bottom:1px solid {t.accent};}}"
            )
            edit.editingFinished.connect(self._save_ports)
            cell_lay.addWidget(edit, 0, Qt.AlignVCenter)
            self._port_edits[port] = edit
            self.port_grid.addWidget(cell, i // 2, i % 2)
        if not ports:
            self.port_grid.addWidget(label("这块屏还没有可用的端口", "setSub"), 0, 0, 1, 2)

    def _rebuild_saved(self, profile: MonitorProfile) -> None:
        while self.saved_lay.count():
            item = self.saved_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        rows: list[tuple[str, MonitorProfile, bool, bool]] = []
        if profile.is_calibrated() and profile.key not in self.cfg.monitors:
            # 内置表命中的当前屏也列出来，否则「已保存」里看不到正在用的这块
            rows.append((profile.key, profile, True, False))
        for key, saved in self.cfg.monitors.items():
            if key == profile_mod.DEFAULT_KEY:
                rows.append((key, saved, False, False))
            else:
                rows.append((key, saved, key == profile.key, True))
        if not rows:
            rows.append(
                (profile_mod.DEFAULT_KEY,
                 self.cfg.monitors.get(profile_mod.DEFAULT_KEY, profile),
                 False, False)
            )
        for i, (key, saved, current, removable) in enumerate(rows):
            if i:
                sep = QFrame()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background:{theme.tokens().line_soft};border:0;")
                self.saved_lay.addWidget(sep)
            row = SavedRow(
                saved,
                current=current,
                removable=removable,
                on_use=self._use_saved,
                on_delete=self._delete_saved,
            )
            row.toggled.connect(lambda opened, r=row: opened and self._reveal(r))
            self.saved_lay.addWidget(row)

    def _reveal(self, row: QWidget) -> None:
        """把刚展开的那条滚进可视区。

        布局要等下一轮事件循环才会重新分配高度，所以延后一帧再滚 ——
        直接调 `ensureWidgetVisible` 用的是展开前的几何，会滚不到位。
        """
        QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(row, 0, 16))

    # ------------------------------------------------------------------ 保存
    def _save_wiring(self, *_args) -> None:
        local = self.combo_local.currentData()
        peer = self.combo_peer.currentData()
        if not local or not peer:
            return
        if local == peer:
            self.toastRequested.emit("这台和对方不能接同一个输入口")
            self._rebuild_combos(self._editable_profile())
            return
        self.cfg.wiring = {"local": local, "peer": peer}
        try:
            self.cfg.sync_sources()
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.saved.emit(self.cfg)

    def _save_ports(self) -> None:
        profile = self._editable_profile()
        ports: dict[str, int] = {}
        for port, edit in getattr(self, "_port_edits", {}).items():
            text = str(edit.text()).strip()
            if not text:
                continue
            try:
                value = int(text)
            except ValueError:
                self.toastRequested.emit(f"{port_label(port)} 的码值不是数字")
                return
            if not 0 <= value <= 0xFF:
                self.toastRequested.emit(f"{port_label(port)} 的码值必须在 0..255")
                return
            ports[port] = value
        if not ports:
            self.toastRequested.emit("至少保留一个端口的码值")
            return
        if ports == profile.ports:
            return
        profile.ports = ports
        profile.calibrated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._persist(profile)

    def _persist(self, profile: MonitorProfile) -> None:
        profile.builtin = False
        self.cfg.monitors[profile.key] = profile
        self.cfg.active_monitor = profile.key
        self.cfg.set_active_profile(profile)
        try:
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.refresh()
        self.saved.emit(self.cfg)

    def _use_saved(self, key: str) -> None:
        profile = copy.deepcopy(self.cfg.monitors[key])
        self.cfg.active_monitor = key
        self.cfg.set_active_profile(profile)
        try:
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.refresh()
        self.saved.emit(self.cfg)

    def _delete_saved(self, key: str) -> None:
        self.cfg.monitors.pop(key, None)
        if self.cfg.active_monitor == key:
            self.cfg.active_monitor = "auto"
        try:
            save_config(self.cfg, self.config_file)
        except (ConfigError, OSError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.refresh()
        self.saved.emit(self.cfg)
