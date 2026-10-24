"""切换编排测试：去抖、目标解析、异步回读、异常不崩溃。"""

from __future__ import annotations

import time

import pytest

from dualswitch.app import SwitchController
from dualswitch.config import from_dict
from dualswitch.ddc.mock import MockBackend


class FakeClock:
    def __init__(self, start: float = 1000.0, step: float = 0.0):
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def make_cfg(**overrides):
    base = {
        "this_host": "windows",
        "readback": {"enabled": False},
        "debounce_ms": 0,
    }
    base.update(overrides)
    return from_dict(base)


def wait_readback(result, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline and result.readback == "pending":
        time.sleep(0.01)
    return result


def test_switch_goes_to_other_host():
    cfg = make_cfg()
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    result = ctl.switch_now()
    assert result.ok is True
    assert result.target == "mac"
    assert result.value == 18
    assert backend.set_calls == [18]
    assert ctl.current_source == "mac"


def test_explicit_target():
    cfg = make_cfg()
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    result = ctl.switch_now("windows")
    assert result.target == "windows"
    assert backend.set_calls == [17]


def test_toggle_mode_alternates():
    cfg = make_cfg(switch_mode="toggle")
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    ctl.switch_now()
    assert backend.set_calls == [18]
    ctl.switch_now()
    assert backend.set_calls == [18, 17]


def test_set_failure_does_not_raise_and_notifies():
    cfg = make_cfg()
    backend = MockBackend(current=17, fail_set=True)
    messages = []
    ctl = SwitchController(cfg, backend, notifier=lambda t, m: messages.append((t, m)))
    result = ctl.switch_now()
    assert result.ok is False
    assert "模拟 Set 失败" in (result.error or "")
    assert messages and messages[0][0] == "切换失败"


def test_unexpected_exception_is_caught():
    class Boom(MockBackend):
        def set_input(self, value: int) -> None:
            raise RuntimeError("驱动层炸了")

    cfg = make_cfg()
    ctl = SwitchController(cfg, Boom())
    result = ctl.switch_now()
    assert result.ok is False
    assert "RuntimeError" in (result.error or "")


def test_dry_run_sends_nothing():
    cfg = make_cfg(dry_run=True)
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    result = ctl.switch_now()
    assert result.ok is True
    assert backend.set_calls == []
    assert result.readback == "skipped"
    assert "[演练]" in result.summary()


def test_readback_confirmed():
    cfg = make_cfg(readback={"enabled": True, "delay_ms": 0, "timeout_ms": 300, "interval_ms": 10})
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    result = wait_readback(ctl.switch_now())
    assert result.readback == "confirmed"


def test_readback_unconfirmed_when_value_mismatch():
    cfg = make_cfg(readback={"enabled": True, "delay_ms": 0, "timeout_ms": 120, "interval_ms": 20})
    backend = MockBackend(current=17, readback_value=99)
    ctl = SwitchController(cfg, backend)
    result = wait_readback(ctl.switch_now())
    assert result.readback == "unconfirmed"
    assert "99" in result.readback_detail


def test_readback_unconfirmed_when_read_fails():
    cfg = make_cfg(readback={"enabled": True, "delay_ms": 0, "timeout_ms": 100, "interval_ms": 20})
    backend = MockBackend(current=17, fail_get=True)
    ctl = SwitchController(cfg, backend)
    result = wait_readback(ctl.switch_now())
    assert result.readback == "unconfirmed"
    assert "读取失败" in result.readback_detail


def test_debounce_drops_fast_repeats():
    cfg = make_cfg(debounce_ms=500)
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend, clock=FakeClock(step=0.01))
    assert ctl.trigger() is True
    assert ctl.trigger() is False
    ctl.stop()


def test_send_latency_measured():
    cfg = make_cfg()
    backend = MockBackend(current=17)
    clock = FakeClock(start=1000.0, step=0.05)  # 每次调用 +50ms
    ctl = SwitchController(cfg, backend, clock=clock)
    result = ctl.switch_now()
    # trigger 时刻 → 发送前取一次时间，至少经过一次 clock 推进
    assert result.send_latency_ms >= 49.0


def test_history_is_bounded():
    cfg = make_cfg()
    ctl = SwitchController(cfg, MockBackend(current=17))
    for _ in range(60):
        ctl.switch_now()
    assert len(ctl.history) <= 50


def test_worker_processes_trigger():
    cfg = make_cfg()
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    ctl.start()
    try:
        assert ctl.trigger() is True
        deadline = time.time() + 2
        while time.time() < deadline and not backend.set_calls:
            time.sleep(0.01)
        assert backend.set_calls == [18]
    finally:
        ctl.stop()


def test_worker_survives_backend_error():
    cfg = make_cfg()
    backend = MockBackend(current=17, fail_set=True)
    ctl = SwitchController(cfg, backend)
    ctl.start()
    try:
        ctl.trigger()
        time.sleep(0.15)
        ctl.trigger()
        time.sleep(0.15)
        assert len(backend.set_calls) == 2
    finally:
        ctl.stop()


