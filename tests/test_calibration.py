"""标定状态机（纯逻辑）。"""

from __future__ import annotations

from dualswitch.ddc.calibration import STABLE_GAP_S, CalibrationPlan
from dualswitch.ddc.profiles import MonitorProfile

PROFILE = MonitorProfile(
    key="AOC:9987",
    name="U27N3G6R4B",
    ports={"hdmi1": 17, "hdmi2": 17, "dp": 15, "usb_c": 100},
)


def _plan(current=100, ports=None):
    return CalibrationPlan.create(
        PROFILE,
        ports or ["hdmi1", "hdmi2", "dp", "usb_c"],
        current_value=current,
    )


def test_start_port_is_excluded_and_known_value_is_recorded():
    plan = _plan()
    assert plan.start_port == "usb_c"
    assert plan.start_value == 100
    assert [s.port for s in plan.steps] == ["hdmi1", "hdmi2", "dp"]
    assert plan.result() == {"usb_c": 100}


def test_two_stable_reads_are_required():
    plan = _plan()
    plan.begin()
    assert plan.feed(17, 1.0) == "changed"
    assert plan.feed(17, 1.1) == "changed"          # 间隔不够
    assert plan.state == "waiting"
    assert plan.feed(17, 1.0 + STABLE_GAP_S + 0.1) == "caught"
    assert plan.current_step.value == 17
    assert plan.state == "caught"


def test_value_equal_to_start_is_never_auto_caught():
    """两个口同码时（HDMI1/HDMI2 都是 17），值不变不能自动判成切走了。"""
    plan = _plan(current=17, ports=["hdmi1", "hdmi2", "usb_c"])
    plan.begin()
    assert plan.feed(17, 1.0) == "same-as-start"
    assert plan.state == "waiting"
    assert plan.manual_catch(17) is True
    assert plan.current_step.value == 17


def test_manual_catch_uses_last_read_when_value_omitted():
    plan = _plan()
    plan.begin()
    plan.feed(15, 1.0)
    assert plan.manual_catch() is True
    assert plan.current_step.value == 15


def test_skip_and_finish():
    plan = _plan()
    plan.begin()
    plan.skip()
    assert plan.steps[0].status == "skipped"
    assert plan.current_step.port == "hdmi2"
    plan.skip()
    plan.skip()
    assert plan.finished
    assert plan.result() == {"usb_c": 100}


def test_mark_switched_back_advances():
    plan = _plan()
    plan.begin()
    plan.feed(17, 1.0)
    plan.feed(17, 2.0)
    assert plan.state == "caught"
    plan.mark_switched_back()
    assert plan.current_step.port == "hdmi2"
    assert plan.state == "waiting"


def test_retry_resets_stability():
    plan = _plan()
    plan.begin()
    plan.feed(15, 1.0)
    plan.retry()
    assert plan.feed(15, 2.0) == "changed"    # 重新要两次
    assert plan.state == "waiting"


def test_without_current_value_all_ports_are_steps():
    plan = _plan(current=None)
    assert plan.start_port == ""
    assert plan.can_auto_switch_back() is False
    assert [s.port for s in plan.steps] == ["hdmi1", "hdmi2", "dp", "usb_c"]


# --------------------------------------------------------------------------- #
# 2026-11-08：真机标定卡死之后补的两类保护
# --------------------------------------------------------------------------- #
def test_start_port_falls_back_to_wiring_when_code_is_unknown():
    """码表把 USB-C 记成 100、实际读到 18 时，起始口不能丢。

    丢了起始口 = 不能自动切回 + 每个读到 18 的口都被判成 same-as-start，
    表现就是「读到 18，一直不稳定」，标定永远走不完。
    """
    plan = CalibrationPlan.create(
        PROFILE,
        ["hdmi1", "hdmi2", "dp", "usb_c"],
        current_value=18,
        fallback_port="usb_c",
    )
    assert plan.start_port == "usb_c"
    assert plan.start_inferred is True
    assert plan.start_value == 18
    assert [s.port for s in plan.steps] == ["hdmi1", "hdmi2", "dp"]
    assert plan.can_auto_switch_back() is True
    # 起始口的码用现读到的值，顺手把过时的码表改对
    assert plan.result() == {"usb_c": 18}


def test_start_port_fallback_ignored_when_code_is_known():
    plan = CalibrationPlan.create(
        PROFILE, ["hdmi1", "hdmi2", "dp", "usb_c"], current_value=100, fallback_port="hdmi1"
    )
    assert plan.start_port == "usb_c"
    assert plan.start_inferred is False


def test_value_of_previous_port_is_not_auto_caught():
    """HDMI1/HDMI2 同码 17：刚抓完 HDMI1，不能立刻把 HDMI2 也判成 17。"""
    plan = CalibrationPlan.create(
        PROFILE, ["hdmi1", "hdmi2", "dp", "usb_c"], current_value=18, fallback_port="usb_c"
    )
    plan.begin()
    assert plan.feed(17, 1.0) == "changed"
    assert plan.feed(17, 2.0) == "caught"
    assert plan.current_step.value == 17
    plan.mark_switched_back()
    assert plan.current_step.port == "hdmi2"
    assert plan.feed(17, 3.0) == "same-as-known"
    assert plan.state == "waiting"
    assert plan.manual_catch() is True
    assert plan.current_step.value == 17


def test_read_failure_breaks_continuity():
    """18/失败/18 这样的抖动不能让状态机以为「连续两次相同」。"""
    plan = _plan()
    plan.begin()
    assert plan.feed(17, 1.0) == "changed"
    assert plan.feed(None, 1.2) == "no-read"
    assert plan.feed(17, 1.4) == "changed"
    assert plan.feed(17, 2.4) == "caught"


def test_manual_catch_never_falls_back_to_start_value():
    """没读到值时不能拿起始口的码去确认当前口（HDMI2 被写成 18 就是这么来的）。"""
    bare = MonitorProfile(key="AOC:9987", name="U27N3G6R4B", ports={"hdmi1": 17, "usb_c": 18})
    plan = CalibrationPlan.create(bare, ["hdmi1", "usb_c"], current_value=18, fallback_port="usb_c")
    plan.begin()
    assert plan.manual_catch() is True          # 码表里 hdmi1=17
    assert plan.current_step.value == 17

    unknown = MonitorProfile(key="X:1", name="x", ports={"hdmi1": 17, "usb_c": 18, "hdmi2": 17})
    plan2 = CalibrationPlan.create(
        unknown, ["hdmi1", "hdmi2", "usb_c"], current_value=18, fallback_port="usb_c"
    )
    plan2.begin()
    plan2.skip()                                # 跳过 hdmi1，hdmi2 还在码表里 → 用 17
    assert plan2.current_step.port == "hdmi2"
    assert plan2.manual_catch() is True
    assert plan2.current_step.value == 17
