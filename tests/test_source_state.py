"""ISSUE-001：画面归属的第三态（没接屏 ≠ 画面在另一台）。

背景：`detect_source()` 原来只有两个分支 —— 「能枚举到 → 在本机」，
「枚举不到 → 在另一台」。拔掉外接屏启动时，m1ddc 只列出内置屏（被过滤掉），
于是界面在启动 4 秒后硬说「当前 Windows」。这里把「从没见过这块屏」单列出来。

判定口径（三条，必须同时成立）：
  ① 从没枚举到过外接屏 → unknown，界面不猜主机；
  ② 见过、之后消失 → 在另一台（这是正常切走的信号，不能回归）；
  ③ 现在能枚举到 → 在本机。
"""

from __future__ import annotations

import time

import pytest

from dualswitch.app import SwitchController
from dualswitch.config import SOURCE_UNKNOWN, from_dict
from dualswitch.ddc.base import DDCError
from dualswitch.ddc.mock import MockBackend


def make_cfg(**overrides):
    base = {"this_host": "windows", "readback": {"enabled": False}, "debounce_ms": 0}
    base.update(overrides)
    return from_dict(base)


def new_controller(monitors=None, **cfg_kw) -> tuple[SwitchController, MockBackend]:
    backend = MockBackend(current=17, monitors=monitors)
    return SwitchController(make_cfg(**cfg_kw), backend), backend


