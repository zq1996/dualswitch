"""键盘跟随的共享逻辑：键位映射、按键状态机、注入端。

这个文件**两端都要真跑**，所以刻意不碰真 Quartz —— `MacInjector(quartz=...)`
允许注入假实现。真实性由 `tools/key_feeder.py --probe` 在 mac 上单独验。

锁死的几条规矩（docs/KEYBOARD-BRIDGE.md §7 / §8）：

* 修饰键按**物理位置**映射：Alt→⌘（Alt+C 就是复制）、Win→⌥。
  这条是用户 2026-11-06 明确指定的，写错的话所有快捷键都会错位。
* 抬起永远放行，按下才需要判据 —— 抬多了无害，抬少了就是卡键。
* 焦点切换必须全量释放，否则"按住 Shift 切屏"会让对端 Shift 永远卡住。
* 过期 epoch 的按键直接丢：切屏前发出的键在切屏后才到，它已经不对应任何画面。
"""

from __future__ import annotations

import pytest

from dualswitch.keyboard import MacInjector
from dualswitch.keyboard.keymap import (
    FLAG_COMMAND,
    FLAG_CONTROL,
    FLAG_OPTION,
    FLAG_SHIFT,
    HID_LALT,
    HID_LCTRL,
    HID_LGUI,
    HID_LSHIFT,
    HID_RALT,
    MOD_LALT,
    MOD_LCTRL,
    MOD_LGUI,
    MOD_LSHIFT,
    MOD_MAP_POSITION,
    MOD_MAP_SEMANTIC,
    cg_flags,
    usage_for_char,
    vk_for,
)
from dualswitch.keyboard.receiver import KeyboardReceiver
from dualswitch.keyboard.state import KeyEvent, KeyState
from dualswitch.sync import protocol as P


# --------------------------------------------------------------------------- #
# 假 Quartz
# --------------------------------------------------------------------------- #
class FakeEvent:
    def __init__(self, vk: int, down: bool) -> None:
        self.vk = vk
        self.down = down
        self.flags = 0


class FakeQuartz:
    kCGEventSourceStateHIDSystemState = 0
    kCGEventSourceStateCombinedSessionState = 1
    kCGHIDEventTap = 0
    kCGEventFlagMaskAlphaShift = 0x00010000

    def __init__(self, *, caps: bool = False, trusted: bool = True) -> None:
        self.posted: list[FakeEvent] = []
        self.caps = caps
        self._trusted = trusted
        self.post_fails = False

    def CGEventSourceCreate(self, state):  # noqa: N802, ARG002
        return object()

    def CGEventCreateKeyboardEvent(self, source, vk, down):  # noqa: N802, ARG002
        if self.post_fails:
            return None
        return FakeEvent(int(vk), bool(down))

    def CGEventSetFlags(self, event, flags):  # noqa: N802
        event.flags = int(flags)

    def CGEventPost(self, tap, event):  # noqa: N802, ARG002
        self.posted.append(event)

    def AXIsProcessTrusted(self):  # noqa: N802
        return self._trusted

    def CGEventSourceFlagsState(self, state):  # noqa: N802, ARG002
        return self.kCGEventFlagMaskAlphaShift if self.caps else 0


def make_receiver(**kw) -> tuple[KeyboardReceiver, FakeQuartz]:
    fake = FakeQuartz()
    injector = MacInjector(quartz=fake)
    return KeyboardReceiver(injector=injector, **kw), fake


def key(usage: int, down: bool, *, epoch: int = 0, seq: int = 0, mods: int = 0) -> dict:
    return P.key_header(epoch, seq, usage, down, mods)


# --------------------------------------------------------------------------- #
# 键位映射
# --------------------------------------------------------------------------- #
def test_letters_map_to_position_not_character():
    assert vk_for(0x04) == 0x00     # a
    assert vk_for(0x14) == 0x0C     # q
    assert vk_for(0x1D) == 0x06     # z
    assert vk_for(0x28) == 0x24     # Enter
    assert vk_for(0x2C) == 0x31     # Space


