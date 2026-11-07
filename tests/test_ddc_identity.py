"""显示器身份解析（纯函数，不碰硬件）。"""

from __future__ import annotations

from dualswitch.ddc import identity
from dualswitch.ddc.identity import DisplayIdentity, parse_edid


def _edid(
    *,
    mfg: str = "AOC",
    pid: int = 9987,
    serial: int = 14094,
    name: str = "U27N3G6R4B",
) -> bytes:
    data = bytearray(128)
    data[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    raw = 0
    for ch in mfg:
        raw = (raw << 5) | (ord(ch) - 64)
    data[8] = (raw >> 8) & 0xFF
    data[9] = raw & 0xFF
    data[10] = pid & 0xFF
    data[11] = (pid >> 8) & 0xFF
    data[12:16] = serial.to_bytes(4, "little")
    data[16] = 32
    data[17] = 2022 - 1990
    data[54:59] = b"\x00\x00\x00\xfc\x00"
    encoded = name.encode("ascii")[:13].ljust(13, b" ")
    data[59:72] = encoded
    return bytes(data)


def test_parse_real_identity_fields():
    ident = parse_edid(_edid())
    assert ident is not None
    assert ident.mfg == "AOC"
    assert ident.product_code == 9987
    assert ident.key() == "AOC:9987"
    assert ident.name == "U27N3G6R4B"
    assert ident.serial == "14094"
    assert (ident.year, ident.week) == (2022, 32)


def test_parse_rejects_bad_header_and_builtin_mfg():
    assert parse_edid(b"\x00" * 128) is None
    assert parse_edid(_edid(mfg="AAA")[:64]) is None


def test_identity_label_contains_key_and_slot():
    ident = DisplayIdentity("AOC", 9987, name="U27N3G6R4B", slot="dispext0")
    assert "AOC:9987" in ident.label()
    assert "slot=dispext0" in ident.label()


def test_read_current_prefers_external_slot(monkeypatch):
    builtin = DisplayIdentity("XXX", 1, name="", slot="disp0")
    external = DisplayIdentity("AOC", 9987, name="U27N3G6R4B", slot="dispext0")
    monkeypatch.setattr(identity, "read_displays", lambda: [builtin, external])
    assert identity.read_current() is external


def test_read_current_without_display_returns_none(monkeypatch):
    monkeypatch.setattr(identity, "read_displays", lambda: [])
    assert identity.read_current() is None


# --------------------------------------------------------------------------- #
# Windows：设备接口名 → PnP 实例（2026-11-07 回归：一级枚举 DeviceID 是空串）
# --------------------------------------------------------------------------- #
def test_pnp_from_device_id_real_windows_string():
    raw = (
        "\\\\?\\DISPLAY#AOC2790#7&3ae6a500&1&UID524"
        "#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
    )
    assert identity.pnp_from_device_id(raw) == "DISPLAY\\AOC2790\\7&3ae6a500&1&UID524"


def test_pnp_from_device_id_rejects_non_display_and_empty():
    assert identity.pnp_from_device_id("") == ""
    assert identity.pnp_from_device_id("\\\\?\\PCI#VEN_1002&DEV_73DF#4&2c1f0a5&0&0008") == ""
    assert identity.pnp_from_device_id("\\\\?\\DISPLAY#AOC2790") == ""


def test_pnp_from_device_id_strips_prefix_by_length_not_charset():
    """`lstrip("\\\\?\\\\")` 是按字符集剥，遇到 `DISPLAY` 里没有的字符前会多吃；
    这里锁死「只剥前缀」。"""
    raw = "\\\\?\\DISPLAY#DELD0E6#1&28a6823a&8&UID256#{guid}"
    assert identity.pnp_from_device_id(raw) == "DISPLAY\\DELD0E6\\1&28a6823a&8&UID256"
