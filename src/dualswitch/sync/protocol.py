"""线上协议：帧编解码 + 认证/加密 + `ClipItem` ↔ 报文映射。

规范见 `docs/CLIPBOARD-SYNC-CONTRACT.md` 第 4 节。**这个文件是两端唯一必须逐字节一致的部分**，
所以它不依赖任何平台相关的东西，也可以单独用 `tools/clip_sync_selftest.py` 压测。

    ┌────────┬────┬──────┬────────────┬──────────────┬───────────┐
    │ magic  │ver │ type │ header_len │    header    │  payload  │
    │ 4B     │1B  │ 1B   │ 4B (大端)  │ header_len B │ 剩余      │
    │ "DSYN" │ 1  │      │            │  UTF-8 JSON  │ 加密后为密文+16B tag │
    └────────┴────┴──────┴────────────┴──────────────┴───────────┘

⚠️ 帧里**没有总长度字段**（契约如此），payload 长度由 header 推出：
优先 `plen`，否则 `image.size`（图片之外的类型 payload 恒为空）。发送端总会写 `plen`，
接收端两种都认，所以对端的实现少写一个字段也不会错位 —— 这是有意留的兼容口子。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from ..clipboard.base import ClipItem, ImageData

MAGIC = b"DSYN"
VERSION = 1
PREFIX_SIZE = 10          # magic(4) + ver(1) + type(1) + header_len(4)
TAG_SIZE = 16
MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024

# 消息类型
T_HELLO = 0x01
T_HELLO_OK = 0x02
T_ONECLIP = 0x10
T_ACK = 0x11
T_PING = 0x20
T_PONG = 0x21
# 远端代发切屏（ISSUE-002）：请求对端代替本机发 DDC Set / 对端回执。
# 旧版本收到 0x30/0x31 会走「忽略未知帧」分支，不会断连 —— 这是有序升级的前提。
T_SWITCH = 0x30
T_SWITCH_ACK = 0x31
T_ERROR = 0x7F

TYPE_NAMES = {
    T_HELLO: "HELLO",
    T_HELLO_OK: "HELLO_OK",
    T_ONECLIP: "ONECLIP",
    T_ACK: "ACK",
    T_PING: "PING",
    T_PONG: "PONG",
    T_SWITCH: "SWITCH",
    T_SWITCH_ACK: "SWITCH_ACK",
    T_ERROR: "ERROR",
}

#: 配对用的固定盐。契约里写的是 `salt=device_id_A`，但那样两端算法不对称
#: （B 要先用 A 的 device_id 才能算 key），配对必须多做一次握手。
#: 这里改成固定串：两端输入同一个 PIN 就得到同一个 key，语义等价、实现简单一半。
#: 代价是无法给不同的对端配不同的 key —— 本工具只有"另一台"，不需要。
PAIR_SALT = b"dualswitch-v1-clipboard"
PBKDF2_ROUNDS = 200_000


class ProtocolError(ValueError):
    """帧格式错、解密失败、counter 重放。收到就应该断开这条连接。"""


# --------------------------------------------------------------------------- #
# 认证 / 加密
# --------------------------------------------------------------------------- #
def derive_key(pin: str) -> bytes:
    """6 位 PIN → 32 字节共享密钥（两端输入同一个 PIN 即得到同一个 key）。"""
    pin = str(pin).strip()
    if not pin:
        raise ValueError("PIN 不能为空")
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), PAIR_SALT, PBKDF2_ROUNDS, 32)


def new_nonce() -> str:
    return secrets.token_hex(16)


def auth_token(key: bytes, nonce: str) -> str:
    """HELLO 里带的一次性挑战应答，防重放（nonce 每次新生成）。"""
    return hmac.new(key, nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def _keystream(key: bytes, counter: int, length: int) -> bytes:
    out = bytearray()
    block = 0
    while len(out) < length:
        out += hashlib.sha256(
            key + counter.to_bytes(8, "big") + block.to_bytes(4, "big")
        ).digest()
        block += 1
    return bytes(out[:length])


def seal(key: bytes, plaintext: bytes, counter: int) -> bytes:
    """XOR 流加密 + 16 字节 HMAC 截断。够用级，不是 AEAD（见契约 4.4 的威胁模型）。"""
    ks = _keystream(key, counter, len(plaintext))
    cipher = bytes(a ^ b for a, b in zip(plaintext, ks))
    return cipher + hmac.new(key, cipher, hashlib.sha256).digest()[:TAG_SIZE]


def unseal(key: bytes, blob: bytes, counter: int) -> bytes:
    if len(blob) < TAG_SIZE:
        raise ProtocolError("密文太短，缺 HMAC")
    cipher, tag = blob[:-TAG_SIZE], blob[-TAG_SIZE:]
    expect = hmac.new(key, cipher, hashlib.sha256).digest()[:TAG_SIZE]
    if not hmac.compare_digest(tag, expect):
        raise ProtocolError("HMAC 校验失败（密钥不匹配或数据被篡改）")
    ks = _keystream(key, counter, len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, ks))


# --------------------------------------------------------------------------- #
# 报文
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Frame:
    type: int
    header: dict[str, Any]
    payload: bytes = b""

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, f"0x{self.type:02x}")

    def __str__(self) -> str:  # 日志用
        return f"<{self.type_name} {self.header.get('digest', '')[:8]} payload={len(self.payload)}B>"


def payload_length(header: dict[str, Any]) -> int:
    """按 header 推算 payload 明文长度（帧里没有总长度字段）。"""
    if "plen" in header:
        try:
            return int(header["plen"])
        except (TypeError, ValueError):
            raise ProtocolError(f"plen 非法：{header['plen']!r}")
    image = header.get("image") or {}
    try:
        return int(image.get("size") or 0)
    except (TypeError, ValueError):
        raise ProtocolError(f"image.size 非法：{image.get('size')!r}")


def encode_frame(
    msg_type: int, header: dict[str, Any], payload: bytes = b"", *, key: bytes | None = None,
    counter: int = 0,
) -> bytes:
    """把一条报文编成帧字节。`key=None` 时明文发送（排障用，header 里 `enc=false`）。"""
    header = dict(header)
    if key is None:
        header["enc"] = False
        header["plen"] = len(payload)
        body = payload
    else:
        header["enc"] = True
        header["ctr"] = int(counter)
        header["plen"] = len(payload)
        body = seal(key, payload, int(counter))
    raw_header = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw_header) > MAX_HEADER_BYTES:
        raise ProtocolError(f"header 过大：{len(raw_header)} 字节")
    return (
        MAGIC
        + bytes((VERSION, msg_type & 0xFF))
        + len(raw_header).to_bytes(4, "big")
        + raw_header
        + body
    )


class FrameReader:
    """流式解帧：TCP 上连续多帧，喂多少解多少。

    同时负责解密与 counter 单调校验（防重放）。一条连接一个实例。
    """

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key
        self._buf = bytearray()
        self._last_counter = -1
        self.encrypted = key is not None

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames: list[Frame] = []
        while True:
            frame = self._next()
            if frame is None:
                return frames
            frames.append(frame)

    def _next(self) -> Frame | None:
        if len(self._buf) < PREFIX_SIZE:
            return None
        if bytes(self._buf[:4]) != MAGIC:
            raise ProtocolError(f"magic 不匹配：{bytes(self._buf[:4])!r}")
        version = self._buf[4]
        if version != VERSION:
            raise ProtocolError(f"协议版本不支持：{version}")
        msg_type = self._buf[5]
        header_len = int.from_bytes(self._buf[6:10], "big")
        if header_len > MAX_HEADER_BYTES:
            raise ProtocolError(f"header_len 过大：{header_len}")
        if len(self._buf) < PREFIX_SIZE + header_len:
            return None

        raw_header = bytes(self._buf[PREFIX_SIZE:PREFIX_SIZE + header_len])
        try:
            header = json.loads(raw_header.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"header 不是合法 JSON：{exc}") from exc
        if not isinstance(header, dict):
            raise ProtocolError("header 必须是 JSON 对象")

        try:
            plain_len = payload_length(header)
        except ProtocolError:
            raise
        encrypted = bool(header.get("enc"))
        body_len = plain_len + (TAG_SIZE if encrypted else 0)
        if plain_len > MAX_PAYLOAD_BYTES:
            raise ProtocolError(f"payload 过大：{plain_len}")
        if len(self._buf) < PREFIX_SIZE + header_len + body_len:
            return None

        body = bytes(
            self._buf[PREFIX_SIZE + header_len:PREFIX_SIZE + header_len + body_len]
        )
        del self._buf[:PREFIX_SIZE + header_len + body_len]

        if encrypted:
            if self._key is None:
                raise ProtocolError("收到加密帧但本端没有密钥")
            counter = int(header.get("ctr", -1))
            if counter <= self._last_counter:
                raise ProtocolError(f"counter 未递增（{counter} <= {self._last_counter}），疑似重放")
            self._last_counter = counter
            body = unseal(self._key, body, counter)
        return Frame(type=msg_type, header=header, payload=body)

    def reset(self) -> None:
        self._buf.clear()
        self._last_counter = -1


# --------------------------------------------------------------------------- #
# ClipItem ↔ 报文
# --------------------------------------------------------------------------- #
def item_to_message(item: ClipItem) -> tuple[dict[str, Any], bytes]:
    """ClipItem → (header, payload)。文本放在 header 里，图片走 payload。"""
    header: dict[str, Any] = {
        "digest": item.digest,
        "ts": float(item.ts),
        "kind": item.kind,
    }
    if item.text:
        header["text"] = item.text
    if item.html:
        header["html"] = item.html
    if item.app:
        header["app"] = item.app
    if item.app_id:
        header["app_id"] = item.app_id
    # path 是本机路径，对端拿到也没用，不传
    payload = b""
    if item.image is not None:
        payload = item.image.png
        header["image"] = {
            "w": int(item.image.width),
            "h": int(item.image.height),
            "size": len(payload),
        }
    return header, payload


def message_to_item(header: dict[str, Any], payload: bytes) -> ClipItem:
    """(header, payload) → ClipItem。digest 用 header 里的（对端算好的），便于环抑制对齐。"""
    kind = header.get("kind")
    text = header.get("text") or None
    html = header.get("html") or None
    image = None
    meta = header.get("image")
    if meta:
        image = ImageData(
            png=payload,
            width=int(meta.get("w") or 0),
            height=int(meta.get("h") or 0),
        )
    if kind not in ("text", "image", "mixed"):
        # 对端给的类型不认识时按内容推断，别因为一个字段丢掉整条内容
        kind = "mixed" if (text and image) else ("image" if image else "text")
    item = ClipItem(
        kind=kind,
        text=text,
        html=html,
        image=image,
        ts=float(header.get("ts") or time.time()),
        source="remote",
        digest=str(header.get("digest") or ""),
        app=str(header.get("app") or ""),
        app_id=str(header.get("app_id") or ""),
    )
    return item


def hello_header(device_id: str, host_label: str, key: bytes | None, *, nonce: str | None = None) -> dict[str, Any]:
    nonce = nonce or new_nonce()
    return {
        "device": device_id,
        "host": host_label,
        "proto": VERSION,
        "nonce": nonce,
        "auth": auth_token(key, nonce) if key else "",
    }


def verify_hello(header: dict[str, Any], key: bytes | None) -> tuple[bool, str]:
    """校验对端 HELLO。返回 (是否通过, 原因)。"""
    nonce = str(header.get("nonce") or "")
    auth = str(header.get("auth") or "")
    if key is None:
        # 本端还没配对：不拒绝连接，只标记为「未认证」，引擎据此停用同步
        return (False, "本端未配对")
    if not nonce or not auth:
        return (False, "对端未配对")
    if not hmac.compare_digest(auth, auth_token(key, nonce)):
        return (False, "密钥不匹配（两端 PIN 不一致）")
    return (True, "ok")


def make_nonce() -> str:
    return new_nonce()


def random_pin() -> str:
    """6 位配对码（首位非 0，避免被当成八进制或被界面吞掉前导零）。"""
    return f"{secrets.randbelow(900_000) + 100_000}"


__all__ = [
    "MAGIC",
    "PBKDF2_ROUNDS",
    "PREFIX_SIZE",
    "PAIR_SALT",
    "VERSION",
    "Frame",
    "FrameReader",
    "ProtocolError",
    "T_ACK",
    "T_ERROR",
    "T_HELLO",
    "T_HELLO_OK",
    "T_ONECLIP",
    "T_PING",
    "T_PONG",
    "TYPE_NAMES",
    "auth_token",
    "derive_key",
    "encode_frame",
    "hello_header",
    "item_to_message",
    "make_nonce",
    "message_to_item",
    "payload_length",
    "random_pin",
    "seal",
    "unseal",
    "verify_hello",
]
