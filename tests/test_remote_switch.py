"""ISSUE-002：远端代发切屏（本机打不到这块屏时，请对端替我们发 DDC Set）。

三层各测一段：
  协议  —— 新帧类型能编能解，旧版本收到会「忽略未知帧」而不是断连
  引擎  —— 请求/回执、超时、幂等去重、非法 VCP 拒绝
  编排  —— 本地失败或从未见过屏时自动改走对端，界面拿到 via=remote
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from dualswitch.app import SwitchController
from dualswitch.config import Config, SOURCE_UNKNOWN, from_dict
from dualswitch.ddc.base import DDCError
from dualswitch.ddc.mock import MockBackend
from dualswitch.sync import protocol as P
from dualswitch.sync.engine import SyncEngine


class FakeTransport:
    """够用的假链路：记录发出去的帧，connected/authenticated 由测试控制。"""

    def __init__(self, connected: bool = True, authenticated: bool = True):
        self.connected = connected
        self.authenticated = authenticated
        self.switch_frames: list[dict] = []
        self.switch_acks: list[dict] = []
        self.peer_device = "peer-1"

    def send_switch(self, header):
        self.switch_frames.append(dict(header))

    def send_switch_ack(self, header):
        self.switch_acks.append(dict(header))

    def describe(self) -> str:
        return "fake"


def make_cfg(**overrides):
    base = {"this_host": "windows", "readback": {"enabled": False}, "debounce_ms": 0}
    base.update(overrides)
    return from_dict(base)


def make_engine(transport=None, executor=None) -> SyncEngine:
    return SyncEngine(
        Config().clipboard,
        host_label="mac",
        transport=transport or FakeTransport(),
        switch_executor=executor,
        vcp_code=0x60,
    )


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --------------------------------------------------------------------------- #
# 协议
# --------------------------------------------------------------------------- #
def test_switch_frame_roundtrip():
    key = b"k" * 32
    raw = P.encode_frame(
        P.T_SWITCH, {"vcp": 96, "value": 18, "req": "abcd1234"}, key=key, counter=1
    )
    frames = P.FrameReader(key=key).feed(raw)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.type == P.T_SWITCH
    assert frame.type_name == "SWITCH"
    assert frame.header["value"] == 18
    assert frame.header["req"] == "abcd1234"


def test_switch_frame_is_plain_parseable_too():
    """debug_plain 模式下也要能解（排障走的就是这条）。"""
    raw = P.encode_frame(P.T_SWITCH_ACK, {"req": "r1", "ok": True})
    frame = P.FrameReader().feed(raw)[0]
    assert frame.type_name == "SWITCH_ACK"
    assert frame.header["ok"] is True


def test_transport_send_switch_requires_link():
    from dualswitch.sync.transport import LinkError, Transport

    transport = Transport(device_id="me", listen_port=0)
    with pytest.raises(LinkError):
        transport.send_switch({"vcp": 96, "value": 18, "req": "r"})


# --------------------------------------------------------------------------- #
# 引擎：请求端
# --------------------------------------------------------------------------- #
def test_request_switch_without_link_fails_fast():
    engine = make_engine(FakeTransport(connected=False))
    ok, detail = engine.request_switch(18, timeout=0.2)
    assert ok is False
    assert "未连接" in detail


def test_request_switch_times_out_without_ack():
    engine = make_engine()
    ok, detail = engine.request_switch(18, timeout=0.2)
    assert ok is False
    assert "未响应" in detail


def test_request_switch_resolves_on_ack():
    transport = FakeTransport()
    engine = make_engine(transport)
    box: dict = {}

    def worker():
        box["result"] = engine.request_switch(18, timeout=2.0)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert wait_for(lambda: bool(transport.switch_frames), 1.0)
    header = transport.switch_frames[0]
    assert header["vcp"] == 0x60 and header["value"] == 18
    engine._on_switch_ack({"req": header["req"], "ok": True, "detail": ""})
    thread.join(timeout=2.0)
    assert box["result"] == (True, "")


def test_request_switch_reports_peer_rejection():
    transport = FakeTransport()
    engine = make_engine(transport)
    box: dict = {}

    def worker():
        box["result"] = engine.request_switch(18, timeout=2.0)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert wait_for(lambda: bool(transport.switch_frames), 1.0)
    engine._on_switch_ack(
        {"req": transport.switch_frames[0]["req"], "ok": False, "detail": "DDC 超时"}
    )
    thread.join(timeout=2.0)
    assert box["result"] == (False, "DDC 超时")


# --------------------------------------------------------------------------- #
# 引擎：服务端
# --------------------------------------------------------------------------- #
def test_serve_switch_calls_executor_and_acks():
    transport = FakeTransport()
    calls: list[int] = []
    engine = make_engine(transport, executor=lambda value: (calls.append(value) or (True, "")))

    engine._on_switch_request({"req": "r1", "vcp": 0x60, "value": 18})
    assert wait_for(lambda: bool(transport.switch_acks), 1.0)
    ack = transport.switch_acks[0]
    assert ack == {"req": "r1", "ok": True, "detail": ""}
    assert calls == [18]
    assert engine.switch_stats["served"] == 1


def test_serve_switch_is_idempotent_per_req():
    transport = FakeTransport()
    calls: list[int] = []
    engine = make_engine(transport, executor=lambda value: (calls.append(value) or (True, "")))

    engine._on_switch_request({"req": "same", "vcp": 0x60, "value": 18})
    assert wait_for(lambda: len(transport.switch_acks) == 1, 1.0)
    engine._on_switch_request({"req": "same", "vcp": 0x60, "value": 18})
    assert wait_for(lambda: len(transport.switch_acks) == 2, 1.0)
    assert calls == [18]                     # 第二次没有重复执行
    assert transport.switch_acks[1]["ok"] is True


def test_serve_switch_rejects_wrong_vcp():
    transport = FakeTransport()
    engine = make_engine(transport, executor=lambda value: (True, ""))
    engine._on_switch_request({"req": "r2", "vcp": 0x10, "value": 18})
    assert wait_for(lambda: bool(transport.switch_acks), 1.0)
    ack = transport.switch_acks[0]
    assert ack["ok"] is False and "VCP" in ack["detail"]


def test_serve_switch_without_executor_fails_cleanly():
    transport = FakeTransport()
    engine = make_engine(transport, executor=None)
    engine._on_switch_request({"req": "r3", "vcp": 0x60, "value": 18})
    assert wait_for(lambda: bool(transport.switch_acks), 1.0)
    assert transport.switch_acks[0]["ok"] is False
    assert "未挂载" in transport.switch_acks[0]["detail"]


# --------------------------------------------------------------------------- #
# 编排：本地优先，失败/未知时改走对端
# --------------------------------------------------------------------------- #
def test_local_failure_falls_back_to_peer():
    backend = MockBackend(current=17, fail_set=True)
    ctl = SwitchController(make_cfg(), backend)
    ctl.remote_switch = lambda value: (True, "")
    result = ctl.switch_now()
    assert result.ok is True
    assert result.via == "remote"
    assert result.readback == "skipped"
    assert ctl.current_source == "mac"


def test_never_seen_monitor_uses_peer():
    """Windows 端看不到屏（画面在 Mac）时，请 Mac 代发 —— 方向是对称的。"""
    backend = MockBackend(current=17, monitors=[])
    ctl = SwitchController(make_cfg(), backend)          # this_host = windows
    ctl.note_scan([])
    assert ctl.source_state == SOURCE_UNKNOWN
    ctl.remote_switch = lambda value: (True, "")
    result = ctl.switch_now()
    assert result.ok is True and result.via == "remote"
    assert result.target == "mac" and result.value == 18
    assert backend.set_calls == []          # 一条本地 Set 都没发
    assert ctl.source_state == "mac"


def test_mac_can_ask_windows_too():
    """反过来同理：Mac 端看不到屏时请 Windows 代发（不是 Mac 专属能力）。"""
    backend = MockBackend(current=18, monitors=[], fail_set=True)
    ctl = SwitchController(make_cfg(this_host="mac"), backend)
    ctl.note_scan([])
    ctl.remote_switch = lambda value: (True, "")
    result = ctl.switch_now()
    assert result.ok is True and result.via == "remote"
    assert result.target == "windows" and result.value == 17
    assert ctl.current_source == "windows"


def test_never_seen_monitor_without_peer_still_fails_clearly():
    backend = MockBackend(current=17, monitors=[])
    ctl = SwitchController(make_cfg(), backend)
    ctl.note_scan([])
    ctl.remote_switch = lambda value: (False, "对端未连接或未配对")
    ctl.remote_ready = lambda: False
    result = ctl.switch_now()
    assert result.ok is False
    assert "未检测到外接显示器" in (result.error or "")


def test_peer_failure_keeps_local_error():
    backend = MockBackend(current=17, fail_set=True)
    ctl = SwitchController(make_cfg(), backend)
    ctl.remote_switch = lambda value: (False, "对端未连接")
    result = ctl.switch_now()
    assert result.ok is False
    assert "模拟 Set 失败" in (result.error or "")


def test_remote_path_skips_when_peer_not_ready():
    backend = MockBackend(current=17, fail_set=True)
    ctl = SwitchController(make_cfg(), backend)
    called: list[int] = []
    ctl.remote_switch = lambda value: (called.append(value) or (True, ""))
    ctl.remote_ready = lambda: False
    result = ctl.switch_now()
    assert result.ok is False
    assert called == []                     # 没连上就别发请求，省一次 3s 超时


# --------------------------------------------------------------------------- #
# 界面：未知态 + 对端在线 → 按钮可用
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(sys.platform == "darwin", reason="旧 ui/mac 已由 ui/mac2 取代")
def test_unknown_state_enables_button_when_peer_ready(qt_app):
    from dualswitch.ui.window import MainWindow

    # Windows 端：只能请 Mac 代发
    win = MainWindow(make_cfg(), "Alt+Space", "v1.0.0")
    win.set_state(SOURCE_UNKNOWN)
    assert win.hero.button.isEnabled() is False

    win.set_remote_available(True)
    assert win.hero.button.isEnabled() is True
    assert "从 Mac 切过来" in win.hero.btn_text.text()
    assert win.hero.kbd.isVisibleTo(win) is True

    win.set_remote_available(False)
    assert win.hero.button.isEnabled() is False
    assert win.hero.btn_text.text() == "未检测到显示器"


@pytest.mark.skipif(sys.platform == "darwin", reason="旧 ui/mac 已由 ui/mac2 取代")
def test_mac_unknown_state_asks_windows(qt_app):
    """反方向：Mac 端未知态时按钮写「从 Windows 切过来」。"""
    from dualswitch.ui.window import MainWindow

    win = MainWindow(make_cfg(this_host="mac"), "Alt+Space", "v1.0.0")
    win.set_state(SOURCE_UNKNOWN)
    win.set_remote_available(True)
    assert win.hero.button.isEnabled() is True
    assert "从 Windows 切过来" in win.hero.btn_text.text()


# --------------------------------------------------------------------------- #
# 设置页「切换模式」= 强制：跳过远端代发，直接发 Set
# --------------------------------------------------------------------------- #
def test_switch_policy_force_skips_peer_on_local_failure():
    backend = MockBackend(current=17, fail_set=True)
    ctl = SwitchController(make_cfg(switch_policy="force"), backend)
    called: list[int] = []
    ctl.remote_switch = lambda value: (called.append(value) or (True, ""))
    result = ctl.switch_now()
    assert result.ok is False
    assert result.via == "local"
    assert called == [], "强制模式不该再请对端代发"


def test_switch_policy_force_sends_set_when_monitor_never_seen():
    backend = MockBackend(current=17, monitors=[])
    ctl = SwitchController(make_cfg(switch_policy="force"), backend)
    ctl.note_scan([])
    ctl.remote_switch = lambda value: (True, "")
    result = ctl.switch_now()
    # 不再走「从未见过 → 请对端」的分支，而是直接本机发一次 Set
    assert backend.set_calls, "强制模式必须真的发 Set"
    assert result.via == "local"


# --------------------------------------------------------------------------- #
# ISSUE-003：屏还挂在总线上、但不在本机输出画面 —— 不能等 DDCError
# --------------------------------------------------------------------------- #
def _aoc():
    from dualswitch.ddc.base import MonitorInfo

    return MonitorInfo(
        index=0, description="AOC Q2790PC", device_id="MONITOR\\AOC2790\\{mock}"
    )


def test_picture_elsewhere_asks_peer_instead_of_blind_local_set():
    """macOS 的真实样子：`display list` 列得出屏，但画面在对面。

    m1ddc 在这种情形下 `set input` 返回 0（假成功）⇒ 永远等不到 DDCError
    ⇒ 永远不触发代发。所以必须靠「活动信号」提前判定。
    """
    backend = MockBackend(current=17, monitors=[_aoc()], active=[])
    ctl = SwitchController(make_cfg(this_host="mac"), backend)
    ctl.remote_switch = lambda value: (True, "")
    ctl.remote_ready = lambda: True

    result = ctl.switch_now("mac")
    assert result.ok is True
    assert result.via == "remote"
    assert result.value == 18
    assert backend.set_calls == [], "屏不在本机时不该再往本机 DDC 总线上插事务"


def test_picture_here_keeps_the_local_path():
    """画面在本机时仍走本地：没必要绕一圈 TCP。"""
    backend = MockBackend(current=18, monitors=[_aoc()], active=[_aoc()])
    ctl = SwitchController(make_cfg(this_host="mac"), backend)
    ctl.remote_switch = lambda value: (True, "")
    ctl.remote_ready = lambda: True

    result = ctl.switch_now("windows")
    assert result.via == "local"
    assert backend.set_calls == [17]


def test_picture_elsewhere_without_peer_fails_loudly():
    """屏不在本机、对端也不可用：如实报错，别用「假成功」骗界面。"""
    backend = MockBackend(current=17, monitors=[_aoc()], active=[])
    ctl = SwitchController(make_cfg(this_host="mac"), backend)
    ctl.remote_switch = lambda value: (False, "对端未连接或未配对")
    ctl.remote_ready = lambda: False

    result = ctl.switch_now("mac")
    assert result.ok is False
    assert "画面不在本机" in (result.error or "")
    assert backend.set_calls == []


def test_picture_probe_unavailable_keeps_old_behaviour():
    """后端分不出「挂着 / 在用」时不能改变原有行为（老版本、缺 pyobjc）。"""
    backend = MockBackend(current=17, monitors=[_aoc()], active=None)
    ctl = SwitchController(make_cfg(this_host="mac"), backend)
    ctl.remote_switch = lambda value: (True, "")
    ctl.remote_ready = lambda: True

    result = ctl.switch_now("windows")
    assert result.via == "local"
    assert backend.set_calls == [17]


# --------------------------------------------------------------------------- #
# ISSUE-003：目标要跟着「画面在哪」走，不能跟着 switch_mode 走
# --------------------------------------------------------------------------- #
def test_target_follows_the_picture_not_the_config():
    """`switch_mode=other` 时旧代码无条件返回 other_host()：
    Mac 端于是永远只会把画面送出去、永远叫不回来（按钮写「切换到 Mac」却发 17）。
    """
    backend = MockBackend(current=17, monitors=[_aoc()], active=[])
    ctl = SwitchController(make_cfg(this_host="mac"), backend)   # 默认 switch_mode=other

    ctl.note_scan([_aoc()])                # 先在本机看到它 → 画面在本机
    assert ctl.source_state == "mac"
    assert ctl._resolve_target() == "windows"

    ctl.note_scan([])                      # 之后枚举不到了 → 画面在 Windows
    assert ctl.source_state == "windows"
    assert ctl._resolve_target() == "mac"  # 「切换到 Mac」= 把画面叫回来


def test_unknown_picture_state_asks_peer_first_when_bringing_here():
    """判不出屏在哪、目标又是「叫回本机」时先请对端。

    对端此刻握着画面时这一步一定有效，比「先在本机发一条注定打不到的 Set」
    更快；也不怕本机的枚举 / DDC 通道在说谎（幻影显示器、假成功）。
    """
    backend = MockBackend(current=17, monitors=[_aoc()], active=None)
    ctl = SwitchController(make_cfg(this_host="mac"), backend)
    ctl.remote_switch = lambda value: (True, "")
    ctl.remote_ready = lambda: True

    result = ctl.switch_now("mac")
    assert result.via == "remote"
    assert backend.set_calls == []


def test_unknown_picture_state_falls_back_to_local_when_peer_refuses():
    """对端没握着画面时会干净地失败 —— 这时候必须改回本地发，别把用户卡死。"""
    backend = MockBackend(current=17, monitors=[_aoc()], active=None)
    ctl = SwitchController(make_cfg(this_host="mac"), backend)
    ctl.remote_switch = lambda value: (False, "对端拒绝")
    ctl.remote_ready = lambda: True

    result = ctl.switch_now("mac")
    assert result.via == "local"
    assert backend.set_calls == [18]
