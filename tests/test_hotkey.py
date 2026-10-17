"""热键解析测试。"""

from __future__ import annotations

import sys

import pytest

from dualswitch.hotkey.base import (
    HotkeyError,
    parse_hotkey,
    to_mac_keycode,
    to_vk,
    to_win_modifiers,
)

IS_MAC = sys.platform == "darwin"


def test_parse_default():
    hk = parse_hotkey("alt+space")
    assert hk.modifiers == ("alt",)
    assert hk.key == "space"
    # describe() 按平台给叫法：Mac 键盘上没有 Alt 键
    assert hk.describe() == ("Option+Space" if IS_MAC else "Alt+Space")
    assert not hk.strict_right_alt


def test_parse_right_alt():
    hk = parse_hotkey("ralt+space")
    assert hk.strict_right_alt is True
    assert hk.has_alt is True
    assert hk.describe() == ("Right Option+Space" if IS_MAC else "Right Alt+Space")


@pytest.mark.skipif(not IS_MAC, reason="只在 macOS 上检查键名")
def test_mac_describe_never_says_alt():
    """mac 键帽上印的是 ⌥ Option，文案里出现 Alt 会让人找不到该按哪个键。"""
    for spec in ("alt+space", "ralt+space", "ctrl+alt+f9", "shift+alt+a"):
        assert "Alt" not in parse_hotkey(spec).describe()
        assert "Win" not in parse_hotkey(spec).describe()


def test_parse_case_and_spaces():
    hk = parse_hotkey("  CTRL + Shift + F9 ")
    assert hk.modifiers == ("ctrl", "shift")
    assert hk.key == "f9"


def test_parse_mac_aliases():
    hk = parse_hotkey("cmd+option+space")
    assert hk.modifiers == ("alt", "win")
    assert "Option" in hk.mac_describe()


def test_modifier_order_is_stable():
    assert parse_hotkey("shift+ctrl+alt+a").modifiers == ("ctrl", "alt", "shift")


def test_missing_modifier_rejected():
    with pytest.raises(HotkeyError, match="至少需要一个修饰键"):
        parse_hotkey("space")


def test_missing_key_rejected():
    with pytest.raises(HotkeyError, match="缺少主键"):
        parse_hotkey("alt+")


def test_two_keys_rejected():
    with pytest.raises(HotkeyError, match="只允许一个主键"):
        parse_hotkey("alt+a+b")


def test_unknown_key_rejected():
    with pytest.raises(HotkeyError, match="无法识别的主键"):
        parse_hotkey("alt+nosuchkey")


def test_duplicate_modifier_rejected():
    with pytest.raises(HotkeyError, match="重复"):
        parse_hotkey("alt+option+space")


def test_empty_rejected():
    with pytest.raises(HotkeyError):
        parse_hotkey("")


def test_vk_mapping():
    assert to_vk("space") == 0x20
    assert to_vk("a") == 0x41
    assert to_vk("7") == 0x37
    assert to_vk("f12") == 0x7B
    assert to_vk("pagedown") == 0x22


def test_win_modifiers():
    assert to_win_modifiers(parse_hotkey("alt+space")) == 0x0001
    # ralt 无法被 RegisterHotKey 区分，退化为 MOD_ALT
    assert to_win_modifiers(parse_hotkey("ralt+space")) == 0x0001
    assert to_win_modifiers(parse_hotkey("ctrl+shift+space")) == 0x0006


def test_mac_keycodes():
    assert to_mac_keycode("space") == 49
    assert to_mac_keycode("a") == 0
    assert to_mac_keycode("f1") == 122
    with pytest.raises(HotkeyError):
        to_mac_keycode("f13")
