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
