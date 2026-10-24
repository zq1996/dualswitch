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

import sys
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
# 冷启动屏在对端（2026-10-23）：光靠「本次运行见过」判不出来
#
# 原来的口径只有一条证据 `_ever_seen_monitor`，而它在冷启动时天然拿不到：
# 应用启动的瞬间屏若在对端，本机从没枚举到过它，于是整个会话停在 unknown。
# 现在补第二条证据「对端在线且已配对」—— 两台机器只共用一块屏。
# --------------------------------------------------------------------------- #
def test_cold_start_screen_on_peer_with_remote_online():
    ctl, _ = new_controller(monitors=[])
    ctl.remote_ready = lambda: True
    ctl.note_scan([])
    assert ctl.source_state == "mac"
    assert ctl.detect_source() == "mac"


def test_cold_start_without_peer_stays_unknown():
    """对端离线时没有第二证据，仍按 ISSUE-001 的保守口径报 unknown（不能回归）。"""
    ctl, _ = new_controller(monitors=[])
    ctl.remote_ready = lambda: False
    ctl.note_scan([])
    assert ctl.source_state == SOURCE_UNKNOWN


def test_remote_probe_failure_is_treated_as_offline():
    """状态查询抛异常不能把判定搞崩，按「离线」处理。"""
    ctl, _ = new_controller(monitors=[])

    def boom():
        raise RuntimeError("transport 炸了")

    ctl.remote_ready = boom
    assert ctl.detect_source() == SOURCE_UNKNOWN


def test_remote_evidence_does_not_override_present_monitor():
    """能枚举到屏时永远判本机 —— 对端在线只是「看不到时」的补充证据。"""
    ctl, _ = new_controller()
    ctl.remote_ready = lambda: True
    assert ctl.detect_source() == "windows"


def test_both_sides_no_monitor_returns_unknown():
    """本地打不到 + 对端在线却没接住 ⇒ 屏不在任何一台，退回 unknown。"""
    ctl, backend = new_controller(monitors=[])
    ctl.remote_ready = lambda: True
    asked: list[int] = []

    def remote_fail(value):
        asked.append(value)
        return False, "对端也没找到显示器"

    def local_fail(value):
        # 真实后端（WindowsBackend._acquire_one）在枚举不到屏时抛这句；
        # MockBackend 不检查 monitors，这里显式补上，否则会走「本地成功」分支
        raise DDCError("未发现物理显示器（可能显示器已切到另一台主机）")

    ctl.remote_switch = remote_fail
    backend.set_input = local_fail
    result = ctl.switch_now()              # 本地 DDCError → 远端也失败
    assert result.ok is False
    assert asked == [18]                   # 确实问过对端，不是直接放弃
    assert ctl.source_state == SOURCE_UNKNOWN


def test_absent_flag_clears_when_monitor_returns():
    """屏插回来要立刻恢复，不能一直卡在 unknown。"""
    ctl, backend = new_controller(monitors=[])

    def local_fail(value):
        raise DDCError("未发现物理显示器")

    backend.set_input = local_fail
    ctl.remote_ready = lambda: True
    ctl.remote_switch = lambda value: (False, "对端也没找到")
    ctl.switch_now()
    assert ctl.source_state == SOURCE_UNKNOWN

    backend._monitors = MockBackend().list_monitors()   # 模拟屏又出现了
    assert ctl.detect_source() == "windows"             # 判定已经恢复
    ctl.note_scan(backend._monitors)                    # 真实路径是轮询，这里直接喂
    assert ctl.source_state == "windows"


def test_settle_window_covers_monitor_switch_time():
    """静默期必须大于显示器完成切换的时间。

    Q2790PC 实测一次切换要 2–3 秒。静默期太短会在「切换进行中」去枚举，
    把「还在切」误判成「没切成功」—— 曾经设成 3 秒就踩了这个坑。
    """
    ctl, _ = new_controller()
    assert ctl.detect_settle_s >= 5.0, (
        f"detect_settle_s={ctl.detect_settle_s} 小于显示器切换耗时（2–3 秒）+ 余量"
    )


# --------------------------------------------------------------------------- #
# 联合判定（T_STATE 0x32，2026-10-23）：问对端「你看不看得到这块屏」
#
# 单机只能看到自己这一侧，于是「冷启动屏在对端」和「拔线」长得一模一样。
# 两台各报一句就能唯一定下来：只有一台看得到 ⇒ 屏在它那边；两台都看不到 ⇒ 没接线。
# --------------------------------------------------------------------------- #
def test_peer_sees_monitor_means_other_host():
    ctl, _ = new_controller(monitors=[])
    ctl.peer_sees_monitor = lambda: True
    assert ctl.detect_source() == "mac"


def test_both_sides_cannot_see_monitor_is_unknown():
    """两台都看不到 ⇒ 屏不在任何一台（拔线 / 关电），不能再说「在对面」。"""
    ctl, _ = new_controller(monitors=[])
    ctl.remote_ready = lambda: True
    ctl.peer_sees_monitor = lambda: False
    assert ctl.detect_source() == SOURCE_UNKNOWN


