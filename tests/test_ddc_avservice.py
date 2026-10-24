"""IOAVService DDC 直读的纯函数部分：请求构造与回复解析。

实测样本（2026-10-24，Q2790R3 真机抓取，ground truth 由用户口述核对）：
    画面在 Windows → 518802006000000e0011cb（0x0011 = 17 = HDMI）
    画面在 Mac     → 518802006000000e0012c8（0x0012 = 18 = USB-C）
m1ddc 畸形校验和下显示器回的垃圾：6e80be00b5d8e9ab5f8afe（应判无效）。

纯函数、不碰硬件，任何平台都能跑。
"""

from __future__ import annotations

from dualswitch.ddc.avservice import build_get_vcp_request, parse_get_vcp_reply

# 2026-10-24 真机样本
FRAME_WINDOWS = bytes.fromhex("518802006000000e0011cb")   # 当前输入 17
FRAME_MAC = bytes.fromhex("518802006000000e0012c8")       # 当前输入 18
GARBAGE_M1DDC = bytes.fromhex("6e80be00b5d8e9ab5f8afe")   # 畸形请求下的回复


def _reseal(frame: bytearray) -> bytearray:
    """改完内容后按规范重算校验和（XOR bytes[0..9] ^ 0x6F）。"""
    csum = 0x6F
    for byte in frame[:10]:
        csum ^= byte
    frame[10] = csum & 0xFF
    return frame


def test_request_uses_vesa_checksum():
    """规范校验和 = 0x6E ^ 0x51 ^ 0x82 ^ 0x01 ^ 0x60 = 0xDC。m1ddc 缺 0x51 那一拍。"""
    assert build_get_vcp_request(0x60) == bytes([0x82, 0x01, 0x60, 0xDC])


def test_parse_real_windows_frame():
    assert parse_get_vcp_reply(FRAME_WINDOWS) == 17


def test_parse_real_mac_frame():
    assert parse_get_vcp_reply(FRAME_MAC) == 18


def test_parse_16bit_value():
    """当前值是 16 位：(cur_h << 8) | cur_l。"""
    frame = bytearray(FRAME_MAC)
    frame[8] = 0x01
    frame[9] = 0x27                          # 0x0127 = 295
    assert parse_get_vcp_reply(bytes(_reseal(frame))) == 295


def test_rejects_bad_checksum():
    frame = bytearray(FRAME_WINDOWS)
    frame[10] ^= 0xFF
    assert parse_get_vcp_reply(bytes(frame)) is None


def test_rejects_m1ddc_garbage_reply():
    """m1ddc 畸形请求下显示器回的垃圾必须判无效 —— 这就是 -118 的真面目。"""
    assert parse_get_vcp_reply(GARBAGE_M1DDC) is None


def test_rejects_error_result_code():
    """RC != 0（0x01 = 不支持该 VCP）时即使校验和过也不算有效读数。"""
    frame = bytearray(FRAME_WINDOWS)
    frame[3] = 0x01
    assert parse_get_vcp_reply(bytes(_reseal(frame))) is None


def test_rejects_wrong_vcp_echo():
    frame = bytearray(FRAME_WINDOWS)
    frame[4] = 0x61                          # 回显与请求不一致
    assert parse_get_vcp_reply(bytes(_reseal(frame))) is None


def test_rejects_wrong_length():
    assert parse_get_vcp_reply(FRAME_WINDOWS[:10]) is None
    assert parse_get_vcp_reply(FRAME_WINDOWS + b"\x00") is None
    assert parse_get_vcp_reply(b"") is None
