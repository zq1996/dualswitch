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
    7  热键：⌥ Space（单击图标打开菜单）  只读行
    8  退出 屏幕切换               可点（危险色） ⌘Q

已按需求移除：探测显示器 / 查看日志 / 打开配置目录（调试入口，不占日常菜单）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

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

    def __init__(self, icon_path, on_click=None):  # noqa: ANN001
        from AppKit import NSImage, NSStatusBar, NSVariableStatusItemLength
        from Foundation import NSMakeSize

        self._on_click = on_click
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
        self._popup_menu()

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


def build_status_item(icon_path, on_click=None):  # noqa: ANN001
    """v2 菜单栏图标；AppKit 不可用时返回 None（v2 不做托盘降级）。"""
    if macos_available():
        try:
            return MacStatusItem(icon_path, on_click)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("dualswitch.ui.mac2").exception("菜单栏状态项创建失败")
    return None
