"""macOS CGEventTap 的事件判定逻辑（用假 Quartz，不需要真键盘 / 无需授权）。

这里锁死的是一个真实踩过的坑：取修饰键位图写成了
`CGEventGetIntegerValueField(event, kCGKeyboardEventFlags)` —— 这个字段压根不存在，
pyobjc 里连常量都没有，于是 tap 回调每次都抛 AttributeError，被兜底 except 吞掉，
表现为「热键按一百次一次都不响，日志里却干净得像没事」。
假 Quartz 故意在缺常量时抛 AttributeError，这条回归就再也回不去了。
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="只在 macOS 上导入")

if sys.platform != "darwin":       # 模块级跳过：否则 import 阶段就先炸，整套用例收集不起来
    pytest.skip("只在 macOS 上导入", allow_module_level=True)

from dualswitch.hotkey.base import Hotkey  # noqa: E402
from dualswitch.hotkey import macos as hk  # noqa: E402


class FakeEvent:
    def __init__(self, keycode: int, flags: int = 0):
        self.keycode = keycode
        self.flags = flags


class FakeQuartz:
    """只实现被用到的接口；其余一律 AttributeError，和 pyobjc 一样严格。"""

    kCGEventKeyDown = 10
    kCGEventKeyUp = 11
    kCGEventFlagsChanged = 12
    kCGEventTapDisabledByTimeout = 0xFFFFFFFE
    kCGEventTapDisabledByUserInput = 0xFFFFFFFF
    kCGKeyboardEventKeycode = 9

    def __init__(self):
        self.enabled = None
        self.field_reads: list[int] = []

    def CGEventGetIntegerValueField(self, event, field):
        self.field_reads.append(field)
        if field != self.kCGKeyboardEventKeycode:
            raise AssertionError(f"不该读这个字段: {field}")
        return event.keycode

    def CGEventGetFlags(self, event):
        return event.flags

    def CGEventTapEnable(self, tap, flag):
        self.enabled = flag

    def __getattr__(self, name):  # 模拟 pyobjc：没有的常量就是没有
        raise AttributeError(name)


def make_backend(modifiers=("ralt",), key="space"):
    hits: list[int] = []
    backend = hk.QuartzEventTapBackend(Hotkey(modifiers=tuple(modifiers), key=key), lambda: hits.append(1))
    backend._keycode = hk.to_mac_keycode(key)
    return backend, hits, FakeQuartz()


def test_right_option_plus_space_triggers_and_is_swallowed():
    backend, hits, Q = make_backend()
    # 先按右 Option（FlagsChanged 带 alternate）
    assert backend.handle_event(Q, None, Q.kCGEventFlagsChanged, FakeEvent(61, hk.FLAG_ALTERNATE)) is not None
    assert backend._ralt_down is True

    out = backend.handle_event(
        Q, None, Q.kCGEventKeyDown, FakeEvent(49, hk.FLAG_ALTERNATE | hk.FLAG_DEVICE_RALT)
    )
    assert hits == [1]
    assert out is None, "swallow=True 时必须吞掉这个按键"
    assert backend.matches == 1


def test_space_without_option_does_not_trigger():
    backend, hits, Q = make_backend()
    backend.handle_event(Q, None, Q.kCGEventKeyDown, FakeEvent(49, 0))
    assert hits == []


def test_other_key_with_option_does_not_trigger():
    backend, hits, Q = make_backend()
    backend.handle_event(Q, None, Q.kCGEventFlagsChanged, FakeEvent(61, hk.FLAG_ALTERNATE))
    backend.handle_event(Q, None, Q.kCGEventKeyDown, FakeEvent(0, hk.FLAG_ALTERNATE))
    assert hits == []


def test_left_option_is_not_right_option():
    """左 Option（设备位 0x20）不该触发 ralt 热键。"""
    backend, hits, Q = make_backend()
    backend.handle_event(Q, None, Q.kCGEventFlagsChanged, FakeEvent(58, hk.FLAG_ALTERNATE | 0x20))
    backend.handle_event(Q, None, Q.kCGEventKeyDown, FakeEvent(49, hk.FLAG_ALTERNATE | 0x20))
    assert hits == []


def test_device_flag_survives_missed_flags_changed():
    """漏掉 FlagsChanged 时靠 NX_DEVICERALTKEYMASK 也能认出来（冷启动就按着键的场景）。"""
    backend, hits, Q = make_backend()
    backend.handle_event(Q, None, Q.kCGEventKeyDown, FakeEvent(49, hk.FLAG_ALTERNATE | hk.FLAG_DEVICE_RALT))
    assert hits == [1]


def test_ctrl_hotkey_variant():
    backend, hits, Q = make_backend(modifiers=("ctrl",), key="r")
    backend._keycode = hk.to_mac_keycode("r")
    backend.handle_event(Q, None, Q.kCGEventKeyDown, FakeEvent(15, hk.FLAG_CONTROL))
    assert hits == [1]


def test_disabled_tap_is_re_enabled():
    """系统关掉 tap（超时/安全输入）必须自己打开，否则热键永久失效。"""
    backend, hits, Q = make_backend()
    backend.handle_event(Q, "tap-obj", Q.kCGEventTapDisabledByTimeout, FakeEvent(0))
    assert Q.enabled is True
    assert backend.tap_resets == 1

    backend.handle_event(Q, "tap-obj", Q.kCGEventTapDisabledByUserInput, FakeEvent(0))
    assert backend.tap_resets == 2


def test_key_up_is_swallowed_too():
    backend, hits, Q = make_backend()
    assert backend.handle_event(Q, None, Q.kCGEventKeyUp, FakeEvent(49)) is None


def test_callback_exception_does_not_break_tap():
    fallback = FakeEvent(49, hk.FLAG_ALTERNATE | hk.FLAG_DEVICE_RALT)
    backend = hk.QuartzEventTapBackend(Hotkey(modifiers=("ralt",), key="space"), lambda: 1 / 0)
    backend._keycode = 49
    Q = FakeQuartz()
    assert backend.handle_event(Q, None, Q.kCGEventKeyDown, fallback) is None
    assert backend.callback_errors == 1
    # 再来一次仍然正常判定，不会因为一次异常就整个哑掉
    assert backend.handle_event(Q, None, Q.kCGEventKeyDown, fallback) is None
    assert backend.callback_errors == 2
