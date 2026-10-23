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
