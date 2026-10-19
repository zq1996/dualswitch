"""远端代发切屏（ISSUE-002）本机端到端自测。

为什么有这个工具：真机验收需要两台机器（Mac + Windows）同时在线，Mac 不在时
`docs/ISSUES.md` 里那条 issue 就一直悬着。但「两台机器」能验证的东西里，
只有**最后一次 DDC 写入**必须真机；其余环节（真实 TCP、认证、帧、回执、幂等、
拒绝、离线快速失败）在本机就能用**两个真实引擎**跑出来 —— 就是本工具。

    python tools/e2e_remote_switch.py

它起两个 SyncEngine（A 扮 Windows、B 扮 Mac），各占一个空闲端口，走 127.0.0.1
的真实 socket + 真实加密 + 真实握手，然后逐项验证。**不发真 DDC**：
执行方用一个记录型 executor，所以不会把显示器切走（切走了这台机器就看不见了）。

真机（有 Mac 时）还要额外确认两件事，本工具无法代替：
  1. 方向 A：Windows 日志出现 `替对端切屏 VCP 0x60=17 → ok`（Mac 请求 Windows 代发）
  2. 方向 B：Mac 端画面真的切回来（Windows 日志 `远端代发切屏 → ok`）
"""

from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dualswitch.clipboard.mock import MockBackend  # noqa: E402
from dualswitch.config import ClipboardConfig  # noqa: E402
from dualswitch.sync.engine import SyncEngine  # noqa: E402

VCP = 0x60
PSK = secrets.token_hex(32)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_engine(name: str, port: int, peer_port: int, vcp: int = VCP):
    calls: list[int] = []
    lock = threading.Lock()

    def executor(value: int) -> tuple[bool, str]:
        with lock:
            calls.append(int(value))
        return True, ""

    cfg = ClipboardConfig(
        port=port,
        peer_port=peer_port,
        peer="127.0.0.1",
        device_id=name,
        psk=PSK,
        discover_port=0,          # 手填了 peer，不会起广播；这里只是保险
    )
    engine = SyncEngine(
        cfg,
        host_label="windows" if name.startswith("win") else "mac",
        backend=MockBackend(),    # 不碰真实剪贴板
        switch_executor=executor,
        vcp_code=vcp,
    )
    return engine, calls, lock


