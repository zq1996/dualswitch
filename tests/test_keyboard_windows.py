"""Windows 捕获端（键盘桥的一半）：闸门、epoch、抬键、钩子共用。

**为什么这些用例必须在 Windows 上跑**：被测模块 `capture_windows.py` 直接
`ctypes.WinDLL("user32")`，非 Windows 连 import 都过不去（见 `test_test_layout.py`
的平台门禁规则）。注入端（macOS）的用例在 `tests/test_keyboard.py`。

这里**不装真钩子**：`KeyboardCapture` 的钩子是可注入的，闸门逻辑不该只能靠
真键盘验。唯一碰系统的地方是 `KeyboardHook` 的引用计数，用假钩子覆盖。
"""

from __future__ import annotations

import sys
import threading

import pytest

if sys.platform != "win32":  # pragma: no cover - 平台门禁
    pytest.skip("Windows 专有捕获端", allow_module_level=True)

from dualswitch.keyboard.capture_windows import LINK_FRESH_S, KeyboardCapture  # noqa: E402


# --------------------------------------------------------------------------- #
# 假件
# --------------------------------------------------------------------------- #
class FakeKB:
    """`KBDLLHOOKSTRUCT` 的最小替身（只用到 injected / flags）。"""

    def __init__(self, injected: bool = False) -> None:
        self.flags = 0x10 if injected else 0

    @property
    def injected(self) -> bool:
        return bool(self.flags & 0x10)


class FakeEngine:
    def __init__(self, ready: bool = True) -> None:
        self.switch_ready = ready
        self.keys: list[dict] = []
        self.states: list[dict] = []
        self.focus: list[dict] = []
        self.fail = False

    def send_key(self, header):
        if self.fail:
            return False
        self.keys.append(dict(header))
        return True

    def send_key_state(self, header):
        self.states.append(dict(header))
        return not self.fail

    def send_focus(self, header):
        self.focus.append(dict(header))
        return True

    def request_peer_state(self, timeout=1.0):  # noqa: ARG002
        return True


class FakeHook:
    def __init__(self) -> None:
        self.error = None
        self.handlers: list = []

    def acquire(self, handler, *, name="", timeout=2.0):  # noqa: ARG002
        self.handlers.append(handler)

    def release(self, handler):
        # bound method 每次取值都是新对象，必须用 ==（`is` 永远不相等）
        self.handlers = [h for h in self.handlers if h != handler]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_capture(**kwargs) -> tuple[KeyboardCapture, FakeEngine, FakeHook, Clock]:
    engine = kwargs.pop("engine", None) or FakeEngine()
    hook = FakeHook()
    clock = Clock()
    mods = kwargs.pop("mods", {})
    caps = kwargs.pop("caps", False)
    cap = KeyboardCapture(
        engine,
        hook=hook,
        clock=clock,
        caps_reader=lambda: caps,
        mods_reader=lambda vk: bool(mods.get(vk, False)),
        hotkey_vk=0x20,                 # alt+space
        hotkey_mods=("alt",),
        release_vk=0x4B,                # ctrl+alt+shift+K
        release_mods=("ctrl", "alt", "shift"),
        **kwargs,
    )
    return cap, engine, hook, clock


def arm(cap: KeyboardCapture, clock: Clock) -> None:
    """把闸门打开：钩子已装 + 转发中 + 链路刚刚成功过。"""
    cap._target = cap.peer_host            # noqa: SLF001 - 测试直接摆状态
    cap._hook_ok = True                    # noqa: SLF001
    cap._last_ok_at = clock()              # noqa: SLF001


def flush(cap: KeyboardCapture) -> None:
    """把队列里的帧同步发出去（真线程在 worker 里做，这里为了确定性手动做）。"""
    import queue as _queue

    while True:
        try:
            kind, header = cap._queue.get_nowait()   # noqa: SLF001
        except _queue.Empty:
            return
        cap._send_item(kind, header)                 # noqa: SLF001


# --------------------------------------------------------------------------- #
# 闸门：四条全绿才吞
# --------------------------------------------------------------------------- #
def test_gate_closed_while_screen_is_local():
    cap, engine, _, clock = make_capture(enabled=True)
    arm(cap, clock)
    cap._target = "windows"                # noqa: SLF001
    assert cap.on_hook_event(0x41, True, FakeKB()) is False   # A
    assert engine.keys == []


def test_gate_closed_when_disabled():
    cap, engine, _, clock = make_capture(enabled=False)
    arm(cap, clock)
    assert cap.on_hook_event(0x41, True, FakeKB()) is False
    assert engine.keys == []


