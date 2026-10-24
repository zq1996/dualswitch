"""鼠标联动（HID++ `CHANGE_HOST` 0x1814）的异常路径。

这个模块的第一原则是**失败必须安静降级**，所以测试重点不是成功路径，
而是各种坏情况：

* 没装 hidapi / 没启用 / 槽位没配
* 没连鼠标（枚举不到）、鼠标在对端
* 设备不响应、设备不支持主机切换、被别的程序占用
* 槽位超过设备主机数（该槽未配对）
* 写入时设备断开（这是**成功**的表现，不是失败）
* 任何未预期异常都不能外泄

见 docs/FEASIBILITY-LOGI-EASYSWITCH.md。
"""
from __future__ import annotations

import pytest

from dualswitch.config import ConfigError, MouseSwitchConfig, from_dict
from dualswitch.mouse import logi_host
from dualswitch.mouse.logi_host import MouseSwitcher


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
def _resp(feature: int, function: int, *params: int, sw: int = 1) -> list[int]:
    """构造一条 HID++ 长报文响应（20 字节）。"""
    buf = [0x11, 0xFF, feature, ((function & 0x0F) << 4) | (sw & 0x0F)]
    buf.extend(params)
    return buf + [0] * (20 - len(buf))


class FakeDevice:
    def __init__(self, responses=None, *, write_error=None, open_error=None,
                 fail_on_func=None):
        self._responses = list(responses or [])
        self._write_error = write_error
        self._open_error = open_error
        # 只在指定 function 上抛错：模拟「切换命令发出后设备立刻断开」，
        # 而不是让 GetFeature 也一起失败。
        self._fail_on_func = fail_on_func
        self.writes: list[bytes] = []
        self.closed = False

    def open_path(self, _path):
        if self._open_error:
            raise self._open_error

    def set_nonblocking(self, _flag):
        pass

    def write(self, buf):
        self.writes.append(bytes(buf))
        if self._write_error:
            func = (buf[3] >> 4) & 0x0F
            if self._fail_on_func is None or func == self._fail_on_func:
                raise self._write_error

    def read(self, _size, _timeout):
        return self._responses.pop(0) if self._responses else []

    def close(self):
        self.closed = True


class FakeHid:
    def __init__(self, devices, paths=None, *, enumerate_error=None):
        self._devices = list(devices)
        self._paths = paths if paths is not None else [b"dev0"]
        self._enumerate_error = enumerate_error

    def enumerate(self, _vid):
        if self._enumerate_error:
            raise self._enumerate_error
        return [
            {"path": p, "usage_page": 0xFF43, "product_string": "Logi POP Mouse"}
            for p in self._paths
        ]

    def device(self):
        return self._devices.pop(0)


def _healthy_device(*, host_count: int = 3, current: int = 0,
                    write_error=None, fail_on_func=None):
    """一台能正常应答 GetFeature / getHostInfo 的鼠标。"""
    return FakeDevice(
        [
            _resp(0x00, 0x00, 0x0A),          # IRoot.GetFeature(0x1814) -> index 0x0A
            _resp(0x0A, 0x00, host_count, current),
        ],
        write_error=write_error,
        fail_on_func=fail_on_func,
    )


@pytest.fixture()
def cfg():
    return MouseSwitchConfig(enabled=True, local_slot=1, peer_slot=2, settle_ms=0)


def _patch_hid(monkeypatch, fake):
    monkeypatch.setattr(logi_host, "hid", fake)


# --------------------------------------------------------------------------- #
# 前置条件：不满足就静默跳过
# --------------------------------------------------------------------------- #
def test_disabled_is_silent_skip():
    sw = MouseSwitcher(MouseSwitchConfig(enabled=False))
    ok, why = sw.available()
    assert ok is False
    assert "未启用" in why
    outcome = sw.switch_to(2)
    assert outcome.ok is False
    assert outcome.skipped is True


def test_missing_hidapi_is_silent_skip(monkeypatch, cfg):
    _patch_hid(monkeypatch, None)
    ok, why = MouseSwitcher(cfg).available()
    assert ok is False
    assert "hidapi" in why


@pytest.mark.parametrize("slot", [0, 4, -1])
def test_unconfigured_peer_slot_is_silent_skip(slot):
    sw = MouseSwitcher(MouseSwitchConfig(enabled=True, local_slot=1, peer_slot=slot))
    ok, why = sw.available()
    assert ok is False
    assert "peer_slot" in why


@pytest.mark.parametrize("slot", [0, 4, -1, None, "2"])
def test_invalid_slot_argument_rejected(monkeypatch, cfg, slot):
    _patch_hid(monkeypatch, FakeHid([_healthy_device()]))
    outcome = MouseSwitcher(cfg).switch_to(slot)  # type: ignore[arg-type]
    assert outcome.ok is False
    assert "非法" in outcome.reason


