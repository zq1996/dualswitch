"""DDC 后端工厂与 mock 后端测试。"""

from __future__ import annotations

import sys

import pytest

from dualswitch.config import from_dict
from dualswitch.ddc import create_backend
from dualswitch.ddc.base import DDCError, MonitorInfo
from dualswitch.ddc.mock import MockBackend


def test_factory_mock():
    backend = create_backend(from_dict({"ddc_backend": "mock"}))
    assert backend.name == "mock"
    assert backend.list_monitors()


def test_factory_auto_picks_platform_backend():
    cfg = from_dict({"ddc_backend": "auto"})
    backend = create_backend(cfg)
    if sys.platform == "win32":
        assert backend.name == "dxva2"
    elif sys.platform == "darwin":
        assert backend.name == "m1ddc"


def test_factory_rejects_wrong_platform():
    if sys.platform == "win32":
        with pytest.raises(DDCError, match="只能"):
            create_backend(from_dict({"ddc_backend": "m1ddc"}))
    elif sys.platform == "darwin":
        with pytest.raises(DDCError, match="只能"):
            create_backend(from_dict({"ddc_backend": "dxva2"}))


def test_factory_command_backend():
    cfg = from_dict(
        {
            "ddc_backend": "command",
            "command_template": ["echo", "{value}"],
            "command_get_template": ["echo", "17"],
        }
    )
    backend = create_backend(cfg)
    assert backend.name == "command"
    assert backend.get_input() == 17


def test_mock_records_calls():
    backend = MockBackend(current=17)
    backend.set_input(27)
    assert backend.current == 27
    assert backend.set_calls == [27]
    assert backend.get_input() == 27


def test_mock_failures():
    backend = MockBackend(fail_set=True, fail_get=True)
    with pytest.raises(DDCError):
        backend.set_input(27)
    with pytest.raises(DDCError):
        backend.get_input()


def test_monitor_matching():
    monitors = [
        MonitorInfo(index=0, description="Generic PnP Monitor", device_id="MONITOR\\DEL1234\\{a}"),
        MonitorInfo(index=1, description="Generic PnP Monitor", device_id="MONITOR\\AOC2790\\{b}"),
    ]
    backend = MockBackend(monitors=monitors, monitor_match="AOC")
    assert backend.pick(monitors).index == 1
    backend2 = MockBackend(monitors=monitors, monitor_match="")
    assert backend2.pick(monitors).index == 0


def test_pick_without_monitors():
    backend = MockBackend(monitors=[])
    with pytest.raises(DDCError, match="未发现"):
        backend.pick([])