def test_position_mapping_puts_command_next_to_space():
    """用户的肌肉记忆：空格左边那颗（Win 键盘 Alt）= ⌘。"""
    assert vk_for(HID_LALT, MOD_MAP_POSITION) == 0x37        # kVK_Command
    assert vk_for(HID_RALT, MOD_MAP_POSITION) == 0x36        # kVK_RightCommand
    assert vk_for(HID_LGUI, MOD_MAP_POSITION) == 0x3A        # kVK_Option
    assert vk_for(HID_LCTRL) == 0x3B                         # kVK_Control
    assert vk_for(HID_LSHIFT) == 0x38                        # kVK_Shift


def test_semantic_mapping_is_the_other_way_round():
    assert vk_for(HID_LGUI, MOD_MAP_SEMANTIC) == 0x37        # Win → ⌘
    assert vk_for(HID_LALT, MOD_MAP_SEMANTIC) == 0x3A        # Alt → ⌥


def test_unmapped_usage_returns_none_instead_of_guessing():
    assert vk_for(0x00) is None          # 保留值
    assert vk_for(0xF0) is None          # 厂商自定义页


def test_flags_follow_the_remap():
    """按的是物理 Alt，注入的是 ⌘，flags 就必须报 ⌘ —— 否则应用看到 ⌥+⌘键码。"""
    assert cg_flags(MOD_LALT, MOD_MAP_POSITION) == FLAG_COMMAND
    assert cg_flags(MOD_LGUI, MOD_MAP_POSITION) == FLAG_OPTION
    assert cg_flags(MOD_LGUI, MOD_MAP_SEMANTIC) == FLAG_COMMAND
    assert cg_flags(MOD_LCTRL | MOD_LSHIFT) == FLAG_CONTROL | FLAG_SHIFT


def test_alt_c_char_table_is_consistent():
    """feeder 用字符表，运行时用 usage 表，两者必须指同一个键。"""
    assert usage_for_char("c") == (0x06, False)
    assert usage_for_char("C") == (0x06, True)
    assert usage_for_char("中") is None


# --------------------------------------------------------------------------- #
# 按键状态机
# --------------------------------------------------------------------------- #
def test_press_and_release_roundtrip():
    st = KeyState()
    assert st.apply(KeyEvent(0x04, True)) != []
    assert st.down_usages == frozenset({0x04})
    assert st.apply(KeyEvent(0x04, False)) != []
    assert st.down_usages == frozenset()


def test_repeat_is_dropped_when_dedupe_on():
    st = KeyState(dedupe_repeat=True)
    st.apply(KeyEvent(0x04, True))
    assert st.apply(KeyEvent(0x04, True)) == []
    assert st.repeats_dropped == 1


def test_repeat_is_forwarded_when_dedupe_off():
    """macOS 若不自动重复，就必须把源端的重复原样转发，否则长按只出一个字。"""
    st = KeyState(dedupe_repeat=False)
    st.apply(KeyEvent(0x04, True))
    assert st.apply(KeyEvent(0x04, True)) != []
    assert st.repeats_dropped == 0


def test_orphan_keyup_is_forwarded():
    """没记录过的抬起照样发：抬多了无害，抬少了就是卡键。"""
    st = KeyState()
    assert st.apply(KeyEvent(0xE1, False)) != []
    assert st.orphan_ups == 1


def test_release_all_clears_everything_and_keeps_order():
    st = KeyState()
    st.apply(KeyEvent(HID_LSHIFT, True))
    st.apply(KeyEvent(0x04, True))
    out = st.release_all()
    assert [e.usage for e in out] == [0x04, HID_LSHIFT]   # 逆序：修饰键最后抬
    assert st.down_usages == frozenset()
    assert st.release_all() == []                          # 幂等


def test_mods_bitmap_reflects_modifiers_only():
    st = KeyState()
    st.apply(KeyEvent(HID_LSHIFT, True))
    st.apply(KeyEvent(HID_LALT, True))
    st.apply(KeyEvent(0x04, True))
    assert st.mods_bitmap() == MOD_LSHIFT | MOD_LALT


