"""显示器 profile：内置表、匹配、降级与接线修复。"""

from __future__ import annotations

from dualswitch.config import from_dict, platform_defaults
from dualswitch.ddc import profiles
from dualswitch.ddc.identity import DisplayIdentity


def _identity(key: str = "AOC:9987", slot: str = "dispext0") -> DisplayIdentity:
    mfg, _, pid = key.partition(":")
    return DisplayIdentity(mfg, int(pid), name="U27N3G6R4B", slot=slot)


def test_port_aliases_and_labels():
    assert profiles.normalize_port("HDMI") == "hdmi1"
    assert profiles.normalize_port("usb-c") == "usb_c"
    assert profiles.normalize_port("DisplayPort") == "dp"
    assert profiles.normalize_port("nope") == ""
    assert profiles.port_label("usb_c") == "USB-C"
    assert profiles.port_label("dp") == "DisplayPort"


def test_builtin_aoc_10128_is_a_three_port_screen():
    """用户确认：Q2790R3 只有 HDMI / DP / USB-C 三个口（不是 4 口）。"""
    profile = profiles.BUILTIN_PROFILES["AOC:10128"]
    assert profile.items() == [("hdmi1", 17), ("dp", 15), ("usb_c", 18)]
    assert "3 口屏" in profile.notes


def test_builtin_aoc_9987_matches_measured_values():
    profile = profiles.BUILTIN_PROFILES["AOC:9987"]
    assert profile.items() == [
        ("hdmi1", 17),
        ("hdmi2", 17),
        ("dp", 15),
        ("usb_c", 100),
    ]
    assert profile.readback == "unreliable"


def test_resolve_active_prefers_builtin_for_known_screen():
    cfg = from_dict({})
    profile = profiles.resolve_active(cfg, _identity())
    assert profile.key == "AOC:9987"
    assert profile.ports["usb_c"] == 100
    # 接线仍是平台的默认：本机 USB-C、对端 HDMI 1
    assert cfg.wiring == {"local": "usb_c", "peer": "hdmi1"}
    assert cfg.target_value(cfg.effective_host()) == 100
    assert cfg.target_value(cfg.other_host()) == 17


def test_user_profile_overrides_builtin():
    cfg = from_dict(
        {
            "monitors": {
                "AOC:9987": {
                    "name": "U27N3G6R4B",
                    "ports": {"hdmi1": 18, "usb_c": 27},
                }
            }
        }
    )
    profile = profiles.resolve_active(cfg, _identity())
    assert profile.ports == {"hdmi1": 18, "usb_c": 27}
    assert profile.builtin is False


def test_unknown_screen_falls_back_to_default_without_error():
    cfg = from_dict({"sources": {"windows": 17, "mac": 18}})
    profile = profiles.resolve_active(cfg, _identity("DELL:1234"))
    assert profile.key == profiles.DEFAULT_KEY
    assert cfg.target_value(cfg.effective_host()) == 18
    assert cfg.target_value(cfg.other_host()) == 17


def test_no_identity_still_uses_default():
    cfg = from_dict({})
    profile = profiles.resolve_active(cfg, None)
    assert profile.key == profiles.DEFAULT_KEY
    assert cfg.active_ports() == [("hdmi1", 17), ("usb_c", 18)]


def test_repair_wiring_drops_ports_missing_from_profile():
    cfg = platform_defaults()
    cfg.wiring = {"local": "usb_c", "peer": "dp"}
    cfg.set_active_profile(
        profiles.MonitorProfile(key="X:1", name="X", ports={"hdmi1": 17, "usb_c": 18})
    )
    assert cfg.wiring["peer"] == "hdmi1", "这块屏没有 DP，接线应退回可用端口"
    assert cfg.wiring["local"] == "usb_c"


def test_sources_are_derived_from_profile_and_wiring():
    cfg = from_dict(
        {
            "monitors": {
                "AOC:9987": {"ports": {"hdmi1": 17, "usb_c": 100}},
            },
            "wiring": {"local": "usb_c", "peer": "hdmi1"},
        }
    )
    assert cfg.sources == {
        cfg.effective_host(): 100,
        cfg.other_host(): 17,
    }
