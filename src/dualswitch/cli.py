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
import logging
import os
import platform
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import __version__, config as config_mod, logging_setup, winenv
from .app import SwitchController
from .config import Config, ConfigError
from .ddc import create_backend
from .ddc.base import DDCError, probe_active_monitors
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
    # 命令行覆盖只作用于**本次运行**：把盘上的原值记下来，`save()` 按原值写回。
    # 不这么做的话，界面上随便点个开关（会落盘）就把 `dry_run: true` 永久写进
    # config.json —— 下次正常启动还在演练模式，用户以为坏了（2026-11-06 真实事故）。
    overrides: dict[str, object] = {}
    if getattr(args, "dry_run", False):
        overrides.setdefault("dry_run", cfg.dry_run)
        cfg.dry_run = True
    if getattr(args, "log_level", None):
        overrides.setdefault("log_level", cfg.log_level)
        cfg.log_level = args.log_level
    if getattr(args, "no_tray", False):
        overrides.setdefault("tray", cfg.tray)
        cfg.tray = False
    cfg._cli_overrides = overrides
    return cfg, path


def _setup_logging(cfg: Config, console: bool = True) -> Path:
    path = config_mod.log_path()
    logging_setup.setup(cfg.log_level, path, console=console)
    # 配置在日志就绪之前就加载完了，读不懂/被隔离之类的消息攒在 config 里，这里补打
    for msg in config_mod.take_load_warnings():
        log.warning("%s", msg)
    return path


def _exe_stamp() -> str:
    """可执行文件的路径 + 时间戳 + 大小。

    排查打包版时最费时间的一步就是「这份日志到底是谁写的」：同一个 `dualswitch.exe`
    可能有好几个残留副本，只看版本号（永远是 1.0.0）分不出来。时间戳+大小能直接对上号。
    """
    exe = Path(sys.executable)
    try:
        st = exe.stat()
    except OSError:
        return str(exe)
    when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"{exe}（{when}，{st.st_size // 1024} KB）"


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
    _install_term_handler()

    try:
        backend = create_backend(cfg)
    except Exception as exc:
        log.error("初始化 DDC 后端失败：%s", exc)
        print(f"[FAIL] 初始化 DDC 后端失败：{exc}")
        return 2

    # 启动时一次性安全探测：只读，不发送 Set（需求文档 3.5）
    startup_monitors: list = []
    try:
        monitors = probe_active_monitors(backend)
        startup_monitors = monitors
        log.info("发现 %d 个物理显示器：%s", len(monitors), "; ".join(m.label() for m in monitors))
    except DDCError as exc:
        log.warning("枚举显示器失败：%s", exc)
    try:
        current = backend.get_input()
        log.info("启动探测：VCP 0x%02X 当前读数 %d（仅供参考，切换不依赖读数）", cfg.vcp_code, current)
    except Exception as exc:
        log.info("启动探测读取失败（符合 Q2790PC HDMI 通道已知行为，后续全部盲发）：%s", exc)

    controller = SwitchController(cfg, backend)
    # 把这次探测的结果直接喂给控制器：界面第一帧就是对的，不用先猜「画面在本机」再纠正
    controller.note_scan(startup_monitors)

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
    clip_engine = _start_clip_engine(cfg, cfg_file, controller, backend)
    if clip_engine is not None:
        # 再喂一次启动探测：上面那次 note_scan 时同步引擎还没挂上，判不了「屏在对端」
        # （冷启动屏在对面时会被判成 unknown）。引擎连上后 `remote_ready` 可用，
        # 这里重判一次，界面首帧就是对的，不用等 2 秒轮询纠正。
        controller.note_scan(startup_monitors)

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
    if clip_engine is not None:
        clip_engine.stop()
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
        "active": None,          # None = 本后端分不出「挂着」和「在用」
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
        # 「这块屏此刻在不在本机输出画面」—— macOS 上它和 list_monitors() 经常不一致，
        # 画面归属、要不要请对端代发全靠这一项，所以单独报出来（ISSUE-003）。
        try:
            active = backend.active_monitors()
        except Exception:  # noqa: BLE001 - 探测失败不影响其余输出
            active = None
        payload["active"] = None if active is None else [m.description for m in active]
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
    if payload["active"] is None:
        print("活动画面  : （本后端分不出，按「物理显示器」判断）")
    elif payload["active"]:
        print(f"活动画面  : {'、'.join(payload['active'])}（画面在本机）")
    else:
        print("活动画面  : 无（屏不在本机输出画面 ⇒ 需要请对端代发）")
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

    # 「有字段我不认识」不是错，但要让人看得见 —— 否则拼错的键会静默失效
    for msg in dict.fromkeys(config_mod.take_load_warnings()):
        add("配置字段", "warn", msg)

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
# clip（剪贴板同步）
# --------------------------------------------------------------------------- #
def _wait_true(predicate, timeout: float) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def _clip_engine(cfg, cfg_file):  # noqa: ANN001
    """按配置起一个引擎（CLI 用，跑完就 stop）。"""
    from .sync.engine import SyncEngine

    config_mod.ensure_device_id_persisted(cfg, cfg_file)
    engine = SyncEngine(cfg.clipboard, host_label=cfg.effective_host())
    engine.start()
    return engine


