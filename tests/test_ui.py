"""界面冒烟测试：不需要显示器，用 Qt 的 offscreen 平台跑。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

from PySide6.QtWidgets import QApplication  # noqa: E402

from dualswitch.config import Config, load  # noqa: E402
from dualswitch.ui import theme  # noqa: E402
from dualswitch.ui.settings import SettingsWindow  # noqa: E402
from dualswitch.ui.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_asset_resolves():
    assert theme.asset("screen-switch.ico").exists()
    assert theme.asset("windows-logo.png").exists()
    assert theme.asset("apple-logo-dim.png").exists()


def test_pretty_hotkey_multiword_modifier():
    """`Right Option` / `Right Alt` 是带空格的单个片段，不能粘成 `RightOption`。

    早期实现先 `replace(" ", "")` 再切分，导致 mac 上键帽显示成 `RightOption Space`。
    """
    from dualswitch.hotkey.base import Hotkey
    from dualswitch.ui.chrome import current

    hk = Hotkey(modifiers=("ralt",), key="space")
    for text in (hk.describe(), hk.mac_describe()):
        pretty = theme.pretty_hotkey(text)
        assert "RightOption" not in pretty
        assert "RightAlt" not in pretty

    if current().PALETTE.key_glyphs:
        assert theme.pretty_hotkey("Right Option+Space") == "⌥ Space"
    else:
        assert theme.pretty_hotkey("Right Alt+Space") == "Right Alt + Space"


def test_main_window_states(app):
    """状态卡随当前主机翻转；两端共用同一套断言（主机身份按平台算）。"""
    cfg = Config()
    win = MainWindow(cfg, "Alt+Space", "v1.0.0")

    here = cfg.effective_host()
    other = cfg.other_host()
    here_title = "Windows" if here == "windows" else "Mac"
    other_title = "Windows" if other == "windows" else "Mac"

    assert win.hero.title.text() == here_title
    assert f"输入源 {cfg.sources[here]}" in win.hero.sub.text()
    assert win.hero.btn_text.text() == f"切换到 {other_title}"

    win.set_state(other)
    assert win.hero.title.text() == other_title
    assert f"输入源 {cfg.sources[other]}" in win.hero.sub.text()
    assert win.card_mac._active is (other == "mac")
    assert win.card_win._active is (other == "windows")

    win.set_busy(True)
    assert win.hero.button.isEnabled() is False
    assert "正在切换到" in win.hero.btn_text.text()
    win.set_busy(False)
    assert win.hero.button.isEnabled() is True

    win.show_banner("error", "SetVCPFeature 返回错误 31")
    assert win.banner.isVisibleTo(win)      # 窗口未 show 时用 isVisibleTo 判断
    assert win.height() > win.win_h()
    win.clear_banner()
    assert win.height() == win.win_h()   # mac 比基准高 20（多一行剪贴板）


def test_main_window_has_settings_entry(app):
    """两端的设置入口都要能触发 openSettingsRequested。

    Windows：自绘标题栏的齿轮；macOS：内容区末尾的「设置…」行（标题栏是系统的）。
    """
    from dualswitch.ui.chrome import current as chrome

    C = chrome()
    win = MainWindow(Config(), "Alt+Space", "v1.0.0")
    fired: list[int] = []
    win.openSettingsRequested.connect(lambda: fired.append(1))

    if C.INLINE_SETTINGS_ROW:
        assert win.row_settings is not None, "mac 主窗口应有「设置…」行"
        win.row_settings.click()
    else:
        assert win.titlebar is not None, "Windows 主窗口应有自绘标题栏"
        win.titlebar.btn_settings.click()
    assert fired == [1]

    # 设置窗口自己不再提供设置入口（避免点进去还是设置）
    settings = SettingsWindow(Config(), Path("config.json"), "Alt+Space", "v1.0.0")
    if C.NATIVE_CHROME:
        assert settings.titlebar is None
    else:
        assert not settings.titlebar.btn_settings.isVisibleTo(settings)


def test_display_page_probe_button(app, tmp_path: Path):
    """显示器页的「检测」按钮应把 probeRequested 抛给 runner。"""
    from PySide6.QtWidgets import QPushButton

    win = SettingsWindow(Config(), tmp_path / "config.json", "Alt+Space", "v1.0.0")
    fired: list[int] = []
    win.probeRequested.connect(lambda: fired.append(1))
    probe = [b for b in win.findChildren(QPushButton) if b.property("role") == "probe"]
    assert probe, "显示器页应有检测按钮"
    probe[0].click()
    assert fired == [1]


def test_settings_save_roundtrip(app, tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    cfg = Config()
    win = SettingsWindow(cfg, cfg_file, "Alt+Space", "v1.0.0")

    win.slider.setValue(1200)
    win.edit_win.setText("17")
    win.edit_mac.setText("18")
    win.edit_match.setText("AOC")
    win._save()

    assert cfg_file.exists()
    saved = load(cfg_file)
    assert saved.debounce_ms == 1200
    assert saved.sources == {"windows": 17, "mac": 18}


def test_settings_rejects_same_source(app, tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    win = SettingsWindow(Config(), cfg_file, "Alt+Space", "v1.0.0")
    win.edit_win.setText("18")
    win.edit_mac.setText("18")
    win._save()
    assert win.error.isVisibleTo(win)
    assert "不能相同" in win.error.text()
    assert not cfg_file.exists()


def test_hotkey_recorder_pauses_global_hotkey(app, tmp_path: Path):
    """录制期间必须发 recordingChanged(True)，否则全局热键会把按键吞掉。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = SettingsWindow(Config(), tmp_path / "config.json", "Alt+Space", "v1.0.0")
    events: list[bool] = []
    win.recordingChanged.connect(events.append)

    QTest.mouseClick(win.recorder, Qt.LeftButton)      # 进入录制
    assert events == [True]
    assert win.recorder._recording is True

    QTest.keyClick(win.recorder, Qt.Key_Space, Qt.AltModifier)   # 录回旧热键
    assert events[-1] is False                          # 退出录制 → 恢复热键
    assert win.recorder.spec() == "alt+space"

    # 切页签 / 保存 / 隐藏窗口都要能取消录制
    QTest.mouseClick(win.recorder, Qt.LeftButton)
    assert events[-1] is True
    win._select(0)
    assert events[-1] is False


