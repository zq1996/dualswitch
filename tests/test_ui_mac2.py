"""v2 界面测试（INTERACTIONS-v2 验收项）。

覆盖：键帽渲染 / 预览窗 450ms 延时与缩略图触发 / 双态空态 / 来源角标 /
打开方式分派 / 菜单栏菜单结构。剪贴板读写一律用假引擎，不碰真实 QClipboard。
"""

from __future__ import annotations

import os
import sys
import time

import pytest

# v2 界面树按平台分发（darwin → ui/mac2，win32 → ui/win2），这份用例只对 mac 树有意义。
# 不在这里拦一道，就会逼着对端在 Windows 上跑它、然后改断言迁就平台 —— 互相改文件的循环。
if sys.platform != "darwin":
    pytest.skip("v2 界面树是 macOS 端的（win32 走 ui/win2）", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dualswitch.config import Config, SOURCE_UNKNOWN, platform_defaults  # noqa: E402
from dualswitch.sync.engine import ClipRecord  # noqa: E402
from dualswitch.ui.mac2 import openers, statusmenu, theme  # noqa: E402
from dualswitch.ui.mac2.clipboard_page import ClipboardPage, ClipRow, Thumb  # noqa: E402
from dualswitch.ui.mac2.pairing_page import PairingPage, PinGroup  # noqa: E402
from dualswitch.ui.mac2.preview import PreviewController  # noqa: E402
from dualswitch.ui.mac2.settings_page import HotkeyKeycaps  # noqa: E402
from dualswitch.ui.mac2.switch_page import SwitchPage  # noqa: E402
from dualswitch.ui.mac2.widgets import KeyCap, KeyCaps  # noqa: E402
from dualswitch.ui.mac2.window import (  # noqa: E402
    FOOT_PAD_L,
    FOOT_TEXT_X,
    PAGE_CLIP,
    PAGE_PAIR,
    PAGE_SETTINGS,
    PAGE_SWITCH,
    MainWindow,
)


# 这棵 UI 树是 macOS 端的，但用例在 Windows 开发机上也要能跑：
# 来源系统 / 家目录缩写都跟着运行平台走，所以期望值不能写死成 Mac。
IS_MAC = sys.platform == "darwin"
LOCAL_SYS = "Mac" if IS_MAC else "Windows"     # 本机平台名（来源角标 / meta 首段）
PEER_SYS = "Windows" if IS_MAC else "Mac"      # 对端平台名
LOCAL_LOGO = "apple" if IS_MAC else "windows"  # 角标用 PLATFORM_LOGO 口径
PEER_LOGO = "windows" if IS_MAC else "apple"
HOME = os.path.expanduser("~")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def cfg():
    return platform_defaults()


class FakeEngine:
    def __init__(self, records=None):
        self._records = list(records or [])
        self.copied = []
        self.repushed = []
        self.dropped = []
        self.cleared = False

    def records(self):
        return list(self._records)

    def status(self):
        return {"authenticated": "no", "peer_device": "-"}

    def copy_to_local(self, record_id):
        self.copied.append(record_id)
        return True

    def repush(self, record_id):
        self.repushed.append(record_id)

    def drop_record(self, record_id):
        self.dropped.append(record_id)
        self._records = [r for r in self._records if r.id != record_id]

    def clear_records(self):
        self._records = []
        self.cleared = True


class FakePairEngine:
    """配对页测试用的假引擎：只回状态，不碰网络 / 配置。"""

    def __init__(self, **status):
        self.status = {
            "state": "idle", "outcome": "", "error": "", "error_kind": "",
            "pin": "", "expires_in": 0.0, "attempts_left": 5,
            "paired": False, "legacy": False, "mismatch": False,
            "connected": False, "peer_device": "", "peer_name": "",
            "paired_at": 0.0, "addr": "", "last_sync_at": 0.0,
        }
        self.status.update(status)
        self.begin_calls = 0
        self.cancel_calls = 0
        self.unpair_calls = 0
        self.refresh_calls = 0
        self.pair_result = {
            "ok": True, "kind": "", "reason": "",
            "peer_device": "windows-3f2a91c4", "peer_name": "DESKTOP-8Q2K",
            "addr": "192.168.1.31:47823",
        }
        self.peers = [
            {"device": "windows-3f2a91c4", "host": "DESKTOP-8Q2K", "ip": "192.168.1.31",
             "port": 47823, "plat": "windows", "pairing": True, "paired": False},
        ]

    def pairing_status(self):
        return dict(self.status)

    def discovered_peers(self):
        return [dict(p) for p in self.peers]

    def begin_pairing(self):
        self.begin_calls += 1
        self.status.update(state="showing", pin="483921", expires_in=300.0)
        return "483921"

    def cancel_pairing(self):
        self.cancel_calls += 1
        self.status.update(state="idle", pin="", expires_in=0.0)

    def unpair(self):
        self.unpair_calls += 1
        self.status.update(paired=False, peer_device="", peer_name="", paired_at=0.0)

    def refresh_discovery(self):
        self.refresh_calls += 1

    def pair_with_peer(self, addr, pin):
        return dict(self.pair_result)


def make_records() -> list[ClipRecord]:
    return [
        ClipRecord(id="t1", kind="text", text="hello world", size=11, state="ok"),
        ClipRecord(
            id="i1", kind="image", png=_tiny_png(), width=8, height=8, size=64, state="pend", progress=0.5,
        ),
        ClipRecord(id="l1", kind="text", text="https://example.com/a", size=21, state="local"),
        ClipRecord(id="c1", kind="text", text="def foo():\n    return 1", size=22, state="fail"),
    ]


def _tiny_png() -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    img = QImage(8, 8, QImage.Format_ARGB32)
    img.fill(Qt.blue)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


# --------------------------------------------------------------------------- #
# 1. 键帽（INTERACTIONS §0.3）
# --------------------------------------------------------------------------- #
def test_keycap_labels_from_describe():
    assert theme.keycap_labels("Right Option+Space") == ["⌥", "Space"]
    assert theme.keycap_labels("Command+Shift+V") == ["⌘", "⇧", "V"]
    assert theme.keycap_labels("未启用") == []


def test_keycap_widgets_are_individual_blocks(app):
    caps = KeyCaps(["⌘", "⇧", "V"])
    blocks = [c for c in caps.findChildren(KeyCap)]
    assert len(blocks) == 3
    assert caps.layout().spacing() == theme.KEY_GAP == 3
    for block in blocks:
        assert block.height() == theme.KEY_H == 18
    # 宽键（Space）≥30px，普通键 ≥18px（min-width 语义，宽度随文字自适应）
    wide = KeyCaps(["⌥", "Space"])
    sizes = [c.width() for c in wide.findChildren(KeyCap)]
    assert sizes[0] >= theme.KEY_W
    assert sizes[1] >= theme.KEY_WIDE_W


def test_keycaps_used_in_four_places(app, cfg):
    """主按钮 / 菜单 / 设置页 2 行 —— 全部键帽组件，不是文本拼接。"""
    from dualswitch.ui.mac2.settings_page import SettingsPage

    # 1) 主按钮
    page = SwitchPage(cfg, "Right Option+Space", "Q2790R3")
    assert page.hero.keys.labels() == ["⌥", "Space"]
    # 2) 菜单
    entries = statusmenu.build_menu_entries(cfg, "windows", "Right Option+Space", {})
    switch_row = [e for e in entries if e.title.startswith("切换到")][0]
    assert switch_row.keys == ("⌥", "Space")
    # 3/4) 设置页两行键帽
    settings = SettingsPage(cfg, "/tmp/dualswitch-test-cfg.json")
    assert settings.hk_main.findChildren(KeyCap)
    assert settings.hk_clip.findChildren(KeyCap)
    assert settings.hk_clip.spec() == "cmd+shift+v"


def test_hotkey_recorder_captures(app, cfg):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    recorder = HotkeyKeycaps("alt+space")
    got = []
    recorder.captured.connect(got.append)
    recorder._set_recording(True)
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier, "v")
    recorder.keyPressEvent(event)
    assert got == ["ctrl+shift+v"]
    assert recorder.spec() == "ctrl+shift+v"


# --------------------------------------------------------------------------- #
# 1.5 配对入口与退出按钮（INTERACTIONS §12.3 / §13）
# --------------------------------------------------------------------------- #
def test_sidebar_footer_keeps_status_and_drops_version_bar(app, cfg):
    win = MainWindow(cfg, "Option+Space", "v1.0.0")
    # 状态行恢复；热键键帽只显示「⌥ Space」，不能再出现「热键 Option」
    assert win.side_conn.text() == "未连接对端"
    assert win.side_hotkey_text.text() == "热键"
    assert win.side_hotkey.labels() == ["⌥", "Space"]
    # 底部 30px 版本条已删除
    assert not hasattr(win, "foot_ver")
    assert not hasattr(win, "foot_text")
    # 侧栏收窄到只够放图标 + 文字 + 状态灯
    assert theme.SIDEBAR_W == 156