def cmd_clip(args) -> int:
    cfg, cfg_file = _load_config(args, create=True)
    _setup_logging(cfg, console=False)
    cb = cfg.clipboard
    action = args.action

    # ---- probe：只看本机剪贴板里有什么类型 ----
    if action == "probe":
        from .clipboard import create_backend as create_clip_backend

        backend = create_clip_backend("auto")
        try:
            info = backend.probe()
        finally:
            backend.close()
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        print("本机剪贴板：")
        for key, value in info.items():
            print(f"  {key:<14}: {value}")
        return 0

    # ---- get：打印当前剪贴板内容（校验用） ----
    if action == "get":
        from .clipboard import create_backend as create_clip_backend

        backend = create_clip_backend("auto")
        try:
            read_now = getattr(backend, "read_now", None)
            item = read_now() if callable(read_now) else backend.poll()
        finally:
            backend.close()
        if item is None:
            print("（剪贴板为空，或只有不支持的类型 / 敏感内容）")
            return 1
        print(f"类型：{item.kind}")
        print(f"指纹：{item.digest[:16]}")
        print(f"大小：{item.wire_size()} 字节")
        if item.text:
            print(f"文本：{item.text[:500]!r}{'…' if len(item.text) > 500 else ''}")
        if item.html:
            print(f"HTML：{item.html[:120]!r}…")
        if item.image is not None:
            print(f"图片：{item.image.width}×{item.image.height}，PNG {item.image.size} 字节")
        return 0

    # ---- pair：两端输入同一个 PIN ----
    if action == "pair":
        from .sync import protocol as proto

        pin = str(args.pin or proto.random_pin()).strip()
        if not (pin.isdigit() and len(pin) == 6):
            print("[FAIL] PIN 必须是 6 位数字")
            return 2
        cb.psk = proto.derive_key(pin).hex()
        config_mod.save(cfg, cfg_file)
        print(f"配对码：{pin}")
        print("已写入共享密钥。请在**另一台机器**上执行：")
        print(f"    dualswitch clip pair --pin {pin}")
        print(f"（配置文件：{cfg_file}）")
        print("两端密钥一致后重新启动程序即可互通。")
        return 0

    # ---- send：手工推一条 ----
    if action == "send":
        from .clipboard import make_image, make_text

        if args.image:
            path = Path(args.image).expanduser()
            if not path.exists():
                print(f"[FAIL] 文件不存在：{path}")
                return 2
            png = path.read_bytes()
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                print("[FAIL] 只支持 PNG（先用预览导出成 PNG，或截图直接传原始文件）")
                return 2
            item = make_image(png)
            if item is None:
                print("[FAIL] 图片超过 32MB 上限")
                return 2
        else:
            text = args.text if args.text is not None else sys.stdin.read()
            if not text.strip():
                print("[FAIL] 没有内容可发送（给个文本参数，或用 --image）")
                return 2
            item = make_text(text)

        engine = _clip_engine(cfg, cfg_file)
        try:
            if not _wait_true(lambda: engine.status()["authenticated"] == "yes", 6.0):
                print(f"[FAIL] 没有连上已配对的对端：{engine.status()['peer']}")
                print("       先在两端执行 dualswitch clip pair --pin <同一个6位码>")
                return 1
            engine.push_item(item)
            ok = _wait_true(
                lambda: engine.records() and engine.records()[0].state in ("ok", "fail"), 8.0
            )
            rec = engine.records()[0] if engine.records() else None
            if ok and rec is not None and rec.state == "ok":
                print(f"已送达对端：{item.describe()}（{rec.id[:8]}）")
                return 0
            print(f"[FAIL] 未确认送达：{rec.state if rec else '无记录'} {rec.detail if rec else ''}")
            return 1
        finally:
            engine.stop()

    # ---- selftest：本机格式矩阵 ----
    if action == "selftest":
        from .clipboard.selfcheck import run_matrix

        results = run_matrix(include_big=not args.fast)
        for result in results:
            print(result.line())
        failed = [r for r in results if r.status == "FAIL"]
        print("-" * 62)
        print(f"{len(results) - len(failed)}/{len(results)} 通过" + (f"，{len(failed)} 项失败" if failed else ""))
        return 1 if failed else 0

    # ---- status（默认） ----
    engine = _clip_engine(cfg, cfg_file)
    try:
        _wait_true(
            lambda: engine.status()["authenticated"] == "yes" or engine.state == "unpaired",
            3.0,
        )
        info = engine.status()
    finally:
        engine.stop()
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    print("剪贴板同步：")
    labels = {
        "enabled": "已启用", "engine": "引擎状态", "backend": "后端", "device": "设备 ID",
        "psk": "已配对", "peer": "对端连接", "peer_device": "对端设备", "authenticated": "认证通过",
        "port": "监听端口", "records": "记录条数", "last_sync": "上次同步",
        "sent": "已发送", "received": "已接收", "dropped": "已丢弃", "error": "错误",
    }
    for key, label in labels.items():
        if key in info:
            print(f"  {label:<8}: {info[key]}")
    if not cb.psk:
        print("\n提示：还没配对。两端执行 dualswitch clip pair --pin <同一个6位码>")
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def _on_keyboard_release(cfg, cfg_file) -> None:  # noqa: ANN001
    """紧急停止组合（默认 Ctrl+Alt+Shift+K）命中：关掉转发并落盘。

    捕获端已经把吞键停了（`_trigger_release` 先 `set_enabled(False)` 再回调），
    这里只负责让**配置**也变成关闭 —— 否则界面还显示"转发中"，重启又会自动开。
    落盘失败不影响本次生效，但必须留痕。
    """
    kf = getattr(cfg, "keyboard_follow", None)
    if kf is None:
        return
    kf.enabled = False
    try:
        from . import config as config_mod

        config_mod.save(cfg, cfg_file)
        log.warning("键盘跟随已被紧急组合关闭（按键已交还本机）")
    except Exception as exc:  # noqa: BLE001 - 落盘失败也要让本次生效
        log.error("紧急停止后保存配置失败（本次仍已生效）：%s", exc)


