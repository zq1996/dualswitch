"""命令行入口。

    dualswitch run                 # 常驻：托盘 + 全局热键
    dualswitch switch --to mac     # 立刻切一次
    dualswitch probe               # 只读探测（不会切屏，安全）
    dualswitch doctor              # 环境自检
    dualswitch config init|show|path
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__, config as config_mod, logging_setup
from .app import SwitchController
from .config import Config, ConfigError
from .ddc import create_backend
from .ddc.base import DDCError
from .hotkey import HotkeyError, create_hotkey, parse_hotkey, resolve_backend_name

log = logging_setup.get("cli")


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _load_config(args, *, create: bool = False) -> tuple[Config, Path]:
    path = Path(args.config).expanduser() if getattr(args, "config", None) else config_mod.config_path()
    if create:
        path, created = config_mod.ensure_config_file(path)
        if created:
            print(f"已生成默认配置文件：{path}")
    cfg = config_mod.load(path, strict=False)
    if getattr(args, "dry_run", False):
        cfg.dry_run = True
    if getattr(args, "log_level", None):
        cfg.log_level = args.log_level
    if getattr(args, "no_tray", False):
        cfg.tray = False
    return cfg, path


def _setup_logging(cfg: Config, console: bool = True) -> Path:
    path = config_mod.log_path()
    logging_setup.setup(cfg.log_level, path, console=console)
    return path


def _print_result(result) -> None:  # noqa: ANN001
    print(result.summary())
    if result.readback == "confirmed":
        print(f"回读确认：{result.readback_detail}")
    elif result.readback == "unconfirmed":
        print(f"回读未确认：{result.readback_detail}（Q2790PC 已知现象，可忽略）")


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    cfg, cfg_file = _load_config(args, create=True)
    log_file = _setup_logging(cfg, console=not args.no_console)

    log.info("=" * 62)
    log.info("dualswitch %s 启动 | %s %s", __version__, platform.system(), platform.release())
    log.info("配置：%s", cfg_file)
    log.info("日志：%s", log_file)
    log.info(
        "本机=%s 目标=%s 热键=%s 后端=%s VCP=0x%02X 值=%s",
        cfg.effective_host(),
        cfg.other_host(),
        cfg.hotkey,
        cfg.ddc_backend,
        cfg.vcp_code,
        cfg.sources,
    )

    try:
        backend = create_backend(cfg)
    except Exception as exc:
        log.error("初始化 DDC 后端失败：%s", exc)
        print(f"[FAIL] 初始化 DDC 后端失败：{exc}")
        return 2

    # 启动时一次性安全探测：只读，不发送 Set（需求文档 3.5）
    try:
        monitors = backend.list_monitors()
        log.info("发现 %d 个物理显示器：%s", len(monitors), "; ".join(m.label() for m in monitors))
    except DDCError as exc:
        log.warning("枚举显示器失败：%s", exc)
    try:
        current = backend.get_input()
        log.info("启动探测：VCP 0x%02X 当前读数 %d（仅供参考，切换不依赖读数）", cfg.vcp_code, current)
    except Exception as exc:
        log.info("启动探测读取失败（符合 Q2790PC HDMI 通道已知行为，后续全部盲发）：%s", exc)

    controller = SwitchController(cfg, backend)

    warning: str | None = None
    hotkey = None
    try:
        hotkey = create_hotkey(cfg, controller.trigger)
        hotkey.start()
        log.info("热键已启用：%s（后端 %s）", hotkey.describe(), hotkey.name)
        if cfg.hotkey_backend in ("auto", "register") and parse_hotkey(cfg.hotkey).strict_right_alt:
            log.warning("RegisterHotKey 不区分左右 Alt，ralt+space 实际会响应任意 Alt+Space")
    except HotkeyError as exc:
        warning = str(exc)
        log.error("热键注册失败：%s", exc)
        print(f"[WARN] 热键未启用：{exc}")
        print("       程序仍在运行，可继续使用托盘菜单切换。")
        hotkey = None

    controller.start()

    from . import tray as tray_mod

    use_tray = cfg.tray and tray_mod.tray_available()
    if cfg.tray and not use_tray:
        log.warning("未安装 pystray / pillow，托盘不可用，降级为无界面常驻")
        print("[WARN] 未安装 pystray / pillow，托盘不可用（pip install pystray pillow）")

    if use_tray:
        app = tray_mod.TrayApp(cfg, controller, backend, cfg_file, log_file, hotkey.describe() if hotkey else "未启用")
        controller.set_notifier(app.notify)
        print("已启动：托盘图标已出现，%s。退出请右键托盘 → 退出。" % (
            f"按 {hotkey.describe()} 切换显示器" if hotkey else "用托盘菜单切换显示器"
        ))
        try:
            app.run(warning=warning)
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，退出")
    else:
        stop = threading.Event()

        def _handle(signum, frame):  # noqa: ANN001
            log.info("收到信号 %s，准备退出", signum)
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):  # pragma: no cover
                pass
        print("已启动（无界面模式），按 %s 切换显示器，Ctrl+C 退出。" % (hotkey.describe() if hotkey else "-"))
        try:
            while not stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass

    controller.stop()
    if hotkey:
        hotkey.stop()
    backend.close()
    log.info("已退出")
    return 0


# --------------------------------------------------------------------------- #
# switch
# --------------------------------------------------------------------------- #
def cmd_switch(args) -> int:
    cfg, _ = _load_config(args)
    _setup_logging(cfg, console=False)
    try:
        backend = create_backend(cfg)
    except Exception as exc:
        print(f"[FAIL] 初始化 DDC 后端失败：{exc}")
        return 2

    controller = SwitchController(cfg, backend)
    target = None if args.to == "other" else args.to
    try:
        result = controller.switch_now(target, source="cli")
    finally:
        backend.close()

    _print_result(result)

    if args.wait_readback and cfg.readback.enabled and result.ok:
        deadline = time.time() + (cfg.readback.delay_ms + cfg.readback.timeout_ms) / 1000.0 + 0.5
        while time.time() < deadline and result.readback == "pending":
            time.sleep(0.05)
        if result.readback == "confirmed":
            print(f"回读确认：{result.readback_detail}")
        elif result.readback == "unconfirmed":
            print(f"回读未确认：{result.readback_detail}")
    return 0 if result.ok else 1


# --------------------------------------------------------------------------- #
# probe（只读，安全）
# --------------------------------------------------------------------------- #
def cmd_probe(args) -> int:
    cfg, cfg_file = _load_config(args)
    payload: dict = {
        "platform": platform.system(),
        "config": str(cfg_file),
        "backend": None,
        "monitors": [],
        "input": None,
        "error": None,
    }
    try:
        backend = create_backend(cfg)
        payload["backend"] = backend.name
        monitors = backend.list_monitors()
        payload["monitors"] = [
            {"index": m.index, "description": m.description, "device": m.device_name, "id": m.device_id}
            for m in monitors
        ]
        try:
            payload["input"] = backend.get_input()
        except DDCError as exc:
            payload["error"] = f"读取 VCP 0x{cfg.vcp_code:02X} 失败：{exc}"
        backend.close()
    except Exception as exc:
        payload["error"] = str(exc)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["monitors"] else 1

    print(f"平台      : {payload['platform']}")
    print(f"配置      : {payload['config']}")
    print(f"DDC 后端  : {payload['backend']}")
    print("物理显示器:")
    if payload["monitors"]:
        for m in payload["monitors"]:
            print(f"  [{m['index']}] {m['description']} | {m['device']} | {m['id']}")
    else:
        print("  （未发现）")
    if payload["input"] is not None:
        print(f"当前输入源: VCP 0x{cfg.vcp_code:02X} = {payload['input']}（读数仅供参考）")
    if payload["error"]:
        print(f"读取提示  : {payload['error']}")
    print("\n提示：probe 只读，不会切换画面。")
    return 0 if payload["monitors"] else 1


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(args) -> int:
    cfg, cfg_file = _load_config(args)
    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    add("Python 版本", "ok" if sys.version_info >= (3, 9) else "fail", platform.python_version())
    add("操作系统", "ok", f"{platform.system()} {platform.release()}")

    if cfg_file.exists():
        try:
            config_mod.validate(config_mod.load(cfg_file))
            add("配置文件", "ok", str(cfg_file))
        except ConfigError as exc:
            add("配置文件", "fail", f"{cfg_file} → {exc}")
    else:
        add("配置文件", "warn", f"{cfg_file} 不存在（首次 run 会自动生成）")

    try:
        parse_hotkey(cfg.hotkey)
        add("热键语法", "ok", f"{cfg.hotkey} → {parse_hotkey(cfg.hotkey).describe()}")
    except HotkeyError as exc:
        add("热键语法", "fail", str(exc))

    try:
        backend_name = resolve_backend_name(cfg)
        add("热键后端", "ok", backend_name)
    except HotkeyError as exc:
        backend_name = "?"
        add("热键后端", "fail", str(exc))

    if sys.platform == "win32" and backend_name in ("register", "hook"):
        from .hotkey.windows import probe_available

        ok, detail = probe_available(parse_hotkey(cfg.hotkey))
        add("热键可用性", "ok" if ok else "fail", detail)
    elif sys.platform == "darwin":
        try:
            from .hotkey.macos import _accessibility_trusted, _import_quartz, permission_target

            _import_quartz()
            trusted = _accessibility_trusted(prompt=False)
            add("pyobjc/Quartz", "ok", "已安装")
            add(
                "辅助功能权限",
                "ok" if trusted else "fail",
                "已授权" if trusted else f"未授权：到「隐私与安全性 → 辅助功能」打开「{permission_target()}」",
            )
        except Exception as exc:
            add("pyobjc/Quartz", "fail", str(exc))

    try:
        backend = create_backend(cfg)
        add("DDC 后端", "ok", backend.name)
        try:
            monitors = backend.list_monitors()
            add("显示器枚举", "ok" if monitors else "fail", f"{len(monitors)} 个：" + "; ".join(m.label() for m in monitors))
        except Exception as exc:
            add("显示器枚举", "fail", str(exc))
        try:
            value = backend.get_input()
            add("VCP 读数", "ok", f"0x{cfg.vcp_code:02X} = {value}（仅供参考）")
        except Exception as exc:
            add("VCP 读数", "warn", f"不可读：{exc}（Q2790PC HDMI 通道已知现象，不影响盲发）")
        backend.close()
    except Exception as exc:
        add("DDC 后端", "fail", str(exc))

    if sys.platform == "darwin":
        from .ddc.macos import find_m1ddc

        # 不能只用 shutil.which：双击 .app 启动时 PATH 里没有 /opt/homebrew/bin
        found = find_m1ddc(cfg.m1ddc_path) if cfg.ddc_backend in ("auto", "m1ddc") else None
        add("m1ddc", "ok" if found else "warn", found or "未找到，可 brew install m1ddc 或改用 command 后端")

    from . import tray as tray_mod

    add("托盘依赖", "ok" if tray_mod.tray_available() else "warn", "pystray + pillow 已安装" if tray_mod.tray_available() else "未安装（可 pip install pystray pillow）")

    log_dir = config_mod.log_path().parent
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe_file = log_dir / ".write_test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        add("日志目录可写", "ok", str(log_dir))
    except Exception as exc:
        add("日志目录可写", "fail", f"{log_dir} → {exc}")

    failed = [c for c in checks if c["status"] == "fail"]
    if args.json:
        print(json.dumps({"checks": checks, "ok": not failed}, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    print(f"dualswitch {__version__} 自检\n" + "-" * 62)
    for c in checks:
        print(f"[{c['status'].upper():4}] {c['name']:<12} {c['detail']}")
    print("-" * 62)
    if failed:
        print(f"发现 {len(failed)} 个阻断项，请先修复后再运行。")
        return 1
    print("自检通过。")
    return 0


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def cmd_config(args) -> int:
    # 子命令用 parents=[common] 且 --config 默认 SUPPRESS，未传时该属性不存在
    raw = getattr(args, "config", None)
    path = Path(raw).expanduser() if raw else config_mod.config_path()
    if args.action == "path":
        print(path)
        return 0
    if args.action == "init":
        if path.exists() and not args.force:
            print(f"配置文件已存在：{path}（加 --force 覆盖）")
            return 1
        config_mod.save(config_mod.platform_defaults(), path)
        print(f"已写入：{path}")
        return 0
    if args.action == "show":
        if not path.exists():
            print(f"配置文件不存在：{path}")
            return 1
        print(path.read_text(encoding="utf-8"))
        return 0
    return 2


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def cmd_ui(args) -> int:
    """带图形界面的常驻模式（主窗口 + 托盘 + 热键）。"""
    from .ui import available as ui_available

    if not ui_available():
        print("[FAIL] 未安装 PySide6，无法启动图形界面。")
        print("       安装：pip install -e \".[gui]\"")
        return 2

    cfg, cfg_file = _load_config(args, create=True)
    log_file = _setup_logging(cfg, console=bool(getattr(args, "console", False)))
    log.info("=" * 62)
    log.info("dualswitch %s 图形界面启动", __version__)

    try:
        backend = create_backend(cfg)
    except Exception as exc:
        log.error("初始化 DDC 后端失败：%s", exc)
        print(f"[FAIL] 初始化 DDC 后端失败：{exc}")
        return 2

    controller = SwitchController(cfg, backend)

    warning: str | None = None
    hotkey = None
    try:
        hotkey = create_hotkey(cfg, controller.trigger)
        hotkey.start()
        log.info("热键已启用：%s（后端 %s）", hotkey.describe(), hotkey.name)
    except HotkeyError as exc:
        warning = str(exc)
        log.error("热键注册失败：%s", exc)
        hotkey = None

    controller.start()

    from .ui.runner import run_ui

    try:
        return run_ui(cfg, cfg_file, log_file, backend, controller, hotkey, warning)
    finally:
        controller.stop()
        if hotkey is not None:
            try:
                hotkey.stop()
            except Exception:  # pragma: no cover
                pass


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="配置文件路径")

    parser = argparse.ArgumentParser(
        prog="dualswitch",
        description="双系统单显示器切换助手：一个热键在 Windows(HDMI) 与 Mac(USB-C) 之间切换",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"dualswitch {__version__}")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="常驻运行（托盘 + 全局热键）", parents=[common])
    run_p.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
    run_p.add_argument("--no-console", action="store_true", help="不向控制台输出日志")
    run_p.add_argument("--dry-run", action="store_true", help="只记录，不真正发送 Set")
    run_p.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    run_p.set_defaults(func=cmd_run)

    ui_p = sub.add_parser("ui", help="图形界面（主窗口 + 托盘 + 全局热键）", parents=[common])
    ui_p.add_argument("--dry-run", action="store_true", help="只记录，不真正发送 Set")
    ui_p.add_argument("--console", action="store_true", help="把日志同时输出到控制台")
    ui_p.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    ui_p.set_defaults(func=cmd_ui)

    sw_p = sub.add_parser("switch", help="立刻切换一次", parents=[common])
    sw_p.add_argument("--to", choices=["other", "windows", "mac", "toggle"], default="other")
    sw_p.add_argument("--dry-run", action="store_true")
    sw_p.add_argument("--wait-readback", action="store_true", help="等待异步回读结果")
    sw_p.set_defaults(func=cmd_switch)

    pr_p = sub.add_parser("probe", help="只读探测显示器与当前输入源", parents=[common])
    pr_p.add_argument("--json", action="store_true")
    pr_p.set_defaults(func=cmd_probe)

    dr_p = sub.add_parser("doctor", help="环境自检", parents=[common])
    dr_p.add_argument("--json", action="store_true")
    dr_p.set_defaults(func=cmd_doctor)

    cf_p = sub.add_parser("config", help="配置管理", parents=[common])
    cf_p.add_argument("action", choices=["path", "init", "show"])
    cf_p.add_argument("--force", action="store_true")
    cf_p.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"[FAIL] 配置错误：{exc}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
