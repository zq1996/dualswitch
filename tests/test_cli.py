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


def test_logging_falls_back_when_default_log_unwritable(tmp_path, monkeypatch):
    """默认日志路径写不了时必须改写到临时目录。

    打包版（--windowed）没有控制台，`setup()` 里如果只是
    `root.warning(...)` 就完事，那行警告没有任何 handler 接，
    程序会变成「有界面、零日志」—— 排查时连"启动过没"都看不出来。
    """
    import dualswitch.logging_setup as logging_setup

    blocked = tmp_path / "logs"
    blocked.mkdir()                      # 路径本身是个目录 → 打开文件必失败
    fallback_dir = tmp_path / "fb"
    fallback_dir.mkdir()

    monkeypatch.setattr(logging_setup, "_configured", False)
    monkeypatch.setattr(logging_setup.tempfile, "gettempdir", lambda: str(fallback_dir))

    root = logging_setup.setup("INFO", blocked, console=False)
    try:
        logging_setup.get("t").info("兜底日志可见")
        for handler in root.handlers:
            handler.flush()
        written = list(fallback_dir.glob("*.log"))
        assert written, "默认日志不可写时必须落到临时目录"
        text = "".join(p.read_text(encoding="utf-8") for p in written)
        assert "兜底日志可见" in text
        assert "默认日志不可写" in text        # 为什么换了地方，也要留在日志里
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