def serve_switch_request(cfg, backend, controller, value: int) -> tuple[bool, str]:
    """对端说「替我切到 value」：校验后**走控制器**发 DDC Set。

    必须走 `controller.set_input_local`，不能直接 `backend.set_input`
    （2026-11-14 事故）：外接屏省电会把屏从 WindowServer 停掉，这时候屏在系统里
    没有输出对象，`m1ddc` 连屏都枚举不到 —— `backend.set_input` 只会回一句
    「Could not find a suitable external display」。走控制器才会先
    `before_switch_to_local()` 把屏恢复出来再发 Set，界面/省电状态也一起对齐。
    """
    if cfg.dry_run:
        return False, "本机处于演练模式，未真正发送指令"
    allowed = {int(v) for v in cfg.sources.values()}
    if int(value) not in allowed:
        return False, f"输入源 {value} 不在本机配置里（允许 {sorted(allowed)}）"
    if backend is None:
        return False, "本机没有可用的 DDC 后端"
    try:
        result = controller.set_input_local(int(value), source="remote")
    except DDCError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - 对端请求不能把引擎搞崩
        return False, f"{type(exc).__name__}: {exc}"
    if getattr(result, "ok", False):
        return True, ""
    return False, getattr(result, "error", "") or "本机发送失败"


