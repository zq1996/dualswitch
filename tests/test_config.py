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


def test_unknown_key_rejected():
    with pytest.raises(ConfigError, match="未知的配置项"):
        from_dict({"hotkeys": "alt+space"})


def test_unknown_readback_key_rejected():
    with pytest.raises(ConfigError, match="未知的 readback 字段"):
        from_dict({"readback": {"delay": 300}})


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


def test_unreadable_config_is_quarantined_not_overwritten(tmp_path):
    """旧版程序读到新字段时，绝不能把用户配置静默覆盖成默认值（2026-10-30 事故）。

    现场：旧版 Config 没有 mouse_switch → from_dict 抛「未知的配置项」→
    load(strict=False) 吞成默认值 → ensure_device_id_persisted 又写回同一文件，
    槽位/配对/device_id 全丢，日志一个字都没有。
    """
    config_mod.take_load_warnings()          # 清掉别的用例留下的
    path = tmp_path / "config.json"
    # future_field = 「本版本还不认识的字段」，等价于 10-30 那次的 mouse_switch：
    # 旧程序读新配置 → 未知配置项 → 整份配置被判非法
    raw = {
        "hotkey": "ctrl+alt+f9",
        "future_field": {"enabled": True, "local_slot": 1, "peer_slot": 2},
        "clipboard": {"psk": "ab" * 32, "device_id": "windows-keepme"},
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg = config_mod.load(path, strict=False)

    # 1) 用默认值继续跑（不能让程序起不来）
    assert cfg.clipboard.device_id == ""
    # 2) 原文件一个字节都不能动
    assert json.loads(path.read_text(encoding="utf-8")) == raw
    # 3) 原件有备份，且备份里能找回用户的字段
    backups = list(tmp_path.glob("config.json.unreadable-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["clipboard"]["device_id"] == "windows-keepme"
    # 4) 警告必须能被看见，且带上真正的原因
    warnings = config_mod.take_load_warnings()
    assert len(warnings) == 1
    assert "future_field" in warnings[0] and backups[0].name in warnings[0]


def test_unreadable_config_refuses_every_later_save(tmp_path):
    """备份了还不够 —— **后续任何落盘都不能覆盖原件**（2026-11-06 二次事故）。

    现场：用 10-30 的旧包（不认识 keyboard_follow）启动 → 判"读不懂" → 备份 +
    默认值继续跑 → `ensure_device_id_persisted` 把默认值写回同一个文件 →
    配对 psk、鼠标槽位、device_id 全被抹掉，用户看到的就是「配对没了、槽位丢了」。
    """
    config_mod.take_load_warnings()
    path = tmp_path / "config.json"
    raw = {
        "hotkey": "alt+space",
        # 「将来才有的字段」= 旧版本程序读新配置时的真实形态
        "future_field": {"enabled": True},
        "mouse_switch": {"local_slot": 1, "peer_slot": 2},
        "clipboard": {"psk": "cd" * 32, "device_id": "windows-keepme"},
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg = config_mod.load(path, strict=False)
    assert getattr(cfg, "_unreadable", False) is True

    # ① 界面里点开关触发的落盘：必须拒绝
    with pytest.raises(ConfigError, match="不会覆盖"):
        config_mod.save(cfg, path)
    # ② 设备 ID 落盘（启动路径）：拒绝但不许炸，也不许改文件
    config_mod.ensure_device_id_persisted(cfg, path)
    # ③ 文件一个字节都没变
    assert json.loads(path.read_text(encoding="utf-8")) == raw
    config_mod.take_load_warnings()


def test_strict_load_still_raises_without_backup(tmp_path):
    """strict=True 是「报错给人看」的路径：不许悄悄备份、不许改文件。"""
    config_mod.take_load_warnings()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkeys": "alt+space"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="未知的配置项"):
        config_mod.load(path)
    assert not list(tmp_path.glob("config.json.unreadable-*"))
    assert config_mod.take_load_warnings() == []