def _wait_state(ctl: SwitchController, want: str, timeout: float = 2.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ctl.source_state == want:
            return want
        time.sleep(0.01)
    return ctl.source_state


# --------------------------------------------------------------------------- #
# ① 从没见过外接屏
# --------------------------------------------------------------------------- #
def test_never_seen_monitor_is_unknown():
    ctl, backend = new_controller(monitors=[])
    assert ctl.detect_source() == SOURCE_UNKNOWN
    assert ctl.source_state == SOURCE_UNKNOWN
    # 内部仍然保留「猜一个」给切换逻辑用，界面不再拿它当事实
    assert ctl.current_source == "windows"


def test_unknown_before_any_scan():
    """一次都没看过的时候也不能装作知道（界面首帧）。"""
    ctl, _ = new_controller(monitors=[])
    assert ctl.source_state == SOURCE_UNKNOWN


def test_startup_scan_seeds_state_without_flash():
    """启动时那次只读探测喂给 note_scan：首帧就是对的，不用先猜再纠正。"""
    ctl, _ = new_controller(monitors=[])
    ctl.note_scan([])
    assert ctl.source_state == SOURCE_UNKNOWN

    other, _ = new_controller()          # MockBackend 默认有一块屏
    other.note_scan(other.backend.list_monitors())
    assert other.source_state == "windows"


# --------------------------------------------------------------------------- #
# ② 见过、之后消失（既有行为，不能回归）
# --------------------------------------------------------------------------- #
def test_seen_then_gone_means_other_host():
    ctl, backend = new_controller()
    assert ctl.detect_source() == "windows"

    backend._monitors = []               # 切到另一台以后，本机枚举不到
    assert ctl.detect_source() == "mac"


def test_state_follows_monitor_disappearance():
    """状态由轮询推进（两轮确认），不是由一次 detect_source() 直接改写。"""
    ctl, backend = new_controller()
    ctl.detect_interval_s = 0.02
    ctl.detect_settle_s = 0.0
    ctl.start()
    try:
        assert _wait_state(ctl, "windows") == "windows"
        backend._monitors = []
        assert _wait_state(ctl, "mac") == "mac"
    finally:
        ctl.stop()


def test_detector_never_writes_unknown_into_current_source():
    """unknown 不能污染 `_current_source` —— 那里只放 windows / mac。"""
    ctl, _ = new_controller(monitors=[])
    ctl.detect_interval_s = 0.02
    ctl.detect_settle_s = 0.0
    ctl.start()
    try:
        time.sleep(0.2)
        assert ctl.source_state == SOURCE_UNKNOWN
        assert ctl.current_source == "windows"      # 没被写成 unknown
    finally:
        ctl.stop()


# --------------------------------------------------------------------------- #
# 判定不了 / 枚举失败
# --------------------------------------------------------------------------- #
def test_enumeration_error_keeps_previous_state():
    ctl, backend = new_controller()

    def boom():
        raise DDCError("mock: 枚举失败")

    backend.list_monitors = boom           # type: ignore[assignment]
    assert ctl.detect_source() is None
    assert ctl.source_state == SOURCE_UNKNOWN   # 还没成功看过，仍是未知
    assert ctl.current_source == "windows"      # 但不影响内部推测


# --------------------------------------------------------------------------- #
# 切换：没屏就别盲发
# --------------------------------------------------------------------------- #
def test_switch_refused_when_monitor_never_seen():
    ctl, backend = new_controller(monitors=[])
    ctl.note_scan([])
    result = ctl.switch_now()
    assert result.ok is False
    assert "未检测到外接显示器" in (result.error or "")
    assert backend.set_calls == []          # 一条 Set 都没发出去


def test_forced_switch_still_allowed():
    """托盘「强制切到本机」/ CLI --to 是用户的明确指令，不能被拦。"""
    ctl, backend = new_controller(monitors=[])
    ctl.note_scan([])
    result = ctl.switch_now(target="mac")
    assert result.ok is True
    assert backend.set_calls == [18]


def test_switch_without_any_scan_still_works():
    """CLI 一次性切换（`dualswitch switch`）从不跑检测，不能被这条守卫误伤。"""
    ctl, backend = new_controller()
    result = ctl.switch_now()
    assert result.ok is True
    assert backend.set_calls == [18]


def test_successful_switch_is_evidence_of_a_monitor():
    """指令发出去了：之后「看不到它」才等于被切走，而不是没接线。"""
    ctl, _ = new_controller(monitors=[])
    ctl.note_scan([])
    assert ctl.source_state == SOURCE_UNKNOWN
    ctl.switch_now(target="mac")
    assert ctl.source_state == "mac"


def test_failed_switch_is_not_evidence():
    ctl, backend = new_controller(monitors=[])
    ctl.note_scan([])
    backend.fail_set = True
    result = ctl.switch_now()
    assert result.ok is False
    assert ctl.source_state == SOURCE_UNKNOWN


# --------------------------------------------------------------------------- #
# 界面
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_window_renders_unknown_without_guessing(qt_app):
    from dualswitch.ui.window import MainWindow

    win = MainWindow(make_cfg(), "Alt+Space", "v1.0.0")
    win.set_state(SOURCE_UNKNOWN)

    assert win.hero.title.text() == "未检测到外接显示器"
    assert "另一台" in win.hero.sub.text()
    assert win.hero.button.isEnabled() is False
    assert win.card_win._active is False and win.card_mac._active is False

    # 回到已知状态：按钮要能再点（否则插上屏以后就一直灰着）
    win.set_state("windows")
    assert win.hero.title.text() == "Windows"
    assert win.hero.button.isEnabled() is True
    assert win.card_win._active is True


def test_busy_false_keeps_button_disabled_in_unknown(qt_app):
    from dualswitch.ui.window import MainWindow

    win = MainWindow(make_cfg(), "Alt+Space", "v1.0.0")
    win.set_state(SOURCE_UNKNOWN)
    win.set_busy(True)
    win.set_busy(False)                    # 切完/切失败都会走这一步
    assert win.hero.button.isEnabled() is False


def test_menu_unknown_disables_switch_entries():
    from dualswitch.ui.statusitem import build_menu_entries

    entries = build_menu_entries(make_cfg(), SOURCE_UNKNOWN, "alt+space", {})
    titles = [e.title for e in entries]
    assert any("当前画面：未检测到外部显示器" in t for t in titles)
    switch = next(e for e in entries if "切换" in e.title and e.kind == "item")
    assert switch.enabled is False
