"""Windows2 界面测试（对应设计 `docs/UI-WINDOWS2-DESIGN.md` §11 验收清单）。

覆盖：窗口骨架（自绘标题栏只留最小化/关闭）/ 三态切换页 / 剪贴板行交互与动作分派 /
预览气泡时序与尺寸 / 设置页五组与落盘 / 托盘菜单 8 行与键帽 / 打开方式分派 /
窗口内清空遮罩 / 键盘导航 / 路径缩略（`~\\Downloads`）。

剪贴板读写一律用假引擎，不碰真实 QClipboard；不开窗、不发 DDC。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

# Windows2 界面树按平台分发（win32 → ui/win2，darwin → ui/mac2），这份用例只对 Windows 树有意义。
# 不在这里拦一道，就会逼着对端在 macOS 上跑它、然后改断言迁就平台 —— 互相改文件的循环。
if sys.platform != "win32":
    pytest.skip("Windows2 界面树是 Windows 端的（darwin 走 ui/mac2）", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from dualswitch.config import SOURCE_UNKNOWN, platform_defaults  # noqa: E402
from dualswitch.sync.engine import ClipRecord  # noqa: E402
from dualswitch.ui.win2 import openers, preview, theme, tray  # noqa: E402
from dualswitch.ui.win2.clipboard_page import ClipboardPage, ClipRow, Thumb  # noqa: E402
from dualswitch.ui.win2.pairing_page import (  # noqa: E402
    ConfirmRequest,
    PairingPage,
    PinGroup,
)
from dualswitch.ui.win2.preview import PreviewController, short_dir  # noqa: E402
from dualswitch.ui.win2.runner import ConfirmOverlay, Toast  # noqa: E402
from dualswitch.ui.win2.settings_page import (  # noqa: E402
    SettingsPage,
    windows_clip_hotkey,
)
from dualswitch.ui.win2.switch_page import SwitchPage  # noqa: E402
from dualswitch.ui.win2.window import (  # noqa: E402
    PAGE_CLIP,
    PAGE_PAIR,
    PAGE_SETTINGS,
    PAGE_SWITCH,
    CaptionButton,
    MainWindow,
)
from dualswitch.ui.win2.widgets import Chip, KeyCap, KeyCaps  # noqa: E402

#: 本机 = Windows、对端 = Mac（win2 是 Windows 端的树，写死不跟运行平台走）
LOCAL_SYS, PEER_SYS = "Windows", "Mac"
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


def _tiny_png() -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    img = QImage(64, 48, QImage.Format_ARGB32)
    img.fill(QColor("#83A8FF"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def make_records() -> list[ClipRecord]:
    return [
        ClipRecord(id="t1", kind="text", text="hello world", size=11, state="ok"),
        ClipRecord(
            id="i1", kind="image", png=_tiny_png(), width=64, height=48,
            size=9000, state="pend", progress=0.5,
        ),
        ClipRecord(id="l1", kind="text", text="https://example.com/a", size=21, state="local"),
        ClipRecord(id="c1", kind="text", text="def foo():\n    return 1", size=22, state="fail"),
    ]


def _file_rec(rid="fd", name="安装包.exe", path=None, png=None):
    path = path or os.path.join(HOME, "Downloads", name)
    return ClipRecord(
        id=rid, kind="mixed" if png else "text", text=name, png=png,
        width=640 if png else 0, height=400 if png else 0, size=20,
        file_size=849 * 1024, state="local", path=path,
        app="资源管理器", app_id=r"C:\Windows\explorer.exe",
    )


# --------------------------------------------------------------------------- #
# 1. 窗口骨架（§3.1 / §11.1）
# --------------------------------------------------------------------------- #
def test_window_is_frameless_with_two_caption_buttons(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    assert (win.width(), win.height()) == (theme.WIN_W, theme.WIN_H) == (780, 580)
    assert win.windowFlags() & Qt.FramelessWindowHint
    assert win.titlebar.height() == theme.TITLEBAR_H == 40
    # 右上角只有最小化 / 关闭（设计明确不放剪贴板、齿轮）
    captions = win.findChildren(CaptionButton)
    assert [b.kind for b in captions] == ["min", "close"]
    for btn in captions:
        assert (btn.width(), btn.height()) == (theme.CAPTION_W, theme.CAPTION_H) == (46, 32)
    win.close()


def test_window_geometry_tokens(app, cfg):
    # 2026-10-22 用户拍板「侧栏跟 mac2」：188 → 156，且可收到 56（§14.2 已拍板）
    assert theme.SIDEBAR_W == 156
    assert theme.SIDEBAR_W_COLLAPSED == 56
    assert (theme.SIDEBAR_HANDLE_W, theme.SIDEBAR_HANDLE_H) == (15, 46)
    assert theme.RADIUS_WIN == 12          # Windows 取 12（mac 是 16）
    assert theme.MENU_W == 240 and theme.MENU_ROW_H == 32
    # 底栏 30px 已删除（2026-10-22 用户：「最下边的版本号那行不要了」）
    win = MainWindow(cfg, "Alt+Space", "v0")
    assert win.findChild(QWidget, "footer") is None, "底栏不许再出现"
    assert win.findChild(QLabel, "footerText") is None
    win.set_footer("对端未连接", ok=False)      # 旧调用点不炸（空实现）
    win.close()


def test_sidebar_switches_three_pages(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    sp = SwitchPage(cfg, "Alt+Space", "AOC Q2790PC")
    cp = ClipboardPage(FakeEngine([]))
    st = SettingsPage(cfg, os.path.join(HOME, "cfg-test.json"))
    win.attach_pages(sp, cp, st)
    assert win.pages.count() == 3
    win.show_page(PAGE_CLIP)
    assert win.on_clipboard_page()
    win.show_page(PAGE_SETTINGS)
    assert win.current_page() == PAGE_SETTINGS
    win.show_page(PAGE_SWITCH)
    assert win.current_page() == PAGE_SWITCH
    win.set_nav_count(12)
    assert win.nav_clip._count == "12"      # 剪贴板行显示条数徽章
    win.close()


def test_close_semantics(app, cfg, monkeypatch):
    """托盘在 → 关窗收进托盘；托盘不在 → 最小化；开关关掉 → 真退出。"""
    win = MainWindow(cfg, "Alt+Space", "v0")
    hidden = []
    quit_requested = []
    win.hiddenToBackground.connect(lambda: hidden.append(True))
    win.quitRequested.connect(lambda: quit_requested.append(True))

    win.close()
    assert hidden == [True] and not win.isVisible()

    win.withdraw_to_tray = False
    minimized = []
    monkeypatch.setattr(win, "showMinimized", lambda: minimized.append(True))
    win.close()
    assert minimized == [True]

    win.withdraw_to_tray = True
    win.quit_on_close = True
    win.close()
    assert quit_requested == [True]


# --------------------------------------------------------------------------- #
# 2. 切换页三态（§5.2 / §11.4）
# --------------------------------------------------------------------------- #
def test_pill_is_drawn_with_rounded_corners(app, cfg):
    """19px 高的药丸自绘圆角 —— QSS 的 border-radius 在这个高度会被 Qt 整条丢掉。"""
    page = SwitchPage(cfg, "Alt+Space", "AOC Q2790PC")
    page.resize(592, 500)
    page.show()
    app.processEvents()
    img = page.hero.pill.grab().toImage()
    corner = img.pixelColor(0, 0)
    middle = img.pixelColor(img.width() // 2, img.height() // 2)
    assert corner.rgb() != middle.rgb(), "左上角还是药丸底色 → 还是方角"
    assert middle.alpha() > 0


def test_switch_page_three_states(app, cfg):
    page = SwitchPage(cfg, "Alt+Space", "AOC Q2790PC")
    page.set_state("windows")
    assert page.hero.name.text() == "Windows"
    assert page.hero.btn_text.text() == "切换到 Mac"
    assert page.hero.keys.labels() == ["Alt", "Space"]
    assert "HDMI" in page.hero.sub.text() and "输入源 17" in page.hero.sub.text()
    assert page.card_win.isEnabled() and page.card_mac.isEnabled()

    page.set_state("mac")
    assert page.hero.btn_text.text() == "切换到 Windows"
    assert "USB-C" in page.hero.sub.text() and "输入源 18" in page.hero.sub.text()

    # unknown：整体置灰、不显示键帽
    page.set_state(SOURCE_UNKNOWN)
    assert page.hero.name.text() == "未检测到外接显示器"
    assert not page.card_win.isEnabled() and not page.card_mac.isEnabled()
    assert not page.hero.keys.isVisible()
    assert not page.hero.button.isEnabled()

    # unknown + 对端在线 → 出现「从 Mac 切过来」（切回入口不能砍）
    page.set_remote_available(True)
    assert page.hero.button.isEnabled()
    assert page.hero.btn_text.text() == "从 Mac 切过来"


def test_device_card_click_toasts_when_already_here(app, cfg):
    page = SwitchPage(cfg, "Alt+Space", "AOC Q2790PC")
    page.set_state("windows")
    toasts, switches = [], []
    page.toastRequested.connect(toasts.append)
    page.deviceClicked.connect(switches.append)
    page._on_device("windows")
    assert toasts == ["画面已经在这台机器上"]
    page._on_device("mac")
    assert switches == ["mac"]


def test_force_disabled_when_unknown(app, cfg):
    page = SwitchPage(cfg, "Alt+Space", "AOC Q2790PC")
    page.set_state(SOURCE_UNKNOWN)
    toasts = []
    page.toastRequested.connect(toasts.append)
    page._on_force()
    assert toasts == ["未检测到显示器，无法强制切换"]


# --------------------------------------------------------------------------- #
# 3. 键帽 / 令牌（§3.3 / §3.4）
# --------------------------------------------------------------------------- #
def test_keycap_labels_are_windows_text():
    assert theme.keycap_labels("Alt+Space") == ["Alt", "Space"]
    assert theme.keycap_labels("Ctrl+Shift+V") == ["Ctrl", "Shift", "V"]
    assert theme.keycap_labels("Win+Q") == ["Win", "Q"]
    assert theme.keycap_labels("未启用") == []


def test_keycap_sizes(app):
    caps = KeyCaps(["Ctrl", "Shift", "V"])
    blocks = caps.findChildren(KeyCap)
    assert len(blocks) == 3
    assert caps.layout().spacing() == theme.KEY_GAP == 3
    assert all(b.height() == theme.KEY_H == 18 for b in blocks)
    # 文字键：单键最小宽 22，Space 34
    plain_caps = KeyCaps(["Alt"])
    space_caps = KeyCaps(["Space"])
    assert plain_caps.findChildren(KeyCap)[0].width() >= theme.KEY_W == 22
    assert space_caps.findChildren(KeyCap)[0].width() >= theme.KEY_WIDE_W == 34


def test_theme_is_windows_light_only():
    theme.set_appearance("dark")             # 传什么都还是浅色（§2「仅浅色」）
    t = theme.tokens()
    assert t.name == "light"
    assert t.ink3 == "#7A8494"               # 元信息提深一档
    # 2026-10-22 定稿：**西文打头、中文回退**（Qt 的 families 是逐字符回退链）。
    # 西文/数字走 Segoe UI，汉字走微软雅黑 UI；反过来的话数字会用雅黑的西文字形，
    # 看着"怪"。雅黑必须在链里，否则汉字掉到宋体（有衬线）。
    assert theme.FONT_FAMILIES[0] in ("Segoe UI Variable Text", "Segoe UI")
    assert "Segoe UI" in theme.FONT_FAMILIES
    assert theme.FONT_FAMILIES.index("Microsoft YaHei UI") < theme.FONT_FAMILIES.index("SimHei")
    assert "Segoe UI Emoji" in theme.FONT_FAMILIES
    assert theme.MONO_FAMILIES[0] == "Cascadia Mono"


def test_type_scale_is_five_steps():
    """字阶只有 10/12/14/17/27 五档，页面里写 12.5 也会被吸附过去（§3.3）。"""
    assert theme.RAMP_PX == (10, 12, 14, 17, 27)
    assert theme.snap_px(12.5) == 12 and theme.snap_px(13.5) == 14
    assert theme.snap_px(9.5) == 10 and theme.snap_px(11.5) == 12
    assert theme.snap_px(25) == 25, "配对码大数字是显示字号，不参与吸附"
    assert theme.snap_px(7.5) == 7.5, "收起态徽章的小字也不能被放大"
    # 字重只留三档（雅黑只有 Regular / Bold 两个真字形，中间值全靠合成）
    assert (theme.W_REG, theme.W_MED, theme.W_BOLD) == (400, 600, 700)
    for raw in (550, 620, 650):
        assert theme.snap_weight(raw) == 600
    assert theme.snap_weight(680) == 700


def test_qss_only_uses_type_scale():
    """整张 QSS 里不许出现字阶以外的字号/字重 —— 否则层级又会糊成一片。"""
    import re

    css = theme.qss()
    sizes = {float(v) for v in re.findall(r"font-size:\s*([\d.]+)px", css)}
    assert sizes <= {10.0, 12.0, 14.0, 17.0, 22.0, 25.0, 27.0}, f"越界字号：{sorted(sizes)}"
    weights = {int(v) for v in re.findall(r"font-weight:\s*(\d+)", css)}
    assert weights <= {400, 600, 700}, f"越界字重：{sorted(weights)}"


def test_qss_has_no_oversized_radius():
    """Qt QSS 对超过半个高度的 `border-radius` **整条不生效**（999px → 方角）。

    2026-10-21 用户截图里「运行中」药丸是方的就是这个原因；两端都踩过。
    """
    css = theme.qss()
    assert "999px" not in css
    import re

    for value in re.findall(r"border-radius://s*(//d+)px", css):
        assert int(value) <= 12, f"圆角 {value}px 超过控件半高，会被 Qt 忽略"


# --------------------------------------------------------------------------- #
# 4. 剪贴板行交互（§6.3 / §6.5 / §11.6）
# --------------------------------------------------------------------------- #
def test_rows_have_fixed_height_and_thumb_badge(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["t1"]
    assert row.height() == theme.ROW_H == 68
    assert row.thumb.width() == theme.THUMB + Thumb.PAD * 2
    assert Thumb.PAD == 3                      # 角标探出 3px（§6.3）
    assert row.meta.text().startswith(LOCAL_SYS)


def test_single_click_copies_double_click_previews(app, cfg):
    class CountingEngine(FakeEngine):
        def __init__(self, records):
            super().__init__(records)
            self.copies = []

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
    from PySide6.QtCore import QPoint

    QTest.mouseClick(row, Qt.LeftButton, pos=QPoint(row.width() // 2, row.height() // 2))
    app.processEvents()
    assert engine.copies == ["t1"], "单击行要直接复制"
    assert page.selected_id() == "t1", "单击同时设成键盘选中行"
    assert not page.preview_controller.is_open()

    QTest.mouseDClick(row, Qt.LeftButton, pos=QPoint(row.width() // 2, row.height() // 2))
    app.processEvents()
    assert page.preview_controller.is_open(), "双击要预览"
    page.preview_controller.hide_now()
    page.close()


def test_preview_delays(app, cfg):
    assert PreviewController.OPEN_DELAY_MS == 450
    assert PreviewController.CLOSE_DELAY_MS == 220

    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["t1"]
    ctrl = page.preview_controller
    ctrl.schedule_open(row.record, row.ui_kind, row.thumb)
    QTest.qWait(200)
    assert not ctrl.is_open(), "200ms 就弹出：延时不对"
    QTest.qWait(350)
    assert ctrl.is_open()
    ctrl.schedule_close()
    QTest.qWait(100)
    assert ctrl.is_open(), "220ms 关闭延时没到就关了"
    QTest.qWait(200)
    assert not ctrl.is_open()


def test_preview_size_caps_and_file_card(app, cfg):
    rec = _file_rec("fi", "湖畔的清晨.png", png=_tiny_png())
    page = ClipboardPage(FakeEngine([rec]))
    page.refresh()
    ctrl = page.preview_controller
    ctrl.open_now(rec, "file", page._rows["fi"].thumb)
    bubble = ctrl.bubble
    # 窗口 = 卡片 + 箭头留白 + 四周阴影留白；上限说的是**卡片**
    assert bubble._inner.width() <= theme.PV_CARD_MAX_W
    assert bubble.width() == bubble._inner.width() + preview.ARROW_MARGIN + preview.SHADOW * 2
    assert bubble._head_title.text() == "文件"
    texts = [w.text() for w in bubble._body_wrap.findChildren(QLabel)]
    assert "湖畔的清晨.png" in texts
    assert short_dir(rec.path) in texts
    assert "~" in short_dir(rec.path)
    # 文件卡的三个按钮：预览 / 资源管理器 / 复制
    assert bubble._btn_preview.isVisible() and bubble._btn_find.isVisible()
    assert bubble._btn_preview.toolTip() == "用默认图片查看器打开"
    assert bubble._btn_find.toolTip() == "在资源管理器中显示"
    ctrl.hide_now()


def test_link_bubble_shows_whole_url(app, cfg):
    """长 URL 不能被裁掉半截（QLabel 的折行高度要按 heightForWidth 量）。"""
    # 全量跑时别的用例可能换过全局 QSS（mac2 的字号不同），这里先还原成 win2 的
    app.setStyleSheet(theme.qss())
    url = "https://www.bilibili.com/?spm_id_from=333.1007.0.0"
    rec = ClipRecord(id="lk", kind="text", text=url, size=len(url), state="local")
    page = ClipboardPage(FakeEngine([rec]))
    page.resize(592, 460)      # 布局要真跑一遍，label.width() 才是实际排版宽度
    page.show()
    page.refresh()
    app.processEvents()
    row = page._rows["lk"]
    assert row.ui_kind == "link"

    page.preview_controller.open_now(rec, row.ui_kind, row.thumb)
    bubble = page.preview_controller.bubble
    label = bubble._body_lay.itemAt(0).widget()
    assert label.text() == url
    # 高度不够就会把第二行裁掉 —— 这正是用户截图里「链接只显示半截」的原因
    assert label.height() >= label.heightForWidth(label.width())
    page.preview_controller.hide_now()


def test_preview_bubble_has_shadow_padding(app, cfg):
    """气泡要在卡片外留出阴影区，否则白卡片压在白窗口上分不出边界。"""
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["t1"]
    page.preview_controller.open_now(row.record, row.ui_kind, row.thumb)
    bubble = page.preview_controller.bubble
    assert bubble._inner.geometry().top() == preview.SHADOW
    assert bubble.height() == bubble._inner.height() + preview.SHADOW * 2
    assert bubble.width() == bubble._inner.width() + preview.ARROW_MARGIN + preview.SHADOW * 2
    page.preview_controller.hide_now()


def test_preview_arrow_is_child(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    row = page._rows["t1"]
    page.preview_controller.open_now(row.record, row.ui_kind, row.thumb)
    bubble = page.preview_controller.bubble
    assert bubble._arrow.parent() is bubble
    assert bubble._arrow.isVisible()
    page.preview_controller.hide_now()


def test_long_text_preview_is_not_truncated(app, cfg):
    page = ClipboardPage(FakeEngine([]))
    text = "验证：全量 pytest 全绿；界面测试补了文件分类、标题、目录、按钮组合。\n" * 6
    rec = ClipRecord(id="lt", kind="text", text=text, size=len(text.encode()),
                     state="local", app="WorkBuddy")
    page.preview_controller.open_now(rec, "text", page)
    bubble = page.preview_controller.bubble
    assert bubble._text_label.text() == text
    assert "…" not in bubble._text_label.text()
    page.preview_controller.hide_now()


# --------------------------------------------------------------------------- #
# 5. 动作分派（§6.4 / §11.9）
# --------------------------------------------------------------------------- #
def test_row_actions_by_type(app, cfg):
    shots = _file_rec("fd", "安装包.exe")
    image_file = _file_rec("fi", "照片.png", png=_tiny_png())
    screenshot = ClipRecord(id="sc", kind="image", png=_tiny_png(), width=64, height=48,
                            size=9000, state="ok", app="截图")
    link = ClipRecord(id="ln", kind="text", text="https://a.b/c", size=9, state="ok")
    page = ClipboardPage(FakeEngine([shots, image_file, screenshot, link]))
    page.refresh()

    def tips(rid):
        row = page._rows[rid]
        return {b.toolTip() for b in row.acts.findChildren(type(row.pill))}

    assert tips("fd") == {"复制", "在资源管理器中显示", "删除"}
    assert tips("fi") == {"复制", "用默认图片查看器打开", "在资源管理器中显示", "删除"}
    assert tips("sc") == {"复制", "用默认图片查看器打开", "删除"}
    assert tips("ln") == {"复制", "打开链接", "删除"}


def test_classify_and_actions():
    assert openers.classify_record("mixed", "a.png", "/x/a.png") == "file"
    assert openers.classify_record("image", None) == "image"
    assert openers.classify_record("text", "https://a.b/c") == "link"
    assert openers.classify_record("text", "def f():\n    pass") == "code"
    assert openers.actions_for("image") == ("preview", "copy")
    assert openers.actions_for("file") == ("explorer", "copy")
    assert openers.actions_for("link") == ("openlink", "copy")


def test_windows_openers_dispatch(tmp_path):
    calls = []
    started = []
    opened = []
    w = openers.WindowsOpeners(
        runner=lambda cmd: calls.append(cmd),
        startfile=started.append,
        browser=opened.append,
    )
    path = w.open_image_preview(_tiny_png(), "clip-test.png")
    assert path.endswith("clip-test.png") and started == [path]

    w.open_image_path(r"C:\x\a.png")
    assert started[-1] == r"C:\x\a.png"

    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    w.reveal_in_explorer(str(target))
    assert calls[-1] == ["explorer", "/select,", str(target)], "文件定位要走资源管理器 /select,"

    w.open_url("https://example.com")
    assert opened == ["https://example.com"]


def test_reveal_falls_back_to_folder(tmp_path):
    calls = []
    w = openers.WindowsOpeners(runner=lambda cmd: calls.append(cmd))
    missing = tmp_path / "gone.txt"
    w.reveal_in_explorer(str(missing))
    assert calls[-1] == ["explorer", str(tmp_path)], "文件不在了就打开所在目录"


# --------------------------------------------------------------------------- #
# 6. 删除 / 清空（§6.8 / §8 / §11.10）
# --------------------------------------------------------------------------- #
def test_row_delete_removes_one(app, cfg):
    engine = FakeEngine(make_records())
    page = ClipboardPage(engine)
    page.refresh()
    page._rows["t1"].deleteRequested.emit("t1")
    assert engine.dropped == ["t1"]
    assert not engine.cleared
    assert len(engine.records()) == 3


def test_trash_requests_in_window_confirm(app, cfg):
    engine = FakeEngine(make_records())
    page = ClipboardPage(engine)
    page.refresh()
    asked = []
    page.clearConfirmRequested.connect(asked.append)
    page.btn_clear.click()
    assert asked == [4], "🗑 要发「窗口内确认」请求，不能直接清"
    assert not engine.cleared

    page.clear_confirmed()                    # 遮罩里点了「清空」
    assert engine.cleared


def test_clear_confirm_overlay(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    win.show()
    overlay = ConfirmOverlay(win)
    done = []
    # 2026-10-22 起遮罩是通用的：标题/说明/按钮文字都由调用方给（配对页也用它）
    overlay.ask(
        "清空本机全部 8 条记录？",
        "只清空本机列表，不影响另一台机器；此操作不可撤销。",
        "清空",
        lambda: done.append(True),
    )
    assert overlay.is_open()
    assert overlay.title.text() == "清空本机全部 8 条记录？"
    assert "不影响另一台机器" in overlay.sub.text()
    assert overlay.btn_ok.text() == "清空"
    assert overlay.btn_ok.objectName() == "dangerBtn"     # 红色「清空」
    # Esc = 取消
    overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not overlay.is_open() and done == []

    overlay.ask("解除和 zq-mac 的配对？", "解除后不再同步", "解除配对",
                lambda: done.append(True))
    assert overlay.btn_ok.text() == "解除配对" and overlay.title.text() == "解除和 zq-mac 的配对？"
    overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert not overlay.is_open() and done == [True]
    win.close()


# --------------------------------------------------------------------------- #
# 7. 键盘导航（§6.5）
# --------------------------------------------------------------------------- #
def test_keyboard_selection_and_actions(app, cfg):
    class CountingEngine(FakeEngine):
        def __init__(self, records):
            super().__init__(records)
            self.copies = []

        def copy_to_local(self, rid):
            self.copies.append(rid)
            return True

    engine = CountingEngine(make_records())
    page = ClipboardPage(engine)
    page.resize(592, 460)
    page.show()
    page.refresh()
    app.processEvents()

    def key(k):
        page.keyPressEvent(QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))

    key(Qt.Key_Down)
    assert page.selected_id() == "t1"
    key(Qt.Key_Down)
    assert page.selected_id() == "i1"
    key(Qt.Key_Up)
    assert page.selected_id() == "t1"
    key(Qt.Key_Return)
    assert engine.copies == ["t1"]
    key(Qt.Key_Delete)
    assert engine.dropped == ["t1"]
    assert page.selected_id() is None
    page.close()


def test_escape_order_preview_then_search_then_selection(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.resize(592, 460)
    page.show()
    page.refresh()
    app.processEvents()
    row = page._rows["t1"]
    page._select("t1")
    page.search_edit.setText("hello")
    page.preview_controller.open_now(row.record, row.ui_kind, row.thumb)

    assert page.handle_escape() and not page.preview_controller.is_open()
    assert page.search_edit.text() == "hello", "第二下才清搜索"
    assert page.handle_escape() and page.search_edit.text() == ""
    assert page.handle_escape() and page.selected_id() is None
    page.close()


def test_search_filters_and_empty_state(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    page.search_edit.setText("zzz-not-found")
    assert page.empty_title.text() == "当前筛选下暂无内容"
    assert page.empty_sub.text() == "换个筛选条件试试"
    page.search_edit.clear()
    assert page.empty_title.text() == "剪贴板为空" or page._visible_records()

    empty = ClipboardPage(FakeEngine([]))
    empty.refresh()
    assert empty.empty_title.text() == "剪贴板为空"
    assert "复制内容后会显示在这里" in empty.empty_sub.text()


def test_chips_height_and_counts(app, cfg):
    page = ClipboardPage(FakeEngine(make_records()))
    page.refresh()
    chip = page._chip_buttons["all"]
    assert isinstance(chip, Chip)
    assert chip.height() == 24                 # §6.2
    assert chip._count == "4"
    assert page._chip_buttons["text"]._count == "2"   # 代码归「文字」
    assert page._chip_buttons["bad"]._count == "2"    # 失败 ∪ 仅本机


# --------------------------------------------------------------------------- #
# 8. 设置页五组（§7 / §11.11）
# --------------------------------------------------------------------------- #
def test_settings_has_five_groups(app, cfg, tmp_path):
    page = SettingsPage(cfg, tmp_path / "config.json")
    titles = [lb.text() for lb in page.findChildren(QLabel) if lb.objectName() == "setSec"]
    assert titles == ["通用", "剪贴板", "热键", "显示器", "高级"]
    # 高级组三个调试入口都在设置里（托盘日常菜单不再有它们）
    assert page.probeRequested is not None
    assert page.openLogRequested is not None
    assert page.openConfigDirRequested is not None


def test_settings_writes_config(app, cfg, tmp_path):
    target = tmp_path / "config.json"
    page = SettingsPage(cfg, target)
    saved = []
    page.saved.connect(saved.append)
    page.sw_notify.setChecked(not cfg.notify)
    page.sw_swallow.setChecked(False)
    page.edit_storage.setText("350")
    page.edit_match.setText("AOC Q2790PC")
    page.edit_win_src.setText("17")
    page.edit_mac_src.setText("18")
    page._apply()

    assert saved, "改动应触发 saved"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["notify"] == (not cfg.notify)
    assert data["swallow_hotkey"] is False
    assert data["appearance"] == "light"
    assert data["clipboard"]["storage_limit_mb"] == 350
    assert data["monitor_match"] == "AOC Q2790PC"
    assert data["sources"] == {"windows": 17, "mac": 18}
    assert data["clipboard_hotkey"] == "ctrl+shift+v", "Windows 用 Ctrl+Shift+V"


def test_clip_hotkey_normalized_from_mac_default():
    assert windows_clip_hotkey("cmd+shift+v") == "ctrl+shift+v"
    assert windows_clip_hotkey("win+alt+v") == "ctrl+alt+v"
    assert windows_clip_hotkey("ctrl+shift+v") == "ctrl+shift+v"
    assert windows_clip_hotkey("") == "ctrl+shift+v"


def test_settings_strict_alt_switches_backend(app, cfg, tmp_path):
    target = tmp_path / "config.json"
    page = SettingsPage(cfg, target)
    page.sw_strict.setChecked(True)
    page._apply()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["hotkey_backend"] == "hook"
    assert "ralt" in data["hotkey"], "严格模式要把 alt 写成 ralt"


def test_settings_storage_clamped_and_clean_button(app, cfg, tmp_path):
    page = SettingsPage(cfg, tmp_path / "config.json")
    page.edit_storage.setText("99999")
    assert page._storage_limit_value() == 5000
    page.edit_storage.setText("1")
    assert page._storage_limit_value() == 10
    hits = []
    page.clearRequested.connect(lambda: hits.append(True))
    page.btn_clean.click()
    assert hits == [True]


def test_settings_sources_must_differ(app, cfg, tmp_path):
    """两端输入源相同 → 校验拦下并 toast，不落盘。"""
    target = tmp_path / "config.json"
    page = SettingsPage(cfg, target)
    toasts = []
    page.toastRequested.connect(toasts.append)
    page.edit_mac_src.setText("17")
    page._apply()
    assert toasts and toasts[0].startswith("保存失败")
    assert not target.exists()


# --------------------------------------------------------------------------- #
# 9. 托盘菜单（§4.2 / §11.3）
# --------------------------------------------------------------------------- #
def test_tray_menu_structure(app, cfg):
    entries = tray.build_menu_entries(cfg, "windows", "Alt+Space", {})
    kinds = [e.kind for e in entries]
    titles = [e.title for e in entries if e.title]
    # 2026-10-22：跟随 mac2 加第 7 行「配对电脑…」→ 9 行 + 5 条分隔线（§14.3）
    assert kinds.count("sep") == 5
    assert len(entries) == 14                     # 9 行 + 5 分隔线
    assert titles[0] == "当前画面：Windows"
    assert "打开主界面" in titles
    assert any(t.startswith("切换到 Mac（USB-C）") for t in titles)
    assert any(t.startswith("强制切到 Mac（USB-C）") for t in titles)
    assert "剪贴板" in titles
    assert "设置…" in titles
    assert any(t.startswith("配对电脑…") for t in titles)
    assert any(t.startswith("热键：Alt Space") for t in titles)
    assert "退出 屏幕切换" in titles
    # 调试入口已移进设置 → 高级
    for banned in ("探测显示器", "查看日志", "打开配置目录"):
        assert not any(banned in t for t in titles)
    # 退出用普通色（Windows 菜单没有红字惯例）
    assert not [e for e in entries if e.title.startswith("退出")][0].danger


def test_tray_pair_row_shares_status_with_sidebar(app, cfg):
    """托盘第 7 行的状态文字必须与侧栏灯同一数据源（§14.3：不许各算一遍）。"""
    actions = {"pair": lambda: None, "pair_state": lambda: "已配对"}
    entries = tray.build_menu_entries(cfg, "windows", "Alt+Space", actions)
    row = [e for e in entries if e.title.startswith("配对电脑…")][0]
    assert row.title == "配对电脑…    已配对"
    assert row.action is actions["pair"]
    # 状态读不出来时只留标题，不抛
    bad = tray.build_menu_entries(cfg, "windows", "Alt+Space",
                                  {"pair_state": lambda: (_ for _ in ()).throw(RuntimeError())})
    assert [e for e in bad if e.title.startswith("配对电脑…")][0].title == "配对电脑…"


def test_tray_menu_keycaps_are_text(app, cfg):
    entries = tray.build_menu_entries(cfg, "windows", "Alt+Space", {})
    switch_row = [e for e in entries if e.title.startswith("切换到")][0]
    assert switch_row.keys == ("Alt", "Space")
    clip_row = [e for e in entries if e.title == "剪贴板"][0]
    assert clip_row.keys == ("Ctrl", "Shift", "V")
    settings_row = [e for e in entries if e.title == "设置…"][0]
    assert settings_row.keys == ("Ctrl", ",")
    quit_row = [e for e in entries if e.title.startswith("退出")][0]
    assert quit_row.keys == ("Ctrl", "Q")


def test_tray_menu_states(app, cfg):
    for current, expect in (
        ("windows", "当前画面：Windows"),
        ("mac", "当前画面：Mac"),
        (SOURCE_UNKNOWN, "当前画面：未检测到"),
    ):
        entries = tray.build_menu_entries(cfg, current, "Alt+Space", {})
        status_row = [e for e in entries if e.kind == "status"][0]
        assert status_row.title == expect

    online = {"remote_ready": lambda: True}
    entries = tray.build_menu_entries(cfg, SOURCE_UNKNOWN, "Alt+Space", online)
    row = [e for e in entries if "切过来" in e.title][0]
    assert row.title == f"从 {PEER_SYS} 切过来" and row.enabled
    offline = {"remote_ready": lambda: False}
    entries = tray.build_menu_entries(cfg, SOURCE_UNKNOWN, "Alt+Space", offline)
    assert not [e for e in entries if "切过来" in e.title]
    force = [e for e in entries if e.title.startswith("强制切换")][0]
    assert not force.enabled, "unknown 态强制切恒灰"


def test_tray_tooltip_text():
    assert tray.tooltip_text("Alt+Space", "windows") == "屏幕切换 · Alt Space · 当前画面：Windows"
    assert "未检测到" in tray.tooltip_text("Alt+Space", SOURCE_UNKNOWN)


def test_menu_rows_render_and_disable(app, cfg):
    entries = tray.build_menu_entries(cfg, SOURCE_UNKNOWN, "Alt+Space",
                                      {"remote_ready": lambda: False,
                                       "open": lambda: None})
    menu = tray.TrayMenu()
    menu.rebuild(entries)
    rows = [w for w in menu.findChildren(tray.MenuRow)]
    assert len(rows) == 9, "9 行菜单（2026-10-22 加「配对电脑…」）"
    assert all(r.height() == theme.MENU_ROW_H == 32 for r in rows)
    assert all(r.width() == theme.MENU_W - 12 for r in rows)
    force_row = [r for r in rows if r.entry.title.startswith("强制切换")][0]
    open_row = [r for r in rows if r.entry.title == "打开主界面"][0]
    assert not force_row._clickable(), "unknown 态强制切恒灰"
    assert open_row._clickable()


def test_menu_trigger_closes_menu(app, cfg):
    called = []
    entries = [tray.MenuEntry("item", "打开主界面", lambda: called.append(True))]
    menu = tray.TrayMenu()
    menu.rebuild(entries)
    menu._trigger(entries[0])
    QTest.qWait(30)
    assert called == [True]


# --------------------------------------------------------------------------- #
# 10. 路径缩略 / 来源（§6.6 / §1.1）
# --------------------------------------------------------------------------- #
def test_short_dir_uses_windows_separator():
    folder = short_dir(os.path.join(HOME, "Downloads", "照片", "a.png"))
    assert folder == "~" + os.sep + "Downloads" + os.sep + "照片"
    assert short_dir("") == ""


def test_meta_shows_app_name_and_icon(app, cfg):
    """元信息要有「来源 · [图标]应用 · 时间 · 大小 · 类型」（设计 §6.3）。"""
    rec = ClipRecord(id="a1", kind="text", text="今晚 8 点验收 v2", size=9, state="ok",
                     app="Windows 资源管理器", app_id=r"C://Windows//explorer.exe")
    page = ClipboardPage(FakeEngine([rec]))
    page.resize(592, 300)
    page.show()
    page.refresh()
    app.processEvents()
    row = page._rows["a1"]
    assert "Windows 资源管理器" in row.meta.text()
    assert row.meta._icons, "来源应用图标没取到（QFileIconProvider 读 exe）"


def test_source_badge_follows_origin_system(app):
    from dualswitch.ui.win2.preview import source_system

    local = ClipRecord(id="a", kind="text", text="x", source="local")
    remote = ClipRecord(id="b", kind="text", text="y", source="remote", peer_name="Mac")
    assert source_system(local) == "windows"      # 本机 = Windows 蓝牌
    assert source_system(remote) == "apple"       # 对端 = Mac 灰牌
    assert Thumb(local, "text")._badge.cacheKey() != Thumb(remote, "text")._badge.cacheKey()


# --------------------------------------------------------------------------- #
# 11. Toast（§9）
# --------------------------------------------------------------------------- #
def test_toast_text_and_timeout(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    win.show()
    toast = Toast(win)
    toast.show_text("已切换到 Mac")
    assert toast.isVisible()
    assert toast._timer.interval() == 1900
    win.close()


# --------------------------------------------------------------------------- #
# 12. 文件记录：本机记录源要把 path / file_size 带过去
# --------------------------------------------------------------------------- #
def test_local_feed_keeps_file_fields(app):
    """没有引擎时的兜底记录源以前把 path/file_size 丢了 → 文件行点不开、没文件名。"""
    from dualswitch.clipboard.base import make_file
    from dualswitch.ui.clipboard_feed import LocalClipFeed

    class OneShotBackend:
        name = "fake"

        def __init__(self, item):
            self.item = item

        def poll(self):
            item, self.item = self.item, None
            return item

        def write(self, item):
            pass

        def close(self):
            pass

    path = os.path.join(HOME, "Downloads", "湖畔的清晨 🎉.png")
    item = make_file(path, png=_tiny_png(), width=6, height=4, size=1024)
    assert item is not None
    feed = LocalClipFeed(backend=OneShotBackend(item))
    rec = feed.poll()
    assert rec is not None
    assert rec.path == path
    assert rec.file_size == 1024
    assert rec.kind == "mixed", "图片文件是「文件名 + 缩略图」"
    assert rec.text == "湖畔的清晨 🎉.png"


# --------------------------------------------------------------------------- #
# 13. Emoji（字体回退 + 不切碎 ZWJ 表情）
# --------------------------------------------------------------------------- #
FAMILY = "👨\u200d👩\u200d👧\u200d👦"      # 7 个码位 / 3 个 ZWJ


def test_emoji_font_is_available(app):
    from PySide6.QtGui import QFontDatabase, QFontMetrics

    assert "Segoe UI Emoji" in theme.FONT_FAMILIES
    if not QFontDatabase.families():
        # offscreen 平台的字体库是空的（families()==0），任何字符都查不到字形；
        # 真实字形由 tools/shots_win2.py 在 windows 平台上出图核对
        pytest.skip("offscreen 平台没有字体库，字形检查交给出图脚本")
    fm = QFontMetrics(theme.qfont(theme.FS_ROW))
    for cp in (0x1F389, 0x2705, 0x1F44D, 0x1F468):
        assert fm.inFontUcs4(cp), f"字体里没有 U+{cp:X} 的字形（会显示成豆腐块）"


def test_safe_prefix_keeps_emoji_cluster(app):
    from dualswitch.ui.win2.widgets import safe_prefix

    assert safe_prefix(FAMILY + "abc", 1) == FAMILY, "不能在 ZWJ 处截断"
    assert safe_prefix("🇨🇳x", 1) == "🇨🇳", "国旗是两个区域指示符，不能拆"
    assert safe_prefix("👍🏽x", 1) == "👍🏽", "肤色修饰符要跟着前一个字"
    assert safe_prefix("hello", 3) == "hel"
    assert safe_prefix("", 5) == ""


def test_emoji_title_is_not_split(app, cfg):
    text = "🎉" * 200 + FAMILY
    rec = ClipRecord(id="e", kind="text", text=text, size=1, state="ok")
    title = ClipRow._title(rec)
    assert title.endswith("…")
    assert not title.endswith("\u200d…"), "截断后挂着一个孤立 ZWJ，表情会碎"


def test_emoji_record_keeps_text_in_list_and_preview(app, cfg):
    emoji = f"🎉 上线 ✅ 🇨🇳 👍🏽 {FAMILY}"
    rec = ClipRecord(id="e1", kind="text", text=emoji, size=len(emoji), state="ok")
    page = ClipboardPage(FakeEngine([rec]))
    page.refresh()
    row = page._rows["e1"]
    assert row.title.full_text() == emoji, "标题要原样保留（折行/省略不能改内容）"

    page.preview_controller.open_now(rec, row.ui_kind, row.thumb)
    bubble = page.preview_controller.bubble
    assert bubble._text_label.text() == emoji
    page.preview_controller.hide_now()


def test_emoji_in_file_name(app, cfg):
    rec = _file_rec("fe", "截图 🎉 2026.png", png=_tiny_png())
    page = ClipboardPage(FakeEngine([rec]))
    page.refresh()
    assert page._rows["fe"].ui_kind == "file"
    assert page._rows["fe"].title.full_text() == "截图 🎉 2026.png"


# --------------------------------------------------------------------------- #
# 8. 配对电脑页（§14.1）+ 侧栏收起 / 退出（§0.4 / §13）
#
# 页面只做「显示 + 收集输入」，所以这里用假引擎给出可控的 pairing_status()，
# 不启发现、不开端口、不落盘 —— 真实协议路径在 tests/test_pairing.py（共享用例）。
# --------------------------------------------------------------------------- #
class FakePairEngine:
    """假配对引擎：状态字典由测试直接改，调用记录在 `calls` 里。"""

    def __init__(self, status=None, peers=None, begin_pin="483921"):
        self.status = dict(FakePairEngine.DEFAULT, **(status or {}))
        self.peers = list(peers or [])
        self.begin_pin = begin_pin
        self.calls = []

    DEFAULT = {
        "state": "idle", "outcome": "", "error": "", "error_kind": "",
        "pin": "", "expires_in": 0.0, "attempts_left": 5,
        "paired": False, "legacy": False, "mismatch": False,
        "connected": False, "peer_device": "", "peer_name": "",
        "paired_at": 0.0, "addr": "", "last_sync_at": 0.0,
    }

    def pairing_status(self):
        return dict(self.status)

    def discovered_peers(self):
        return list(self.peers)

    def begin_pairing(self):
        self.calls.append("begin")
        self.status.update(state="showing", pin=self.begin_pin, expires_in=300.0, outcome="")
        return self.begin_pin

    def cancel_pairing(self):
        self.calls.append("cancel")
        self.status.update(state="idle", pin="", expires_in=0.0)

    def pair_with_peer(self, addr, pin):  # noqa: ANN001, ARG002
        self.calls.append(("pair", addr, pin))
        return {"ok": True, "peer_name": "zq-mac-mini", "peer_device": "macos-1",
                "addr": "192.168.1.42:47823"}

    def unpair(self):
        self.calls.append("unpair")
        self.status.update(paired=False, peer_device="", peer_name="", addr="", paired_at=0.0)

    def refresh_discovery(self):
        self.calls.append("refresh")


def _peers():
    return [
        {"device": "windows-3f2a91c4", "host": "DESKTOP-8Q2K", "ip": "192.168.1.31",
         "port": 47823, "plat": "windows", "paired": False, "pairing": True},
        {"device": "macos-77aa", "host": "zq-mac-mini", "ip": "192.168.1.42",
         "port": 47823, "plat": "macos", "paired": True, "pairing": False},
    ]


def test_pairing_page_is_fourth_page(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    seen = []
    win.pageChanged.connect(seen.append)
    sp = SwitchPage(cfg, "Alt+Space", "AOC Q2790PC")
    cp = ClipboardPage(FakeEngine([]))
    st = SettingsPage(cfg, os.path.join(HOME, "cfg-test.json"))
    pair = PairingPage(FakePairEngine())
    win.attach_pages(sp, cp, st, pair)
    assert win.pages.count() == 4
    win.show_page(PAGE_PAIR)
    assert win.current_page() == PAGE_PAIR
    assert win.pages.currentWidget() is pair
    assert win.nav_pair.isChecked() and not win.nav_settings.isChecked()
    assert seen == [PAGE_PAIR], "runner 靠 pageChanged 做 activate/deactivate"
    win.close()


def test_pairing_page_discover_lists_peers(app):
    page = PairingPage(FakePairEngine(peers=_peers()))
    page.activate()
    assert page.state() == "discover"
    assert page.top_pill.text() == "未配对"
    assert page.scan_hint.text() == "找到 2 台"
    assert set(page._peer_rows) == {"windows-3f2a91c4", "macos-77aa"}
    # 已与别的电脑配对 → 「替换配对」；正常 → 「配对」
    assert page._peer_rows["macos-77aa"]["btn"].text() == "替换配对"
    assert page._peer_rows["windows-3f2a91c4"]["btn"].text() == "配对"


def test_pairing_page_show_code_then_deactivate_cancels(app):
    engine = FakePairEngine(peers=_peers())
    page = PairingPage(engine)
    page.activate()
    page._start_show_code()
    assert page.state() == "showcode"
    assert [b.text() for b in page._pin_blocks] == list("483921")
    assert "5:00 后失效" in page.pin_count.text()
    assert page.top_pill.text() == "等待配对"
    # 离开页面 = 取消（码立即失效，不落盘）
    page.deactivate()
    assert "cancel" in engine.calls


def test_pairing_page_pin_input_and_wrong_code(app):
    engine = FakePairEngine(peers=_peers())
    page = PairingPage(engine)
    page.activate()
    page._select_peer(_peers()[0])
    assert page.state() == "entercode"
    assert not page.btn_pair.isEnabled(), "6 位没填满时「配对」置灰"
    page.pin_group.fill(0, "123456")
    assert page.btn_pair.isEnabled()
    page._show_code_error(4, "配对码不正确")
    assert page.pin_error.isVisible() or not page.pin_error.isHidden()
    assert "还可以试 4 次" in page.pin_error_text.text()
    page._show_invalid_code()
    assert not page.pin_group.is_editable(), "5 次用完：输入禁用、只留返回列表"


def test_pairing_page_unpair_uses_window_mask(app):
    """解除配对必须走窗口内遮罩（§14.1），不能自己弹 QDialog / 页内小卡片。"""
    engine = FakePairEngine({
        "paired": True, "state": "paired", "peer_device": "windows-3f2a91c4",
        "peer_name": "DESKTOP-8Q2K", "addr": "192.168.1.31:47823", "connected": True,
    })
    page = PairingPage(engine)
    requests = []
    page.confirmRequested.connect(requests.append)
    page.activate()
    assert page.state() == "paired"
    page._ask_unpair()
    assert len(requests) == 1
    req = requests[0]
    assert "解除和 DESKTOP-8Q2K 的配对" in req.title
    assert req.confirm_text == "解除配对"
    assert engine.calls == [], "点按钮只发确认请求，不能先动引擎"
    req.on_confirm()
    assert "unpair" in engine.calls


def test_pairing_page_replace_confirm(app):
    engine = FakePairEngine(peers=_peers(), status={"paired": True,
                                                    "peer_device": "macos-other",
                                                    "peer_name": "旧电脑"})
    page = PairingPage(engine)
    requests = []
    page.confirmRequested.connect(requests.append)
    page.activate()
    page._pick_peer(_peers()[0])
    assert requests and "替换现有的 旧电脑" in requests[0].title
    assert page.state() == "paired", "确认之前停在已配对页，不进输入码页"
    assert page._selected == {}


def test_pairing_page_no_dialog_in_source():
    """守「只有一个窗口」：配对页不许**实例化** QDialog（注释里提到它不算）。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "dualswitch" / "ui" / "win2"
    text = (src / "pairing_page.py").read_text(encoding="utf-8")
    assert "QDialog(" not in text
    assert "import QDialog" not in text


