"""Windows 托盘（Windows 的「菜单栏」，设计 §4）。

交互（§4.1）：
  · 左键单击 → 打开主界面并置前（**不切换画面**）
  · 双击     → 同左键（两次单击不会连切）
  · 右键     → 弹菜单（菜单是唯一「随手切一下」的入口）
  · 悬停     → tooltip：`屏幕切换 · Alt Space · 当前画面：Windows`

菜单（§4.2）：9 行 + 5 条分隔线，宽 240、行高 32、圆角 6；hover 主色实底 + 白字，
键帽同步反白。实现是 `QMenu` + `QWidgetAction` 自绘行（键帽与窗口内完全一致），
Win10 上若自绘行 hover 闪烁，`DUALSWITCH_NATIVE_MENU=1` 退化为原生 QAction +
`setShortcut` 文本（只显示、不注册）。

2026-10-22 跟随 mac2 加了第 7 行「配对电脑… 已配对/未配对」（`docs/UI-WINDOWS2-DESIGN.md`
§14.3）：标题后跟状态文字，与侧栏状态灯同一数据源；行数 +1、分隔线 +1。

已移除：探测显示器 / 查看日志 / 打开配置目录 → 移进 **设置 → 高级**（§7.5）。
退出用普通色（Windows 菜单没有「红字菜单项」惯例）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import QMenu, QWidget, QWidgetAction

from . import theme

log = logging.getLogger("dualswitch.ui.win2.tray")

PORT = {"windows": "HDMI", "mac": "USB-C"}


@dataclass(frozen=True)
class MenuEntry:
    """一行菜单。字段与 mac2 的同名结构保持一致（`checked` / `default` 给降级链用）。"""

    kind: str = "item"                 # item / sep / label / status（status=带状态灯的只读行）
    title: str = ""
    action: Callable[[], None] | None = None
    enabled: bool = True
    danger: bool = False               # 保留字段；Windows 端不画红字（§4.2）
    icon: str = ""
    keys: tuple[str, ...] = field(default_factory=tuple)
    led: str = ""                      # status 行的状态灯：ok / idle / none
    checked: bool = False              # 降级链（pystray / win32tray）兼容字段
    default: bool = False


def build_menu_entries(cfg, current: str, hotkey_label: str, actions: dict) -> list[MenuEntry]:
    """生成托盘菜单内容。`current` 是画面归属（windows / mac / unknown）。"""
    known = current in PORT
    remote_ok = bool(actions.get("remote_ready") and actions["remote_ready"]())
    if known:
        other = "mac" if current == "windows" else "windows"
        other_title = "Mac" if other == "mac" else "Windows"
        self_title = "Windows" if current == "windows" else "Mac"
        switch_title = f"切换到 {other_title}（{PORT[other]}）"
        # 「强制切」= 与主按钮同一目标，但跳过「本地优先、失败才请对端」策略直接发 Set
        force_title = f"强制切到 {other_title}（{PORT[other]}）"
        status_title = f"当前画面：{self_title}"
        led = "ok"
    else:
        peer = "Windows" if cfg.other_host() == "windows" else "Mac"
        switch_title = f"从 {peer} 切过来" if remote_ok else "切换（未检测到显示器）"
        force_title = "强制切换（未检测到显示器）"
        status_title = "当前画面：未检测到"
        led = "none"

    hotkey_keys = tuple(theme.keycap_labels(hotkey_label))
    # 配对电脑（§14.3）：标题后跟状态文字，**与侧栏状态灯同一数据源**
    # （runner 传进来的 `pair_state` 就是 `pairing_page.status_text()`），不各算一遍。
    pair_state = ""
    if actions.get("pair_state") is not None:
        try:
            pair_state = str(actions["pair_state"]() or "")
        except Exception:  # noqa: BLE001 - 状态读不出来不影响菜单可用
            pair_state = ""
    pair_title = "配对电脑…" + (f"    {pair_state}" if pair_state else "")
    return [
        MenuEntry("status", status_title, led=led),
        MenuEntry("sep"),
        MenuEntry("item", "打开主界面", actions.get("open"), icon="open"),
        MenuEntry("sep"),
        MenuEntry(
            "item", switch_title, actions.get("switch"), icon="switch",
            enabled=known or remote_ok, keys=hotkey_keys,
        ),
        MenuEntry("item", force_title, actions.get("force"), icon="force", enabled=known),
        MenuEntry("sep"),
        MenuEntry("item", "剪贴板", actions.get("clipboard"), icon="clip",
                  keys=("Ctrl", "Shift", "V")),
        MenuEntry("item", "设置…", actions.get("settings"), icon="settings",
                  keys=("Ctrl", ",")),
        MenuEntry("sep"),
        MenuEntry("item", pair_title, actions.get("pair"), icon="pair"),
        MenuEntry("sep"),
        MenuEntry(
            "label",
            f"热键：{' '.join(hotkey_keys) or '未启用'}（左键打开主界面）",
        ),
        MenuEntry("item", "退出 屏幕切换", actions.get("quit"), icon="quit",
                  keys=("Ctrl", "Q")),
    ]


def menu_shape(entries: list[MenuEntry]) -> list[tuple[str, str]]:
    """测试用：(kind, title) 清单。"""
    return [(e.kind, e.title) for e in entries]


def tooltip_text(hotkey_label: str, current: str) -> str:
    """托盘 tooltip（§4.1）：`屏幕切换 · Alt Space · 当前画面：Windows`。"""
    name = {"windows": "Windows", "mac": "Mac"}.get(current, "未检测到")
    hotkey = (hotkey_label or "未启用").replace("+", " ")
    return f"屏幕切换 · {hotkey} · 当前画面：{name}"


# --------------------------------------------------------------------------- #
# 自绘菜单行（32 高）
# --------------------------------------------------------------------------- #
class MenuRow(QWidget):
    """一行菜单：状态灯 + 文案 + 右对齐键帽；hover 主色实底 + 白字、键帽反白。

    键帽不交给子控件，而是在 paintEvent 里按 `theme` 的键帽规格画 —— 这样 hover
    反白、行高 32、圆角 6 三件事都在一处控制，不会出现「行变蓝了键帽还是灰的」。
    """

    PAD_X = 10
    LED = 7
    KEY_H = 18
    KEY_R = 5
    KEY_GAP = 3

    def __init__(self, entry: MenuEntry, on_trigger, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self.entry = entry
        self._on_trigger = on_trigger
        self._hover = False
        self.setFixedHeight(theme.MENU_ROW_H)
        self.setFixedWidth(theme.MENU_W - 12)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        clickable = entry.kind == "item" and entry.action is not None and entry.enabled
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)

    # ---- 事件 ----
    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self._clickable():
            self._on_trigger(self.entry)
        super().mouseReleaseEvent(event)

    def _clickable(self) -> bool:
        return (
            self.entry.kind == "item"
            and self.entry.action is not None
            and self.entry.enabled
        )

    # ---- 绘制 ----
    def _key_rects(self, fm: QFontMetrics) -> list[tuple[int, int]]:
        """每个键帽的 (x, 宽)，右对齐。"""
        rects: list[tuple[int, int]] = []
        x = self.width() - self.PAD_X
        for label_text in reversed(self.entry.keys):
            min_w = theme.KEY_WIDE_W if theme.is_wide_key(label_text) else theme.KEY_W
            w = max(min_w, fm.horizontalAdvance(label_text) + 11)
            x -= w
            rects.append((x, w))
            x -= self.KEY_GAP
        return list(reversed(rects))

    def paintEvent(self, event):  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        hovered = self._hover and self._clickable()
        if hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(t.accent))
            p.drawRoundedRect(self.rect(), 6, 6)

        is_label = self.entry.kind == "label"
        is_status = self.entry.kind == "status"
        enabled = self.entry.enabled if self.entry.kind == "item" else True
        if hovered:
            fg = t.on_accent
        elif not enabled:
            fg = t.ink3
        elif is_label:
            fg = t.ink3
        else:
            fg = t.ink

        x = self.PAD_X
        if is_status:
            dot_color = t.ok if self.entry.led == "ok" else t.idle
            if hovered:
                dot_color = t.on_accent
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(dot_color))
            p.drawEllipse(x, (self.height() - self.LED) // 2, self.LED, self.LED)
            x += self.LED + 8

        key_fm = QFontMetrics(theme.qfont(theme.KEY_FONT_PX, 650))
        # 按不动的行不给键帽暗示（与 hero 禁用态同一条规则，§5.2）
        rects = self._key_rects(key_fm) if enabled else []
        text_right = (rects[0][0] - 10) if rects else (self.width() - self.PAD_X)

        font = theme.qfont(11.5 if is_label else 12.5, 500 if not is_label else 400)
        p.setFont(font)
        p.setPen(theme.qcolor(fg))
        fm = QFontMetrics(font)
        title = fm.elidedText(self.entry.title, Qt.ElideRight, max(20, text_right - x))
        p.drawText(x, 0, text_right - x, self.height(), Qt.AlignLeft | Qt.AlignVCenter, title)

        if rects:
            p.setFont(theme.qfont(theme.KEY_FONT_PX, 650))
            for (kx, kw), label_text in zip(rects, self.entry.keys):
                ky = (self.height() - self.KEY_H) // 2
                if hovered:
                    p.setPen(theme.qcolor("rgba(255,255,255,72)"))
                    p.setBrush(theme.qcolor("rgba(255,255,255,51)"))
                else:
                    p.setPen(theme.qcolor(t.key_border))
                    p.setBrush(theme.qcolor(t.key_bg))
                p.drawRoundedRect(kx, ky, kw, self.KEY_H, self.KEY_R, self.KEY_R)
                p.setPen(theme.qcolor(t.on_accent if hovered else t.key_fg))
                p.drawText(kx, ky, kw, self.KEY_H, Qt.AlignCenter, label_text)
        p.end()


# --------------------------------------------------------------------------- #
# 菜单
# --------------------------------------------------------------------------- #
class TrayMenu(QMenu):
    """托盘右键菜单：自绘行（默认）或原生 QAction（降级）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("trayMenu")
        self._native = os.environ.get("DUALSWITCH_NATIVE_MENU") == "1"
        self.setMinimumWidth(theme.MENU_W)

    def rebuild(self, entries: list[MenuEntry]) -> None:
        self.clear()
        if self._native:
            self._rebuild_native(entries)
        else:
            self._rebuild_rows(entries)

    # ---- 自绘行 ----
    def _rebuild_rows(self, entries: list[MenuEntry]) -> None:
        for entry in entries:
            if entry.kind == "sep":
                self.addSeparator()
                continue
            row = MenuRow(entry, self._trigger)
            act = QWidgetAction(self)
            act.setDefaultWidget(row)
            self.addAction(act)

    def _trigger(self, entry: MenuEntry) -> None:
        """点行：先关菜单，再执行（否则菜单会压在新窗口上）。"""
        self.close()
        if entry.action is None:
            return
        QTimer.singleShot(0, entry.action)

    # ---- 原生降级 ----
    def _rebuild_native(self, entries: list[MenuEntry]) -> None:
        for entry in entries:
            if entry.kind == "sep":
                self.addSeparator()
                continue
            text = entry.title
            if entry.keys:
                text = f"{text}\t{' '.join(entry.keys)}"
            act = self.addAction(text)
            act.setEnabled(entry.enabled and entry.action is not None)
            if entry.action is not None and entry.enabled:
                act.triggered.connect(entry.action)