# --------------------------------------------------------------------------- #
# 注入端
# --------------------------------------------------------------------------- #
def test_inject_dispatches_with_flags():
    rx, fake = make_receiver()
    assert rx.handle_key(key(0x04, True, seq=0))
    assert len(fake.posted) == 1
    assert fake.posted[0].vk == 0x00 and fake.posted[0].down is True


def test_shift_is_injected_as_a_real_modifier_key():
    rx, fake = make_receiver()
    rx.handle_key(key(HID_LSHIFT, True, seq=0, mods=MOD_LSHIFT))
    rx.handle_key(key(0x04, True, seq=1, mods=MOD_LSHIFT))
    assert [e.vk for e in fake.posted] == [0x38, 0x00]
    assert fake.posted[1].flags == FLAG_SHIFT


def test_stale_epoch_is_dropped():
    rx, fake = make_receiver()
    rx.handle_focus(P.focus_header(5, "mac"))
    assert not rx.handle_key(key(0x04, True, epoch=4, seq=0))
    assert fake.posted == []
    assert rx.stats["dropped_stale"] == 1


def test_sequence_regression_is_dropped():
    rx, _ = make_receiver()
    assert rx.handle_key(key(0x04, True, seq=7))
    assert not rx.handle_key(key(0x04, True, seq=7))
    assert rx.stats["dropped_stale"] == 1


def test_new_epoch_adopted_and_leftovers_released():
    rx, fake = make_receiver()
    rx.handle_key(key(0x04, True, epoch=1, seq=0))
    fake.posted.clear()
    rx.handle_key(key(0x05, True, epoch=2, seq=0))
    # 换 epoch 时先把上一轮残留抬掉，再注入新的
    assert [(e.vk, e.down) for e in fake.posted] == [(0x00, False), (0x0B, True)]


def test_unmapped_key_is_dropped_alone():
    rx, fake = make_receiver()
    assert not rx.handle_key(key(0xF0, True, seq=0))
    assert fake.posted == []
    assert rx.stats["dropped_unmapped"] == 1


def test_disabled_receiver_injects_nothing():
    rx, fake = make_receiver(enabled=False)
    assert not rx.handle_key(key(0x04, True))
    assert fake.posted == []
    assert rx.stats["dropped_disabled"] == 1


def test_focus_releases_held_keys():
    rx, fake = make_receiver()
    rx.handle_key(key(HID_LSHIFT, True, seq=0, mods=MOD_LSHIFT))
    rx.handle_key(key(0x04, True, seq=1, mods=MOD_LSHIFT))
    fake.posted.clear()
    released = rx.on_focus("windows")
    assert released == 2
    assert all(e.down is False for e in fake.posted)
    assert rx.state.down_usages == frozenset()


def test_on_focus_does_not_move_epoch():
    """epoch 只能由键盘所在端推进。本端跟着数会数出两套号，之后所有帧都被判过期。"""
    rx, _ = make_receiver()
    rx.handle_focus(P.focus_header(3, "mac"))
    rx.on_focus("windows")
    assert rx.epoch == 3


def test_resync_releases_only_extra_keys():
    rx, fake = make_receiver()
    rx.handle_key(key(HID_LSHIFT, True, seq=0, mods=MOD_LSHIFT))
    rx.handle_key(key(0x04, True, seq=1, mods=MOD_LSHIFT))
    fake.posted.clear()
    fixed = rx.handle_state({"e": 0, "down": [0x04]})
    assert fixed == 1
    assert [(e.vk, e.down) for e in fake.posted] == [(0x38, False)]
    assert rx.state.down_usages == frozenset({0x04})


def test_resync_never_presses_a_key_the_peer_claims_is_down():
    """对端说按着、本端没按 —— 绝不补按下。补错了是误输入。"""
    rx, fake = make_receiver()
    fixed = rx.handle_state({"e": 0, "down": [0x04, HID_LSHIFT]})
    assert fixed == 0
    assert fake.posted == []


