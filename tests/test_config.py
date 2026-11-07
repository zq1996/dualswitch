"""配置解析 / 校验测试。"""

from __future__ import annotations

import json

import pytest

from dualswitch import config as config_mod
from dualswitch.config import ConfigError, ReadbackConfig, from_dict


def test_defaults_are_valid():
    cfg = from_dict({})
    assert cfg.sources == {"windows": 17, "mac": 18}
    assert cfg.vcp_code == 0x60
    assert cfg.effective_host() in ("windows", "mac")
    assert cfg.other_host() != cfg.effective_host()


def test_partial_override_keeps_defaults():
    cfg = from_dict({"hotkey": "ralt+space", "sources": {"mac": 18}})
    assert cfg.hotkey == "ralt+space"
    assert cfg.sources["windows"] == 17
    assert cfg.sources["mac"] == 18
    # 用默认值本身做期望：回读延迟要跟着「显示器切换耗时」调整，
    # 写死 300 会在改默认值时假失败
    assert cfg.readback.delay_ms == ReadbackConfig().delay_ms


def test_readback_merge():
    cfg = from_dict({"readback": {"enabled": False, "delay_ms": 250}})
    assert cfg.readback.enabled is False
    assert cfg.readback.delay_ms == 250          # 显式给的值必须覆盖默认
    assert cfg.readback.timeout_ms == ReadbackConfig().timeout_ms


def test_readback_timeout_exceeds_delay():
    """回读总预算必须大于首次等待，否则一轮都读不到（静默期 + 切换耗时）。"""
    rb = ReadbackConfig()
    assert rb.timeout_ms > rb.delay_ms, (
        f"timeout_ms({rb.timeout_ms}) 必须大于 delay_ms({rb.delay_ms})，否则回读永远不执行"
    )
    assert rb.delay_ms >= 2500, (
        "Q2790PC 实测切换要 2–3 秒，回读太早会在切换进行中插事务，可能打断切换"
    )


def test_unknown_key_is_kept_not_rejected():
    """拼错/未来字段都不许让整份配置失效（2026-11-06 事故）。"""
    cfg = from_dict({"hotkeys": "alt+space"})
    assert cfg.hotkey == from_dict({}).hotkey       # 拼错的键不生效
    assert cfg._extra == {"hotkeys": "alt+space"}   # 但也不丢


def test_unknown_readback_key_is_kept():
    cfg = from_dict({"readback": {"delay": 300}})
    assert cfg._extra["readback"] == {"delay": 300}
    assert config_mod.Config().readback.delay_ms != 300


def test_unknown_key_can_be_rejected_explicitly():
    """需要「有没有不认识的键」的显式检查时才报错（keep_unknown=False）。"""
    with pytest.raises(ConfigError, match="未知的配置项"):
        from_dict({"hotkeys": "alt+space"}, keep_unknown=False)
    with pytest.raises(ConfigError, match="未知的 readback 字段"):
        from_dict({"readback": {"delay": 300}}, keep_unknown=False)


def test_same_source_values_rejected():
    with pytest.raises(ConfigError, match="不能相同"):
        from_dict({"sources": {"windows": 17, "mac": 17}})


def test_unknown_host_rejected():
    with pytest.raises(ConfigError, match="未知主机"):
        from_dict({"sources": {"windows": 17, "linux": 5}})


def test_vcp_range():
    with pytest.raises(ConfigError, match="vcp_code"):
        from_dict({"vcp_code": 300})


def test_bad_hotkey_rejected():
    with pytest.raises(ConfigError, match="hotkey"):
        from_dict({"hotkey": "space"})


def test_bad_enum_rejected():
    with pytest.raises(ConfigError, match="switch_mode"):
        from_dict({"switch_mode": "random"})
    with pytest.raises(ConfigError, match="ddc_backend"):
        from_dict({"ddc_backend": "magic"})


def test_command_backend_requires_placeholder():
    with pytest.raises(ConfigError, match="占位符"):
        from_dict({"ddc_backend": "command", "command_template": ["ddcutil", "setvcp", "60", "17"]})
    cfg = from_dict({"ddc_backend": "command", "command_template": ["ddcutil", "setvcp", "60", "{value}"]})
    assert cfg.command_template[-1] == "{value}"


