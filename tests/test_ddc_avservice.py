"""IOAVService DDC 直读的纯函数部分：请求构造与回复解析。

实测样本（2026-10-24，Q2790R3 真机抓取，ground truth 由用户口述核对）：
    画面在 Windows → 518802006000000e0011cb（0x0011 = 17 = HDMI）
    画面在 Mac     → 518802006000000e0012c8（0x0012 = 18 = USB-C）
m1ddc 畸形校验和下显示器回的垃圾：6e80be00b5d8e9ab5f8afe（应判无效）。

纯函数、不碰硬件，任何平台都能跑。
"""

from __future__ import annotations

from dualswitch.ddc.avservice import (
    AVServiceI2C,
    build_get_vcp_request,
    build_set_vcp_request,
    parse_get_vcp_reply,
)

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


def test_run_exclusive_executes_under_lock():
    """run_exclusive 只是「持锁执行」：Set 通道用它和读事务互斥。"""
    av = AVServiceI2C()
    assert av.run_exclusive(lambda: 42) == 42
    av2 = AVServiceI2C()
    seen = []
    av2.run_exclusive(lambda: seen.append("in"))
    assert seen == ["in"]


# ---------------------------------------------------------------- Set 通道
def test_set_request_matches_real_frame():
    """Set-VCP 帧与真机发出去的一模一样：84 03 60 00 11 c9（目标 17）。

    校验和种子必须含 0x51 子地址（0x6E ^ 0x51），与 Get 那条 bug 同源。
    """
    assert build_set_vcp_request(0x60, 17) == bytes.fromhex("8403600011c9")
    assert build_set_vcp_request(0x60, 18) == bytes.fromhex("8403600012ca")


def _fake_av(external, ok=True):
    """造一个「两个句柄、只有 external 的 VCP 读能过校验」的替身。"""
    av = AVServiceI2C()
    av._handles = [object(), external]
    av._lib = ("ctypes",)          # 只要不是 False，candidates 就会给出句柄
    av._good = None

    def _read(code):
        if not ok:
            return None
        av._good = external        # 与真实实现一致：读成功就记住这个句柄
        return 18

    av._get_vcp_locked = _read
    return av


def test_external_handle_is_picked_by_vcp_reply():
    """内建屏代理对 I2C 也回 rc=0、也有合法 EDID —— 只有「VCP 回复能过校验」能区分。"""
    external = object()
    av = _fake_av(external)
    assert av._external_locked() is external
    assert av._good is external          # 记住这个句柄，后续写直接复用


def test_no_external_handle_without_vcp_reply():
    external = object()
    av = _fake_av(external, ok=False)
    assert av._external_locked() is None
    assert av._good is None


def test_set_vcp_targets_external_handle():
    external = object()
    av = _fake_av(external)
    writes: list[tuple[object, bytes]] = []
    av._i2c_write = lambda lib, handle, payload: (writes.append((handle, payload)) or 0)
    assert av.set_vcp(0x60, 18) is True
    assert writes == [(external, build_set_vcp_request(0x60, 18))]


def test_set_vcp_is_reentrant_inside_run_exclusive():
    """set_input 把「唤醒 + 写」包在 run_exclusive 里，组内又调 set_vcp：
    锁必须可重入，否则自己把自己锁死（真机上进程直接挂住被巡检杀掉）。"""
    import threading

    av = _fake_av(object())
    av._i2c_write = lambda lib, handle, payload: 0
    done: list[bool] = []
    worker = threading.Thread(target=lambda: done.append(av.run_exclusive(lambda: av.set_vcp(0x60, 17))))
    worker.start()
    worker.join(2.0)
    assert done == [True], "run_exclusive 内再进 set_vcp 被锁死"


def test_set_vcp_without_handle_is_false():
    av = _fake_av(object(), ok=False)
    assert av.set_vcp(0x60, 18) is False