def wait_ready(engine: SyncEngine, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if engine.switch_ready:
            return True
        time.sleep(0.05)
    return False


def case_real_executor() -> None:
    """真实 CLI 执行器：`_start_clip_engine` 到底有没有把 switch_executor 挂上。

    这一环最容易「看起来实现了、其实没接」——2026-10-19 合并时就出现过接线参数不匹配
    导致启动即崩。这里用 `dry_run=True` 跑真实装配：演练模式下执行方**必须明确拒绝**，
    返回 ok=False + 「演练模式」，绝不能假装成功。
    """
    from pathlib import Path

    from dualswitch.app import SwitchController
    from dualswitch.cli import _start_clip_engine
    from dualswitch.config import Config
    from dualswitch.ddc import create_backend

    port_cli, port_peer = free_port(), free_port()
    cfg = Config(dry_run=True)
    cfg.ddc_backend = "mock"
    cfg.clipboard = ClipboardConfig(
        port=port_cli,
        peer=f"127.0.0.1:{port_peer}",
        psk=PSK,
        device_id="win-cli-e2e",
        discover_port=0,
    )
    backend = create_backend(cfg)
    controller = SwitchController(cfg, backend)
    cfg_file = Path(".pytest-tmp/e2e-cli-config.json")
    cfg_file.parent.mkdir(parents=True, exist_ok=True)

    engine = _start_clip_engine(cfg, cfg_file, controller, backend)
    if engine is None:
        record("真实 CLI 执行器已挂上", False, "剪贴板引擎没起来")
        return

    peer, _, _ = make_engine("mac-e2e", port_peer, port_cli)
    peer.start()
    try:
        ready = wait_ready(peer, 10.0)
        ok, detail = peer.request_switch(17, timeout=3.0)
        record(
            "真实 CLI 执行器已挂上（演练模式明确拒绝）",
            ready and (not ok) and ("演练" in detail),
            f"回执 ok={ok} · {detail or '-'}",
        )
    finally:
        peer.stop()
        engine.stop()


def main() -> int:
    pa, pb = free_port(), free_port()
    a, calls_a, lock_a = make_engine("win-e2e", pa, pb)
    b, calls_b, lock_b = make_engine("mac-e2e", pb, pa)

    print(f"端口：A(Windows)={pa} B(Mac)={pb} · 加密=开 · 127.0.0.1 真实 socket")
    a.start()
    b.start()
    try:
        ok_a = wait_ready(a)
        ok_b = wait_ready(b)
        record("链路建立（两端都 authenticated）", ok_a and ok_b,
               f"A.ready={ok_a} B.ready={ok_b}")

        # ---- 方向 A：Windows 请 Mac 代发（画面在 USB-C，Windows 看不到屏）
        t0 = time.monotonic()
        ok, detail = a.request_switch(17)
        ms = (time.monotonic() - t0) * 1000
        with lock_b:
            got = list(calls_b)
        record("方向 A：Windows 请 Mac 代发 17", ok and got == [17],
               f"回执={detail or 'ok'} 执行方收到={got} 耗时 {ms:.0f}ms")

        # ---- 方向 B：Mac 请 Windows 代发
        t0 = time.monotonic()
        ok, detail = b.request_switch(18)
        ms = (time.monotonic() - t0) * 1000
        with lock_a:
            got = list(calls_a)
        record("方向 B：Mac 请 Windows 代发 18", ok and got == [18],
               f"回执={detail or 'ok'} 执行方收到={got} 耗时 {ms:.0f}ms")

        # ---- 幂等：同一个 req 连发两次，只执行一次
        with lock_b:
            before = len(calls_b)
        served_before = b.switch_stats["served"]
        req = secrets.token_hex(8)
        a._transport.send_switch({"vcp": VCP, "value": 18, "req": req})  # noqa: SLF001 - 自测工具
        time.sleep(0.4)
        a._transport.send_switch({"vcp": VCP, "value": 18, "req": req})  # noqa: SLF001
        time.sleep(0.8)
        with lock_b:
            after = len(calls_b)
        record("重复 req 只执行一次（幂等）", after - before == 1,
               f"执行次数 {after - before}（应为 1）· served +{b.switch_stats['served'] - served_before}")

        # ---- 非法 VCP 被拒（防止一端改了 VCP 另一端盲发到别的控制项）
        with lock_b:
            before = len(calls_b)
        failed_before = b.switch_stats["failed"]
        a._transport.send_switch({"vcp": 0x10, "value": 18, "req": secrets.token_hex(8)})  # noqa: SLF001
        time.sleep(0.8)
        with lock_b:
            after = len(calls_b)
        record("非法 VCP（0x10）被拒、不执行", after == before and b.switch_stats["failed"] > failed_before,
               f"执行次数 {after - before}（应为 0）· failed +{b.switch_stats['failed'] - failed_before}")

        # ---- 对端离线：必须立刻失败，不要白等 3 秒
        b.stop()
        time.sleep(0.3)
        t0 = time.monotonic()
        ok, detail = a.request_switch(17, timeout=3.0)
        ms = (time.monotonic() - t0) * 1000
        record("对端离线：立即失败（不等 3 秒超时）", (not ok) and ms < 500,
               f"耗时 {ms:.0f}ms · {detail}")

        # ---- 重启后能自愈（链路重连）
        b2, calls_b2, _ = make_engine("mac-e2e", pb, pa)
        b2.start()
        ok = wait_ready(a, 10.0) and wait_ready(b2, 10.0)
        ok2, detail = a.request_switch(17, timeout=3.0) if ok else (False, "未重连")
        record("对端重启后自动重连并可用", ok and ok2, f"重连={ok} 回执={detail or 'ok'}")
        b2.stop()
    finally:
        for engine in (a, b):
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass

    # ---- 真实 CLI 执行器（演练模式）：证明 switch_executor 真的挂上了
    case_real_executor()

    failed = [r for r in results if not r[1]]
    print("-" * 66)
    print(f"{len(results) - len(failed)}/{len(results)} 通过" + (f"，{len(failed)} 项失败" if failed else ""))
    print("说明：本工具不写真实 DDC（执行方是记录型 executor），所以不会把显示器切走。")
    print("真机还要补两条：Windows 日志 `替对端切屏 VCP 0x60=17 → ok`、Mac 日志 `远端代发切屏 → ok`。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