def test_bool_string_coercion():
    cfg = from_dict({"tray": "false", "notify": "0", "dry_run": "yes"})
    assert cfg.tray is False
    assert cfg.notify is False
    assert cfg.dry_run is True


def test_save_and_load(tmp_path):
    path = tmp_path / "config.json"
    cfg = from_dict({"hotkey": "ctrl+alt+f9", "sources": {"windows": 18, "mac": 27}})
    config_mod.save(cfg, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["hotkey"] == "ctrl+alt+f9"
    loaded = config_mod.load(path)
    assert loaded.hotkey == "ctrl+alt+f9"
    assert loaded.sources["windows"] == 18


def test_load_missing_returns_defaults(tmp_path):
    cfg = config_mod.load(tmp_path / "nope.json")
    # 默认值按平台走：Windows 是 alt+space，macOS 是右 Option（ralt+space）
    default = config_mod.platform_defaults()
    assert cfg.hotkey == default.hotkey
    assert cfg.sources == default.sources


def test_load_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="合法 JSON"):
        config_mod.load(path)


def test_ensure_config_file(tmp_path):
    path, created = config_mod.ensure_config_file(tmp_path / "c.json")
    assert created is True and path.exists()
    _, created2 = config_mod.ensure_config_file(path)
    assert created2 is False