def _start_clip_engine(cfg, cfg_file, controller, backend=None):  # noqa: ANN001
    """把剪贴板引擎挂到切屏链路上。失败只降级，绝不影响切屏。

    **即使 `clipboard.enabled=false` 也要起引擎**：配对状态、对端代发切屏
    都走这条链路。以前这里第一行就 `return None`，于是关掉剪贴板同步的
    同时把「请对端代发」一起废掉了，界面上还只显示「对端离线」。
    内容同步是否开启由引擎自己按 `cfg.enabled` 决定（见 SyncEngine.start）。
    """
    try:
        from .sync.engine import SyncEngine

        def _serve_switch(value: int) -> tuple[bool, str]:
            return serve_switch_request(cfg, backend, controller, int(value))

        config_mod.ensure_device_id_persisted(cfg, cfg_file)

        # 键盘跟随（默认关）：**键盘挂在哪台，哪台就是捕获端**。
        #   · Windows：装 `WH_KEYBOARD_LL`，画面在 Mac 时吞键 + 发 T_KEY；
        #   · macOS：收 T_KEY 注入本机（不吞键，也吞不了 —— 安全输入那条硬伤）。
        # 两端都由 `keyboard.create_endpoint()` 建，这里不写平台分支。
        # **端点总是建**（哪怕开关关着）：界面要靠它显示状态、读往返延迟，
        # 没对象就没法显示。捕获端在关着时不装钩子（见 `KeyboardCapture.start`）。
        from .keyboard import create_endpoint

        kf = cfg.keyboard_follow
        key_endpoint = create_endpoint(
            cfg,
            # 紧急停止组合：关掉转发 + 落盘（捕获端会先停吞键再回调）
            on_release=lambda: _on_keyboard_release(cfg, cfg_file),
            # 转发期间由捕获端自己认切屏热键：热键的修饰键已经被吞给 Mac，
            # Windows 看不到 Alt 按下，RegisterHotKey 根本不会触发（见 capture_windows）
            on_hotkey=controller.trigger,
            rtt_probe=None,      # 引擎还没建，建好后回填
        )
        if kf.enabled:
            ok, why = key_endpoint.available()
            if ok:
                log.info("键盘跟随已启用：%s", key_endpoint.describe())
            else:
                # 不可用是正常状态（没装 pyobjc / 没授权 / 钩子被拦），但要看得见
                log.warning("键盘跟随已启用但不可用：%s", why)

        engine = SyncEngine(
            cfg.clipboard,
            host_label=cfg.effective_host(),
            switch_executor=_serve_switch,
            vcp_code=cfg.vcp_code,
            save_config=lambda: config_mod.save(cfg, cfg_file),
            version=__version__,
            on_key=key_endpoint.handle_key,
            on_key_state=key_endpoint.handle_state,
            on_focus=key_endpoint.handle_focus,
        )
        # 端点挂到引擎上：界面（UiRunner2）通过 `engine.keyboard` 拿它做开关。
        # `attach` 顺便接上往返探测（借 T_STATE 请求/回执，不新增协议帧）。
        engine.keyboard = key_endpoint
        if hasattr(key_endpoint, "attach"):
            key_endpoint.attach(engine)
        engine.start()
        if kf.enabled and hasattr(key_endpoint, "start"):
            # 捕获端要装钩子（注入端没有 start，注入随帧发生）
            try:
                key_endpoint.start()
                # 冷启动时画面可能已经在 Mac（上一次切过去没切回来）：首帧就把焦点
                # 对齐，否则要等用户手动切一次才生效。归属 unknown 就不猜。
                src = controller.source_state
                if src in ("windows", "mac"):
                    key_endpoint.on_focus(src)
            except Exception as exc:  # noqa: BLE001 - 钩子装不上只降级
                log.error("键盘捕获启动失败（切屏不受影响）：%s", exc)
        controller.add_before_switch(lambda source, target: engine.push_now(f"switch:{source}"))
        # 本机打不到这块屏时（画面在对面 / 没接线），交给对端代发
        controller.remote_switch = engine.request_switch
        controller.remote_ready = lambda: engine.switch_ready
        # 键盘跟随：切屏成功后把"还按着的键"全抬起来，避免修饰键卡死；
        # 捕获端还要借此推进 epoch（谁决定按键去向，谁定 epoch）
        controller.set_keyboard(key_endpoint)

        # 画面归属联合判定（T_STATE 0x32）：两台各报「我看不看得到这块屏」，
        # 单机判不了的两种情况（冷启动屏在对端 / 拔线）都能唯一定下来。
        def _sees_monitor() -> bool:
            if backend is None:
                return False
            try:
                monitors = probe_active_monitors(backend)
            except Exception:  # noqa: BLE001 - 枚举失败按「看不到」答
                return False
            needle = (cfg.monitor_match or "").strip()
            return any(m.matches(needle) for m in monitors) if needle else bool(monitors)

        engine.set_monitor_probe(_sees_monitor)
        controller.peer_sees_monitor = engine.peer_sees_monitor
        log.info("剪贴板同步已挂载（设备 %s，端口 %s）", cfg.clipboard.device_id, cfg.clipboard.port)
        return engine
    except Exception as exc:  # noqa: BLE001 - 剪贴板坏了不能连累切屏
        log.error("剪贴板同步启动失败（切屏不受影响）：%s", exc)
        return None


