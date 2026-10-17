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


def test_main_window_states(app):
    cfg = Config()
    win = MainWindow(cfg, "Alt+Space", "v1.0.0")

    assert win.hero.title.text() == "Windows"
    assert "输入源 17" in win.hero.sub.text()
    assert win.hero.btn_text.text() == "切换到 Mac"

    win.set_state("mac")
    assert win.hero.title.text() == "Mac"
    assert "输入源 18" in win.hero.sub.text()
    assert win.card_mac._active is True
    assert win.card_win._active is False

    win.set_busy(True)
    assert win.hero.button.isEnabled() is False
    assert "正在切换到" in win.hero.btn_text.text()
    win.set_busy(False)
    assert win.hero.button.isEnabled() is True

    win.show_banner("error", "SetVCPFeature 返回错误 31")
    assert win.banner.isVisibleTo(win)      # 窗口未 show 时用 isVisibleTo 判断
    assert win.height() > theme.WIN_H
    win.clear_banner()
    assert win.height() == theme.WIN_H


def test_main_window_has_settings_entry(app):
    """主页面标题栏的设置按钮应能触发 openSettingsRequested。"""
    win = MainWindow(Config(), "Alt+Space", "v1.0.0")
    fired: list[int] = []
    win.openSettingsRequested.connect(lambda: fired.append(1))
    win.titlebar.btn_settings.click()
    assert fired == [1]

    # 设置窗口自身不再显示设置齿轮
    settings = SettingsWindow(Config(), Path("config.json"), "Alt+Space", "v1.0.0")
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


def test_tray_double_click_switches_single_click_does_not(app):
    """托盘改成双击切换：单击不响应，避免误触。"""
    from PySide6.QtWidgets import QSystemTrayIcon

    from dualswitch.ui.runner import UiRunner

    calls: list[int] = []

    class Stub:
        def do_switch(self):
            calls.append(1)

    UiRunner._on_tray_activated(Stub(), QSystemTrayIcon.Trigger)
    assert calls == []                       # 单击：不切
    UiRunner._on_tray_activated(Stub(), QSystemTrayIcon.DoubleClick)
    assert calls == [1]                      # 双击：切
