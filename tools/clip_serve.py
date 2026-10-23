#!/usr/bin/env python3
"""常驻剪贴板引擎（联调用的最小宿主，等价于 GUI 里那个引擎，但没有界面）。

为什么要它：`dualswitch clip status/send` 跑完就 stop，端口只在那一瞬间开着，
两台机器永远碰不上。联调时一端必须有个长期活着的监听。

    python tools/clip_serve.py                 # 用当前配置（端口 / 配对密钥 / 设备 ID）
    python tools/clip_serve.py --seconds 600   # 到点自动退出
    python tools/clip_serve.py --port 47823 --log outputs/clip_serve.log

mac 上必须用带 pyobjc 的解释器，否则后端会静默降级成 NullBackend：
    ~/.workbuddy/binaries/python/envs/default/bin/python tools/clip_serve.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dualswitch import config as config_mod  # noqa: E402
from dualswitch.clipboard import make_image, make_text  # noqa: E402
from dualswitch.sync.engine import SyncEngine  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="常驻剪贴板同步引擎（无界面）")
    ap.add_argument("--seconds", type=float, default=0, help="0 = 一直跑")
    ap.add_argument("--peer", default="", help="手填对端 ip[:port]，留空则用配置里的")
    ap.add_argument("--port", type=int, default=0, help="覆盖监听端口（默认用配置）")
    ap.add_argument("--send", default="", help="认证通过后推一条文本，然后继续常驻")
    ap.add_argument("--send-image", default="", help="同上，推一张 PNG")
    ap.add_argument("--send-wait", type=float, default=8.0, help="等认证的上限（秒）")
    args = ap.parse_args(argv)

    cfg = config_mod.load()
    cb = cfg.clipboard
    if args.port:
        cb.port = int(args.port)
    if args.peer:
        cb.peer = args.peer

    print(f"设备 {cb.device_id} / 监听 {cb.port} / 发现 {cb.discover_port} / "
          f"对端 {cb.peer or '(发现)'} / 已配对 {'是' if cb.psk else '否'}", flush=True)
    if not cb.psk:
        print("⚠️  没有配对密钥，握手会被拒；先跑 clip pair --pin <码>", flush=True)

    engine = SyncEngine(cb, host_label=cfg.effective_host())
    engine.start()

    # ---- 可选：在同一个进程里发包（`clip send` 会另起引擎，跟这里的 47823 抢端口）----
    if args.send or args.send_image:
        deadline = time.monotonic() + args.send_wait
        while time.monotonic() < deadline:
            if engine.status().get("authenticated") == "yes":
                break
            time.sleep(0.1)
        ok_auth = engine.status().get("authenticated") == "yes"
        print(f"认证={'yes' if ok_auth else 'no'}，开始推送…", flush=True)
        if not ok_auth:
            print("⚠️  等不到认证，取消推送（对端没起来 / 密钥不一致）", flush=True)
        elif args.send_image:
            png = Path(args.send_image).expanduser().read_bytes()
            item = make_image(png)
            if item is None:
                print("⚠️  图片超过上限，未发送", flush=True)
            else:
                engine.push_item(item)
                print(f"已推图片 {len(png)} 字节", flush=True)
        else:
            engine.push_item(make_text(args.send))
            print(f"已推文本 {len(args.send)} 字：{args.send!r}", flush=True)
        time.sleep(1.5)                      # 等 ACK 回来
        for rec in engine.records()[:1]:
            print(f"记录 {rec.id[:8]} state={rec.state} detail={rec.detail}", flush=True)

    deadline = time.monotonic() + args.seconds if args.seconds else None
    last = ""
    try:
        while True:
            st = engine.status()
            line = (f"引擎={st.get('engine')} 认证={st.get('authenticated')} "
                    f"对端={st.get('peer')} 发={st.get('sent')} 收={st.get('received')} "
                    f"丢={st.get('dropped')} 记录={st.get('records')}")
            if line != last:                      # 只在变化时打印，日志可读
                print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)
                last = line
            if deadline and time.monotonic() >= deadline:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        print("引擎已停止", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