def _log_source_evidence(backend, controller) -> None:  # noqa: ANN001
    """把「画面归属」的三路证据一次写进启动日志。

    报「初始显示在 Mac、实际在 Windows」这类问题时，只看结论没法定位是
    哪一路证据错了。这一行把三路并排列出来：

      · 活动屏   —— AppKit 的活动信号（`CGDisplayIsActive`，最硬的本地判据）
      · DDC 枚举 —— m1ddc / EnumDisplayMonitors（历史上「列得出不在用的屏」）
      · 对端上报 —— T_STATE 心跳（`None` = 问不出来 / 链路还没通）

    「活动屏=<空>」而「DDC 枚举=AOC…」正是本次要修的那种错位。
    """

    def _labels(probe) -> str:  # noqa: ANN001
        try:
            items = probe()
        except Exception as exc:  # noqa: BLE001 - 诊断行不能把启动搞崩
            return f"<失败 {type(exc).__name__}>"
        if items is None:
            return "<分不出>"
        return "、".join(m.label() for m in items) or "<空>"

    peer = getattr(controller, "peer_sees_monitor", None)
    try:
        peer_text = "<未挂载>" if peer is None else str(peer())
    except Exception:  # noqa: BLE001
        peer_text = "<查询失败>"
    log.info(
        "归属判据：活动屏=%s；DDC 枚举=%s；对端上报=%s；界面判定=%s",
        _labels(getattr(backend, "active_monitors", lambda: None)),
        _labels(backend.list_monitors),
        peer_text,
        controller.source_state,
    )


def _install_term_handler() -> None:
    """把 SIGTERM 变成正常退出，好让 `finally` 里的收尾真的跑到。

    为什么在意：省电模块会把外接屏从 WindowServer 停掉，**必须**在退出时恢复。
    `killall` / 注销 / 关机发的都是 SIGTERM，而 Python 默认是直接死掉，
    `finally` 与 atexit 都不执行 —— 那就会留下一块黑着的外接屏。
    """
    if sys.platform == "win32":
        return
    import signal

    def _raise(signum, _frame):  # noqa: ANN001
        log.info("收到信号 %s，按正常退出处理（会恢复外接屏）", signum)
        # Qt 的事件循环会把 SystemExit 吞掉（实测 2026-11-06：日志打了，finally 没跑），
        # 所以恢复动作必须在这里就地做完，别指望上层 finally。
        try:
            from . import display_power

            display_power.restore_pending()
        except Exception:  # noqa: BLE001 - 救命动作失败也要继续退出
            log.exception("退出前恢复外接屏失败")
        # 不能只 raise SystemExit 就交给上层（2026-11-13 复测确认）：这个 handler 是在
        # Qt 的 C++ 事件派发里被调起来的，异常一出来 PySide 就直接进解释器退出，接着
        # 析构全部 QWidget —— 父对象先被销毁的那个 SIGSEGV。症状是退出码对了（143），
        # 但 atexit 里冒一串 `Error calling Python override of QObject::eventFilter`，
        # 偶尔还留一份崩溃报告。上层的 finally 同样跑不到，所以这里冲刷完日志直接结束
        # 进程（与 ui2 的 `runner._hard_exit` 同一个道理）。
        log.info("信号退出：收尾完成，直接结束进程（%s）", 128 + signum)
        try:
            logging.shutdown()
        except Exception:  # noqa: BLE001
            pass
        os._exit(128 + signum)

    try:
        signal.signal(signal.SIGTERM, _raise)
        signal.signal(signal.SIGHUP, _raise)
    except (ValueError, OSError):  # 非主线程 / 平台不支持
        pass


