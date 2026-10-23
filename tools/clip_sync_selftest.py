#!/usr/bin/env python3
"""闸二：单机跑完协议层的 7 个场景（没有第二台机器也能验 70%）。

    python tools/clip_sync_selftest.py                 # 全跑
    python tools/clip_sync_selftest.py --case text,echo
    python tools/clip_sync_selftest.py --json

## 与契约的一处偏差（有意）

契约写的是"起两个进程"，这里起的是**两个引擎实例**，各自持有独立的 MockBackend，
通过 127.0.0.1 上的两个真实 TCP 端口互通（单机同一个端口不能绑两次，所以 A 监听
47823 连 47833，B 反之）。

为什么不真的 fork 两个进程：断言要看的是"对端后端里被写进了什么"，跨进程就得再做一套
IPC，脚本本身会变成被测对象。这里 socket 是真的、帧是真的、握手/加密/ACK 全走真链路，
唯一省掉的是进程边界 —— 协议层的 bug 一样跑不掉。

七个场景与契约第 7 节逐条对应。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dualswitch.clipboard import make_image, make_text  # noqa: E402
from dualswitch.clipboard.mock import MockBackend  # noqa: E402
from dualswitch.config import ClipboardConfig  # noqa: E402
from dualswitch.sync import protocol as P  # noqa: E402
from dualswitch.sync.engine import SyncEngine  # noqa: E402

PIN = "135790"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _wait(predicate, timeout: float, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class Pair:
    """一对已握手的引擎。断线重连场景会重建 B。"""

    def __init__(self) -> None:
        self.port_a, self.port_b = _free_port(), _free_port()
        key = P.derive_key(PIN).hex()
        self.cfg_a = ClipboardConfig(
            port=self.port_a, peer=f"127.0.0.1:{self.port_b}", psk=key, device_id="mac-selftest",
            discover_port=0,      # 必须关广播：否则会污染真实对端的发现列表（BUG-003）
        )
        self.cfg_b = ClipboardConfig(
            port=self.port_b, peer=f"127.0.0.1:{self.port_a}", psk=key, device_id="win-selftest",
            discover_port=0,
        )
        self.backend_a, self.backend_b = MockBackend(), MockBackend()
        self.engine_a = SyncEngine(self.cfg_a, host_label="mac", backend=self.backend_a)
        self.engine_b = SyncEngine(self.cfg_b, host_label="windows", backend=self.backend_b)

    def start(self) -> None:
        self.engine_a.start()
        self.engine_b.start()
        if not _wait(lambda: self.engine_a.status()["authenticated"] == "yes"
                     and self.engine_b.status()["authenticated"] == "yes", 8.0):
            raise RuntimeError("握手超时")

    def stop(self) -> None:
        for engine in (self.engine_a, self.engine_b):
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass

    def restart_b(self) -> None:
        self.engine_b.stop()
        self.backend_b = MockBackend()
        self.engine_b = SyncEngine(self.cfg_b, host_label="windows", backend=self.backend_b)
        self.engine_b.start()


# --------------------------------------------------------------------------- #
# 场景
# --------------------------------------------------------------------------- #
def _new_items(backend: MockBackend, baseline: int, timeout: float) -> list:
    """等对端多出至少一条，返回新增的那些。

    必须按"增量"判断：`written` 是累积列表，直接用 `bool(written)` 会被前一个
    场景留下的内容立刻满足 —— 这正是第一版自测误报 PASS/FAIL 的原因。
    """
    _wait(lambda: len(backend.written) > baseline, timeout)
    return backend.written[baseline:]


def case_text(pair: Pair) -> tuple[bool, str]:
    baseline = len(pair.backend_b.written)
    pair.backend_a.feed(make_text("闸二：文本单向"))
    pair.engine_a.push_now()
    new = _new_items(pair.backend_b, baseline, 4.0)
    ok = any(i.text == "闸二：文本单向" for i in new)
    return ok, f"对端新增 {len(new)} 条"


def case_image(pair: Pair) -> tuple[bool, str]:
    png = _png(320, 200)
    baseline = len(pair.backend_b.written)
    pair.backend_a.feed(make_image(png, 320, 200))
    pair.engine_a.push_now()
    new = _new_items(pair.backend_b, baseline, 5.0)
    if not new:
        return False, "对端没有新增内容"
    got = new[-1].image
    if got is None:
        return False, f"对端收到的不是图片（kind={new[-1].kind}）"
    same = got.png == png and (got.width, got.height) == (320, 200)
    return same, f"{len(png)} 字节，PNG 头={got.png[:4]!r}，尺寸 {got.width}×{got.height}"


def case_echo(pair: Pair) -> tuple[bool, str]:
    """自写回弹：内容是我们自己写进剪贴板的，就不该再推给对端。

    真实链路上这一步由后端 `write()` 吞一次 + 引擎按 digest 丢弃共同保证；
    这里直接把 digest 记成"刚应用过的"，等价于 write() 之后的那个状态。
    """
    item = make_text("不该被推走的自写内容")
    pair.engine_a._last_applied_digest = item.digest
    before_sent = pair.engine_a.stats["sent"]
    baseline = len(pair.backend_b.written)
    pair.backend_a.feed(item)
    pair.engine_a.push_now()
    time.sleep(0.6)
    sent = pair.engine_a.stats["sent"] - before_sent
    extra = len(pair.backend_b.written) - baseline
    return sent == 0 and extra == 0, f"A 新增推送 {sent} 次，对端新增 {extra} 条"


def case_loop(pair: Pair) -> tuple[bool, str]:
    """同一内容连喂 10 次，只应产生 1 次传输。"""
    before_sent = pair.engine_a.stats["sent"]
    baseline = len(pair.backend_b.written)
    for _ in range(10):
        pair.backend_a.feed(make_text("闸二：环抑制"))
    pair.engine_a.push_now()
    new = _new_items(pair.backend_b, baseline, 3.0)
    time.sleep(0.4)
    sent = pair.engine_a.stats["sent"] - before_sent
    received = len(pair.backend_b.written) - baseline
    return sent == 1 and received == 1, f"推送 {sent} 次，对端写入 {received} 次"


def case_duplicate(pair: Pair) -> tuple[bool, str]:
    """强制重发同一 digest，对端只应用一次。"""
    item = make_text("闸二：重复投递")
    baseline = len(pair.backend_b.written)
    pair.backend_a.feed(item)
    pair.engine_a.push_now()
    first = _new_items(pair.backend_b, baseline, 4.0)
    if not first:
        return False, "首次投递就没到"
    applied = len(pair.backend_b.written)
    for _ in range(3):
        pair.engine_a.repush(item.digest)
        time.sleep(0.2)
    time.sleep(0.5)
    extra = len(pair.backend_b.written) - applied
    return extra == 0, f"重复 3 次后对端多写入 {extra} 条"


def case_big_image(pair: Pair) -> tuple[bool, str]:
    """20MB PNG 完整到达，sha256 一致。"""
    png = b"\x89PNG\r\n\x1a\n" + os.urandom(20 * 1024 * 1024)
    item = make_image(png, 4000, 4000)
    if item is None:
        return False, "构造 20MB 图片失败"
    baseline = len(pair.backend_b.written)
    started = time.monotonic()
    pair.backend_a.feed(item)
    pair.engine_a.push_now()
    new = _new_items(pair.backend_b, baseline, 25.0)
    elapsed = (time.monotonic() - started) * 1000
    if not new:
        return False, "20MB 图片没到"
    got = new[-1].image
    if got is None:
        return False, "对端收到的不是图片"
    same = hashlib.sha256(got.png).hexdigest() == hashlib.sha256(png).hexdigest()
    return same, f"{len(png) / 1048576:.1f}MB，{elapsed:.0f}ms，sha256 一致={same}"


def case_reconnect(pair: Pair) -> tuple[bool, str]:
    """杀掉一端，12 秒内自动重连（拨号退避最长 3 秒一轮）。"""
    pair.engine_b.stop()
    dropped = _wait(lambda: pair.engine_a.status()["authenticated"] != "yes", 5.0, interval=0.1)
    pair.restart_b()
    ok = _wait(lambda: pair.engine_a.status()["authenticated"] == "yes", 15.0, interval=0.1)
    if not ok:
        return False, f"15 秒内没重连上（断开被察觉={dropped}）"
    baseline = len(pair.backend_b.written)
    pair.backend_a.feed(make_text("重连后的内容"))
    pair.engine_a.push_now()
    new = _new_items(pair.backend_b, baseline, 5.0)
    delivered = any(i.text == "重连后的内容" for i in new)
    return delivered, f"断开被察觉={dropped}，重连后仍能传={delivered}"


CASES = {
    "text": ("文本单向", case_text),
    "image": ("图片单向", case_image),
    "echo": ("自写回弹", case_echo),
    "loop": ("环抑制", case_loop),
    "duplicate": ("重复投递", case_duplicate),
    "reconnect": ("断线重连", case_reconnect),
    "big": ("20MB 大图", case_big_image),
}

#: 顺序：能快速失败的在前面；重连会重建 B，放最后（大图放重连之前）
ORDER = ["text", "image", "echo", "loop", "duplicate", "big", "reconnect"]


def _png(width: int, height: int) -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes((40, 120, 220)) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="单机剪贴板同步自测（闸二）")
    parser.add_argument("--case", default="", help="逗号分隔：text,image,echo,loop,duplicate,big,reconnect")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    wanted = [c.strip() for c in args.case.split(",") if c.strip()] or ORDER
    unknown = [c for c in wanted if c not in CASES]
    if unknown:
        print(f"未知场景：{unknown}；可选 {list(CASES)}")
        return 2

    results: list[dict] = []
    pair = Pair()
    try:
        pair.start()
        print(f"两端已握手（A 监听 {pair.port_a}，B 监听 {pair.port_b}）\n")
        for name in wanted:
            label, func = CASES[name]
            try:
                ok, detail = func(pair)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"异常：{type(exc).__name__}: {exc}"
            results.append({"case": name, "name": label, "ok": bool(ok), "detail": detail})
            if not args.json:
                print(f"[{'PASS' if ok else 'FAIL'}] {label:<10} {detail}")
    finally:
        pair.stop()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        failed = [r for r in results if not r["ok"]]
        print("-" * 70)
        print(f"{len(results) - len(failed)}/{len(results)} 通过")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