def test_sidebar_collapse_and_restore(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    sp = SwitchPage(cfg, "Alt+Space", "AOC Q2790PC")
    cp = ClipboardPage(FakeEngine([]))
    st = SettingsPage(cfg, os.path.join(HOME, "cfg-test.json"))
    win.attach_pages(sp, cp, st, PairingPage(FakePairEngine()))
    win.set_nav_count(12)
    assert win.sidebar.width() == theme.SIDEBAR_W == 156
    win.set_sidebar_collapsed(True)
    assert win.sidebar.width() == theme.SIDEBAR_W_COLLAPSED == 56
    assert win.is_sidebar_collapsed()
    assert not win.sec_core.isVisible() and not win.sec_system.isVisible()
    assert win.nav_pair.is_collapsed() and win.nav_pair.toolTip() == "配对电脑"
    assert win.btn_quit.isVisible() or not win.btn_quit.isHidden()
    assert win.side_handle.toolTip() == "展开侧栏"
    win.set_sidebar_collapsed(False)
    assert win.sidebar.width() == 156 and win.side_handle.toolTip() == "收起侧栏"
    win.close()


def test_quit_button_emits_and_hotkey_hint(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    fired = []
    win.quitRequested.connect(lambda: fired.append(True))
    win.btn_quit.click()
    assert fired == [True]
    assert win.btn_quit.toolTip() == "退出应用（Ctrl+Q）"
    # 热键未启用时侧栏给警示文案（不是显示一个空键帽组）
    win.set_side_hotkey("未启用")
    assert win.side_hotkey_text.text() == "热键未启用"
    win.close()


def test_nav_pair_status_dot(app, cfg):
    win = MainWindow(cfg, "Alt+Space", "v0")
    win.nav_pair.set_status_dot("ok", "已配对")
    assert win.nav_pair.status_dot_tooltip() == "已配对"
    assert win.nav_pair.count_tone() == "ok"
    win.nav_pair.set_status_dot("bad", "需重新配对")
    assert win.nav_pair.status_dot_tooltip() == "需重新配对"
    assert win.nav_pair.count_tone() == "bad"
    win.nav_pair.set_collapsed(True)
    assert win.nav_pair._status_dot.width() == 7, "收起态状态灯缩到 7px"
    win.close()


# --------------------------------------------------------------------------- #
# 9. 排版与视觉噪音（2026-10-22 用户反馈：字体怪 / 虚线框 / 底栏 / 遮挡）
# --------------------------------------------------------------------------- #
def test_focus_rect_style_installed(app):
    """点过的按钮不许留灰色虚线框（用户截图点了「取消」「清理」两处）。

    注意：**不能**用 `isinstance(app.style(), NoFocusRectStyle)` 断言 —— 一旦设了
    QSS，Qt 会把应用样式包进内部的 QStyleSheetStyle，`app.style()` 只能报出壳子的
    基类（QCommonStyle），壳子其实还在链上。所以这里直接验证「PE_FrameFocusRect
    画不出东西」，并用插桩确认它真的会被调用到（2026-10-22 实测：装壳子后
    ghostBtn 的 PE_FrameFocusRect 确实进了壳子并被吞掉）。
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleOption

    from dualswitch.ui.win2 import focus_style

    proxy = focus_style.NoFocusRectStyle(app.style())
    img = QImage(48, 24, QImage.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    painter = QPainter(img)
    option = QStyleOption()
    option.rect = QRect(0, 0, 48, 24)
    proxy.drawPrimitive(QStyle.PE_FrameFocusRect, option, painter)
    painter.end()
    assert img.pixelColor(1, 1) == QColor("#FFFFFF"), "焦点虚线框被画出来了"

    # install 幂等：重复装不会套娃，壳子挂在 app 上（防 use-after-free）
    focus_style.install(app)
    first = getattr(app, "_dualswitch_focus_style")
    focus_style.install(app)
    assert getattr(app, "_dualswitch_focus_style") is first
    assert isinstance(first, focus_style.NoFocusRectStyle)


def test_settings_storage_row_children_do_not_overlap(app, cfg):
    """「已用 0.0 MB [200] MB [清理]」四段不许互相压住（用户截图里 MB 被盖掉了）。"""
    from itertools import combinations

    from dualswitch.ui.win2.widgets import ElidedLabel

    page = SettingsPage(cfg, os.path.join(HOME, "cfg-test.json"))
    page.resize(624, 620)
    page.show()
    QTest.qWait(20)
    box = page.btn_clean.parentWidget()
    kids = [
        w for w in box.children()
        if isinstance(w, QWidget) and w.isVisible() and w.width() > 0
    ]
    for a, b in combinations(kids, 2):
        assert not a.geometry().intersects(b.geometry()), (
            f"{a.objectName() or type(a).__name__} 与 {b.objectName() or type(b).__name__} 重叠"
        )
    # 行题 / 副行改成 ElidedLabel：字号变大时先省略文字，而不是让控件压上来
    row = page.btn_clean.parentWidget().parentWidget()
    assert row.findChildren(ElidedLabel), "设置行的文字必须可省略（否则会顶出行宽）"
    page.close()