def cmd_ui2(args) -> int:
    """v2 图形界面（宽窗口 + 侧栏三页）—— 与 `ui` 并行的新树，验收后旧树才删。"""
    from .ui import available as ui_available

    if not ui_available():
        print("[FAIL] 未安装 PySide6，无法启动图形界面。")
        print("       安装：pip install -e \".[gui]\"")
        return 2

    cfg, cfg_file = _load_config(args, create=True)
    log_file = _setup_logging(cfg, console=bool(getattr(args, "console", False)))
    log.info("=" * 62)
    log.info("dualswitch %s v2 图形界面启动（ui2）", __version__)
    log.info("可执行文件：%s", _exe_stamp())
    log.info("日志：%s", log_file)
    winenv.log_environment(log)
    _install_term_handler()

    try:
        backend = create_backend(cfg)
    except Exception as exc:
        log.error("初始化 DDC 后端失败：%s", exc)
        print(f"[FAIL] 初始化 DDC 后端失败：{exc}")
        return 2

    # 启动时一次性只读探测（与 cmd_run 一致）。ui / ui2 原来都没有这一步，
    # 界面首帧必然显示「未检测到外接显示器」，要等 2 秒轮询才纠正 —— 看着就像判错了。
    startup_monitors: list = []
    try:
        startup_monitors = probe_active_monitors(backend)
        log.info(
            "发现 %d 个物理显示器：%s",
            len(startup_monitors),
            "; ".join(m.label() for m in startup_monitors),
        )
    except DDCError as exc:
        log.warning("枚举显示器失败：%s", exc)

    controller = SwitchController(cfg, backend)
    controller.note_scan(startup_monitors)

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
    clip_engine = _start_clip_engine(cfg, cfg_file, controller, backend)
    if clip_engine is not None:
        # 再喂一次启动探测：上面那次 note_scan 时同步引擎还没挂上，判不了「屏在对端」
        # （冷启动屏在对面时会被判成 unknown）。引擎连上后 `remote_ready` 可用，
        # 这里重判一次，界面首帧就是对的，不用等 2 秒轮询纠正。
        controller.note_scan(startup_monitors)
    # 三路证据落日志：界面首帧判错时，这一行能直接指出是哪一路证据错了
    _log_source_evidence(backend, controller)

    # v2 界面按平台分发：Windows → ui/win2（自绘标题栏 + 托盘），其它 → ui/mac2。
    # 旧 `ui` 子命令一行不动（两条路并行，验收后旧树才删）。
    if sys.platform == "win32":
        from .ui.win2.runner import run_ui2
    else:
        from .ui.mac2.runner import run_ui2

    try:
        return run_ui2(cfg, cfg_file, log_file, backend, controller, hotkey, warning,
                       clip_engine=clip_engine)
    finally:
        if clip_engine is not None:
            clip_engine.stop()
        controller.stop()
        if hotkey is not None:
            try:
                hotkey.stop()
            except Exception:  # pragma: no cover
                pass