def test_peer_state_none_falls_back_to_old_rules():
    """问不出来（对端旧版本 / 断链）时回落旧判据，两端版本不一致也不能变差。"""
    ctl, _ = new_controller(monitors=[])
    ctl.remote_ready = lambda: True
    ctl.peer_sees_monitor = lambda: None
    assert ctl.detect_source() == "mac"          # 靠「对端在线」兜底

    bare, _ = new_controller(monitors=[])
    bare.peer_sees_monitor = lambda: None
    assert bare.detect_source() == SOURCE_UNKNOWN


def test_peer_sees_beats_stale_absent_flag():
    """对端说看得到时，本地那个「两台都打不到」的旧标记要让位。"""
    ctl, backend = new_controller(monitors=[])

    def local_fail(value):
        raise DDCError("未发现物理显示器")

    backend.set_input = local_fail
    ctl.remote_ready = lambda: True
    ctl.remote_switch = lambda value: (False, "对端也没找到")
    ctl.switch_now()                              # 置上 _monitor_absent
    assert ctl.source_state == SOURCE_UNKNOWN

    ctl.peer_sees_monitor = lambda: True          # 对端现在说它看得到
    assert ctl.detect_source() == "mac"


def test_present_monitor_wins_over_peer_state():
    """本机能枚举到时永远判本机 —— 对端说什么都不该覆盖这条硬事实。"""
    ctl, _ = new_controller()
    ctl.peer_sees_monitor = lambda: True
    assert ctl.detect_source() == "windows"


# --------------------------------------------------------------------------- #
# 界面
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(sys.platform == "darwin", reason="旧 ui/mac 已由 ui/mac2 取代")
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


@pytest.mark.skipif(sys.platform == "darwin", reason="旧 ui/mac 已由 ui/mac2 取代")
def test_busy_false_keeps_button_disabled_in_unknown(qt_app):
    from dualswitch.ui.window import MainWindow

    win = MainWindow(make_cfg(), "Alt+Space", "v1.0.0")
    win.set_state(SOURCE_UNKNOWN)
    win.set_busy(True)
    win.set_busy(False)                    # 切完/切失败都会走这一步
    assert win.hero.button.isEnabled() is False


@pytest.mark.skipif(sys.platform == "darwin", reason="旧 ui/mac 已由 ui/mac2 取代")
def test_menu_unknown_disables_switch_entries():
    from dualswitch.ui.statusitem import build_menu_entries

    entries = build_menu_entries(make_cfg(), SOURCE_UNKNOWN, "alt+space", {})
    titles = [e.title for e in entries]
    assert any("当前画面：未检测到外部显示器" in t for t in titles)
    switch = next(e for e in entries if "切换" in e.title and e.kind == "item")
    assert switch.enabled is False


# --------------------------------------------------------------------------- #
# DDC 直读优先（2026-10-24 破案后新增：显示器亲口报输入 > 一切枚举推断）
# --------------------------------------------------------------------------- #
class _ReadBackend(MockBackend):
    """模拟「读通道真实可用」的后端（m1ddc 走 IOAVService 直读后的样子）。"""

    supports_ddc_detect = True


def test_detect_prefers_direct_ddc_read():
    """直读可用时归属以显示器报的输入为准，不看枚举结果。"""
    backend = _ReadBackend(current=18)          # 显示器说 18 → mac
    ctl = SwitchController(make_cfg(), backend)
    assert ctl.detect_source() == "mac"         # 枚举里明明有屏（会说 windows），直读赢
    backend.current = 17
    assert ctl.detect_source() == "windows"


def test_detect_falls_back_to_enumeration_when_read_fails():
    """读失败（老机器/链路断）回落枚举推断，行为与直读落地前完全一致。"""
    backend = _ReadBackend(current=18, fail_get=True)
    ctl = SwitchController(make_cfg(), backend)
    assert ctl.detect_source() == "windows"     # 枚举到屏 → 本机（this_host=windows）


def test_backend_without_decl_supports_keeps_old_behaviour():
    """没声明 supports_ddc_detect 的后端（mock / Windows）不吃直读路径。"""
    ctl, backend = new_controller()             # MockBackend 不声明 → False
    assert ctl._read_host() is None
    assert ctl.detect_source() == "windows"     # 仍走枚举推断


def test_unmapped_input_value_falls_back():
    """显示器报了配置之外的值（OSD 切到第三输入）→ 不猜，回落枚举推断。"""
    backend = _ReadBackend(current=27)          # 27 不在 sources 里
    ctl = SwitchController(make_cfg(), backend)
    assert ctl.detect_source() == "windows"


def test_note_scan_uses_direct_read_over_monitors():
    """启动探测同样直读优先：首帧就是真值，不靠「见过屏」猜。"""
    backend = _ReadBackend(current=18)          # 画面在 mac，但枚举也能看到屏
    ctl = SwitchController(make_cfg(), backend)
    ctl.note_scan(backend.list_monitors())
    assert ctl.source_state == "mac"            # 直读赢，而不是被 phantom 枚举带成 windows


def test_poll_commits_read_host_immediately():
    """直读是即时真值：一轮就落状态，不吃「两轮确认」的 4 秒延迟。"""
    backend = _ReadBackend(current=18)
    ctl = SwitchController(make_cfg(), backend)
    ctl.detect_interval_s = 0.02
    ctl.detect_settle_s = 0.0
    ctl.start()
    try:
        assert _wait_state(ctl, "mac") == "mac"
        backend.current = 17
        assert _wait_state(ctl, "windows") == "windows"
    finally:
        ctl.stop()