def test_gate_closed_when_link_not_ready():
    engine = FakeEngine(ready=False)
    cap, _, _, clock = make_capture(engine=engine, enabled=True)
    arm(cap, clock)
    assert cap.on_hook_event(0x41, True, FakeKB()) is False
    assert engine.keys == []


def test_gate_closed_when_no_successful_send_within_window():
    cap, engine, _, clock = make_capture(enabled=True)
    arm(cap, clock)
    clock.now += LINK_FRESH_S + 0.01       # 心跳断了
    assert cap.on_hook_event(0x41, True, FakeKB()) is False
    assert engine.keys == []


def test_gate_open_forwards_key_with_modifier_bitmap():
    cap, engine, _, clock = make_capture(enabled=True)
    arm(cap, clock)
    assert cap.on_hook_event(0xA0, True, FakeKB()) is True    # 左 Shift 按下
    assert cap.on_hook_event(0x41, True, FakeKB()) is True    # A
    flush(cap)
    usage = [k["u"] for k in engine.keys]
    assert usage == [0xE1, 0x04]
    assert engine.keys[1]["m"] == 0x02, "A 的修饰键位图里必须带上 Shift"


def test_injected_events_are_never_swallowed():
    """回环防护：`LLKHF_INJECTED` 的事件直接放行，不路由也不吞。"""
    cap, engine, _, clock = make_capture(enabled=True)
    arm(cap, clock)
    assert cap.on_hook_event(0x41, True, FakeKB(injected=True)) is False
    assert engine.keys == []


def test_unmapped_vk_is_passed_and_counted():
    cap, engine, _, clock = make_capture(enabled=True)
    arm(cap, clock)
    assert cap.on_hook_event(0xFF, True, FakeKB()) is False
    assert engine.keys == []
    assert cap.stats["dropped_unmapped"] == 1


# --------------------------------------------------------------------------- #
# 白名单：热键 / 紧急停止
# --------------------------------------------------------------------------- #
def test_hotkey_is_handled_locally_while_forwarding():
    """转发期间热键由捕获端自己认 —— **不能**放行给 Windows。

    现场：热键是 Alt+Space，而 Alt 在转发期间已经被吞给 Mac 了，Windows 根本看不到
    Alt 按下。放行 Space 的话 RegisterHotKey / 钩子后端都不会触发 —— 用户按热键毫无
    反应，等于**再也切不回来**（§8.7 最怕的那件事）。
    """
    fired = threading.Event()
    mods = {0xA4: True}
    cap, engine, _, clock = make_capture(
        enabled=True, mods=mods, on_hotkey=lambda: fired.set()
    )
    arm(cap, clock)

    assert cap.on_hook_event(0x20, True, FakeKB()) is True    # Alt+Space：吞掉
    assert fired.wait(1.0), "转发期间按热键必须真的触发切屏"
    assert cap.on_hook_event(0x20, False, FakeKB()) is True   # 抬起也吞（不漏半下）
    flush(cap)
    assert engine.keys == [], "热键一个字节都不该发给对端"

    # 没按 Alt 的普通空格照常转发
    mods[0xA4] = False
    assert cap.on_hook_event(0x20, True, FakeKB()) is True
    flush(cap)
    assert [k["u"] for k in engine.keys] == [0x2C]


def test_hotkey_passes_through_when_not_forwarding():
    """画面在本机时不接管热键：交给系统热键后端，行为与没装键盘桥时一致。"""
    fired = threading.Event()
    cap, engine, _, clock = make_capture(
        enabled=True, mods={0xA4: True}, on_hotkey=lambda: fired.set()
    )
    arm(cap, clock)
    cap._target = "windows"                    # noqa: SLF001 - 画面回到本机
    assert cap.on_hook_event(0x20, True, FakeKB()) is False
    assert not fired.wait(0.1)
    flush(cap)
    assert engine.keys == []


def test_release_chord_stops_forwarding_and_reports():
    asked: list[bool] = []
    cap, engine, _, clock = make_capture(
        enabled=True,
        mods={0xA2: True, 0xA4: True, 0xA0: True},
        on_release_request=lambda: asked.append(True),
    )
    arm(cap, clock)
    cap.on_hook_event(0x41, True, FakeKB())      # 先按着一个键
    flush(cap)
    swallowed = cap.on_hook_event(0x4B, True, FakeKB())   # Ctrl+Alt+Shift+K
    # **必须吞掉**：它的修饰键已经被吞给 Mac 了，放行就是一个光秃秃的 `k`
    # 打进 Windows 前台窗口
    assert swallowed is True
    assert asked == [True]
    assert cap.enabled is False
    assert cap.forwarding is False