def cmd_display(args) -> int:
    """外接屏省电：看状态 / 手动停用 / 恢复（私有 API 的兜底入口）。

    正常情况下由常驻进程自己管（画面走了停、回来恢复）。这里给人手一根救命绳：
    万一进程被 kill、屏留在停用态，`display reset` 就能救回来；连状态文件也没了
    就先 `display list` 看 id，再 `display on <id>`。
    """
    from . import display_power as dp

    ok, why = dp.available()
    action = getattr(args, "action", "list") or "list"
    if not ok:
        print(f"[SKIP] {why}")
        return 0 if action == "list" else 2

    if action == "list":
        infos = dp.list_displays()
        if not infos:
            print("（没有枚举到任何显示器）")
        for d in infos:
            print(d.label())
        return 0

    if action == "off":
        ids = dp.disable_external_displays()
        print(f"已停用：{ids or '（没有可停用的外接屏）'}")
        return 0

    if action == "on":
        ids = [int(i) for i in args.ids]
        done = dp.enable_displays(ids)
        print(f"已恢复：{done}")
        return 0

    if action == "reset":
        done = dp.restore_pending()
        print(f"已恢复：{done or '（没有残留的停用状态）'}")
        return 0

    print("[FAIL] 未知动作")
    return 2


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


    ui2_p = sub.add_parser("ui2", help="v2 图形界面（宽窗口 + 侧栏导航）", parents=[common])
    ui2_p.add_argument("--dry-run", action="store_true", help="只记录，不真正发送 Set")
    ui2_p.add_argument("--console", action="store_true", help="把日志同时输出到控制台")
    ui2_p.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    ui2_p.set_defaults(func=cmd_ui2)

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

    dp_p = sub.add_parser(
        "display", help="外接屏省电（画面不在本机时停用外接屏 / 手动恢复）", parents=[common]
    )
    dp_sub = dp_p.add_subparsers(dest="action")
    dp_p.set_defaults(func=cmd_display, action="list")
    dp_sub.add_parser("list", help="列出所有显示器（含被停用的）", parents=[common]).set_defaults(
        func=cmd_display, action="list"
    )
    dp_sub.add_parser("off", help="停用所有在用外接屏", parents=[common]).set_defaults(
        func=cmd_display, action="off"
    )
    dp_on = dp_sub.add_parser("on", help="恢复指定显示器", parents=[common])
    dp_on.add_argument("ids", nargs="+", type=int, help="显示器 id（先 display list 看）")
    dp_on.set_defaults(func=cmd_display, action="on")
    dp_sub.add_parser("reset", help="恢复上次残留的停用状态（崩了之后的救命绳）", parents=[common]).set_defaults(
        func=cmd_display, action="reset"
    )

    clip_p = sub.add_parser("clip", help="剪贴板同步（状态 / 配对 / 发送 / 自检）", parents=[common])
    clip_sub = clip_p.add_subparsers(dest="action")

    clip_status = clip_p
    clip_status.add_argument("--json", action="store_true", help="JSON 输出")
    clip_status.set_defaults(func=cmd_clip, action="status")

    # 显式注册 status：加到 subparsers 之后，`clip status` 会被当成"未注册的子命令"报错
    clip_sub.add_parser("status", help="连接状态（默认）", parents=[common]).set_defaults(
        func=cmd_clip, action="status", json=False
    )

    clip_probe = clip_sub.add_parser("probe", help="打印本机剪贴板里的全部类型", parents=[common])
    clip_probe.add_argument("--json", action="store_true")
    clip_probe.set_defaults(func=cmd_clip, action="probe")

    clip_get = clip_sub.add_parser("get", help="打印当前剪贴板内容", parents=[common])
    clip_get.set_defaults(func=cmd_clip, action="get")

    clip_pair = clip_sub.add_parser("pair", help="配对（两端输入同一个 6 位码）", parents=[common])
    clip_pair.add_argument("--pin", default=None, help="6 位配对码；不给则随机生成一个")
    clip_pair.set_defaults(func=cmd_clip, action="pair")

    clip_send = clip_sub.add_parser("send", help="手工推一条到对端", parents=[common])
    clip_send.add_argument("text", nargs="?", default=None, help="要发送的文本")
    clip_send.add_argument("--image", default=None, help="要发送的 PNG 文件")
    clip_send.set_defaults(func=cmd_clip, action="send")

    clip_self = clip_sub.add_parser("selftest", help="本机格式矩阵自检", parents=[common])
    clip_self.add_argument("--fast", action="store_true", help="跳过 4K 大图（省时间）")
    clip_self.set_defaults(func=cmd_clip, action="selftest")

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
