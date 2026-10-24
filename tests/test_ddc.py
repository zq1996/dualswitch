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


# --------------------------------------------------------------------------- #
# 活动信号枚举（ISSUE-003）：macOS 的 m1ddc 在屏切走之后照样列得出这块屏
# --------------------------------------------------------------------------- #
def _fake_screen(number: int, name: str):
    """够用的 NSScreen 替身：只实现 deviceDescription / localizedName。"""

    class _Screen:
        def deviceDescription(self):
            class _Desc:
                def objectForKey_(self, key):  # noqa: N802 - ObjC 风格
                    return number

            return _Desc()

        def localizedName(self):
            return name

    return _Screen()


def _install_fake_pyobjc(monkeypatch, screens, builtin=(), inactive=()):
    import types

    appkit = types.ModuleType("AppKit")

    class NSScreen:
        @staticmethod
        def screens():
            return list(screens)

    appkit.NSScreen = NSScreen
    quartz = types.ModuleType("Quartz")
    quartz.CGDisplayIsBuiltin = lambda number: number in builtin
    # 「在线」不等于「在用」：屏切到 HDMI 之后 macOS 仍把它留在在线列表里，
    # 但 CGDisplayIsActive 会翻 False。默认全部激活，个别用例点名 inactive。
    quartz.CGDisplayIsActive = lambda number: number not in inactive
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)


def test_mac_active_monitors_ignores_builtin_screen(monkeypatch):
    """内置屏必须被排除：mac 默认 monitor_match 为空，留着它「画面永远在本机」。"""
    from dualswitch.ddc.macos import M1ddcBackend

    _install_fake_pyobjc(
        monkeypatch,
        [_fake_screen(1, "Built-in Retina Display"), _fake_screen(2, "AOC Q2790PC")],
        builtin={1},
    )
    monitors = M1ddcBackend().active_monitors()
    assert [m.description for m in monitors] == ["AOC Q2790PC"]


def test_mac_active_monitors_empty_when_only_builtin(monkeypatch):
    from dualswitch.ddc.macos import M1ddcBackend

    _install_fake_pyobjc(monkeypatch, [_fake_screen(1, "Built-in Retina Display")], builtin={1})
    assert M1ddcBackend().active_monitors() == []


def test_mac_active_monitors_skips_online_but_inactive_screen(monkeypatch):
    """**用户 2026-10-23 报的「初始显示在 Mac、实际在 Windows」就是这一条。**

    屏切到 HDMI 之后 macOS 常把它留在在线列表里（NSScreen 照列），
    只是不再往它扫画面 —— 这时 `CGDisplayIsActive` 才是判据。
    """
    from dualswitch.ddc.macos import M1ddcBackend

    _install_fake_pyobjc(
        monkeypatch,
        [_fake_screen(1, "Built-in Retina Display"), _fake_screen(2, "AOC Q2790PC")],
        builtin={1},
        inactive={2},
    )
    assert M1ddcBackend().active_monitors() == []


def test_mac_active_monitors_returns_none_without_pyobjc(monkeypatch):
    """缺 pyobjc 时必须说「我不知道」，不能假装「看不到屏」。"""
    from dualswitch.ddc.macos import M1ddcBackend

    monkeypatch.setitem(sys.modules, "AppKit", None)
    assert M1ddcBackend().active_monitors() is None


def test_mac_active_monitors_returns_none_without_display_name(monkeypatch):
    """取不到显示名就没法按 monitor_match 匹配，整轮作废好过给假答案。"""
    from dualswitch.ddc.macos import M1ddcBackend

    _install_fake_pyobjc(monkeypatch, [_fake_screen(2, "")])
    assert M1ddcBackend().active_monitors() is None


def test_probe_active_monitors_falls_back_when_backend_cannot_tell():
    from dualswitch.ddc.base import probe_active_monitors

    backend = MockBackend(monitors=[MonitorInfo(index=0, description="AOC")], active=None)
    assert probe_active_monitors(backend) == backend.list_monitors()


def test_probe_active_monitors_falls_back_on_probe_error():
    from dualswitch.ddc.base import probe_active_monitors

    class Boom(MockBackend):
        def active_monitors(self):
            raise RuntimeError("IOKit 炸了")

    backend = Boom(monitors=[MonitorInfo(index=0, description="AOC")])
    assert probe_active_monitors(backend) == backend.list_monitors()


class _FakeAV:
    """替身：只记录写了几条 Set、写的是什么。"""

    def __init__(self, ok: bool = True):
        self.calls: list[tuple[int, int]] = []
        self.ok = ok

    def run_exclusive(self, fn):
        return fn()

    def set_vcp(self, code: int, value: int) -> bool:
        self.calls.append((int(code), int(value)))
        return self.ok


@pytest.mark.skipif(sys.platform != "darwin", reason="m1ddc 后端专用")
def test_mac_set_input_always_sends_twice(monkeypatch):
    """本机发送固定两条：读完再写，第一条必被吞，第二条才生效（2026-10-24 人眼确认）。

    实测：读一次 VCP 之后的第一条 Set 回 rc=0 但画面 12s 不动，紧接着第二条 0.15~5s
    内必中；只写不读时连发都中。app 里每次点击前几乎必然刚读过（回读线程 / 进页面直读 /
    热键发号前直读），所以省不掉这一条。同一台屏从 Windows 发一条就切 ⇒ 不是显示器的问题。
    """
    from dualswitch.ddc import macos as mac
    from dualswitch.ddc.macos import M1ddcBackend

    b = M1ddcBackend()
    av = _FakeAV()
    b._av = av
    monkeypatch.setattr(mac, "SET_REPEAT_GAP_S", 0.0)
    b.set_input(17)
    assert av.calls == [(0x60, 17), (0x60, 17)]

    # 紧接着再点一次也还是两条 —— 与「距上次发送多久」无关，别再加回热通道优化
    av.calls.clear()
    b.set_input(18)
    assert av.calls == [(0x60, 18), (0x60, 18)]


@pytest.mark.skipif(sys.platform != "darwin", reason="m1ddc 后端专用")
def test_mac_set_input_falls_back_to_m1ddc(monkeypatch):
    """本机直写通道不可用（老系统 / 拿不到外接屏句柄）时退回 m1ddc，同样两条。"""
    from dualswitch.ddc import macos as mac
    from dualswitch.ddc.macos import M1ddcBackend

    b = M1ddcBackend()
    b._av = _FakeAV(ok=False)
    calls = []
    monkeypatch.setattr(b, "_run", lambda args, display_args=None: calls.append(list(args)) or "")
    monkeypatch.setattr(b, "_display_args", lambda: [])
    monkeypatch.setattr(mac, "SET_REPEAT_GAP_S", 0.0)
    b.set_input(17)
    assert calls == [["set", "input", "17"], ["set", "input", "17"]]
