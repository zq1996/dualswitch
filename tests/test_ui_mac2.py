"""v2 界面测试（INTERACTIONS-v2 验收项）。

覆盖：键帽渲染 / 预览窗 450ms 延时与缩略图触发 / 双态空态 / 来源角标 /
打开方式分派 / 菜单栏菜单结构。剪贴板读写一律用假引擎，不碰真实 QClipboard。
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dualswitch.config import Config, SOURCE_UNKNOWN, platform_defaults  # noqa: E402
from dualswitch.sync.engine import ClipRecord  # noqa: E402
from dualswitch.ui.mac2 import openers, statusmenu, theme  # noqa: E402
from dualswitch.ui.mac2.clipboard_page import ClipboardPage, ClipRow, Thumb  # noqa: E402
from dualswitch.ui.mac2.preview import PreviewController  # noqa: E402
from dualswitch.ui.mac2.settings_page import HotkeyKeycaps  # noqa: E402
from dualswitch.ui.mac2.switch_page import SwitchPage  # noqa: E402
from dualswitch.ui.mac2.widgets import KeyCap, KeyCaps  # noqa: E402
from dualswitch.ui.mac2.window import PAGE_CLIP, PAGE_SETTINGS, PAGE_SWITCH, MainWindow  # noqa: E402


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
# 2. 菜单栏菜单结构（INTERACTIONS §1）
# --------------------------------------------------------------------------- #
def test_menu_structure_rows(app, cfg):
    entries = statusmenu.build_menu_entries(cfg, "windows", "Right Option+Space", {})
    kinds = [e.kind for e in entries]
    titles = [e.title for e in entries if e.title]
    # 8 行内容 + 4 条分隔线（§1 表格）
    assert kinds.count("sep") == 4
    assert len(entries) == 12
    assert titles[0] == "当前画面：Windows"
    assert "打开主界面" in titles
    assert any(t.startswith("切换到 Mac（USB-C）") for t in titles)
    assert any(t.startswith("强制切到 Mac（USB-C）") for t in titles)
    assert "剪贴板" in titles
    assert "设置…" in titles
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
    assert switch_row.title == "从 Windows 切过来"
    assert switch_row.enabled
    actions_off = {"remote_ready": lambda: False}
    entries2 = statusmenu.build_menu_entries(cfg, SOURCE_UNKNOWN, "alt+space", actions_off)
    assert all("切过来" not in e.title for e in entries2)


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
    # mac 端：本机复制 = Mac 角标；remote = Windows 角标（来源系统，不是来源应用）
    assert source_system(local) == "apple"
    assert source_system(remote) == "windows"
    thumb_local = Thumb(local, "text")
    thumb_remote = Thumb(remote, "text")
    assert thumb_local._badge.cacheKey() != thumb_remote._badge.cacheKey()


def test_row_shows_source_in_meta(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    meta = page._rows["l1"].meta.text()
    assert meta.startswith("本机")
    assert "链接" in meta


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
    """行内 hover 动作按类型分派：图片→预览/访达，链接→打开链接。"""
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    img_row = page._rows["i1"]
    kinds = [b.toolTip() for b in img_row.acts.findChildren(type(img_row.pill))]
    assert "复制" in kinds
    assert "用图片预览打开" in kinds
    assert "在访达中显示" in kinds
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
    win.attach_pages(sp, cp, st)
    assert win.pages.count() == 3
    win.show_page(PAGE_CLIP)
    assert win.on_clipboard_page()
    win.show_page(PAGE_SWITCH)
    assert win.current_page() == PAGE_SWITCH
    win.show_page(PAGE_SETTINGS)
    assert win.current_page() == PAGE_SETTINGS
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
    assert data["appearance"] == "system"
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


def test_clear_menu_offers_ranges(app, cfg):
    """🗑 是「按时间清理」，不是一键全清。"""
    from pathlib import Path

    import dualswitch.ui.mac2.clipboard_page as cp

    source = Path(cp.__file__).read_text(encoding="utf-8")
    for label in ("清理 1 天前的记录", "清理 7 天前的记录", "清理 30 天前的记录", "清空全部…"):
        assert label in source
    assert "_clear_with_confirm" in source


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
    assert {b.toolTip() for b in acts} >= {"复制", "在访达中显示", "删除"}

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
    assert parts[0] == ("本机", True)
    assert parts[-1][0] == "链接"
    assert " · " in page._rows["l1"].meta.text()


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