def test_hotkey_recorder_hints_missing_modifier(app, tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = SettingsWindow(Config(), tmp_path / "config.json", "Alt+Space", "v1.0.0")
    QTest.mouseClick(win.recorder, Qt.LeftButton)
    QTest.keyClick(win.recorder, Qt.Key_A)              # 没有修饰键
    assert win.recorder._recording is True              # 仍在录制
    assert "修饰键" in win.recorder.text()


def test_save_keeps_live_config_untouched(app, tmp_path: Path):
    """保存时不能就地改运行中的 Config。

    曾经就因为这个，runner 里 old_hotkey = self.cfg.hotkey 读到的已经是新值，
    「热键变了吗」永远为 False，改完热键根本不重建。
    """
    cfg_file = tmp_path / "config.json"
    cfg = Config()
    win = SettingsWindow(cfg, cfg_file, "Alt+Space", "v1.0.0")
    emitted: list = []
    win.saved.connect(emitted.append)

    win.recorder.set_spec("ctrl+alt+f12")
    win._save()

    assert emitted and emitted[0].hotkey == "ctrl+alt+f12"   # 新配置已发出
    assert cfg.hotkey == "alt+space"                         # 运行中的对象保持原样
    assert cfg.sources == {"windows": 17, "mac": 18}

    # 校验失败也不能把运行状态带脏
    win.edit_win.setText("18")
    win.edit_mac.setText("18")
    win._save()
    assert cfg.sources == {"windows": 17, "mac": 18}


def test_closing_window_goes_to_background(app):
    """关窗口 = 收进菜单栏，而不是退出；并且要发信号（mac 靠它收掉程序坞图标）。

    真正退出走 `quit_completely()`，那条路径不该再发「转入后台」。
    """
    cfg = Config()
    win = MainWindow(cfg, "Alt+Space", "v1.0.0")
    seen: list[int] = []
    win.hiddenToBackground.connect(lambda: seen.append(1))

    win.show()
    win.close()
    assert seen == [1], "关窗口必须通知外层（否则程序坞图标留在那儿）"
    assert win.isHidden()

    win.quit_completely()
    assert seen == [1], "真正退出时不该再当一次「转入后台」"


def test_dock_follows_window_only_on_mac():
    from dualswitch.ui.chrome import current

    chrome = current()
    # mac: 窗口收起后从程序坞消失；Windows 没有这个概念
    assert chrome.DOCK_FOLLOWS_WINDOW is (chrome.NAME == "macos")
    assert callable(chrome.set_dock_visible)


def test_tray_single_click_does_not_switch(app):
    """Windows 托盘：单击不响应，双击才切（防误触）。"""
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QSystemTrayIcon

    from dualswitch.ui.chrome.win import QtStatusItem

    calls: list[int] = []
    item = QtStatusItem(QIcon(), lambda: calls.append(1))
    try:
        item._activated(QSystemTrayIcon.Trigger)
        assert calls == []                   # 单击：不切
        item._activated(QSystemTrayIcon.DoubleClick)
        assert calls == [1]                  # 双击：切
    finally:
        item.hide()


def test_menu_entries_track_state(app):
    """菜单栏/托盘菜单的「当前画面」「切换目标」要跟着状态翻。"""
    from dualswitch.ui.statusitem import build_menu_entries

    entries = build_menu_entries(Config(), "windows", "alt+space", {})
    titles = [e.title for e in entries]
    assert any("当前画面：Windows" in x for x in titles)
    assert any("切换到 Mac" in x for x in titles)

    flipped = [e.title for e in build_menu_entries(Config(), "mac", "alt+space", {})]
    assert any("当前画面：Mac" in x for x in flipped)
    assert any("切换到 Windows" in x for x in flipped)


def test_close_without_tray_minimizes_instead_of_hiding(app):
    """托盘不可用时不能再把窗口藏起来 —— 否则用户既没窗口也没图标，只剩任务管理器。"""
    win = MainWindow(Config(), "Alt+Space", "v1.0.0")
    seen: list[int] = []
    win.hiddenToBackground.connect(lambda: seen.append(1))

    win.withdraw_to_tray = False
    win.show()
    win.close()

    assert seen == [], "没有托盘时不该发「转入后台」"
    assert not win.isHidden(), "没有托盘时必须留在任务栏"
    assert win.isMinimized()
    win.quit_completely()


def test_app_icon_survives_broken_image_plugin(app, tmp_path: Path, monkeypatch):
    """`.ico` 读不出来时要退回代码画的图标，而不是留个空图标（托盘会一片空白）。"""
    from dualswitch.ui import runner as runner_mod

    assert not runner_mod.load_app_icon().isNull()      # 正常路径：包内资源可用

    broken = tmp_path / "screen-switch.ico"
    broken.write_bytes(b"not an icon at all")
    monkeypatch.setattr(theme, "app_icon", lambda: broken)

    icon = runner_mod.load_app_icon()
    assert not icon.isNull(), "图标读不出来时必须兜底，不能让托盘变成空图标"


def test_qt_status_item_reports_availability(app):
    """托盘对象要能自报「可不可用 / 可见 / 位置」，日志与降级都靠它。"""
    from PySide6.QtGui import QIcon

    from dualswitch.ui.chrome.win import QtStatusItem

    item = QtStatusItem(QIcon(), None)
    try:
        assert isinstance(item.is_available(), bool)
        assert isinstance(item.is_visible(), bool)
        assert isinstance(item.geometry(), str)
        item._log_state("测试")          # 空图标要能记日志而不是抛异常
    finally:
        item.hide()


def test_pystray_menu_from_entries(app):
    """Qt 托盘失灵时的兜底实现要把共享菜单条目原样翻过去（双击 = 切换）。"""
    pytest.importorskip("pystray", reason="未安装 pystray")
    from dualswitch.ui.chrome.win import pystray_menu
    from dualswitch.ui.statusitem import build_menu_entries

    calls: list[str] = []
    entries = build_menu_entries(
        Config(),
        "windows",
        "alt+space",
        {"switch": lambda: calls.append("switch"), "quit": lambda: calls.append("quit")},
    )
    menu = pystray_menu(entries)

    items = [i for i in menu.items if i is not menu.SEPARATOR]
    assert any(i.text.startswith("切换到") and i.default for i in items), "双击应触发切换"
    assert any(i.text.startswith("退出") for i in items)

    # 只读行（当前画面 / 热键提示）要禁用，且不能触发任何动作
    label = next(i for i in items if i.text.startswith("当前画面"))
    assert label.enabled is False

    switch = next(i for i in items if i.default)
    switch(None)
    assert calls == ["switch"]
