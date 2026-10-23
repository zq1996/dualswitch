"""最小常驻宿主：只跑剪贴板同步引擎，不弹界面、不注册热键、不碰 DDC。

联调用。`clip status` / `clip send` 这类子命令都是「起引擎 → 用完 stop()」，
端口随即关闭，对端永远连不上；要联调就得有一个不退出的进程。

用法::

    python tools/win_clip_serve.py --seconds 1800
    python tools/win_clip_serve.py --seconds 600 --interval 3

它只做三件事：起引擎、每 N 秒打印一行状态、收到记录时把内容打出来。
刻意**不**装 switch executor —— 切屏联调走真实界面，不在这个脚本里做。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dualswitch import config as config_mod  # noqa: E402
from dualswitch.sync.engine import SyncEngine  # noqa: E402


def _one_line(engine: SyncEngine) -> str:
    st = engine.status()
    return (
        f"state={st.get('engine')} peer={st.get('peer')} "
        f"auth={st.get('authenticated')} peer_dev={st.get('peer_device')} "
        f"port={st.get('port')} sent={st.get('sent')} recv={st.get('received')} "
        f"drop={st.get('dropped')} err={st.get('error')}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="常驻剪贴板同步引擎（联调用）")
    ap.add_argument("--seconds", type=int, default=1800, help="跑多久（默认 1800 秒）")
    ap.add_argument("--interval", type=float, default=5.0, help="状态打印间隔秒")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--send", default=None, help="连上对端后自动推一条文本")
    ap.add_argument("--send-image", default=None, help="连上对端后自动推一张 PNG（路径）")
    ap.add_argument("--send-after", type=float, default=0.0,
                    help="连上之后再等几秒才发（默认 0 = 一认证通过就发）")
    ap.add_argument("--watch", default=None,
                    help="监听这个文本文件：内容一变就把新内容推给对端（联调按需发信用）")
    args = ap.parse_args()

    cfg_file = Path(args.config).expanduser() if args.config else config_mod.config_path()
    cfg = config_mod.load(cfg_file)
    config_mod.ensure_device_id_persisted(cfg, cfg_file)

    cb = cfg.clipboard
    print(f"配置：{cfg_file}")
    print(f"设备：{cb.device_id}  端口：{cb.port}  发现端口：{cb.discover_port}")
    print(f"已配对：{'yes' if cb.psk else 'no'}  peer_device={cb.peer_device or '-'}")
    if not cb.psk:
        print("[FAIL] 还没配对，先跑 dualswitch clip pair --pin <6位码>")
        return 2

    engine = SyncEngine(cb, host_label=cfg.effective_host())
    engine.start()
    print(f"[OK] 引擎已启动，开始常驻 {args.seconds}s（Ctrl+C 提前结束）", flush=True)
    print(f"     监听 TCP {cb.port} / UDP {cb.discover_port}", flush=True)

    seen: dict[str, str] = {}          # id -> 上次打印时的 state（状态变了再打一次）
    sent = False
    watch_path = Path(args.watch).expanduser() if args.watch else None
    watch_seen = watch_path.read_text(encoding="utf-8").strip() if (
        watch_path and watch_path.exists()) else ""
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(args.interval)
            st = engine.status()
            print(f"[{time.strftime('%H:%M:%S')}] {_one_line(engine)}", flush=True)

            # 按需发信：watch 文件内容一变就推一条（联调 T6 断连测试靠这个）
            if watch_path is not None and watch_path.exists():
                now_text = watch_path.read_text(encoding="utf-8").strip()
                if now_text and now_text != watch_seen:
                    watch_seen = now_text
                    from dualswitch.clipboard import make_text

                    item = make_text(now_text)
                    engine.push_item(item)
                    print(f"    → [watch] 已推送 {item.describe()}", flush=True)

            # 连上之后自动推一条（只推一次）
            if (args.send or args.send_image) and not sent and st.get("authenticated") == "yes":
                time.sleep(args.send_after)
                if args.send_image:
                    from dualswitch.clipboard import make_image

                    path = Path(args.send_image).expanduser()
                    png = path.read_bytes()
                    item = make_image(png)
                    if item is None:
                        print(f"[FAIL] {path.name} 超过 32MB 上限，没发", flush=True)
                        sent = True
                        continue
                    print(f"    → 准备发图 {path.name}（{len(png)} 字节 / "
                          f"{item.image.width}×{item.image.height}）", flush=True)
                else:
                    from dualswitch.clipboard import make_text

                    item = make_text(args.send)
                t0 = time.monotonic()
                engine.push_item(item)
                sent = True
                print(f"    → 已推送 {item.describe()}，等回执…（push 耗时 "
                      f"{(time.monotonic() - t0) * 1000:.0f}ms）", flush=True)

            for rec in engine.records():
                if seen.get(rec.id) == rec.state:
                    continue
                seen[rec.id] = rec.state
                text = (rec.text or "")[:200].replace("\n", "\\n")
                print(f"    ← 记录 {rec.id[:8]} kind={rec.kind} state={rec.state} "
                      f"source={rec.source} from={rec.peer_name or '-'} "
                      f"size={rec.size} text={text!r} detail={rec.detail}", flush=True)
    except KeyboardInterrupt:
        print("收到 Ctrl+C，收工")
    finally:
        engine.stop()
        st = engine.status()
        print(f"引擎已停止。累计：发送 {st.get('sent')} / 接收 {st.get('received')} "
              f"/ 丢弃 {st.get('dropped')} / 错误 {st.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