# --------------------------------------------------------------------------- #
# 焦点 / epoch / 抬键
# --------------------------------------------------------------------------- #
def test_focus_to_peer_starts_forwarding_and_bumps_epoch():
    cap, engine, _, clock = make_capture(enabled=True)
    assert cap.epoch == 0
    cap.on_focus("mac")
    assert cap.forwarding is True
    assert cap.epoch == 1
    assert engine.focus[-1]["target"] == "mac"
    assert engine.focus[-1]["e"] == 1


def test_focus_back_to_local_stops_forwarding():
    cap, engine, _, clock = make_capture(enabled=True)
    cap.on_focus("mac")
    cap.on_focus("windows")
    assert cap.forwarding is False
    assert cap.epoch == 2
    assert engine.focus[-1]["target"] == "windows"


def test_focus_change_releases_held_keys():
    """按住 Shift 切屏 → 必须给对端补 keyup，否则对面 Shift 永远卡住。"""
    cap, engine, _, clock = make_capture(enabled=True)
    arm(cap, clock)
    cap.on_hook_event(0xA0, True, FakeKB())
    flush(cap)
    engine.keys.clear()
    cap.on_focus("windows")
    flush(cap)
    released = [k for k in engine.keys if k["d"] == 0]
    assert [k["u"] for k in released] == [0xE1]


def test_disable_releases_and_stops_swallowing():
    cap, engine, _, clock = make_capture(enabled=True)
    arm(cap, clock)
    cap.on_hook_event(0x41, True, FakeKB())
    flush(cap)
    engine.keys.clear()
    cap.set_enabled(False)
    flush(cap)
    assert [k["u"] for k in engine.keys] == [0x04]
    assert all(k["d"] == 0 for k in engine.keys)
    assert cap.on_hook_event(0x42, True, FakeKB()) is False


def test_set_enabled_is_idempotent():
    """重复打开不该再推 epoch —— epoch 一乱，对端会把正常按键全判成过期。"""
    cap, engine, _, _ = make_capture(enabled=True)
    cap.start()                     # 先真启动（装钩子 + 起 worker）
    try:
        cap.on_focus("mac")
        epoch_before = cap.epoch
        frames_before = len(engine.focus)
        cap.set_enabled(True)
        assert cap.epoch == epoch_before
        assert len(engine.focus) == frames_before
    finally:
        cap.stop()


# --------------------------------------------------------------------------- #
# 心跳 / 发送
# --------------------------------------------------------------------------- #
def test_worker_sends_heartbeat_with_down_bitmap_and_caps():
    """心跳三合一：活着 + 按下对账 + CapsLock 对齐。"""
    import time

    cap, engine, _, _ = make_capture(enabled=True, key_state_hz=50, caps=True)
    cap._target = cap.peer_host            # noqa: SLF001
    cap.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and not engine.states:
            time.sleep(0.02)
    finally:
        cap.stop()
    assert engine.states, "50Hz 下 2 秒内必须发出过心跳"
    assert engine.states[0]["caps"] == 1
    assert "down" in engine.states[0]


def test_send_failure_closes_the_gate():
    """送不出去就必须停止吞键 —— 否则按键掉进看不见的窗口。"""
    import time

    engine = FakeEngine()
    cap, _, _, clock = make_capture(engine=engine, enabled=True, key_state_hz=50)
    arm(cap, clock)
    engine.fail = True
    cap.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and cap.stats["send_failures"] == 0:
            time.sleep(0.02)
    finally:
        cap.stop()
    assert cap.stats["send_failures"] > 0


# --------------------------------------------------------------------------- #
# 钩子共用（热键 + 键盘桥只有一个 WH_KEYBOARD_LL）
# --------------------------------------------------------------------------- #
def test_hotkey_backend_registers_into_the_shared_hook(monkeypatch):
    """热键后端不再自己装钩子，而是向唯一钩子注册匹配器（§8.6）。"""
    from dualswitch.hotkey import windows as hw
    from dualswitch.hotkey.base import parse_hotkey

    fake = FakeHook()
    monkeypatch.setattr(hw, "shared_hook", lambda: fake)

    calls: list[int] = []
    backend = hw.LowLevelHookBackend(parse_hotkey("alt+space"), lambda: calls.append(1))
    monkeypatch.setattr(backend, "_mods_ok", lambda: True)
    backend.start()
    assert len(fake.handlers) == 1

    handler = fake.handlers[0]
    assert handler(0x20, True, FakeKB()) is True     # 命中 → 吞
    assert calls == [1]
    assert handler(0x20, False, FakeKB()) is True    # 抬起也吞（否则漏给系统）
    assert handler(0x41, True, FakeKB()) is False    # 其它键不碰

    backend.stop()
    assert fake.handlers == []