def test_quit_button_hover_shows_red_tint(app, cfg):
    """自绘退出按钮必须自己跟踪 hover：Enter 后粉底出现，Leave 后恢复透明。"""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    win = MainWindow(cfg, "Option+Space", "v1.0.0")
    win.show()
    app.processEvents()
    btn = win.btn_quit

    QApplication.sendEvent(btn, QEvent(QEvent.Enter))
    btn.update()
    app.processEvents()
    img = btn.grab().toImage()
    px = img.pixelColor(img.width() - 3, img.height() // 2)
    assert px.red() - px.blue() >= 8, "hover 没有淡红底"

    QApplication.sendEvent(btn, QEvent(QEvent.Leave))
    btn.update()
    app.processEvents()
    img = btn.grab().toImage()
    px = img.pixelColor(img.width() - 3, img.height() // 2)
    assert abs(px.red() - px.blue()) <= 3, "离开后粉底没有消失"
    win.close()


def test_collapsed_badges_hug_top_right_corner(app, cfg):
    """收起态两个角标统一右对齐：离按钮右边缘 6px。"""
    win = MainWindow(cfg, "Option+Space", "v1.0.0")
    win.set_nav_count(4)
    win.nav_pair.set_status_dot("ok", "已配对")
    win.set_sidebar_collapsed(True)
    win.show()
    app.processEvents()

    dot = win.nav_pair._status_dot
    assert dot.x() == win.nav_pair.width() - 6 - dot.width()

    # 数量徽章：扫描顶部 12 行里的主色像素，量它离右边缘的距离
    nav = win.nav_clip
    img = nav.grab().toImage()
    scale = max(1, img.width() // max(1, nav.width()))
    xs = []
    for y in range(12 * scale):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.blue() > 200 and 50 < c.red() < 160:
                xs.append(x)
    assert xs, "收起态数量徽章没有画出来"
    inset = (img.width() - 1 - max(xs)) / scale
    assert 3 <= inset <= 7, f"角标离右边缘 {inset}px，应约 6px"
    win.close()


def test_sidebar_footer_rows_share_the_same_text_column(app, cfg):
    from PySide6.QtCore import QPoint

    win = MainWindow(cfg, "Option+Space", "v1.0.0")
    win.resize(theme.WIN_W, theme.WIN_H)
    win.show()
    app.processEvents()
    x_conn = win.side_conn.mapTo(win, QPoint(0, 0)).x()
    x_hotkey = win.side_hotkey_text.mapTo(win, QPoint(0, 0)).x()
    x_quit_text = win.btn_quit.mapTo(win, QPoint(0, 0)).x() + FOOT_TEXT_X
    # 侧栏 10 + 行内边距 10 + 16 引导列 + 8 间距 = 44
    assert x_conn == x_hotkey == x_quit_text == 44
    win.close()


def test_quit_button_icon_has_left_padding_and_handle_has_arrow(app, cfg):
    """退出图标不能贴粉底左边缘；收起把手里必须真的画出箭头。"""
    win = MainWindow(cfg, "Option+Space", "v1.0.0")
    win.show()
    app.processEvents()

    # 按钮铺满 footer 宽度，粉底从按钮 0 开始 → 图标左侧必须留出 FOOT_PAD_L。
    img = win.btn_quit.grab().toImage()
    scale = max(1, img.width() // max(1, win.btn_quit.width()))
    dark = 0
    for x in range(FOOT_PAD_L * scale):
        for y in range(img.height()):
            if img.pixelColor(x, y).value() < 150:
                dark += 1
    assert dark == 0, "退出图标侵入了左侧 padding 区"
    icon_dark = sum(
        1
        for x in range(FOOT_PAD_L * scale, (FOOT_PAD_L + 16) * scale)
        for y in range(img.height())
        if img.pixelColor(x, y).value() < 200
    )
    assert icon_dark > 0, "退出图标没有画出来"

    # 把手中央必须有箭头像素（深浅取决于主题 ink2）
    handle = win.side_handle
    handle.set_collapsed(False)
    himg = handle.grab().toImage()
    hs = max(1, himg.width() // max(1, handle.width()))
    cx = himg.width() // 2
    cy = himg.height() // 2
    arrow = sum(
        1
        for x in range(cx - 4 * hs, cx + 4 * hs)
        for y in range(cy - 7 * hs, cy + 7 * hs)
        if himg.pixelColor(x, y).value() < 170
    )
    assert arrow > 0, "收起把手里没有箭头"
    win.close()


def test_pair_nav_uses_status_dot_with_tooltip(app, cfg):
    win = MainWindow(cfg, "Option+Space", "v1.0.0")
    win.nav_pair.set_status_dot("bad", "需重新配对")
    dot = win.nav_pair._status_dot
    assert dot is not None and not dot.isHidden()
    assert dot.width() == dot.height() == 8
    assert win.nav_pair.status_dot_tooltip() == "需重新配对"
    assert win.nav_pair._count == ""          # 不再显示文字徽章
    win.nav_pair.set_status_dot("ok", "已配对")
    assert win.nav_pair.status_dot_tooltip() == "已配对"


def test_sidebar_collapses_to_icons_only(app, cfg):
    win = MainWindow(cfg, "Option+Space", "v1.0.0")
    win.set_nav_count(12)
    win.nav_pair.set_status_dot("idle", "未配对")
    win.set_sidebar_collapsed(True)

    assert win.is_sidebar_collapsed()
    assert win.sidebar.width() == theme.SIDEBAR_W_COLLAPSED == 56
    assert win.sec_core.isHidden() and win.sec_system.isHidden()
    assert win.conn_box.isHidden() and win.hk_box.isHidden()
    assert win.quit_sep.isHidden()
    assert win.btn_quit._collapsed
    assert win.side_handle.toolTip() == "展开侧栏"
    assert win.side_handle.x() == win.sidebar.width() - win.side_handle.W // 2
    for nav in (win.nav_switch, win.nav_clip, win.nav_pair, win.nav_settings):
        assert nav.is_collapsed()
    # 数量还在，收起时改画成图标右上角的小徽章
    assert win.nav_clip._count == "12"
    assert win.nav_clip.toolTip() == "剪贴板"
    # 配对状态灯缩小并压在图标右上角
    dot = win.nav_pair._status_dot
    assert dot.width() == dot.height() == 7
    assert dot.x() + dot.width() <= win.nav_pair.width()
    assert win.nav_pair.status_dot_tooltip() == "未配对"

    win.toggle_sidebar()
    assert not win.is_sidebar_collapsed()
    assert win.sidebar.width() == theme.SIDEBAR_W == 156
    assert not win.conn_box.isHidden() and not win.hk_box.isHidden()
    assert not win.btn_quit._collapsed
    assert win.side_handle.toolTip() == "收起侧栏"


def test_sidebar_has_pair_nav_and_quit_button(app, cfg):
    win = MainWindow(cfg, "alt+space", "v0")
    assert win.nav_pair.text() == "配对电脑"
    assert win.btn_quit.text() == "退出应用"
    assert "⌘Q" in win.btn_quit.toolTip()
    assert win.btn_quit.height() == 31

    hits = []
    win.quitRequested.connect(lambda: hits.append(True))
    win.btn_quit.click()
    assert hits == [True], "退出按钮应只触发一次统一退出入口"


def test_pair_nav_opens_pair_page(app, cfg):
    win = MainWindow(cfg, "alt+space", "v0")
    win.attach_pages(
        SwitchPage(cfg, "alt+space", ""),
        ClipboardPage(FakeEngine([])),
        __import__(
            "dualswitch.ui.mac2.settings_page", fromlist=["SettingsPage"]
        ).SettingsPage(cfg, "/tmp/dualswitch-test-cfg.json"),
        PairingPage(FakePairEngine()),
    )
    changed = []
    win.pageChanged.connect(changed.append)
    win.nav_pair.click()
    assert win.current_page() == PAGE_PAIR
    assert changed[-1] == PAGE_PAIR


def test_pairing_page_goes_through_states(app):
    engine = FakePairEngine()
    page = PairingPage(engine)
    page.resize(592, 520)
    page.show()
    page.activate()
    assert page._state == "discover"
    assert len(page._peer_rows) == 1

    page._start_show_code()
    assert page._state == "showcode"
    assert engine.begin_calls == 1
    assert [b.text() for b in page._pin_blocks] == list("483921")

    page._cancel_to_discover()
    assert engine.cancel_calls == 1
    assert page._state == "discover"

    page._select_peer(engine.peers[0])
    assert page._state == "entercode"
    page.pin_group.fill(0, "123456")
    assert page.btn_pair.isEnabled()

    page._handle_result(engine.pair_result)
    assert page._state == "success"
    assert page.success_title.text() == "已和 DESKTOP-8Q2K 配对"


def test_pairing_page_unpair_needs_inline_confirm(app):
    engine = FakePairEngine(paired=True, peer_device="windows-3f2a91c4",
                            peer_name="DESKTOP-8Q2K", addr="192.168.1.31:47823")
    page = PairingPage(engine)
    page.activate()
    assert page._state == "paired"
    assert page.confirm_box.isHidden()
    page._ask_unpair()
    assert not page.confirm_box.isHidden()
    page._do_unpair()
    assert engine.unpair_calls == 1
    assert page._state == "discover"


def test_pairing_page_status_text(app):
    engine = FakePairEngine()
    page = PairingPage(engine)
    assert page.status_text() == "未配对"
    engine.status.update(paired=True, peer_device="windows-3f2a91c4")
    assert page.status_text() == "已配对"
    engine.status.update(mismatch=True)
    assert page.status_text() == "需重新配对"


def test_pin_group_paste_and_backspace(app):
    group = PinGroup()
    group.fill(0, "48a39 21")          # 非数字要过滤
    assert group.pin() == "483921"
    first = group.edit(0)
    first.setText("")
    assert group.pin() == "83921"
    group.clear()
    assert group.pin() == ""


# --------------------------------------------------------------------------- #
# 2. 菜单栏菜单结构（INTERACTIONS §1）
# --------------------------------------------------------------------------- #
def test_menu_structure_rows(app, cfg):
    entries = statusmenu.build_menu_entries(cfg, "windows", "Right Option+Space", {})
    kinds = [e.kind for e in entries]
    titles = [e.title for e in entries if e.title]
    # 9 行内容 + 5 条分隔线（§1 表格 + §12.3 配对入口）
    assert kinds.count("sep") == 5
    assert len(entries) == 14
    assert titles[0] == "当前画面：Windows"
    assert "打开主界面" in titles
    assert any(t.startswith("切换到 Mac（USB-C）") for t in titles)
    assert any(t.startswith("强制切到 Mac（USB-C）") for t in titles)
    assert "剪贴板" in titles
    assert "设置…" in titles
    assert any(t.startswith("配对电脑…") for t in titles)
    assert any(t.startswith("热键：") for t in titles)
    assert "退出 屏幕切换" in titles
    # 已移除的调试入口不许回来
    for banned in ("探测显示器", "查看日志", "打开配置目录"):
        assert not any(banned in t for t in titles)


def test_menu_status_flips_with_source(app, cfg):
    for current, expect in (
        ("windows", "当前画面：Windows"),
        ("mac", "当前画面：Mac"),
        (SOURCE_UNKNOWN, "当前画面：未检测到"),
    ):
        entries = statusmenu.build_menu_entries(cfg, current, "Right Option+Space", {})
        status_row = [e for e in entries if e.kind == "status"][0]
        assert status_row.title == expect


def test_menu_unknown_uses_remote_text(app, cfg):
    actions = {"remote_ready": lambda: True}
    entries = statusmenu.build_menu_entries(cfg, SOURCE_UNKNOWN, "alt+space", actions)
    switch_row = [e for e in entries if e.kind == "item" and "切过来" in e.title][0]
    assert switch_row.title == f"从 {PEER_SYS} 切过来"
    assert switch_row.enabled
    actions_off = {"remote_ready": lambda: False}
    entries2 = statusmenu.build_menu_entries(cfg, SOURCE_UNKNOWN, "alt+space", actions_off)
    assert all("切过来" not in e.title for e in entries2)


def test_menu_pair_entry_carries_state(app, cfg):
    actions = {
        "pair": lambda: None,
        "pair_state": lambda: "已配对",
    }
    entries = statusmenu.build_menu_entries(cfg, "windows", "alt+space", actions)
    pair_row = [e for e in entries if e.title.startswith("配对电脑…")][0]
    assert pair_row.action is actions["pair"]
    assert pair_row.title.endswith("已配对")
    # 状态为「未配对」时同一行只换后缀，标题不变
    actions["pair_state"] = lambda: "未配对"
    entries = statusmenu.build_menu_entries(cfg, "windows", "alt+space", actions)
    pair_row = [e for e in entries if e.title.startswith("配对电脑…")][0]
    assert pair_row.title.endswith("未配对")


def test_menu_danger_quit(app, cfg):
    entries = statusmenu.build_menu_entries(cfg, "windows", "alt+space", {})
    quit_row = [e for e in entries if "退出" in e.title][0]
    assert quit_row.danger
    assert quit_row.keys == ("⌘", "Q")


# --------------------------------------------------------------------------- #
# 3. 悬浮预览窗：450ms 延时 + 缩略图触发（INTERACTIONS §3.4）
# --------------------------------------------------------------------------- #
def test_preview_delays_are_frset_values():
    assert PreviewController.OPEN_DELAY_MS == 450
    assert PreviewController.CLOSE_DELAY_MS == 220


def test_preview_opens_only_after_450ms(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    ctrl = page.preview_controller
    row = page._rows["t1"]
    # 悬停缩略图 → 450ms 内不开，过了才开
    ctrl.schedule_open(row.record, row.ui_kind, row.thumb)
    QTest.qWait(200)
    assert not ctrl.is_open(), "200ms 就弹出：延时不对"
    QTest.qWait(350)
    assert ctrl.is_open(), "550ms 还没弹出：450ms 触发失败"
    assert ctrl.current_id() == "t1"
    ctrl.hide_now()
    assert not ctrl.is_open()


def test_preview_not_triggered_by_row_body(app, cfg):
    """触发区只有缩略图 —— 行体 enter 不弹（§3.4「不是整行」）。"""
    from PySide6.QtCore import QEvent

    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["t1"]
    ctrl = page.preview_controller
    # 行体 enter 只显 hover 动作，不进 preview 控制器
    QApplication.sendEvent(row, QEvent(QEvent.Enter))
    QTest.qWait(500)
    assert not ctrl.is_open()
    # 缩略图悬停才弹
    row.thumb.thumbHoverEntered.emit()
    QTest.qWait(500)
    assert ctrl.is_open()
    ctrl.hide_now()


def test_preview_size_caps(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["i1"]
    ctrl = page.preview_controller
    ctrl.open_now(row.record, row.ui_kind, row.thumb)
    bubble = ctrl.bubble
    assert bubble.width() <= theme.PV_IMG_MAX_W + 20
    assert bubble.height() <= theme.PV_IMG_MAX_H + 40
    ctrl.hide_now()


def test_preview_close_delay(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["t1"]
    ctrl = page.preview_controller
    ctrl.open_now(row.record, row.ui_kind, row.thumb)
    assert ctrl.is_open()
    ctrl.schedule_close()
    QTest.qWait(100)
    assert ctrl.is_open(), "220ms 关闭延时还没到就关了"
    QTest.qWait(200)
    assert not ctrl.is_open()


# --------------------------------------------------------------------------- #
# 4. 双态空态（INTERACTIONS §3.7）
# --------------------------------------------------------------------------- #
def test_empty_states_are_distinct(app, cfg):
    page = ClipboardPage(FakeEngine([]))
    page.refresh()
    assert page.empty_title.text() == "剪贴板为空"
    assert "复制内容后会显示在这里" in page.empty_sub.text()

    page2 = ClipboardPage(FakeEngine(make_records()))
    page2.refresh()
    page2._set_filter("file")          # 文件 P2 不同步 → 必然空
    assert page2.empty_title.text() == "当前筛选下暂无内容"
    assert page2.empty_sub.text() == "换个筛选条件试试"

    # 搜索无结果走同一「筛选空态」
    page3 = ClipboardPage(FakeEngine(make_records()))
    page3.refresh()
    page3.search_edit.setText("zzz-not-found")
    assert page3.empty_title.text() == "当前筛选下暂无内容"


# --------------------------------------------------------------------------- #
# 5. 来源系统角标（INTERACTIONS §3.3）
# --------------------------------------------------------------------------- #
def test_source_badge_follows_origin_system(app, cfg):
    from dualswitch.ui.mac2.preview import source_system

    local = ClipRecord(id="a", kind="text", text="x", source="local")
    remote = ClipRecord(id="b", kind="text", text="y", source="remote", peer_name="Windows")
    # 本机复制 = 本机系统角标；remote = 对端系统角标（来源系统，不是来源应用）
    assert source_system(local) == LOCAL_LOGO
    assert source_system(remote) == PEER_LOGO
    thumb_local = Thumb(local, "text")
    thumb_remote = Thumb(remote, "text")
    assert thumb_local._badge.cacheKey() != thumb_remote._badge.cacheKey()


def test_row_shows_source_in_meta(app, cfg):
    """来源显示平台名（Mac / Windows），不是「本机/对端」——用户 2026-10-21 指定。"""
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    meta = page._rows["l1"].meta.text()
    assert meta.startswith(LOCAL_SYS)
    assert "链接" in meta
    remote = ClipRecord(id="r1", kind="text", text="x", source="remote", state="ok")
    row = ClipRow(remote)
    assert row.meta.text().startswith(PEER_SYS)


# --------------------------------------------------------------------------- #
# 6. 打开方式按类型分派（INTERACTIONS §3.3）
# --------------------------------------------------------------------------- #
def test_classify_record():
    assert openers.classify_record("image", None) == "image"
    assert openers.classify_record("mixed", "x") == "image"
    assert openers.classify_record("text", "https://a.b/c") == "link"
    assert openers.classify_record("text", "hello") == "text"
    assert openers.classify_record("text", "def f():\n    pass") == "code"


def test_actions_by_type():
    assert "preview" in openers.actions_for("image")
    assert "finder" in openers.actions_for("file")
    assert "openlink" in openers.actions_for("link")
    assert openers.actions_for("text") == ("copy",)


def test_openers_dispatch_injectable():
    calls = []

    def fake_run(cmd):
        calls.append(cmd)

    mac = openers.MacOpeners(runner=fake_run)
    png = _tiny_png()
    path = mac.open_image_preview(png, "clip-test.png")
    assert calls[-1][:2] == ["open", "-a"]
    assert "Preview" in calls[-1]
    assert path.endswith("clip-test.png")

    mac.reveal_in_finder("/tmp/x.txt")
    assert calls[-1] == ["open", "-R", "/tmp/x.txt"]

    mac.open_url("https://example.com")
    assert calls[-1] == ["open", "https://example.com"]


def test_row_buttons_dispatch(app, cfg):
    """行内 hover 动作按类型分派。

    截图 → 预览（**没有「打开文件」**，内存里的图没有文件可打开）；
    链接 → 打开链接。
    """
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    img_row = page._rows["i1"]
    kinds = [b.toolTip() for b in img_row.acts.findChildren(type(img_row.pill))]
    assert "复制" in kinds
    assert "用图片预览打开" in kinds
    assert not [k for k in kinds if k.startswith("打开文件")]
    link_row = page._rows["l1"]
    kinds = [b.toolTip() for b in link_row.acts.findChildren(type(img_row.pill))]
    assert "打开链接" in kinds
    assert "用图片预览打开" not in kinds


# --------------------------------------------------------------------------- #
# 7. 四态药丸 + 行交互（INTERACTIONS §3.6/§3.8）
# --------------------------------------------------------------------------- #
def test_pill_states_and_retry(app, cfg):
    engine = FakeEngine(make_records())
    page = ClipboardPage(engine)
    page.refresh()
    assert page._rows["t1"].pill.text() == "已同步"
    assert page._rows["i1"].pill.text() == "传输中"
    assert page._rows["l1"].pill.text() == "仅本机"
    assert page._rows["c1"].pill.text() == "失败"
    # 仅本机 / 失败 = 重试
    page._rows["c1"].pill.click()
    assert engine.repushed == ["c1"]
    # 已同步 = 复制
    page._rows["t1"].pill.click()
    assert engine.copied == ["t1"]


def test_row_double_click_copies(app, cfg):
    engine = FakeEngine(make_records())
    page = ClipboardPage(engine)
    page.refresh()
    row = page._rows["t1"]
    row.activated.emit(row.record.id)
    assert engine.copied == ["t1"]


# --------------------------------------------------------------------------- #
# 8. 窗口骨架（INTERACTIONS §0.1）
# --------------------------------------------------------------------------- #
def test_window_geometry_and_pages(app, cfg):
    win = MainWindow(cfg, "alt+space", "v0")
    assert (win.width(), win.height()) == (theme.WIN_W, theme.WIN_H) == (780, 580)
    sp = SwitchPage(cfg, "alt+space", "")
    cp = ClipboardPage(FakeEngine([]))
    from dualswitch.ui.mac2.settings_page import SettingsPage

    st = SettingsPage(cfg, "/tmp/dualswitch-test-cfg.json")
    pair = PairingPage(FakePairEngine())
    win.attach_pages(sp, cp, st, pair)
    assert win.pages.count() == 4
    win.show_page(PAGE_CLIP)
    assert win.on_clipboard_page()
    win.show_page(PAGE_SWITCH)
    assert win.current_page() == PAGE_SWITCH
    win.show_page(PAGE_SETTINGS)
    assert win.current_page() == PAGE_SETTINGS
    win.show_page(PAGE_PAIR)
    assert win.current_page() == PAGE_PAIR
    win.close()


def test_unknown_disables_devices_and_button(app, cfg):
    page = SwitchPage(cfg, "alt+space", "Q2790R3")
    page.set_state(SOURCE_UNKNOWN)
    assert page.hero.name.text() == "未检测到外接显示器"
    assert not page.card_win.isEnabled()
    assert not page.card_mac.isEnabled()
    assert not page.hero.button.isEnabled() or page.hero.remote_ok
    # 对端在线时留「从 X 切过来」（ISSUE-002）
    page.set_remote_available(True)
    assert page.hero.button.isEnabled()
    assert "切过来" in page.hero.btn_text.text()


# --------------------------------------------------------------------------- #
# 9. 设置即时生效（INTERACTIONS §4.2）
# --------------------------------------------------------------------------- #
def test_settings_writes_config_on_toggle(app, cfg, tmp_path):
    from dualswitch.ui.mac2.settings_page import SettingsPage

    target = tmp_path / "config.json"
    page = SettingsPage(cfg, target)
    saved = []
    page.saved.connect(saved.append)
    page.sw_notify.setChecked(not cfg.notify)
    page._apply()
    assert saved, "开关变化应触发 saved"
    assert target.exists()
    import json

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["notify"] == (not cfg.notify)
    assert data["switch_policy"] == "standard"
    assert data["appearance"] == "light", "外观固定浅色：设置页已移除深色模式"
    assert data["clipboard"]["storage_limit_mb"] == 200
    assert data["clipboard"]["hover_preview"] is True


# --------------------------------------------------------------------------- #
# 10. 图标库（开源图标，不再自绘）—— 用户返工要求 1
# --------------------------------------------------------------------------- #
def test_icon_library_covers_every_ui_name():
    from dualswitch.ui.mac2 import iconlib
    from dualswitch.ui.mac2.widgets import NAV_ICONS, TOOL_ICONS

    for name in TOOL_ICONS.values():
        assert name in iconlib.LUCIDE or name in iconlib.FILLED, name
    for name in NAV_ICONS.values():
        assert name in iconlib.LUCIDE, name
    # 平台 logo 必须是品牌图标（Simple Icons），不是手绘
    assert "apple" in iconlib.FILLED and "windows" in iconlib.FILLED
    assert iconlib.FILLED["apple"].startswith("<path")
    assert iconlib.LUCIDE["clipboard"].startswith("<")


def test_icon_pixmap_is_tinted_and_scaled(app):
    from dualswitch.ui.mac2 import icons

    pm = icons.svg_pixmap("clipboard", 20, "#FF0000")
    assert pm.width() == 20 * pm.devicePixelRatio() or pm.width() == 20
    img = pm.toImage()
    reds = [
        img.pixelColor(x, y)
        for y in range(img.height())
        for x in range(img.width())
        if img.pixelColor(x, y).alpha() > 200
    ]
    assert reds, "图标没画出来"
    assert all(c.red() > 200 and c.green() < 60 for c in reds), "图标没有被染成指定颜色"


def test_platform_plate_has_gradient_and_white_logo(app):
    from dualswitch.ui.mac2 import icons

    pm = icons.plate_pixmap("apple", 62)
    img = pm.toImage()
    corner = img.pixelColor(2, 2)
    assert corner.alpha() == 0, "圆角外应当透明"
    top_left = img.pixelColor(6, 6)
    bottom_right = img.pixelColor(img.width() - 7, img.height() - 7)
    assert top_left.alpha() == 255
    assert top_left.name() != bottom_right.name(), "平台牌应当是渐变填充"


# --------------------------------------------------------------------------- #
# 11. 暂停记录（真功能）—— 用户返工要求 4
# --------------------------------------------------------------------------- #
class PauseAwareEngine(FakeEngine):
    def __init__(self, records=None):
        super().__init__(records)
        self.paused_calls: list[bool] = []

    def set_paused(self, paused: bool) -> None:
        self.paused_calls.append(bool(paused))


def test_pause_button_drives_engine(app, cfg):
    engine = PauseAwareEngine(make_records())
    page = ClipboardPage(engine)
    page.refresh()
    assert page.btn_pause.icon_name() == "pause"
    page.btn_pause.click()
    assert engine.paused_calls == [True], "暂停没有传到引擎：按钮只是换了个图标"
    assert page.paused
    assert page.btn_pause.icon_name() == "play"
    page.btn_pause.click()
    assert engine.paused_calls == [True, False]
    assert not page.paused


def test_engine_pause_discards_local_clipboard(app):
    """暂停期间照常 poll（消费 changeCount）但不入库，恢复后也不补发。"""
    from dualswitch.sync.engine import SyncEngine

    class Backend:
        name = "fake"
        host = "mac"

        def __init__(self):
            self.calls = 0

        def poll(self):
            from dualswitch.clipboard.base import make_text

            self.calls += 1
            return make_text(f"item-{self.calls}", source="mac")

        def write(self, item):  # noqa: ANN001
            pass

        def probe(self):
            return {"backend": "fake"}

        def close(self):
            pass

    cfg = platform_defaults()
    backend = Backend()
    class FakeTransport:
        authenticated = False
        connected = False
        peer_host = ""

    engine = SyncEngine(cfg.clipboard, backend=backend, transport=FakeTransport())
    engine.set_paused(True)
    engine._pump_local()
    assert backend.calls == 1, "暂停时仍要 poll 一次，把变更消费掉"
    assert engine.records() == [], "暂停期间不应写入记录"
    engine.set_paused(False)
    engine._pump_local()
    assert len(engine.records()) == 1


# --------------------------------------------------------------------------- #
# 12. 删除语义 —— 用户返工要求 5
# --------------------------------------------------------------------------- #
def test_row_delete_removes_exactly_one(app, cfg):
    engine = FakeEngine(make_records())
    page = ClipboardPage(engine)
    page.refresh()
    page._rows["t1"].deleteRequested.emit("t1")
    assert engine.dropped == ["t1"]
    assert not engine.cleared, "行内删除不许清空全部"
    assert len(engine.records()) == 3
    assert "t1" not in page._rows


def test_clear_all_requires_confirmation(app, cfg, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from dualswitch.ui.mac2 import clipboard_page as cp

    engine = FakeEngine(make_records())
    page = ClipboardPage(engine)
    page.refresh()

    monkeypatch.setattr(cp.ClearConfirm, "exec", lambda self: QDialog.Rejected)
    page._clear_with_confirm()
    assert not engine.cleared, "取消确认后不许删"

    monkeypatch.setattr(cp.ClearConfirm, "exec", lambda self: QDialog.Accepted)
    page._clear_with_confirm()
    assert engine.cleared


def test_trash_opens_confirm_dialog(app, cfg, monkeypatch):
    """🗑 直接弹确认框（不再弹 1/7/30 天的小菜单）。"""
    from dualswitch.ui.mac2 import clipboard_page as cp

    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    called = []
    monkeypatch.setattr(page, "_clear_with_confirm", lambda: called.append(True))
    page.btn_clear.click()
    assert called == [True]


# --------------------------------------------------------------------------- #
# 13. 剪贴板页不留文字按钮 —— 用户返工要求 3
# --------------------------------------------------------------------------- #
def test_clipboard_page_buttons_are_icon_only(app, cfg):
    from dualswitch.ui.mac2.widgets import IconButton

    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    assert page.btn_pause.text() == ""
    assert page.btn_clear.text() == ""

    row = page._rows["i1"]
    acts = row.acts.findChildren(IconButton)
    assert acts and all(b.text() == "" for b in acts)
    assert {b.toolTip() for b in acts} >= {"复制", "用图片预览打开", "删除"}

    page.preview_controller.open_now(row.record, row.ui_kind, row.thumb)
    bubble = page.preview_controller.bubble
    for btn in (bubble._btn_copy, bubble._btn_find, bubble._btn_preview):
        assert btn.text() == ""
        assert btn.toolTip()
    page.preview_controller.hide_now()


# --------------------------------------------------------------------------- #
# 14. 渲染细节：键帽不换行、旧控件不留影、元信息分点
# --------------------------------------------------------------------------- #
def test_keycap_never_wraps_and_fits_text(app):
    cap = KeyCap("Space")
    assert "\n" not in cap.text()
    assert cap.width() >= cap.fontMetrics().horizontalAdvance("Space")
    assert cap.height() == theme.KEY_H


def test_set_keys_does_not_leave_ghost_caps(app):
    caps = KeyCaps(["⌥", "Space"])
    caps.set_keys(["⌘", "Q"])
    assert len(caps._caps) == 2
    assert len(caps.findChildren(KeyCap)) == 2, "旧键帽没撤下，会叠影"


def test_meta_line_parts_have_dots(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    parts = page._rows["l1"].meta.parts()
    assert parts[0] == (LOCAL_SYS, True)
    assert parts[-1][0] == "链接"
    assert " · " in page._rows["l1"].meta.text()


def test_meta_line_includes_source_app(app, cfg):
    """`Mac · 微信 · 时间 …`：有 app 就插在来源后面。"""
    rec = ClipRecord(id="a1", kind="text", text="hi", source="local", state="ok",
                     app="微信", app_id="com.tencent.xinWeChat")
    row = ClipRow(rec)
    parts = [p for p, _ in row.meta.parts()]
    assert parts[:2] == [LOCAL_SYS, "微信"]

    shot = ClipRecord(id="a2", kind="text", text="hi", source="local", state="ok", app="截图")
    assert [p for p, _ in ClipRow(shot).meta.parts()][:2] == [LOCAL_SYS, "截图"]


def test_theme_qcolor_parses_rgba_tokens():
    c = theme.qcolor("rgba(78,135,255,33)")
    assert c.isValid()
    assert (c.red(), c.green(), c.blue(), c.alpha()) == (78, 135, 255, 33)


# --------------------------------------------------------------------------- #
# 15. 设置项真的生效（不是只写配置）
# --------------------------------------------------------------------------- #
def test_hover_preview_setting_blocks_preview(app, cfg):
    """设置页关掉「悬浮预览」→ 悬停缩略图不再弹窗。"""
    from PySide6.QtTest import QTest

    page = ClipboardPage(FakeEngine(make_records()), hover_enabled=lambda: False)
    page.refresh()
    page._rows["t1"].thumb.thumbHoverEntered.emit()
    QTest.qWait(520)
    assert not page.preview_controller.is_open()

    page._hover_enabled = lambda: True
    page._rows["t1"].thumb.thumbHoverEntered.emit()
    QTest.qWait(520)
    assert page.preview_controller.is_open()
    page.preview_controller.hide_now()


def test_retention_days_prunes_old_records(app, cfg):
    """「保留历史」= 7 天 → 引擎真要清掉更早的条目。"""
    import time as _time

    from dualswitch.clipboard.base import make_text
    from dualswitch.sync.engine import SyncEngine

    class FakeTransport:
        authenticated = False
        connected = False
        peer_host = ""

    engine = SyncEngine(cfg.clipboard, backend=None, transport=FakeTransport())
    engine.cfg.retention_days = 7
    fresh = engine._add(make_text("fresh", source="mac"), source="local", state="local")
    old = engine._add(make_text("old", source="mac"), source="local", state="local")
    old.ts = _time.time() - 8 * 86400
    engine._last_expire_at = 0.0
    engine._expire_old()
    ids = [r.id for r in engine.records()]
    assert fresh.id in ids
    assert old.id not in ids

    engine.cfg.retention_days = 0          # 不限：一条都不许删
    engine._last_expire_at = 0.0
    engine._expire_old()
    assert len(engine.records()) == 1


# --------------------------------------------------------------------------- #
# 16. 预览气泡：箭头是子控件、三段不挤压（第三轮返工）
# --------------------------------------------------------------------------- #
def test_preview_arrow_is_visible_child(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["t1"]
    ctrl = page.preview_controller
    ctrl.open_now(row.record, row.ui_kind, row.thumb)
    bubble = ctrl.bubble
    arrow = bubble._arrow
    assert arrow.parent() is bubble and arrow.isVisible()
    # 箭头贴在卡片朝锚点的那条边上
    if bubble.arrow_right:
        assert arrow.x() + arrow.width() >= bubble.width() - 2
    else:
        assert arrow.x() <= 2
    ctrl.hide_now()


def test_preview_footer_not_squeezed(app, cfg):
    """底栏按钮不许被裁：底栏高度 == 它的 sizeHint，按钮完整落在底栏内。"""
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    ctrl = page.preview_controller
    for rid in ("t1", "i1"):
        row = page._rows[rid]
        ctrl.open_now(row.record, row.ui_kind, row.thumb)
        bubble = ctrl.bubble
        assert bubble._foot.height() == bubble._foot.sizeHint().height()
        btn = bubble._btn_copy
        btn_bottom = bubble._foot.y() + btn.y() + btn.height()
        assert btn_bottom <= bubble.height() - 6, "复制按钮贴到底边了"
        right = bubble._inner.x() + btn.x() + btn.width()
        assert right <= bubble.width() - 8, "复制按钮贴到右边了"
        ctrl.hide_now()


def test_preview_image_never_upscaled(app, cfg):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    img = QImage(120, 80, QImage.Format_ARGB32)
    img.fill(QColor("#83A8FF"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    rec = ClipRecord(id="im", kind="image", png=bytes(buf.data()), width=120, height=80,
                     size=9000, state="ok")
    page = ClipboardPage(FakeEngine([rec]))
    page.refresh()
    row = page._rows["im"]
    ctrl = page.preview_controller
    ctrl.open_now(row.record, row.ui_kind, row.thumb)
    from PySide6.QtWidgets import QLabel

    labels = [
        w for w in ctrl.bubble._body_wrap.findChildren(QLabel)
        if w.pixmap() is not None and not w.pixmap().isNull()
    ]
    assert labels, "没有图片标签"
    pm = labels[0].pixmap()
    # 用逻辑尺寸判断（物理宽 ÷ dpr），否则 2x 屏上会误判成放大
    assert pm.width() / max(1.0, pm.devicePixelRatio()) <= 120, "小图被放大了，会糊"
    ctrl.hide_now()


def test_preview_image_retina_sharp(app, cfg):
    """缩放位图必须按 devicePixelRatio 渲染真实像素（2026-10-23）。

    只渲逻辑像素的话，521 宽的位图在 2x 屏上会被插值放大 2 倍，
    截图里的文字直接糊掉 —— 这是用户实测报的 bug。
    """
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QLabel

    img = QImage(1200, 900, QImage.Format_ARGB32)
    img.fill(QColor("#83A8FF"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    rec = ClipRecord(id="sharp", kind="image", png=bytes(buf.data()),
                     width=1200, height=900, size=9000, state="ok")
    page = ClipboardPage(FakeEngine([rec]))
    page.refresh()
    row = page._rows["sharp"]
    ctrl = page.preview_controller
    ctrl.open_now(row.record, row.ui_kind, row.thumb)
    bubble = ctrl.bubble
    labels = [
        w for w in bubble._body_wrap.findChildren(QLabel)
        if w.pixmap() is not None and not w.pixmap().isNull()
    ]
    assert labels, "没有图片标签"
    pm = labels[0].pixmap()
    dpr = bubble.devicePixelRatioF()
    # 位图的 dpr 必须和窗口一致，否则就是没按真实像素渲染
    assert abs(pm.devicePixelRatio() - dpr) < 0.01, f"pixmap dpr={pm.devicePixelRatio()} ≠ 窗口 {dpr}"
    # 物理像素 ≈ 逻辑显示尺寸 × dpr
    logical_w = pm.width() / max(1.0, pm.devicePixelRatio())
    assert abs(logical_w - bubble._image_w) <= 1, "逻辑宽度与气泡宽度不一致"
    assert pm.width() >= logical_w * dpr * 0.99, "位图物理像素不足，2x 屏上会发虚"
    ctrl.hide_now()


def test_preview_measure_uses_logical_pixmap_height(app, cfg):
    """_measure_body_child 必须按**逻辑**高度算（2026-10-23）。

    pixmap 存的是物理像素：Retina 下 `pix.height()` 是逻辑高度的 2 倍。
    直接拿它算会把窗口高度翻倍，预览窗出现大片空白、图片被挤成一条缝。
    用 setDevicePixelRatio(2) 的合成 pixmap 稳定复现，不依赖测试机是不是 retina。
    """
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QLabel

    from dualswitch.ui.mac2.preview import PreviewBubble

    pm = QPixmap(200, 100)
    pm.fill(QColor("#83A8FF"))
    pm.setDevicePixelRatio(2.0)          # 模拟 Retina：物理 200×100 = 逻辑 100×50
    label = QLabel()
    label.setPixmap(pm)

    got = PreviewBubble._measure_body_child(label, 500)
    assert got == 52, f"应按逻辑高度 50 计算（+2 padding），实际 {got} —— 说明用了物理像素 100"


def test_preview_image_clamps_to_bounds(app, cfg):
    """图片预览走 Frset 的 clamp 模型：下限 346×226、上限 562×626（2026-10-23 实测）。

    逻辑是「内容自报 + 硬钳制」：
      · 小图不放大，但窗口撑到下限，别塌成一条；
      · 大图按内容框等比缩，窗口封顶。
    """
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    def make(src_w: int, src_h: int) -> bytes:
        # 必须按真实像素生成：_build_image 是用 PNG 的实际像素算缩放因子的，
        # 这里若预先缩图，测到的就是缩略图尺寸而不是原图行为。
        img = QImage(src_w, src_h, QImage.Format_ARGB32)
        img.fill(QColor("#83A8FF"))
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        return bytes(buf.data())

    def bubble_size(src_w: int, src_h: int) -> tuple[int, int]:
        rec = ClipRecord(id=f"c{src_w}x{src_h}", kind="image", png=make(src_w, src_h),
                         width=src_w, height=src_h, size=9000, state="ok")
        page = ClipboardPage(FakeEngine([rec]))
        page.refresh()
        row = page._rows[rec.id]
        ctrl = page.preview_controller
        ctrl.open_now(row.record, row.ui_kind, row.thumb)
        size = (ctrl.bubble.width(), ctrl.bubble.height())
        ctrl.hide_now()
        return size

    # 小图：窗口顶到下限，图本身不放大
    assert bubble_size(60, 40) == (theme.PV_IMG_MIN_W, theme.PV_IMG_MIN_H)

    # 各种极端比例：宽度/高度都必须落在上下限之间
    for src in ((1200, 180), (260, 1800), (900, 900)):
        w, h = bubble_size(*src)
        assert theme.PV_IMG_MIN_W <= w <= theme.PV_IMG_MAX_W, f"{src} 宽度越界：{w}"
        assert theme.PV_IMG_MIN_H <= h <= theme.PV_IMG_MAX_H, f"{src} 高度越界：{h}"

    # 竖图被高度封顶 → 宽度落在下限，而不是按比例缩成一条
    w, h = bubble_size(260, 1800)
    assert w == theme.PV_IMG_MIN_W
    assert h >= theme.PV_IMG_MAX_H - 12

    # 方图吃满内容框 → 宽度接近上限
    w, _ = bubble_size(900, 900)
    assert w >= theme.PV_IMG_MAX_W - 60


# --------------------------------------------------------------------------- #
# 17. 图片文件 → 预览图 + 来源应用（mac 后端，第三轮）
# --------------------------------------------------------------------------- #
def test_image_file_becomes_preview(app, tmp_path):
    pytest.importorskip("AppKit")
    pytest.importorskip("Quartz")
    from PySide6.QtGui import QColor, QImage

    from dualswitch.clipboard.macos import MacClipboardBackend

    src = tmp_path / "截图示例.png"
    img = QImage(300, 200, QImage.Format_ARGB32)
    img.fill(QColor("#83A8FF"))
    assert img.save(str(src), "PNG")

    backend = MacClipboardBackend.__new__(MacClipboardBackend)   # 不连真实粘贴板
    built = backend._preview_of_image_file(str(src))
    assert built is not None
    png, width, height = built
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (width, height) == (300, 200), "元信息要用原图尺寸，不是缩略图尺寸"

    class FakePasteboard:
        def stringForType_(self, _type):
            return src.as_uri()

    backend._pb = FakePasteboard()
    got = backend._image_from_file_url(["public.file-url"])
    assert got is not None
    assert got[3] == str(src), "要记住原文件路径（预览直接开原文件）"


def test_image_file_url_ignores_non_image(app, tmp_path):
    pytest.importorskip("AppKit")
    from dualswitch.clipboard.macos import MacClipboardBackend

    txt = tmp_path / "note.txt"
    txt.write_text("hello", encoding="utf-8")

    class FakePasteboard:
        def stringForType_(self, _type):
            return txt.as_uri()

    backend = MacClipboardBackend.__new__(MacClipboardBackend)
    backend._pb = FakePasteboard()
    assert backend._image_from_file_url(["public.file-url"]) is None


def test_file_url_beats_finder_icon_bitmap(app, tmp_path):
    """访达复制图片时粘贴板里还有一张文件图标的位图 —— 必须用文件预览，不能用它。"""
    pytest.importorskip("AppKit")
    from PySide6.QtGui import QColor, QImage

    from dualswitch.clipboard.macos import MacClipboardBackend

    src = tmp_path / "真图.png"
    img = QImage(64, 48, QImage.Format_ARGB32)
    img.fill(QColor("#FF8800"))
    assert img.save(str(src), "PNG")

    white = QImage(16, 16, QImage.Format_ARGB32)
    white.fill(QColor("#FFFFFF"))
    from PySide6.QtCore import QBuffer, QIODevice

    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    white.save(buf, "PNG")

    class FakePasteboard:
        def types(self):
            return ["public.file-url", "public.png"]

        def stringForType_(self, _type):
            return src.as_uri()

        def dataForType_(self, _type):
            return buf.data()

    backend = MacClipboardBackend.__new__(MacClipboardBackend)
    backend._pb = FakePasteboard()
    item = backend._read(app="访达", app_id="com.apple.finder")
    assert item is not None
    assert item.path == str(src)
    assert item.image.width == 64 and item.image.height == 48, "用的是文件预览，不是空白图标"
    assert item.app == "访达"


# --------------------------------------------------------------------------- #
# 18. 存储上限：可编辑 + 超限淘汰（第三轮）
# --------------------------------------------------------------------------- #
def test_storage_limit_is_editable(app, cfg, tmp_path):
    import json

    from dualswitch.ui.mac2.settings_page import SettingsPage

    target = tmp_path / "config.json"
    page = SettingsPage(cfg, target)
    page.edit_storage.setText("350")
    page._apply()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["clipboard"]["storage_limit_mb"] == 350

    page.edit_storage.setText("99999")          # 越界要夹到 5000
    assert page._storage_limit_value() == 5000

    hits = []
    page.clearRequested.connect(lambda: hits.append(True))
    page.btn_clean.click()
    assert hits == [True], "「清理」按钮要发清空请求"


def test_engine_drops_oldest_when_over_storage_limit(app, cfg):
    from dualswitch.clipboard.base import make_text
    from dualswitch.sync.engine import SyncEngine

    class FakeTransport:
        authenticated = False
        connected = False
        peer_host = ""

    engine = SyncEngine(cfg.clipboard, backend=None, transport=FakeTransport())
    engine.cfg.storage_limit_mb = 1                 # 1MB
    blob = "x" * 400_000
    first = engine._add(make_text(blob + "1", source="mac"), source="local", state="local")
    engine._add(make_text(blob + "2", source="mac"), source="local", state="local")
    engine._add(make_text(blob + "3", source="mac"), source="local", state="local")
    ids = [r.id for r in engine.records()]
    assert first.id not in ids, "超过上限应丢掉最旧的一条"
    assert len(ids) == 2


# --------------------------------------------------------------------------- #
# 19. 文件（访达复制）：缩略图 / 标题 / 目录 / 按钮组合（用户 2026-10-21 参考 Frset）
# --------------------------------------------------------------------------- #
def _file_rec(rid="fd", name="ChatGPTInstaller.dmg", path=None, png=None):
    path = path or os.path.join(HOME, "Downloads", name)
    return ClipRecord(
        id=rid, kind="mixed" if png else "text", text=name, png=png,
        width=640 if png else 0, height=400 if png else 0, size=20,
        file_size=849 * 1024, state="local", path=path,
        app="访达", app_id="com.apple.finder",
    )


def _tiny_png():
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    img = QImage(64, 48, QImage.Format_ARGB32)
    img.fill(QColor("#83A8FF"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def test_file_record_is_classified_as_file(app):
    from dualswitch.ui.mac2 import openers

    # 图片文件也是「文件」（path 优先），不是「图片」
    assert openers.classify_record("mixed", "a.png", "/x/a.png") == "file"
    assert openers.classify_record("text", "a.dmg", "/x/a.dmg") == "file"
    assert openers.classify_record("mixed", "a.png") == "image"


def test_file_row_title_and_meta_show_name_and_folder(app):
    from dualswitch.ui.mac2.preview import short_dir

    page = ClipboardPage(FakeEngine([_file_rec()]))
    page.refresh()
    row = page._rows["fd"]
    assert row.ui_kind == "file"
    assert row.title.text() == "ChatGPTInstaller.dmg"
    meta = row.meta.text()
    assert "打开文件" not in meta
    assert meta.startswith(LOCAL_SYS)
    assert "访达" in meta
    assert "~/Downloads" in meta, "文件行的元信息要给目录"
    assert not meta.endswith("文件"), "目录已经说明是文件，不要再加类型标签"
    assert short_dir(os.path.join(HOME, "Downloads", "x.png")) == "~/Downloads"


def test_file_row_buttons_by_type(app, cfg):
    """截图 = 只有预览；图片文件 = 预览 + 打开文件；普通文件 = 只有打开文件。"""
    shots = _file_rec("fd", "ChatGPTInstaller.dmg")
    image_file = _file_rec("fi", "照片.png", png=_tiny_png())
    screenshot = ClipRecord(id="sc", kind="image", png=_tiny_png(), width=64, height=48,
                            size=9000, state="ok", app="截图")
    page = ClipboardPage(FakeEngine([shots, image_file, screenshot]))
    page.refresh()

    def tips(rid):
        return {b.toolTip() for b in page._rows[rid].acts.findChildren(type(page._rows[rid].pill))}

    assert tips("fd") == {"复制", "打开文件（在访达中显示）", "删除"}
    assert tips("fi") == {"复制", "用图片预览打开", "打开文件（在访达中显示）", "删除"}
    assert tips("sc") == {"复制", "用图片预览打开", "删除"}


def test_file_bubble_buttons_by_type(app, cfg):
    shots = _file_rec("fd", "ChatGPTInstaller.dmg")
    image_file = _file_rec("fi", "照片.png", png=_tiny_png())
    screenshot = ClipRecord(id="sc", kind="image", png=_tiny_png(), width=64, height=48,
                            size=9000, state="ok", app="截图")
    page = ClipboardPage(FakeEngine([shots, image_file, screenshot]))
    page.refresh()
    ctrl = page.preview_controller
    for rid, want_preview, want_find in (
        ("fd", False, True), ("fi", True, True), ("sc", True, False),
    ):
        row = page._rows[rid]
        ctrl.open_now(row.record, row.ui_kind, row.thumb)
        assert ctrl.bubble._btn_preview.isVisible() is want_preview, rid
        assert ctrl.bubble._btn_find.isVisible() is want_find, rid
        ctrl.hide_now()


def test_file_bubble_is_file_card_not_image(app, cfg, tmp_path):
    """图片文件的气泡是「文件」卡：标题写「文件」，正文是文件名 + 目录。"""
    rec = _file_rec("fi", "照片.png", png=_tiny_png())
    page = ClipboardPage(FakeEngine([rec]))
    page.refresh()
    page.preview_controller.open_now(rec, "file", page._rows["fi"].thumb)
    bubble = page.preview_controller.bubble
    assert bubble._head_title.text() == "文件"
    from PySide6.QtWidgets import QLabel

    texts = [w.text() for w in bubble._body_wrap.findChildren(QLabel)]
    assert "照片.png" in texts, "文件卡正文要有文件名"
    assert "~/Downloads" in texts, "文件卡正文要有目录"
    body_pixmaps = [
        w for w in bubble._body_wrap.findChildren(QLabel)
        if w.objectName() != "pvFileIcon"
        and w.pixmap() is not None and not w.pixmap().isNull()
    ]
    assert not body_pixmaps, "文件卡不该塞图片正文（缩略图里已经有了）"
    page.preview_controller.hide_now()


def test_file_icon_follows_extension(app):
    from dualswitch.ui.mac2 import icons

    assert icons.file_glyph("/a/b.png") == "file-image"
    assert icons.file_glyph("/a/b.dmg") == "file-archive"
    assert icons.file_glyph("/a/notes.txt") == "file"
    assert icons.file_glyph("/a/Makefile") == "file"
    pm = icons.file_pixmap("ChatGPTInstaller.dmg", 52)
    assert pm.height() > pm.width(), "文件图标 = 文档 + 下面一行扩展名"


def test_engine_keeps_file_local_and_uses_real_size(app, cfg):
    """普通文件不跨端（文件本身传不过去），且列表显示真实文件大小。"""
    from dualswitch.clipboard.base import make_file
    from dualswitch.sync.engine import SyncEngine

    sent: list = []

    class FakeTransport:
        authenticated = True
        connected = True
        peer_host = "win"

        def send_clip(self, item):
            sent.append(item)

    engine = SyncEngine(cfg.clipboard, backend=None, transport=FakeTransport())
    item = make_file("/tmp/ChatGPTInstaller.dmg", size=849 * 1024, app="访达")
    engine._send(item)
    assert sent == [], "文件不该发给对端"
    rec = engine.records()[0]
    assert rec.state == "local"
    assert rec.path == "/tmp/ChatGPTInstaller.dmg"
    assert rec.file_size == 849 * 1024
    from dualswitch.ui.mac2.preview import display_size

    assert display_size(rec) == 849 * 1024


def test_image_file_still_syncs(app, cfg):
    """图片文件仍按图片跨端（内容能过去），只是本机多带 path。"""
    from dualswitch.clipboard.base import make_file
    from dualswitch.sync.engine import SyncEngine

    sent: list = []

    class FakeTransport:
        authenticated = True
        connected = True
        peer_host = "win"

        def send_clip(self, item):
            sent.append(item)

    engine = SyncEngine(cfg.clipboard, backend=None, transport=FakeTransport())
    item = make_file("/tmp/a.png", png=_tiny_png(), width=64, height=48, size=9000)
    engine._send(item)
    assert len(sent) == 1
    assert sent[0].image is not None


# --------------------------------------------------------------------------- #
# 20. 单击行 = 直接复制（用户 2026-10-21）
# --------------------------------------------------------------------------- #
def test_single_click_copies_row(app, cfg):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    class CountingEngine(FakeEngine):
        def __init__(self, records):
            super().__init__(records)
            self.copies: list[str] = []

        def copy_to_local(self, rid):
            self.copies.append(rid)
            return True

    engine = CountingEngine(make_records())
    page = ClipboardPage(engine)
    page.resize(592, 460)
    page.show()
    page.refresh()
    app.processEvents()

    row = page._rows["t1"]
    QTest.mouseClick(row, Qt.LeftButton, pos=QPoint(row.width() // 2, row.height() // 2))
    app.processEvents()
    assert engine.copies == ["t1"], "单击行应该直接复制，不能只是弹预览"
    assert not page.preview_controller.is_open(), "单击不再弹预览"

    # 双击 = 预览
    QTest.mouseDClick(row, Qt.LeftButton, pos=QPoint(row.width() // 2, row.height() // 2))
    app.processEvents()
    assert page.preview_controller.is_open(), "双击应该预览"
    page.preview_controller.hide_now()


# --------------------------------------------------------------------------- #
# 21. 文本预览：完整显示，不截断；长到超屏幕才内部滚动（用户 2026-10-21）
# --------------------------------------------------------------------------- #
def _bubble_with(page, text):
    rec = ClipRecord(id="lt", kind="text", text=text, size=len(text.encode()),
                     state="local", app="WorkBuddy")
    page.preview_controller.open_now(rec, "text", page)
    return page.preview_controller.bubble


def test_text_preview_shows_everything(app, cfg):
    """预览要展示全部文字：不许截断、不许「…」、不许压到底栏。"""
    page = ClipboardPage(FakeEngine([]))
    text = ("验证：全量 pytest 全绿；界面测试补了文件分类、标题、目录、按钮组合、"
            "文件卡、扩展名字形、引擎不发文件、单击复制。\n") * 8
    bubble = _bubble_with(page, text)
    label = bubble._text_label
    assert label.text() == text, "文本预览被截断了"
    assert "…" not in label.text()
    # 正文完整落在滚动区域内，不越界到底栏
    assert label.height() >= bubble._text_scroll.height() - 2 or \
        bubble._text_scroll.verticalScrollBar().maximum() >= 0
    assert label.y() + min(label.height(), bubble._text_scroll.height()) <= \
        bubble._body_wrap.height() - 14 + 2
    page.preview_controller.hide_now()


def test_text_preview_grows_beyond_old_cap(app, cfg):
    """内容多就长高（以前被死板限制在 270px 高）。"""
    from dualswitch.ui.mac2 import theme as _theme

    page = ClipboardPage(FakeEngine([]))
    text = "短句。" * 400                        # 约 1200 字，明显超过旧上限
    bubble = _bubble_with(page, text)
    assert bubble.height() > _theme.PV_TXT_MAX_H, "长文本要把预览窗撑高，而不是裁掉"
    assert bubble._text_label.text() == text
    page.preview_controller.hide_now()


def test_text_preview_scrolls_when_taller_than_screen(app, cfg):
    """超过屏幕高度 → 预览窗到屏幕上限，正文可滚（内容一个字都不少）。"""
    page = ClipboardPage(FakeEngine([]))
    text = "验证：全量 pytest 全绿。" * 900       # 远超一屏，但没到渲染上限
    bubble = _bubble_with(page, text)
    bar = bubble._text_scroll.verticalScrollBar()
    assert bubble.height() <= bubble._screen_cap() + 2
    assert bar.maximum() > 0, "超屏文本必须能滚动"
    assert bubble._text_label.text() == text, "滚动也不能裁字"
    page.preview_controller.hide_now()


def test_text_preview_caps_only_huge_text(app, cfg):
    """只有极端大的文本才限制渲染量，并且明确写出来。"""
    from dualswitch.ui.mac2 import theme as _theme

    page = ClipboardPage(FakeEngine([]))
    text = "验证：全量 pytest 全绿。" * 4000      # 超过 PV_TEXT_MAX_CHARS
    assert len(text) > _theme.PV_TEXT_MAX_CHARS
    bubble = _bubble_with(page, text)
    assert len(bubble._text_label.text()) == _theme.PV_TEXT_MAX_CHARS
    assert bubble._text_note is not None
    assert "只渲染了前" in bubble._text_note.text()
    page.preview_controller.hide_now()


def test_short_text_preview_not_scrolled(app, cfg):
    page = ClipboardPage(FakeEngine([]))
    bubble = _bubble_with(page, "明天下午三点，会议室 B")
    assert bubble._text_label.text() == "明天下午三点，会议室 B"
    assert bubble._text_scroll.verticalScrollBar().maximum() == 0, "短文本不该出现滚动条"
    page.preview_controller.hide_now()


# --------------------------------------------------------------------------- #
# 22. 重复复制不会多出一条（去重 + 提到最前）
# --------------------------------------------------------------------------- #
def test_repeat_copy_does_not_duplicate(app, cfg):
    from dualswitch.clipboard.base import make_text
    from dualswitch.sync.engine import SyncEngine

    class FakeTransport:
        authenticated = False
        connected = False
        peer_host = ""

    engine = SyncEngine(cfg.clipboard, backend=None, transport=FakeTransport())
    for text in ("A", "B", "A", "C", "A"):
        engine._add(make_text(text, source="mac"), source="local", state="local")
    assert len(engine.records()) == 3, "同样的内容重复复制不能堆多条"
    assert [r.text for r in engine.records()] == ["A", "C", "B"], \
        "重复复制的内容要提到最前面（按最近使用排序）"


# --------------------------------------------------------------------------- #
# 23. 文件引用 URL（file:///.file/id=…）要解析成真实路径
# --------------------------------------------------------------------------- #
def test_file_reference_url_resolves_to_real_path(app, tmp_path):
    pytest.importorskip("AppKit")
    from Foundation import NSURL

    from dualswitch.clipboard.macos import MacClipboardBackend

    target = tmp_path / "ChatGPTInstaller.dmg"
    target.write_bytes(b"x" * 64)
    ref = str(NSURL.fileURLWithPath_(str(target)).fileReferenceURL())
    assert "/.file/id=" in ref, "构造出来的应当就是访达那种引用型 URL"

    class FakePasteboard:
        def stringForType_(self, _type):
            return ref

    backend = MacClipboardBackend.__new__(MacClipboardBackend)
    backend._pb = FakePasteboard()
    got = backend._file_url_path(["public.file-url"])
    assert got == str(target), "引用型 URL 必须解析成真实路径，否则文件打不开"


def test_hero_pill_is_rounded(app, cfg):
    """「运行中 / 待确认」药丸必须是圆角。

    2026-10-22 的现场：QSS 里写的是 `border-radius: 999px`（照 CSS 的胶囊写法搬过来），
    Qt 遇到「圆角 > 控件高度的一半」会**整块退化成直角** —— 20px 高的标签上就是一个方框，
    用户一眼看到「待确认没切圆角」。所以圆角必须写成具体像素，且改高度时要跟着改。
    """
    old = app.styleSheet()
    app.setStyleSheet(theme.qss())
    try:
        page = SwitchPage(cfg, "alt+space", "Q2790R3")
        page.set_state(SOURCE_UNKNOWN)
        page.show()
        app.processEvents()
        pill = page.hero.pill
        assert pill.objectName() == "pillWarn"
        img = pill.grab().toImage()
        corner = img.pixelColor(0, 0).alpha()
        edge = img.pixelColor(img.width() - 3, img.height() // 2).alpha()
        assert edge > 0, "药丸底色没画出来"
        # 直角时角与边一样实；圆角时角明显更透
        assert corner < edge * 0.75, (
            f"药丸圆角没生效：角 alpha={corner} / 边 alpha={edge}"
            f"（{pill.width()}×{pill.height()}，圆角必须 ≤ 高度一半）"
        )
        page.close()
    finally:
        app.setStyleSheet(old)


# --------------------------------------------------------------------------- #
# 剪贴板热键：回调跨线程，绝不能碰 Qt（2026-10-23 按热键即崩）
# --------------------------------------------------------------------------- #
def test_clip_hotkey_callback_defers_to_main_thread(app, cfg):
    """CGEventTap 的回调在后台线程执行，直接动界面会让进程当场崩掉。

    守卫三件事：
      1. `_build_clipboard_hotkey` 交给热键的回调里**不能**出现 show_page；
      2. 回调只 emit 信号；
      3. 信号确实连到了在主线程干活的槽。
    """
    import ast
    import inspect
    import textwrap

    from dualswitch.ui.mac2 import runner as runner_mod

    build_src = textwrap.dedent(inspect.getsource(runner_mod.UiRunner2._build_clipboard_hotkey))
    # 用 AST 看真实调用，别被注释里的字样骗了
    calls = [
        n for n in ast.walk(ast.parse(build_src))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "show_page"
    ]
    assert not calls, "热键回调直接调了 show_page —— tap 线程操作 QWidget 会立刻崩溃"
    assert "clipboardHotkeyPressed.emit" in build_src, "热键回调必须只 emit 信号"

    slot_src = inspect.getsource(runner_mod.UiRunner2._on_clipboard_hotkey)
    assert "show_page" in slot_src, "主线程槽里才应该真正切页"

    init_src = inspect.getsource(runner_mod.UiRunner2.__init__)
    assert "clipboardHotkeyPressed.connect(self._on_clipboard_hotkey)" in init_src, \
        "信号没有连到主线程槽，热键会静默失效"


# --------------------------------------------------------------------------- #
# Dock 图标：Qt 的 setWindowIcon 传不进 Dock（2026-10-23 热键唤起显示白纸）
# --------------------------------------------------------------------------- #
def test_dock_icon_is_set_on_nsapplication(app, cfg):
    """Dock 图标必须显式塞给 NSApplication。

    macOS 的 Dock 只看 `NSApp.applicationIconImage` 或 .app 的 Info.plist，
    `QApplication.setWindowIcon()` 管不到。命令行启动时没有 bundle 图标，
    热键把窗口唤到前台就是一张白纸。
    """
    NSAppKit = pytest.importorskip("AppKit")

    from dualswitch.ui.mac2 import runner as runner_mod

    bubble = runner_mod.UiRunner2.__new__(runner_mod.UiRunner2)   # 只借方法，不建窗口
    bubble._apply_dock_icon()

    img = NSAppKit.NSApplication.sharedApplication().applicationIconImage()
    assert img is not None, "Dock 图标没设上"
    size = img.size()
    assert size.width >= 256, f"Dock 图标分辨率太低：{size.width}×{size.height}"


def test_dock_icon_call_exists_in_init(app, cfg):
    """无 pyobjc 的环境也能跑的守卫：构造函数里必须调用 _apply_dock_icon。"""
    import inspect

    from dualswitch.ui.mac2 import runner as runner_mod

    init_src = inspect.getsource(runner_mod.UiRunner2.__init__)
    assert "_apply_dock_icon()" in init_src, "构造函数没有设置 Dock 图标"
    method_src = inspect.getsource(runner_mod.UiRunner2._apply_dock_icon)
    assert "setApplicationIconImage_" in method_src, "没有真正调用 NSApplication.setApplicationIconImage_"
