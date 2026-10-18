"""线上协议单测：帧编解码、加密、counter 防重放、ClipItem 映射。

这些用例不碰网络，也不碰剪贴板，任何平台任何环境都能跑。
"""

from __future__ import annotations

import json

import pytest

from dualswitch.clipboard import make_image, make_text
from dualswitch.sync import protocol as P


def _png(n: int = 32) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + bytes(range(256))[: n % 256] * 1


def test_frame_roundtrip_plain():
    raw = P.encode_frame(P.T_ONECLIP, {"digest": "abc", "text": "你好"}, b"")
    reader = P.FrameReader(None)
    frames = reader.feed(raw)
    assert len(frames) == 1
    f = frames[0]
    assert f.type == P.T_ONECLIP
    assert f.header["digest"] == "abc"
    assert f.header["enc"] is False
    assert f.header["plen"] == 0
    assert f.payload == b""


def test_frame_with_image_payload():
    payload = _png(200)
    header = {"digest": "d1", "kind": "image", "image": {"w": 2, "h": 2, "size": len(payload)}}
    raw = P.encode_frame(P.T_ONECLIP, header, payload)
    frames = P.FrameReader(None).feed(raw)
    assert frames[0].payload == payload


def test_plain_frame_accepts_header_without_plen():
    """对端实现少写 plen 时，长度要能按 image.size 推出来（兼容口子）。"""
    raw = P.encode_frame(P.T_ONECLIP, {"digest": "x", "image": {"size": 10}}, b"0123456789")
    # 手动去掉 plen，模拟另一端的实现
    header_len = int.from_bytes(raw[6:10], "big")
    header = json.loads(raw[10:10 + header_len])
    header.pop("plen")
    new_header = json.dumps(header).encode()
    rebuilt = (
        raw[:6]
        + len(new_header).to_bytes(4, "big")
        + new_header
        + raw[10 + header_len:]
    )
    frames = P.FrameReader(None).feed(rebuilt)
    assert frames[0].payload == b"0123456789"


def test_fragmented_feed():
    """TCP 上不会有帧边界，逐字节喂也要能解出来。"""
    key = P.derive_key("123456")
    raw = P.encode_frame(P.T_ONECLIP, {"digest": "z", "text": "分片"}, b"abc", key=key, counter=0)
    reader = P.FrameReader(key)
    got = []
    for byte in raw:
        got.extend(reader.feed(bytes([byte])))
    assert len(got) == 1
    assert got[0].payload == b"abc"


def test_multiple_frames_in_one_chunk():
    key = P.derive_key("123456")
    raw = b"".join(
        P.encode_frame(P.T_ACK, {"digest": str(i), "ok": True}, b"", key=key, counter=i)
        for i in range(5)
    )
    frames = P.FrameReader(key).feed(raw)
    assert [f.header["digest"] for f in frames] == ["0", "1", "2", "3", "4"]


def test_bad_magic_rejected():
    with pytest.raises(P.ProtocolError, match="magic"):
        P.FrameReader(None).feed(b"XXXX" + b"\x01" + b"\x10" + b"\x00\x00\x00\x02" + b"{}")


def test_wrong_key_raises():
    key1, key2 = P.derive_key("111111"), P.derive_key("222222")
    raw = P.encode_frame(P.T_ONECLIP, {"digest": "a"}, b"secret", key=key1, counter=0)
    with pytest.raises(P.ProtocolError, match="HMAC"):
        P.FrameReader(key2).feed(raw)


def test_replay_counter_rejected():
    key = P.derive_key("123456")
    first = P.encode_frame(P.T_ACK, {"digest": "a"}, b"", key=key, counter=5)
    second = P.encode_frame(P.T_ACK, {"digest": "b"}, b"", key=key, counter=5)
    reader = P.FrameReader(key)
    reader.feed(first)
    with pytest.raises(P.ProtocolError, match="counter"):
        reader.feed(second)


def test_encrypted_frame_from_plain_reader_rejected():
    key = P.derive_key("123456")
    raw = P.encode_frame(P.T_ACK, {"digest": "a"}, b"", key=key, counter=0)
    with pytest.raises(P.ProtocolError, match="没有密钥"):
        P.FrameReader(None).feed(raw)


def test_seal_unseal_roundtrip():
    key = P.derive_key("999999")
    data = "中文 payload".encode() * 100
    blob = P.seal(key, data, 7)
    assert blob != data
    assert P.unseal(key, blob, 7) == data


def test_derive_key_is_stable_and_pin_specific():
    assert P.derive_key("123456") == P.derive_key("123456")
    assert P.derive_key("123456") != P.derive_key("123457")
    assert len(P.derive_key("123456")) == 32
    with pytest.raises(ValueError):
        P.derive_key("  ")


def test_verify_hello():
    key = P.derive_key("123456")
    header = P.hello_header("mac-1", "mac", key)
    assert P.verify_hello(header, key) == (True, "ok")
    # 密钥不同 → 拒绝
    ok, reason = P.verify_hello(header, P.derive_key("654321"))
    assert ok is False and "密钥" in reason
    # 本端未配对 → 不通过，但不抛
    ok, reason = P.verify_hello(header, None)
    assert ok is False and "未配对" in reason


def test_item_message_roundtrip_text():
    item = make_text("跨端文本", html="<b>跨端文本</b>", source="mac")
    header, payload = P.item_to_message(item)
    assert payload == b""
    back = P.message_to_item(header, payload)
    assert back.text == item.text
    assert back.html == item.html
    assert back.kind == "text"
    assert back.digest == item.digest
    assert back.source == "remote"


def test_item_message_roundtrip_image():
    png = _png(64)
    item = make_image(png, 4, 4)
    assert item is not None
    header, payload = P.item_to_message(item)
    assert header["image"]["size"] == len(png)
    back = P.message_to_item(header, payload)
    assert back.image is not None
    assert back.image.png == png
    assert (back.image.width, back.image.height) == (4, 4)
    assert back.kind == "image"


def test_item_message_roundtrip_mixed():
    png = _png(16)
    item = make_image(png, 2, 2, text="附图说明")
    assert item is not None and item.kind == "mixed"
    header, payload = P.item_to_message(item)
    back = P.message_to_item(header, payload)
    assert back.kind == "mixed"
    assert back.text == "附图说明"
    assert back.image is not None


def test_message_to_item_guesses_kind_when_missing():
    png = _png(8)
    back = P.message_to_item({"image": {"w": 1, "h": 1, "size": len(png)}}, png)
    assert back.kind == "image"


def test_payload_length_prefers_plen():
    assert P.payload_length({"plen": 12, "image": {"size": 99}}) == 12
    assert P.payload_length({"image": {"size": 99}}) == 99
    assert P.payload_length({}) == 0
    with pytest.raises(P.ProtocolError):
        P.payload_length({"plen": "abc"})


def test_random_pin_is_six_digits():
    for _ in range(50):
        pin = P.random_pin()
        assert len(pin) == 6 and pin.isdigit() and pin[0] != "0"


def test_type_names_cover_all_types():
    for msg_type in (P.T_HELLO, P.T_HELLO_OK, P.T_ONECLIP, P.T_ACK, P.T_PING, P.T_PONG, P.T_ERROR):
        assert msg_type in P.TYPE_NAMES
