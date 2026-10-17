"""CLI 冒烟测试：不碰硬件，只验证参数解析与配置子命令。"""

from __future__ import annotations

import pytest

from dualswitch.cli import build_parser, main


def test_no_args_prints_help(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "run" in out and "probe" in out


def test_version_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "dualswitch" in capsys.readouterr().out


def test_config_path_without_flag(capsys):
    """回归：子命令未传 --config 时不能 AttributeError。"""
    assert main(["config", "path"]) == 0
    assert "config.json" in capsys.readouterr().out


def test_config_init_and_show(tmp_path, capsys):
    target = tmp_path / "cfg.json"
    assert main(["config", "init", "--config", str(target)]) == 0
    assert target.exists()
    assert main(["config", "show", "--config", str(target)]) == 0
    out = capsys.readouterr().out
    assert '"windows": 17' in out
    assert '"mac": 18' in out


def test_config_init_refuses_overwrite(tmp_path, capsys):
    target = tmp_path / "cfg.json"
    assert main(["config", "init", "--config", str(target)]) == 0
    assert main(["config", "init", "--config", str(target)]) == 1
    assert "已存在" in capsys.readouterr().out
    assert main(["config", "init", "--config", str(target), "--force"]) == 0


def test_config_show_missing(tmp_path, capsys):
    assert main(["config", "show", "--config", str(tmp_path / "nope.json")]) == 1
    assert "不存在" in capsys.readouterr().out


def test_parser_defaults():
    args = build_parser().parse_args(["run"])
    assert args.command == "run"
    assert args.no_tray is False
    assert not hasattr(args, "config")  # SUPPRESS：未传就不该有该属性


def test_switch_accepts_targets():
    parser = build_parser()
    for target in ("other", "windows", "mac", "toggle"):
        assert parser.parse_args(["switch", "--to", target]).to == target
