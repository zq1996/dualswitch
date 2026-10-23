"""配对流程端到端自测（本机两个真实引擎，不碰真机、不碰真实剪贴板）。

为什么有这个工具：2026-10-23 用户实测「前端配对配不上，出码直接提示失效」，
但两端各自单测全绿、CLI 配对也通 —— 问题只在**两个真实引擎同时在线**时出现。
真机复现成本高（要两台机器 + 两个人），本机两个引擎就能把机制跑出来。

    python tools/e2e_pairing.py

三个场景：
  1. 空闲对端不得打死配对码 —— 两端都未配对，B 自动往 A 连，A 出码后码必须活着
     （当前实现 FAIL：码约 1.5 秒就被「5 次认证失败」作废，这正是用户看到的现象）
  2. 正常配对 —— A 出码、B 输码，两端都必须落盘成 paired
  3. 输错码 —— B 用错码，A 的码不应被误伤成「已失效」（除非真的猜满次数）

退出码：0 = 全过，1 = 有 FAIL（当前场景 1 会 FAIL，修好后转绿）。
"""

from __future__ import annotations

import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from dualswitch.clipboard.mock import MockBackend  # noqa: E402
from dualswitch.config import ClipboardConfig  # noqa: E402
from dualswitch.sync.engine import SyncEngine  # noqa: E402

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make(name: str, port: int, peer: str = "", psk: str = "", peer_device: str = ""):
    cfg = ClipboardConfig(
        port=port,
        peer=peer,
        psk=psk,
        peer_device=peer_device,
        device_id=name,
        discover_port=0,          # 手填 peer / 本机自测：绝不起广播（见 BUG-003）
    )
    return SyncEngine(cfg, host_label="windows" if name.startswith("win") else "mac",
                      backend=MockBackend())


def case_idle_peer_must_not_kill_code() -> None:
    """场景 1：对端只是「闲置着自动重连」，不该把出码端的配对码打成失效。

    真实链路（2026-10-23 19:28 用户实测）：
      两端都未配对 → A 点「显示配对码」→ B 引擎仍在按 ~200ms 无退避地自动重连 →
      每次连上都被 A 判「本端未配对」→ 计入 `_on_auth_failed` 的每 IP 计数 →
      5 次 ≈ 1.5 秒 → `cancel_pairing(errors=True)` → 码作废 → UI 弹「配对码已失效」。

    注意这些失败**不可能是猜码**：猜码者一定带着某个 key（他输了 PIN），
    失败形态是 HMAC 校验失败（走 ProtocolError，根本不进 `_on_auth_failed`）。
    所以「未配对」类失败被计入限流，是语义反了。
    """
    p_a, p_b = free_port(), free_port()
    a = make("win-show", p_a)
    b = make("mac-idle", p_b, peer=f"127.0.0.1:{p_a}")
    a.start()
    b.start()
    try:
        time.sleep(0.5)
        pin = a.begin_pairing()
        t0 = time.monotonic()
        died_at = None
        while time.monotonic() - t0 < 10.0:
            time.sleep(0.2)
            st = a.pairing_status()
            if st["outcome"] == "failed" or st["state"] != "showing":
                died_at = time.monotonic() - t0
                break
        if died_at is None:
            record("场景1 空闲对端不打死配对码", True, "码活了 10 秒")
        else:
            record(
                "场景1 空闲对端不打死配对码",
                False,
                f"码在 {died_at:.1f}s 就没了（err={a.pairing_status()['error']!r}）",
            )
    finally:
        a.stop()
        b.stop()


def case_normal_pairing() -> None:
    """场景 2：A 出码、B 输码 → 两端都必须落盘。"""
    p_a, p_b = free_port(), free_port()
    a = make("win-show", p_a)
    b = make("mac-enter", p_b)
    a.start()
    b.start()
    try:
        pin = a.begin_pairing()
        res = b.pair_with_peer(("127.0.0.1", p_a), pin, timeout=8.0)
        ok_enter = bool(res.get("ok"))
        # 出码端要等事件循环把「connected」回调跑完，最多 3 秒
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not a.cfg.psk:
            time.sleep(0.1)
        ok_show = bool(a.cfg.psk) and a.pairing_status()["outcome"] == "ok"
        record("场景2 输码端配对成功", ok_enter, f"peer={res.get('peer_device')}")
        record("场景2 出码端也落盘", ok_show,
               f"psk={'有' if a.cfg.psk else '空'} outcome={a.pairing_status()['outcome']!r}")
    finally:
        a.stop()
        b.stop()


def case_wrong_pin() -> None:
    """场景 3：输错码 → 输码端报失败，出码端的码**不该**立刻变成「已失效」。"""
    p_a, p_b = free_port(), free_port()
    a = make("win-show", p_a)
    b = make("mac-enter", p_b)
    a.start()
    b.start()
    try:
        pin = a.begin_pairing()
        wrong = "000000" if pin != "000000" else "111111"
        res = b.pair_with_peer(("127.0.0.1", p_a), wrong, timeout=4.0)
        st = a.pairing_status()
        record("场景3 输码端报错", not res.get("ok"), f"reason={res.get('reason')!r}")
        record("场景3 出码端的码没被误伤", st["state"] == "showing",
               f"state={st['state']} err={st['error']!r}")
    finally:
        a.stop()
        b.stop()


def main() -> int:
    print("配对流程端到端自测（两个真实引擎 / 127.0.0.1 真实 socket / 不碰真机）")
    print("-" * 66)
    case_idle_peer_must_not_kill_code()
    case_normal_pairing()
    case_wrong_pin()
    print("-" * 66)
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} 通过" + (f"，{len(failed)} 项失败" if failed else ""))
    if failed:
        print("\n已知问题：场景 1 的失败对应 BUG-007（出码即失效），根因见 tools/e2e_pairing.py 文档串。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