def test_injection_failure_is_counted_not_raised():
    rx, fake = make_receiver()
    fake.post_fails = True
    assert rx.handle_key(key(0x04, True, seq=0))    # 帧本身合法
    assert rx.stats["injected"] == 0
    assert rx.injector.stats()["failed"] >= 1


# --------------------------------------------------------------------------- #
# 注入器本身
# --------------------------------------------------------------------------- #
def test_injector_reports_trust():
    assert MacInjector(quartz=FakeQuartz(trusted=True)).trusted() is True
    assert MacInjector(quartz=FakeQuartz(trusted=False)).trusted() is False


def test_caps_lock_alignment_only_toggles_when_needed():
    fake = FakeQuartz(caps=False)
    injector = MacInjector(quartz=fake)
    assert injector.set_caps_lock(False) is False        # 已经一致，不动手
    assert fake.posted == []
    assert injector.set_caps_lock(True) is True
    assert [(e.vk, e.down) for e in fake.posted] == [(0x39, True), (0x39, False)]


def test_caps_lock_state_is_readable():
    assert MacInjector(quartz=FakeQuartz(caps=True)).caps_lock_on() is True
    assert MacInjector(quartz=FakeQuartz(caps=False)).caps_lock_on() is False


def test_focus_frame_can_align_caps_lock():
    fake = FakeQuartz(caps=False)
    rx = KeyboardReceiver(injector=MacInjector(quartz=fake), align_caps=True)
    rx.handle_focus(P.focus_header(2, "mac", caps=True))
    assert [(e.vk, e.down) for e in fake.posted] == [(0x39, True), (0x39, False)]


# --------------------------------------------------------------------------- #
# 协议字段（两端必须一致，所以钉死名字）
# --------------------------------------------------------------------------- #
def test_key_header_fields():
    header = P.key_header(3, 9, 0x04, True, MOD_LSHIFT, ts_ms=120)
    assert header == {"e": 3, "s": 9, "u": 4, "d": 1, "m": MOD_LSHIFT, "t": 120}


def test_key_state_header_always_sends_down_list():
    header = P.key_state_header(1, 2, [0x04, 0x05], caps=True, rtt_ms=3.4)
    assert header == {"e": 1, "s": 2, "down": [4, 5], "caps": 1, "rtt": 3.4}


def test_key_frame_survives_the_wire():
    from dualswitch.sync.protocol import FrameReader, encode_frame

    raw = encode_frame(P.T_KEY, P.key_header(1, 2, 0x04, True, MOD_LSHIFT))
    frames = FrameReader(None).feed(raw)
    assert len(frames) == 1
    assert frames[0].type == P.T_KEY
    assert frames[0].header["u"] == 4 and frames[0].header["d"] == 1


# --------------------------------------------------------------------------- #
# 配置与接线
# --------------------------------------------------------------------------- #
def test_keyboard_follow_is_off_by_default():
    from dualswitch.config import DEFAULTS

    assert DEFAULTS.keyboard_follow.enabled is False
    assert DEFAULTS.keyboard_follow.mod_map == "position"    # 用户选的那套


def test_keyboard_follow_config_roundtrip():
    from dualswitch.config import from_dict

    cfg = from_dict({"keyboard_follow": {"enabled": True, "key_state_hz": 20}})
    assert cfg.keyboard_follow.enabled is True
    assert cfg.keyboard_follow.key_state_hz == 20


def test_release_chord_must_differ_from_switch_hotkey():
    """两者相同的话，按一下既切屏又停转发，用户会以为键盘坏了。"""
    from dualswitch.config import ConfigError, from_dict

    with pytest.raises(ConfigError, match="release_chord"):
        from_dict({"hotkey": "alt+space",
                   "keyboard_follow": {"enabled": True, "release_chord": "alt+space"}})


def test_mac_as_capture_host_is_rejected_while_unimplemented():
    from dualswitch.config import ConfigError, from_dict

    with pytest.raises(ConfigError, match="capture_host"):
        from_dict({"keyboard_follow": {"enabled": True, "capture_host": "mac"}})


