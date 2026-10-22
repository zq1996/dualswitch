"""菜单栏弹出菜单：v2 的 8 行结构（INTERACTIONS §1）。

`build_menu_entries()` 是纯函数（可单测）：只算文案 / 类型 / 快捷键 / 可点性，
不碰 AppKit。真正弹菜单的是 `MacStatusItem`（本文件内，v2 自带一份，
不动 `chrome/mac.py` 的旧实现 —— 旧 UI 要保持一行不改）。

菜单结构（自上而下，分隔线分组）：

    1  ● 当前画面：Windows        只读行（灰字+状态灯）
    ───
    2  打开主界面                  可点
    ───
    3  切换到 Mac（USB-C）         可点  ⌥Space
    4  强制切到 Mac（USB-C）       可点        （跳过本地优先策略）
    ───
    5  剪贴板                      可点  ⌘⇧V
    6  设置…                       可点  ⌘,
    ───
    7  配对电脑…  已配对 / 未配对     可点（与侧栏同一个页面）
    ───
    8  热键：⌥ Space（单击图标打开菜单）  只读行
    9  退出 屏幕切换               可点（危险色） ⌘Q

已按需求移除：探测显示器 / 查看日志 / 打开配置目录（调试入口，不占日常菜单）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt

from . import theme

PORT = {"windows": "HDMI", "mac": "USB-C"}


@dataclass(frozen=True)
class MenuEntry:
    kind: str = "item"                 # item / sep / label / status（status=带状态灯的只读行）
    title: str = ""
    action: Callable[[], None] | None = None
    enabled: bool = True
    danger: bool = False
    icon: str = ""                     # 15px 单色图标名（open/switch/force/clip/settings/quit）
    keys: tuple[str, ...] = field(default_factory=tuple)   # 键帽串（⚠ NSMenu 用 keyEquivalent 呈现）
    led: str = ""                      # status 行的状态灯：ok / idle / none


def build_menu_entries(cfg, current: str, hotkey_label: str, actions: dict) -> list[MenuEntry]:
    """生成菜单内容。`current` 是画面归属（windows / mac / unknown）。"""
    known = current in PORT
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
        remote_ok = bool(actions.get("remote_ready") and actions["remote_ready"]())
        switch_title = f"从 {peer} 切过来" if remote_ok else "切换（未检测到显示器）"
        force_title = "强制切换（未检测到显示器）"
        status_title = "当前画面：未检测到"
        led = "none"

    hotkey_keys = tuple(theme.keycap_labels(hotkey_label))
    # NSMenu 没有右对齐状态列：状态直接跟在固定标题后，仍然是「标题 + 状态」两段语义。
    pair_state = ""
    if actions.get("pair_state") is not None:
        try:
            pair_state = str(actions["pair_state"]() or "")
        except Exception:  # noqa: BLE001
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
        MenuEntry("item", "剪贴板", actions.get("clipboard"), icon="clip", keys=("⌘", "⇧", "V")),
        MenuEntry("item", "设置…", actions.get("settings"), icon="settings", keys=("⌘", ",")),
        MenuEntry("sep"),
        MenuEntry("item", pair_title, actions.get("pair"), icon="pair"),
        MenuEntry("sep"),
        MenuEntry(
            "label",
            f"热键：{' '.join(hotkey_keys) or '未启用'}（单击图标打开菜单）",
        ),
        MenuEntry("item", "退出 屏幕切换", actions.get("quit"), icon="quit", danger=True, keys=("⌘", "Q")),
    ]


def menu_shape(entries: list[MenuEntry]) -> list[tuple[str, str]]:
    """测试用：(kind, title) 清单。"""
    return [(e.kind, e.title) for e in entries]


# --------------------------------------------------------------------------- #
# NSStatusItem（v2 自带；不改 chrome/mac.py 的旧实现）
# --------------------------------------------------------------------------- #
def macos_available() -> bool:
    import sys

    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


#: 菜单行不再放图标（用户 2026-10-21：菜单上的图标删掉，行会太高）。
#: 状态行前面那盏小灯保留 —— 它表示「画面在哪台机器」，不是装饰。
MENU_ICONS: dict[str, str] = {}


def _png_bytes(pixmap) -> bytes:  # noqa: ANN001
    """QPixmap → PNG 字节（给 NSImage 用）。"""
    from PySide6.QtCore import QBuffer, QIODevice

    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(buf.data())


def _led_pixmap(ok: bool, size: int = 12):  # noqa: ANN201
    from PySide6.QtGui import QColor, QPainter, QPixmap

    from . import theme

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(theme.qcolor(theme.tokens().ok if ok else theme.tokens().idle))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return pm


_TARGET_CLASS = None


def _target_class():
    global _TARGET_CLASS
    if _TARGET_CLASS is not None:
        return _TARGET_CLASS
    import objc
    from Foundation import NSObject

    class _StatusTarget(NSObject):
        def initWithOwner_(self, owner):  # noqa: N802
            self = objc.super(_StatusTarget, self).init()
            if self is None:
                return None
            self._owner = owner
            return self

        def statusClicked_(self, sender):  # noqa: ANN001, N802
            self._owner.on_status_clicked()

        def menuAction_(self, sender):  # noqa: ANN001, N802
            self._owner.on_menu_action(sender.tag())

    _TARGET_CLASS = _StatusTarget
    return _TARGET_CLASS


class MacStatusItem:
    """菜单栏状态项：左右键都弹菜单（v2：单击图标 = 打开菜单，INTERACTIONS §1）。"""

    kind = "menubar"

    def __init__(self, icon_path, on_click=None, on_open=None):  # noqa: ANN001
        from AppKit import NSImage, NSStatusBar, NSVariableStatusItemLength
        from Foundation import NSMakeSize

        self._on_click = on_click
        self._on_open = on_open
        self._pending_click = None      # 单击延迟弹菜单用的 QTimer
        self._handlers: dict[int, Callable[[], None]] = {}
        self._next_tag = 200
        self._menu = None

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self._item.retain()
        try:
            self._item.setVisible_(True)
        except Exception:  # noqa: BLE001
            pass
        self._button = self._item.button()
        image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
        if image is not None and image.isValid():
            image.setSize_(NSMakeSize(18, 18))
            image.setTemplate_(True)
            self._button.setImage_(image)
        else:  # pragma: no cover
            self._button.setTitle_("\u21c4")

        from AppKit import NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp

        self._button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self._target = _target_class().alloc().initWithOwner_(self)
        self._target.retain()
        self._button.setTarget_(self._target)
        self._button.setAction_("statusClicked:")

    # ------------------------------------------------------------------ 回调
    def on_status_clicked(self) -> None:
        """单击 = 弹菜单，双击 = 打开主界面（用户 2026-10-21 指定）。

        NSStatusItem 不能只靠 clickCount 区分：单击会立刻弹菜单，第二次点击就落到
        菜单上了。所以单击延迟 240ms（约一个双击间隔）再弹；双击在窗口期内到达就
        取消弹菜单、直接开窗口。
        """
        clicks = 1
        try:
            from AppKit import NSApp

            event = NSApp.currentEvent()
            if event is not None:
                clicks = int(event.clickCount())
        except Exception:  # noqa: BLE001
            clicks = 1
        if clicks >= 2:
            self._cancel_pending_click()
            if self._on_open is not None:
                self._on_open()
            return
        self._cancel_pending_click()
        try:
            from PySide6.QtCore import QTimer

            timer = QTimer(self._button)
            timer.setSingleShot(True)
            timer.setInterval(240)
            timer.timeout.connect(self._popup_menu)
            timer.start()
            self._pending_click = timer
        except Exception:  # noqa: BLE001 - 没 Qt 就退回立刻弹
            self._popup_menu()

    def _cancel_pending_click(self) -> None:
        timer = self._pending_click
        self._pending_click = None
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:  # noqa: BLE001
                pass

    def on_menu_action(self, tag: int) -> None:
        handler = self._handlers.get(tag)
        if handler is None:
            return
        try:
            handler()
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("dualswitch.ui.mac2").exception("菜单项处理失败")

    def _popup_menu(self) -> None:
        if self._menu is None:
            return
        from AppKit import NSPoint

        try:
            location = NSPoint(0, self._button.bounds().size.height + 4)
            self._menu.popUpMenuPositioningItem_atLocation_inView_(None, location, self._button)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("dualswitch.ui.mac2").exception("菜单栏菜单弹出失败")

    # ------------------------------------------------------------------ 接口
    def set_tooltip(self, text: str) -> None:
        try:
            self._button.setToolTip_(text)
        except Exception:  # noqa: BLE001
            pass

    def set_menu(self, entries: list[MenuEntry]) -> None:
        from AppKit import (
            NSMenu,
            NSMenuItem,
            NSEventModifierFlagCommand,
            NSEventModifierFlagShift,
        )

        self._handlers.clear()
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for entry in entries:
            if entry.kind == "sep":
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            if entry.kind in ("label", "status") or entry.action is None:
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(entry.title, None, "")
                item.setEnabled_(False)
                if entry.kind == "status":
                    # 设计稿：状态行前面一个状态灯（本机=绿，另一台/未知=灰）
                    image = self._nsimage(_led_pixmap(entry.led != "none"), template=False)
                    if image is not None:
                        item.setImage_(image)
                menu.addItem_(item)
                continue
            tag = self._next_tag
            self._next_tag += 1
            self._handlers[tag] = entry.action
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(entry.title, "menuAction:", "")
            item.setTarget_(self._target)
            item.setTag_(tag)
            if entry.danger:
                try:
                    from AppKit import NSColor

                    item.setForegroundColor_(NSColor.systemRedColor())
                except Exception:  # noqa: BLE001
                    pass
            if entry.keys:
                # 键帽串 → 原生 keyEquivalent（右对齐符号，观感即键帽）
                letter = next(
                    (k for k in reversed(entry.keys) if k not in ("⌘", "⇧", "⌃", "⌥")),
                    "",
                )
                equiv = {"Space": " ", "↩": "\r", "⇥": "\t"}.get(letter, letter.lower())
                if equiv:
                    item.setKeyEquivalent_(equiv)
                    mask = 0
                    for k in entry.keys:
                        if k == "⌘":
                            mask |= NSEventModifierFlagCommand
                        elif k == "⇧":
                            mask |= NSEventModifierFlagShift
                    item.setKeyEquivalentModifierMask_(mask or NSEventModifierFlagCommand)
            menu.addItem_(item)
        self._menu = menu
        self._item.setMenu_(None)

    @staticmethod
    def _icon_pixmap(lucide_name: str, size: int = 15):  # noqa: ANN201
        from . import icons

        return icons.svg_pixmap(lucide_name, size, "#000000", 2.0)

    @staticmethod
    def _nsimage(pixmap, template: bool = True):  # noqa: ANN001, ANN202
        """QPixmap → NSImage；template=True 时由系统按菜单明暗自动反色。"""
        try:
            from AppKit import NSImage
            from Foundation import NSData, NSMakeSize

            raw = _png_bytes(pixmap)
            data = NSData.dataWithBytes_length_(raw, len(raw))
            image = NSImage.alloc().initWithData_(data)
            if image is None:
                return None
            image.setSize_(NSMakeSize(pixmap.width(), pixmap.height()))
            image.setTemplate_(bool(template))
            return image
        except Exception:  # noqa: BLE001
            return None

    def show(self) -> None:
        self._item.setVisible_(True)

    def hide(self) -> None:
        self._item.setVisible_(False)

    def is_visible(self) -> bool:
        try:
            return bool(self._item.isVisible())
        except Exception:  # noqa: BLE001
            return False

    def is_available(self) -> bool:
        return True

    def geometry(self) -> str:
        try:
            rect = self._button.window().frame()
            return f"{int(rect.origin.x)},{int(rect.origin.y)} {int(rect.size.width)}x{int(rect.size.height)}"
        except Exception:  # noqa: BLE001
            return ""

    def notify(self, title: str, message: str, icon=None, msecs: int = 4000) -> None:  # noqa: ANN001
        import logging

        logging.getLogger("dualswitch.ui.mac2").info("[通知] %s：%s", title, message)


def build_status_item(icon_path, on_click=None, on_open=None):  # noqa: ANN001
    """v2 菜单栏图标；AppKit 不可用时返回 None（v2 不做托盘降级）。"""
    if macos_available():
        try:
            return MacStatusItem(icon_path, on_click, on_open)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("dualswitch.ui.mac2").exception("菜单栏状态项创建失败")
    return None