# --------------------------------------------------------------------------- #
# 设备侧异常
# --------------------------------------------------------------------------- #
def test_no_device_found_is_not_an_error(monkeypatch, cfg):
    """鼠标在对端或没连时，本机枚举不到——这是物理约束下的常态。"""
    _patch_hid(monkeypatch, FakeHid([], paths=[]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is False
    assert outcome.skipped is False
    assert "枚举不到" in outcome.reason


def test_enumerate_exception_is_silent(monkeypatch, cfg):
    _patch_hid(monkeypatch, FakeHid([], enumerate_error=OSError("bus error")))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is False
    assert "枚举不到" in outcome.reason


def test_open_failure_falls_through_to_next_device(monkeypatch, cfg):
    """第一个集合被占用/没权限时，应该换下一个继续试。"""
    busy = FakeDevice(open_error=OSError("access denied"))
    good = _healthy_device()
    _patch_hid(monkeypatch, FakeHid([busy, good], paths=[b"busy", b"good"]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is True
    assert outcome.sent is True
    assert busy.closed is True


def test_device_without_change_host_is_skipped(monkeypatch, cfg):
    """不支持主机切换的设备（返回 feature index 0）应被跳过，不误发命令。"""
    dumb = FakeDevice([_resp(0x00, 0x00, 0x00)])   # GetFeature -> 0
    good = _healthy_device()
    _patch_hid(monkeypatch, FakeHid([dumb, good], paths=[b"dumb", b"good"]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is True
    assert dumb.writes and all(b[3] != 0x11 for b in dumb.writes)


def test_device_not_responding_is_skipped(monkeypatch, cfg):
    """设备在但不回话（休眠/固件卡住）：不抛异常，如实报告。"""
    silent = FakeDevice([])
    _patch_hid(monkeypatch, FakeHid([silent], paths=[b"silent"]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is False
    assert "无响应" in outcome.reason or "不支持" in outcome.reason


def test_slot_beyond_host_count_rejected(monkeypatch, cfg):
    """只有 2 个主机的设备，切 3 号槽会失联——必须在发命令前拦住。"""
    dev = _healthy_device(host_count=2)
    _patch_hid(monkeypatch, FakeHid([dev]))
    outcome = MouseSwitcher(cfg).switch_to(3)
    assert outcome.ok is False
    assert "未配对" in outcome.reason or "超过" in outcome.reason
    # 关键：一条 Set 都没发出去
    assert all(b[3] != 0x11 for b in dev.writes)


def test_single_host_device_rejected(monkeypatch, cfg):
    dev = _healthy_device(host_count=1)
    _patch_hid(monkeypatch, FakeHid([dev]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is False
    assert "无法切换" in outcome.reason


# --------------------------------------------------------------------------- #
# 正常路径 & 报文正确性
# --------------------------------------------------------------------------- #
def test_already_on_target_slot_sends_nothing(monkeypatch, cfg):
    dev = _healthy_device(current=1)   # 当前就在 2 号槽
    _patch_hid(monkeypatch, FakeHid([dev]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is True
    assert outcome.sent is False
    assert "无需切换" in outcome.reason
    assert all(b[3] != 0x11 for b in dev.writes)


def test_switch_report_encoding(monkeypatch, cfg):
    """报文位序：byte3 高半字节 = Function，低半字节 = Software ID。

    写反不会报错，只会被设备当成 GetProtocolVersion（返回 04 05），
    是这次研究里最难排查的一个坑，必须用测试钉住。
    """
    dev = _healthy_device(current=0)
    _patch_hid(monkeypatch, FakeHid([dev]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is True

    set_cmd = [w for w in dev.writes if w[3] == 0x11]
    assert len(set_cmd) == 1
    cmd = set_cmd[0]
    assert cmd[0] == 0x11              # report id
    assert cmd[1] == 0xFF              # 蓝牙直连 device index
    assert cmd[2] == 0x0A              # CHANGE_HOST 运行时索引
    assert cmd[3] == 0x11              # func=1(setCurrentHost), sw=1
    assert cmd[4] == 1                 # 槽位 2 -> 零基 1


def test_write_error_after_send_counts_as_success(monkeypatch, cfg):
    """成功切换会让设备立刻断开，写入抛错是**预期**行为，不能当失败。"""
    dev = _healthy_device(write_error=OSError("device disconnected"), fail_on_func=1)
    _patch_hid(monkeypatch, FakeHid([dev]))
    outcome = MouseSwitcher(cfg).switch_to(2)
    assert outcome.ok is True
    assert outcome.sent is True


def test_unexpected_exception_never_propagates(monkeypatch, cfg):
    """任何未预期异常都必须被吃掉——切鼠标不能拖垮调用方。"""

    class Exploding:
        def __getattr__(self, _name):
            raise RuntimeError("boom")

    _patch_hid(monkeypatch, Exploding())
    outcome = MouseSwitcher(cfg).switch_to(2)   # 不抛异常即为通过
    assert outcome.ok is False


def test_device_is_closed_on_every_path(monkeypatch, cfg):
    dev = _healthy_device()
    _patch_hid(monkeypatch, FakeHid([dev]))
    MouseSwitcher(cfg).switch_to(2)
    assert dev.closed is True


# --------------------------------------------------------------------------- #
# 配置校验
# --------------------------------------------------------------------------- #
def test_config_rejects_same_slot():
    with pytest.raises(ConfigError):
        from_dict({"mouse_switch": {"enabled": True, "local_slot": 1, "peer_slot": 1}})


@pytest.mark.parametrize("bad", [0, 4, -1])
def test_config_rejects_out_of_range_slot(bad):
    with pytest.raises(ConfigError):
        from_dict({"mouse_switch": {"enabled": True, "local_slot": 1, "peer_slot": bad}})


def test_config_allows_empty_slots_when_disabled():
    """关闭状态下允许留空，方便用户先写一半配置。"""
    cfg = from_dict({"mouse_switch": {"enabled": False}})
    assert cfg.mouse_switch.enabled is False
    assert cfg.mouse_switch.peer_slot == 0


def test_config_rejects_unknown_field():
    with pytest.raises(ConfigError):
        from_dict({"mouse_switch": {"nope": 1}})


def test_config_rejects_negative_settle():
    with pytest.raises(ConfigError):
        from_dict({"mouse_switch": {"settle_ms": -1}})


# --------------------------------------------------------------------------- #
# 编排层：钩子只在切屏成功后触发，且只让最新一代生效
# --------------------------------------------------------------------------- #
def _controller(mouse=None, *, fail_set=False):
    from dualswitch.app import SwitchController
    from dualswitch.ddc.mock import MockBackend

    raw: dict = {"this_host": "windows"}
    if mouse is not None:
        raw["mouse_switch"] = mouse
    cfg = from_dict(raw)
    cfg.readback.enabled = False      # 测试里不需要回读线程
    return SwitchController(cfg, MockBackend(fail_set=fail_set, current=17))


def test_hook_skips_when_disabled():
    ctl = _controller({"enabled": False})
    ctl._after_screen_switch("mac")
    assert ctl._mouse_gen == 0


def test_hook_skips_when_target_is_local():
    """画面回本机时本机什么都不做——鼠标在对端，由对端推回来。"""
    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2})
    ctl._after_screen_switch("windows")
    assert ctl._mouse_gen == 0


def test_hook_schedules_when_target_is_peer():
    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2})
    ctl._after_screen_switch("mac")
    assert ctl._mouse_gen == 1


def test_hook_ignores_empty_target():
    """host_of_value 认不出取值时给空串：没有方向，就不能乱切。"""
    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2})
    ctl._after_screen_switch("")
    assert ctl._mouse_gen == 0


def test_failed_switch_does_not_touch_mouse():
    """切屏失败（没连显示器 / DDC 报错）时绝不能去动鼠标。"""
    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2}, fail_set=True)
    ctl.set_input_local(18)
    assert ctl._mouse_gen == 0


def test_successful_switch_schedules_mouse():
    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2})
    ctl.set_input_local(18)           # 画面去 mac
    assert ctl._mouse_gen == 1


def test_switch_back_does_not_schedule_mouse():
    """画面切回本机：本机不推（够不着），交给对端的钩子。"""
    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2})
    ctl.set_input_local(17)           # 画面回 windows（本机）
    assert ctl._mouse_gen == 0


def test_stale_mouse_task_is_skipped(monkeypatch):
    """连点两次切换时，旧的那一代必须放弃，避免把鼠标推来推去。"""
    from dualswitch.mouse.logi_host import MouseSwitchOutcome

    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2})
    calls: list[int] = []
    monkeypatch.setattr(
        ctl._mouse, "switch_to",
        lambda slot: calls.append(slot) or MouseSwitchOutcome(True, "ok", sent=True, slot=slot),
    )

    ctl._mouse_gen = 5
    ctl._mouse_task(4, 2)      # 过期的一代
    assert calls == []

    ctl._mouse_task(5, 2)      # 最新一代
    assert calls == [2]


def test_mouse_task_swallows_exceptions(monkeypatch):
    """线程内任何异常都不能逃出去（daemon 线程抛异常会污染日志/进程）。"""
    ctl = _controller({"enabled": True, "local_slot": 1, "peer_slot": 2})

    def boom(_slot):
        raise RuntimeError("boom")

    monkeypatch.setattr(ctl._mouse, "switch_to", boom)
    ctl._mouse_gen = 1
    ctl._mouse_task(1, 2)      # 不抛异常即为通过

