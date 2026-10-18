"""macOS 外观：系统标题栏（红绿灯）+ 菜单栏 NSStatusItem + 全局菜单栏。

契约说明见 `chrome/base.py`。这个文件是 macOS 端的「外观真源」：
改 mac 界面只改这里，不会碰到 Windows 的文件。

几个刻意的平台取舍：

| 项 | Windows | macOS | 原因 |
|---|---|---|---|
| 窗口外框 | 自绘无边框 + 14px 圆角 + 投影 | 交给系统 | 自绘的圆角窗口在 mac 上很出戏，红绿灯也是系统画的 |
| 常驻入口 | 托盘，双击切换 | 菜单栏，单击切换 | mac 菜单栏图标没有双击习惯；单击更符合直觉 |
| 菜单栏图标 | 彩色 .ico | 单色模板图 | 模板图会随浅色/深色菜单栏自动反色 |
| 设置入口 | 标题栏齿轮 | 应用菜单 ⌘, + 内容区一行 | mac 应用的标准位置是应用菜单 |
| 系统通知 | 托盘气泡 | 暂不做（需 UNUserNotificationCenter 授权） | 用窗口内横幅替代，不额外索要权限 |
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

from .base import Palette

log = logging.getLogger("dualswitch.ui.chrome.mac")

NAME = "macos"
PLATFORM_LABEL = "macOS"

NATIVE_CHROME = True
BODY_TOP = 14
INLINE_SETTINGS_ROW = True
SETTINGS_IN_WINDOW = True        # 设置是主窗口内的一页（点返回回主页面），不另开窗口
CLICK_HINT = "单击图标打开菜单"
REOPEN_ON_ACTIVATE = True
DOCK_FOLLOWS_WINDOW = True       # 窗口收起 → 从程序坞消失；菜单栏图标照常留着
STRICT_BACKEND = "auto"          # mac 只有 CGEventTap 一种后端，左右由 spec 决定
SETTINGS_ROW_HINT = "\u2318 ,"
INLINE_CLIPBOARD_ROW = True      # 剪贴板入口放主页面列表（mac 没有自绘标题栏）
CLIPBOARD_ROW_HINT = "\u2318\u21e7V"
EXTRA_H = 20                     # 主窗口多一行列表，加高 20px

BACKEND_OPTIONS = ["auto", "m1ddc", "command", "mock"]

APP_ICON_NAMES = ("screen-switch.icns", "screen-switch.ico")

MATCH_DESC = "按 m1ddc 列出的显示器名匹配；留空 = 只接一台外接屏时直接用它"

HOTKEY_COPY = {
    "strict_title": "只认右 Option",
    "strict_desc": "关掉则左右 Option 都能触发（macOS 天然可区分左右）",
    "tray_title": "关闭窗口后仍在菜单栏运行",
    "tray_desc": "菜单栏图标常驻，随时可切或打开主界面",
    "swallow_desc": "不再把这次按键传给其它程序",
}

PALETTE = Palette(
    accent="#0A7AFF",
    accent_deep="#0060DF",
    accent_soft="#EAF3FF",
    primary_top="#3D93FF",
    primary_bottom="#0A6BE0",
    primary_top_hover="#4C9CFF",
    primary_bottom_hover="#0B75F0",
    primary_top_down="#0A7AFF",
    primary_bottom_down="#0857BC",
    font_families=("SF Pro Text", "SF Pro Display", "PingFang SC", "Helvetica Neue"),
    body_font_size=13.0,
    app_font_point=11.0,
    key_glyphs={
        # 系统文案：⌘ ⌃ ⌥ ⇧。Alt / Right Alt 是 describe() 的旧叫法，留作兜底，
        # 保证任何路径漏过来的 "Alt" 也会显示成 ⌥，而不是在界面上出现 Mac 没有的键名
        "Command": "\u2318",
        "Win": "\u2318",
        "Control": "\u2303",
        "Ctrl": "\u2303",
        "Option": "\u2325",
        "Alt": "\u2325",
        "Right Option": "\u2325",
        "Right Alt": "\u2325",
        "Shift": "\u21e7",
    },
)

# 系统自己画窗口和圆角，这里不能再叠一层描边/圆角
ROOT_QSS = "background: #FFFFFF; border: 0; border-radius: 0;"
STATUSBAR_QSS = ""
QSS_EXTRA = ""


# --------------------------------------------------------------------------- #
# 窗口：mac 用系统标题栏，不返回自绘控件
# --------------------------------------------------------------------------- #
def titlebar(title: str, show_settings: bool = True):  # noqa: ANN201
    return None


# --------------------------------------------------------------------------- #
# 菜单栏
# --------------------------------------------------------------------------- #
_MACOS_AVAILABLE = sys.platform == "darwin"


def macos_available() -> bool:
    """AppKit 是否可用（缺 pyobjc 时返回 False，调用方退回收托盘）。"""
    if not _MACOS_AVAILABLE:
        return False
    try:
        import AppKit  # noqa: F401
    except Exception:  # pragma: no cover - 依赖缺失
        return False
    return True


_TARGET_CLASS = None


def _target_class():
    """延迟构造 Objective-C 目标类（pyobjc 要求类在模块级注册，且只需一次）。"""
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
    """NSStatusBar 状态项：左键切换、右键菜单、单色模板图标。

    为什么不用 QSystemTrayIcon：Qt 在 macOS 上设置了 contextMenu 之后，
    左右键都会弹菜单，没法做到「左键切换」；托盘图标的模板化也控制不了。
    """

    kind = "menubar"

    def __init__(self, icon_path, on_left_click: Callable[[], None]):  # noqa: ANN001
        from AppKit import NSImage, NSStatusBar, NSVariableStatusItemLength
        from Foundation import NSMakeSize

        self._on_left_click = on_left_click
        self._entries: list = []
        self._menu = None
        self._handlers: dict[int, Callable[[], None]] = {}
        self._next_tag = 100

        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self._item.retain()
        # 某些情况下系统会把新建的状态项当作隐藏（尤其启动早期创建时），显式打开一次
        try:
            self._item.setVisible_(True)
        except Exception:  # pragma: no cover - 老系统没有该方法
            pass
        self._button = self._item.button()

        image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
        image_ok = bool(image is not None and image.isValid())
        if image_ok:
            image.setSize_(NSMakeSize(18, 18))
            image.setTemplate_(True)          # 随菜单栏明暗自动反色
            self._button.setImage_(image)
        else:  # pragma: no cover - 资源缺失时至少还能点
            self._button.setTitle_("\u21c4")
        # 排障用：菜单栏图标「看不见」时，先看这行日志判断是没建出来还是建了图不显示
        log.info(
            "菜单栏状态项：图标=%s 有效=%s 长度=%s 宽=%s 可见=%s",
            icon_path,
            image_ok,
            self._item.length(),
            getattr(self._button.bounds().size, "width", "?"),
            self._item.isVisible(),
        )

        # 左右键都回调进来，自己判断是「切换」还是「弹菜单」
        from AppKit import NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp

        self._button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self._target = _target_class().alloc().initWithOwner_(self)
        self._target.retain()
        self._button.setTarget_(self._target)
        self._button.setAction_("statusClicked:")

    # ------------------------------------------------------------------ 回调
    def on_status_clicked(self) -> None:
        """左右键都弹菜单 —— macOS 菜单栏图标的默认心智就是「点开是一张菜单」。

        明面操作（切换/强制切换/设置/退出）全在菜单里，不再依赖右键或单击即切。
        """
        self._popup_menu()

    def on_menu_action(self, tag: int) -> None:
        handler = self._handlers.get(tag)
        if handler is None:
            return
        try:
            handler()
        except Exception:  # pragma: no cover
            log.exception("菜单项处理失败")

    def _popup_menu(self) -> None:
        if self._menu is None:
            return
        from AppKit import NSPoint

        try:
            # 贴着图标下沿弹出，和系统菜单栏一致
            location = NSPoint(0, self._button.bounds().size.height + 4)
            self._menu.popUpMenuPositioningItem_atLocation_inView_(None, location, self._button)
        except Exception:  # pragma: no cover
            log.exception("菜单栏菜单弹出失败")

    # ------------------------------------------------------------------ 接口
    def set_tooltip(self, text: str) -> None:
        try:
            self._button.setToolTip_(text)
        except Exception:  # pragma: no cover
            pass

    def set_menu(self, entries) -> None:       # noqa: ANN001 - list[MenuEntry]
        from AppKit import NSMenu, NSMenuItem

        self._entries = list(entries)
        self._handlers.clear()
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        for entry in entries:
            if entry.kind == "sep":
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            if entry.kind == "label" or entry.action is None:
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
            item.setState_(1 if entry.checked else 0)
            if entry.key:
                from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagShift

                item.setKeyEquivalent_(entry.key)
                mask = NSEventModifierFlagCommand
                if entry.modifier == "cmd_shift":
                    mask |= NSEventModifierFlagShift
                item.setKeyEquivalentModifierMask_(mask)
            menu.addItem_(item)

        self._menu = menu
        self._item.setMenu_(None)       # 菜单只在右键时手动弹出，左键留给切换

    def show(self) -> None:
        self._item.setVisible_(True)

    def hide(self) -> None:
        self._item.setVisible_(False)

    def is_visible(self) -> bool:
        try:
            return bool(self._item.isVisible())
        except Exception:  # pragma: no cover
            return False

    def is_available(self) -> bool:
        """菜单栏只有「建没建出来」这一说，能走到这里就说明可用。"""
        return True

    def geometry(self) -> str:
        """状态项在菜单栏里的位置；排障用，和 Qt 托盘保持同名接口。"""
        try:
            rect = self._button.window().frame()
            return f"{int(rect.origin.x)},{int(rect.origin.y)} {int(rect.size.width)}x{int(rect.size.height)}"
        except Exception:  # pragma: no cover
            return ""

    def notify(self, title: str, message: str, icon=None, msecs: int = 4000) -> None:  # noqa: ANN001
        log.info("[通知] %s：%s", title, message)


def build_status_item(icon, on_click: Callable[[], None]):  # noqa: ANN001
    """macOS 优先原生菜单栏；缺少 pyobjc 时退回收托盘。"""
    from .. import theme

    if macos_available():
        try:
            item = MacStatusItem(theme.asset("menubar.png"), on_click)
            log.info("状态项实现：原生 NSStatusBar（菜单栏）")
            return item
        except Exception:  # pragma: no cover - 资源或 AppKit 异常都不该让程序起不来
            log.exception("菜单栏状态项创建失败，退回 Qt 托盘")
    else:  # pragma: no cover - 依赖缺失
        log.warning("AppKit 不可用，退回 Qt 托盘（QSystemTrayIcon）")
    from .win import QtStatusItem

    return QtStatusItem(icon, on_click)


def build_fallback_status_item(on_click: Callable[[], None]):  # noqa: ANN001, ARG001
    """mac 没有「第二实现」的需要：菜单栏要么建得出来，要么 AppKit 就不可用。

    （AppKit 不可用时 `build_status_item` 已经退到 Qt 托盘了。）
    """
    return None


def build_app_menu(runner):  # noqa: ANN001

    """macOS 全局菜单栏：应用菜单里放「设置 ⌘, / 隐藏 ⌘H / 退出 ⌘Q」。"""
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtWidgets import QMenu, QMenuBar

    bar = QMenuBar()                      # 无父对象 = 顶到系统菜单栏
    app_menu = QMenu("屏幕切换", bar)

    act_settings = QAction("设置…", runner.window)
    act_settings.setShortcut(QKeySequence("Ctrl+,"))
    act_settings.triggered.connect(runner.show_settings)
    app_menu.addAction(act_settings)
    app_menu.addSeparator()

    act_hide = QAction("隐藏 屏幕切换", runner.window)
    act_hide.setShortcut(QKeySequence("Ctrl+H"))
    # 走 runner.hide_window 而不是 window.hide：要顺手把程序坞图标收掉
    act_hide.triggered.connect(runner.hide_window)
    app_menu.addAction(act_hide)

    act_quit = QAction("退出 屏幕切换", runner.window)
    act_quit.setShortcut(QKeySequence("Ctrl+Q"))
    act_quit.triggered.connect(runner.quit)
    app_menu.addAction(act_quit)

    bar.addMenu(app_menu)
    return bar


def activate(window) -> None:  # noqa: ANN001
    """从菜单栏/设置项进来时应用不是前台，要显式抢一次焦点。"""
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:  # pragma: no cover
        pass


def set_dock_visible(visible: bool) -> None:
    """在「程序坞里有这个 App」和「只剩菜单栏图标」之间切换。

    窗口收起（红叉 / ⌘W / ⌘H）后切成 Accessory：程序坞图标、⌘Tab 条目、
    应用菜单一起消失，但菜单栏 NSStatusItem 不受影响 —— 它是系统级的，
    跟激活策略无关。再把窗口叫出来时切回 Regular，三者一起回来。

    注意别用 Info.plist 的 `LSUIElement`：那是启动期一次性生效的，
    设成 true 就永远不会出现在程序坞（窗口打开时也不出现），满足不了
    「窗口开着时有 Dock 图标、收起来就没有」这个要求。
    """
    try:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSApplicationActivationPolicyRegular,
        )

        policy = (
            NSApplicationActivationPolicyRegular
            if visible
            else NSApplicationActivationPolicyAccessory
        )
        app = NSApplication.sharedApplication()
        if app.activationPolicy() != policy:
            app.setActivationPolicy_(policy)
            log.info(
                "激活策略 → %s（程序坞图标%s）",
                "Regular" if visible else "Accessory",
                "显示" if visible else "隐藏",
            )
    except Exception:  # pragma: no cover - 拿不到 AppKit 时不该影响主流程
        log.warning("切换程序坞显示状态失败", exc_info=True)


# --------------------------------------------------------------------------- #
# 「辅助功能」权限（mac 独有：没它 CGEventTap 建不起来，全局热键就是聋的）
# --------------------------------------------------------------------------- #
def permission_target() -> str:
    """该去系统设置里为谁打勾。脚本运行时是终端本身，打包后是「屏幕切换」。"""
    from ...hotkey.macos import permission_target as _target

    return _target()


def accessibility_granted(prompt: bool = False) -> bool:
    from ...hotkey.macos import accessibility_granted as _granted

    return _granted(prompt=prompt)


def request_accessibility() -> bool:
    """弹出系统授权框并把「辅助功能」面板打开。返回当前是否已授权。"""
    from ...hotkey.macos import request_accessibility as _request

    return _request()


def open_accessibility_settings() -> None:
    from ...hotkey.macos import open_accessibility_settings as _open

    _open()


def notify(status, title: str, message: str, icon=None) -> None:  # noqa: ANN001
    status.notify(title, message, icon, 4000)


def open_path(path) -> None:  # noqa: ANN001
    """用系统默认程序打开文件/目录（macOS：open 命令）。"""
    import subprocess

    try:
        subprocess.Popen(["open", str(path)])
    except Exception:  # pragma: no cover
        log.warning("打开路径失败 %s", path, exc_info=True)