# --------------------------------------------------------------------------- #
# 外部切换检测：另一台机器上执行 CLI 时，本进程只能靠「显示器还在不在」反推
# --------------------------------------------------------------------------- #
def _wait_source(ctl, expected: str, timeout: float = 2.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline and ctl.current_source != expected:
        time.sleep(0.01)
    return ctl.current_source


def test_detect_source_by_monitor_presence():
    backend = MockBackend(current=17)
    ctl = SwitchController(make_cfg(), backend)
    assert ctl.detect_source() == "windows"

    backend._monitors = []          # 显示器已切到另一台，本机枚举不到
    assert ctl.detect_source() == "mac"


def test_detector_picks_up_external_switch():
    """模拟：应用切到 Mac 后，在 Mac 上执行 CLI 切回 Windows。"""
    cfg = make_cfg()
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    ctl.detect_interval_s = 0.02
    ctl.detect_settle_s = 0.0
    ctl.start()
    try:
        assert ctl.current_source == "windows"

        backend._monitors = []                       # 外部切走
        assert _wait_source(ctl, "mac") == "mac"

        backend._monitors = list(MockBackend()._monitors)   # 外部切回
        assert _wait_source(ctl, "windows") == "windows"
    finally:
        ctl.stop()


def test_detector_respects_settle_after_own_switch():
    """自己刚切完的静默期内，即使显示器状态还没变，也不能把状态翻回去。"""
    cfg = make_cfg()
    backend = MockBackend(current=17)   # 显示器列表保持不变 = 切换有滞后
    ctl = SwitchController(cfg, backend)
    ctl.detect_interval_s = 0.02
    ctl.detect_settle_s = 30.0
    ctl.start()
    try:
        ctl.switch_now()                # → mac
        time.sleep(0.25)
        assert ctl.current_source == "mac"
    finally:
        ctl.stop()


# --------------------------------------------------------------------------- #
# 显式通道（docs/SCREEN-OWNERSHIP.md §3.2）
#
# 2026-10-24 的教训：把「本机发」和「请对端发」串成一条自动链路时，m1ddc 的
# 假成功让「到底哪条起了作用」无法判断。两条通道必须互不牵连 —— 下面每条
# 用例都在守这一个约束。
# --------------------------------------------------------------------------- #
def test_host_of_value_maps_config():
    cfg = make_cfg()
    ctl = SwitchController(cfg, MockBackend(current=17))
    assert ctl.host_of_value(17) == "windows"
    assert ctl.host_of_value(18) == "mac"
    assert ctl.host_of_value(99) == ""


def test_local_channel_only_touches_local_backend():
    cfg = make_cfg()
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    remote: list[int] = []
    ctl.remote_switch = lambda v: (remote.append(v), (True, ""))[1]
    ctl.remote_ready = lambda: True

    result = ctl.set_input_local(18)

    assert result.ok is True
    assert result.via == "local"
    assert result.target == "mac"
    assert backend.set_calls == [18]
    assert remote == [], "本机通道不许顺手请对端"


def test_remote_channel_does_not_touch_local_backend():
    cfg = make_cfg()
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    remote: list[int] = []
    ctl.remote_switch = lambda v: (remote.append(v), (True, ""))[1]
    ctl.remote_ready = lambda: True

    result = ctl.switch_remote(18)

    assert result.ok is True
    assert result.via == "remote"
    assert remote == [18]
    assert backend.set_calls == [], "代发通道本机一条 DDC 都不该发"
    assert result.readback == "skipped"


def test_remote_channel_reports_offline_reason():
    cfg = make_cfg()
    backend = MockBackend(current=17)
    ctl = SwitchController(cfg, backend)
    ctl.remote_ready = lambda: False

    def _boom(_value: int):  # 不该被调用
        raise AssertionError("对端离线时不该发请求")

    ctl.remote_switch = _boom
    result = ctl.switch_remote(17)

    assert result.ok is False
    assert "离线" in (result.error or "") or "未配对" in (result.error or "")
    assert backend.set_calls == []


def test_remote_channel_without_engine_says_so():
    cfg = make_cfg()
    ctl = SwitchController(cfg, MockBackend(current=17))
    result = ctl.switch_remote(17)
    assert result.ok is False
    assert "同步引擎" in (result.error or "")


def test_remote_channel_surfaces_peer_refusal():
    cfg = make_cfg()
    ctl = SwitchController(cfg, MockBackend(current=17))
    ctl.remote_ready = lambda: True
    ctl.remote_switch = lambda v: (False, "VCP 0x60 与本机配置不一致")
    result = ctl.switch_remote(17)
    assert result.ok is False
    assert "不一致" in (result.error or "")