def test_unknown_fields_are_preserved_on_roundtrip(tmp_path):
    """不认识的字段不能丢（2026-11-06 事故的直接原因）。

    现场：老版程序（Config 里没有 keyboard_follow）读新配置 → 整份判非法 →
    默认值接管 → 再存盘，于是 mouse_switch 的槽位（本机 2 / 对端 1）、配对 psk、
    device_id 全被抹掉，只有一个「从默认值开始」的配置文件留在原地。

    新语义：不认识的字段收进 `_extra`，落盘时原样写回；只有取值真的非法才降级。
    """
    config_mod.take_load_warnings()
    path = tmp_path / "config.json"
    raw = {
        "hotkey": "ctrl+alt+f9",
        "future_field": {"enabled": True, "local_slot": 1, "peer_slot": 2},
        "mouse_switch": {"enabled": True, "local_slot": 2, "peer_slot": 1, "future_sub": 7},
        "clipboard": {"psk": "ab" * 32, "device_id": "windows-keepme"},
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg = config_mod.load(path, strict=False)

    # 1) 认识的字段照常生效（不是「整份退回默认值」）
    assert cfg.hotkey == "ctrl+alt+f9"
    assert cfg.mouse_switch.enabled is True
    assert cfg.mouse_switch.local_slot == 2
    assert cfg.clipboard.device_id == "windows-keepme"
    # 2) 不认识的字段收好了，且能报出来
    assert cfg._extra["future_field"] == {"enabled": True, "local_slot": 1, "peer_slot": 2}
    assert cfg._extra["mouse_switch"] == {"future_sub": 7}
    assert cfg._degraded is False
    assert not list(tmp_path.glob("config.json.unreadable-*"))
    warnings = config_mod.take_load_warnings()
    assert len(warnings) == 1 and "future_field" in warnings[0]

    # 3) 存盘后两个未来字段都还在
    config_mod.save(cfg, path)
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back["future_field"] == {"enabled": True, "local_slot": 1, "peer_slot": 2}
    assert back["mouse_switch"]["future_sub"] == 7
    assert back["mouse_switch"]["local_slot"] == 2
    assert back["clipboard"]["device_id"] == "windows-keepme"


def test_invalid_value_is_quarantined_and_never_written_back(tmp_path):
    """取值非法（不是「不认识」）才降级；降级后绝不许把默认值写回用户文件。"""
    config_mod.take_load_warnings()
    path = tmp_path / "config.json"
    raw = {
        "mouse_switch": {"enabled": True, "local_slot": 9, "peer_slot": 1},  # 越界
        "clipboard": {"psk": "ab" * 32, "device_id": "mac-keepme"},
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg = config_mod.load(path, strict=False)

    assert cfg._degraded is True
    assert json.loads(path.read_text(encoding="utf-8")) == raw      # 原件一字未动
    backups = list(tmp_path.glob("config.json.unreadable-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["clipboard"]["device_id"] == "mac-keepme"

    # 降级状态下的任何落盘都必须被拦住，否则等于用默认值替用户删配置
    cfg.hotkey = "ctrl+alt+f10"
    with pytest.raises(ConfigError, match="拒绝覆盖"):
        config_mod.save(cfg, path)
    assert json.loads(path.read_text(encoding="utf-8")) == raw

    # 设备 ID 靠旁边的小文件稳定下来，重启后对端还认得这台机器
    first = config_mod.ensure_device_id_persisted(cfg, path)
    assert first == cfg.clipboard.device_id
    assert json.loads(path.read_text(encoding="utf-8")) == raw
    again = config_mod.load(path, strict=False)
    assert again.clipboard.device_id == first


def test_unreadable_config_refuses_every_later_save(tmp_path):
    """备份了还不够 —— **后续任何落盘都不能覆盖原件**（2026-11-06 二次事故）。

    现场：用 10-30 的旧包（不认识 keyboard_follow）启动 → 判"读不懂" → 备份 +
    默认值继续跑 → `ensure_device_id_persisted` 把默认值写回同一个文件 →
    配对 psk、鼠标槽位、device_id 全被抹掉，用户看到的就是「配对没了、槽位丢了」。

    注意「读不懂」现在**只指非法取值**：不认识的字段一律收进 `_extra` 保留
    （见 `test_strict_load_keeps_unknown_field_too`），所以这里用越界的 local_slot
    触发降级，不能用「未来字段」——那个已经不报错了。
    """
    config_mod.take_load_warnings()
    path = tmp_path / "config.json"
    raw = {
        "hotkey": "alt+space",
        # 开启跟随 + 越界槽位 = 真正「取值非法」（不认识的字段现在只会进 _extra）
        "mouse_switch": {"enabled": True, "local_slot": 9, "peer_slot": 2},
        "clipboard": {"psk": "cd" * 32, "device_id": "windows-keepme"},
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg = config_mod.load(path, strict=False)
    # 两套标记都要亮：`_unreadable`（Windows 侧）与 `_degraded`（macOS 侧）
    assert getattr(cfg, "_unreadable", False) is True
    assert cfg._degraded is True

    # ① 界面里点开关触发的落盘：必须拒绝
    with pytest.raises(ConfigError, match="拒绝覆盖"):
        config_mod.save(cfg, path)
    # ② 设备 ID 落盘（启动路径）：走 sidecar 小文件，不许炸、不许改配置
    config_mod.ensure_device_id_persisted(cfg, path)
    # ③ 文件一个字节都没变
    assert json.loads(path.read_text(encoding="utf-8")) == raw
    config_mod.take_load_warnings()


def test_save_keeps_previous_config_as_bak(tmp_path):
    """每次落盘前留一份上一版，覆盖事故至少有东西可回滚。"""
    path = tmp_path / "config.json"
    cfg = from_dict({"hotkey": "ctrl+alt+f9"})
    config_mod.save(cfg, path)
    assert not (tmp_path / "config.json.bak").exists()   # 第一次没有「上一版」

    cfg.hotkey = "ctrl+alt+f10"
    config_mod.save(cfg, path)
    bak = tmp_path / "config.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text(encoding="utf-8"))["hotkey"] == "ctrl+alt+f9"
    assert json.loads(path.read_text(encoding="utf-8"))["hotkey"] == "ctrl+alt+f10"


def test_save_does_not_backup_a_corrupt_file(tmp_path):
    """现文件已经坏了，别拿它把上一份好备份盖掉。"""
    path = tmp_path / "config.json"
    bak = tmp_path / "config.json.bak"
    bak.write_text(json.dumps({"hotkey": "ctrl+alt+f9"}), encoding="utf-8")
    path.write_text("{not json", encoding="utf-8")

    config_mod.save(from_dict({}), path)

    assert json.loads(bak.read_text(encoding="utf-8"))["hotkey"] == "ctrl+alt+f9"


def test_display_power_defaults_and_partial_merge():
    """外接屏省电默认开：画面不在本机时不再为它渲染（2026-11-06 发烫问题）。"""
    cfg = from_dict({})
    assert cfg.display_power.enabled is True
    assert cfg.display_power.off_delay_ms == 2500
    assert cfg.display_power.probe_interval_ms == 1500

    cfg = from_dict({"display_power": {"enabled": False, "off_delay_ms": 0}})
    assert cfg.display_power.enabled is False
    assert cfg.display_power.off_delay_ms == 0
    assert cfg.display_power.probe_interval_ms == 1500   # 没写的用默认值


def test_display_power_rejects_silly_probe_interval():
    with pytest.raises(ConfigError, match="probe_interval_ms"):
        from_dict({"display_power": {"probe_interval_ms": 10}})


def test_strict_load_keeps_unknown_field_too(tmp_path):
    """strict 只决定「读不懂要不要抛」，不认识字段一律保留（settings 存盘也走这条）。"""
    config_mod.take_load_warnings()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"future_field": 1, "hotkey": "alt+space"}), encoding="utf-8")
    cfg = config_mod.load(path)
    assert cfg._extra == {"future_field": 1}
    assert cfg.hotkey == "alt+space"
    assert not list(tmp_path.glob("config.json.unreadable-*"))
    assert len(config_mod.take_load_warnings()) == 1


def test_strict_load_raises_on_invalid_value_without_backup(tmp_path):
    """strict=True 是「报错给人看」的路径：不许悄悄备份、不许改文件。"""
    config_mod.take_load_warnings()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"mouse_switch": {"enabled": True, "local_slot": 9, "peer_slot": 1}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="local_slot"):
        config_mod.load(path)
    assert not list(tmp_path.glob("config.json.unreadable-*"))
    assert config_mod.take_load_warnings() == []


# --------------------------------------------------------------------------- #
# V3：旧 sources → profile + wiring
# --------------------------------------------------------------------------- #
def test_legacy_sources_migrate_to_default_profile():
    cfg = from_dict({"sources": {"windows": 17, "mac": 18}})
    assert cfg.monitors["default"].ports == {"hdmi1": 17, "usb_c": 18}
    local_port = "hdmi1" if cfg.effective_host() == "windows" else "usb_c"
    assert cfg.wiring["local"] == local_port
    # 迁移只搬「哪台机器接哪个口」，码值跟着那台机器走：Windows 17、Mac 18
    assert cfg.target_value("windows") == 17
    assert cfg.target_value("mac") == 18


def test_new_profile_allows_same_code_on_different_ports():
    """AOC U27N3G6R4B 的 HDMI1/HDMI2 都是 17，旧校验不能误伤 profile。"""
    cfg = from_dict(
        {
            "monitors": {
                "AOC:9987": {"ports": {"hdmi1": 17, "hdmi2": 17, "usb_c": 100}},
            },
            "wiring": {"local": "usb_c", "peer": "hdmi1"},
        }
    )
    assert cfg.active_profile().ports["hdmi2"] == 17
    assert cfg.target_value(cfg.other_host()) == 17


def test_profile_unknown_subfield_survives_save_and_load(tmp_path):
    path = tmp_path / "config.json"
    raw = {
        "monitors": {
            "AOC:9987": {
                "name": "U27N3G6R4B",
                "ports": {"usb_c": 100, "hdmi1": 17},
                "future_profile_field": 7,
            }
        },
        "wiring": {"local": "usb_c", "peer": "hdmi1"},
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg = config_mod.load(path)
    assert cfg.monitors["AOC:9987"].ports["usb_c"] == 100
    assert cfg._extra["monitors"]["AOC:9987"] == {"future_profile_field": 7}

    config_mod.save(cfg, path)
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back["monitors"]["AOC:9987"]["future_profile_field"] == 7


def test_to_dict_keeps_legacy_sources_in_sync():
    cfg = from_dict(
        {
            "monitors": {"AOC:9987": {"ports": {"hdmi1": 17, "usb_c": 100}}},
            "wiring": {"local": "usb_c", "peer": "hdmi1"},
        }
    )
    data = cfg.to_dict()
    assert data["sources"] == {
        cfg.effective_host(): 100,
        cfg.other_host(): 17,
    }


def test_same_effective_values_rejected():
    with pytest.raises(ConfigError, match="切换无效果"):
        from_dict(
            {
                "monitors": {"AOC:9987": {"ports": {"hdmi1": 17, "hdmi2": 17}}},
                "wiring": {"local": "hdmi1", "peer": "hdmi2"},
            }
        )