# --------------------------------------------------------------------------- #
# 托盘图标
# --------------------------------------------------------------------------- #
class WinTray:
    """QSystemTrayIcon 封装：左键/双击开窗，右键菜单。"""

    kind = "tray"

    def __init__(self, icon: QIcon, on_open: Callable[[], None] | None = None):
        from PySide6.QtWidgets import QSystemTrayIcon

        self._QSystemTrayIcon = QSystemTrayIcon
        self._on_open = on_open
        self._menu = TrayMenu()
        self._tray = QSystemTrayIcon(icon)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._activated)
        self._tray.show()
        self._log_state("首次注册")

    # ---- 交互 ----
    def _activated(self, reason) -> None:  # noqa: ANN001
        if reason in (self._QSystemTrayIcon.Trigger, self._QSystemTrayIcon.DoubleClick):
            if self._on_open is not None:
                self._on_open()

    # ---- 诊断（沿用现有排查路径：docs/TROUBLESHOOTING.md §4） ----
    def _log_state(self, when: str) -> None:
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
        except Exception:  # noqa: BLE001
            log.exception("读取托盘状态失败（%s）", when)

    def is_available(self) -> bool:
        try:
            return bool(self._QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:  # noqa: BLE001
            return False

    def geometry(self) -> str:
        try:
            rect = self._tray.geometry()
            if rect.isNull() or rect.width() <= 0:
                return ""
            return f"{rect.left()},{rect.top()} {rect.width()}x{rect.height()}"
        except Exception:  # noqa: BLE001
            return ""

    def reassert(self) -> None:
        try:
            self._tray.hide()
            self._tray.show()
        except Exception:  # noqa: BLE001
            log.exception("重新注册托盘图标失败")
            return
        self._log_state("重新注册")

    def recreate(self) -> None:
        icon = self._tray.icon()
        tooltip = self._tray.toolTip()
        menu = self._menu
        try:
            self._tray.activated.disconnect(self._activated)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._tray.hide()
            self._tray.deleteLater()
        except Exception:  # noqa: BLE001
            log.exception("销毁旧的托盘图标失败")
        self._tray = self._QSystemTrayIcon(QIcon(icon))
        self._tray.activated.connect(self._activated)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip(tooltip)
        self._tray.show()
        self._log_state("重建")

    # ---- 接口 ----
    def set_tooltip(self, text: str) -> None:
        self._tray.setToolTip(text)

    def set_menu(self, entries: list[MenuEntry]) -> None:
        try:
            self._menu.rebuild(entries)
            self._tray.setContextMenu(self._menu)
        except Exception:  # noqa: BLE001 - 自绘行出问题时退化为原生菜单
            log.exception("自绘菜单失败，退化为原生 QAction")
            self._menu = TrayMenu()
            self._menu._native = True  # noqa: SLF001
            self._menu.rebuild(entries)
            self._tray.setContextMenu(self._menu)

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def is_visible(self) -> bool:
        return bool(self._tray.isVisible())

    def notify(self, title: str, message: str, icon=None, msecs: int = 4000) -> None:  # noqa: ANN001
        try:
            self._tray.showMessage(title, message, icon, msecs)
        except Exception:  # noqa: BLE001
            log.info("[通知] %s：%s", title, message)


def build_status_item(icon: QIcon, on_open: Callable[[], None] | None = None):  # noqa: ANN201
    """托盘图标；系统托盘不可用时返回 None（调用方退回「关窗 = 最小化」）。"""
    try:
        item = WinTray(icon, on_open)
    except Exception:  # noqa: BLE001
        log.exception("创建托盘图标失败")
        return None
    if not item.is_available():
        log.warning("系统托盘不可用（远程桌面 / 精简系统），关窗将改为最小化")
        return None
    return item