def test_engine_routes_key_frames_to_the_receiver():
    """引擎只搬运：帧进来 → 回调 → 注入。这条守的是接线，不是逻辑。"""
    from dualswitch.config import ClipboardConfig
    from dualswitch.sync.engine import SyncEngine

    rx, fake = make_receiver()
    engine = SyncEngine(ClipboardConfig(), host_label="mac", on_key=rx.handle_key)
    engine._handle_key_frame(P.key_header(1, 0, 0x04, True))   # noqa: SLF001
    assert len(fake.posted) == 1
    assert engine.keyboard_stats["received"] == 1


def test_engine_swallows_receiver_errors():
    """注入端抛异常绝不能把链路带下去。"""
    from dualswitch.config import ClipboardConfig
    from dualswitch.sync.engine import SyncEngine

    def boom(header):  # noqa: ARG001
        raise RuntimeError("注入炸了")

    engine = SyncEngine(ClipboardConfig(), host_label="mac", on_key=boom)
    engine._handle_key_frame({"u": 4, "d": 1})   # noqa: SLF001 - 不该抛
    assert engine.keyboard_stats["received"] == 1


# --------------------------------------------------------------------------- #
# Windows 捕获端的键位表（数据是共享的，所以在两端都跑）
# --------------------------------------------------------------------------- #
def test_windows_vk_table_covers_every_letter_digit_and_modifier():
    """捕获端拿到的只有 vkCode，翻不回 usage 就等于那个键消失。

    字母/数字/修饰键是"少了立刻能感觉到"的部分，逐个钉住。
    """
    from dualswitch.keyboard.keymap import usage_for_vk

    for i in range(26):                       # A–Z：VK 0x41–0x5A ↔ usage 0x04–0x1D
        assert usage_for_vk(0x41 + i) == 0x04 + i
    for i in range(9):                        # 1–9
        assert usage_for_vk(0x31 + i) == 0x1E + i
    assert usage_for_vk(0x30) == 0x27         # 0
    # 修饰键必须区分左右：Windows 的 LL 钩子给的就是左右不同的 VK
    assert usage_for_vk(0xA2) == HID_LCTRL
    assert usage_for_vk(0xA3) == 0xE4
    assert usage_for_vk(0xA0) == HID_LSHIFT
    assert usage_for_vk(0xA1) == 0xE5
    assert usage_for_vk(0xA4) == HID_LALT
    assert usage_for_vk(0xA5) == HID_RALT
    assert usage_for_vk(0x5B) == HID_LGUI
    assert usage_for_vk(0x5C) == 0xE7


def test_windows_vk_table_round_trips_every_vk():
    """VK → usage → VK 必须回到同一个键码（反向表不许把键改到别处）。

    两个**有意合并**的别名不参与反向等价，单独在下面钉住：
    `0x31/0x32` 都是反斜杠位置、`0x28/0x58` 都是回车。
    """
    from dualswitch.keyboard.keymap import USAGE_TO_WIN_VK, VK_TO_USAGE

    for vk, usage in VK_TO_USAGE.items():
        assert USAGE_TO_WIN_VK[usage] == vk, f"VK 0x{vk:02X} 翻成了 usage 0x{usage:02X} 又翻丢"


def test_windows_vk_table_collapses_known_aliases():
    """别名合并是刻意的：注入端对这两组键的行为完全一致，不必区分。"""
    from dualswitch.keyboard.keymap import usage_for_vk

    assert usage_for_vk(0xDC) == 0x31, "Non-US #~ 按反斜杠位置处理（与 mac 侧一致）"
    assert usage_for_vk(0x0D) == 0x28, "小键盘回车识别成主回车"


def test_windows_vk_table_shares_the_vk_names_with_the_hotkey_parser():
    """热键解析器（`to_vk`）与捕获端的表必须是同一套 VK，否则白名单会失配。"""
    from dualswitch.hotkey.base import to_vk
    from dualswitch.keyboard.keymap import USAGE_TO_WIN_VK, usage_for_vk

    assert usage_for_vk(to_vk("k")) == 0x0E
    assert usage_for_vk(to_vk("space")) == 0x2C
    assert USAGE_TO_WIN_VK[0x0E] == to_vk("k")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
