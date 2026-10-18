"""同步引擎单测：真起两条 TCP 连接（127.0.0.1 上的两个端口），但剪贴板用 MockBackend。

这样既验证了协议 / 握手 / ACK 全链路，又不需要真的占用系统剪贴板，跑在 CI 里也安全。

单机两个进程没法绑同一个端口，所以这里用两个不同端口互指 —— 生产环境两端的
`clipboard.port` 都是 47823，`peer_port` 留 0 即可。
"""

from __future__ import annotations

import socket
import time

import pytest

from dualswitch.clipboard import make_image, make_text
from dualswitch.clipboard.mock import MockBackend
from dualswitch.config import ClipboardConfig
from dualswitch.sync import protocol as P
from dualswitch.sync.engine import SyncEngine, parse_peer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _wait_until(predicate, timeout: float = 4.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture()
def pair():
    """两条引擎 + 两个 mock 后端，已握手完成。"""
    port_a, port_b = _free_port(), _free_port()
    key_hex = P.derive_key("123456").hex()
    cfg_a = ClipboardConfig(
        port=port_a, peer=f"127.0.0.1:{port_b}", psk=key_hex, device_id="mac-aaaa"
    )
    cfg_b = ClipboardConfig(
        port=port_b, peer=f"127.0.0.1:{port_a}", psk=key_hex, device_id="win-bbbb"
    )
    backend_a, backend_b = MockBackend(), MockBackend()
    engine_a = SyncEngine(cfg_a, host_label="mac", backend=backend_a)
    engine_b = SyncEngine(cfg_b, host_label="windows", backend=backend_b)
    engine_a.start()
    engine_b.start()
    ok = _wait_until(lambda: engine_a.status()["authenticated"] == "yes"
                     and engine_b.status()["authenticated"] == "yes", timeout=6.0)
    try:
        assert ok, f"握手未完成：A={engine_a.state} B={engine_b.state}"
        yield engine_a, backend_a, engine_b, backend_b
    finally:
        engine_a.stop()
        engine_b.stop()


def test_text_syncs_both_ways(pair):
    engine_a, backend_a, engine_b, backend_b = pair

    backend_a.feed(make_text("mac → windows"))
    engine_a.push_now()
    assert _wait_until(lambda: any(i.text == "mac → windows" for i in backend_b.written))

    backend_b.feed(make_text("windows → mac"))
    engine_b.push_now()
    assert _wait_until(lambda: any(i.text == "windows → mac" for i in backend_a.written))

    rec = engine_a.records()[0]
    assert rec.state == "ok" and rec.source == "remote"


def test_image_syncs_as_png(pair):
    engine_a, backend_a, _engine_b, backend_b = pair
    png = b"\x89PNG\r\n\x1a\n" + bytes(4096)
    item = make_image(png, 32, 32)
    assert item is not None

    backend_a.feed(item)
    engine_a.push_now()

    assert _wait_until(lambda: bool(backend_b.written)), "图片没有到达对端"
    got = backend_b.written[-1]
    assert got.image is not None
    assert got.image.png == png
    assert (got.image.width, got.image.height) == (32, 32)


def test_image_disabled_locally_records_without_sending(pair):
    engine_a, backend_a, _engine_b, backend_b = pair
    engine_a.cfg.image_enabled = False

    backend_a.feed(make_image(b"\x89PNG\r\n\x1a\n" + bytes(32), 4, 4))
    engine_a.push_now()
    time.sleep(0.4)

    assert not backend_b.written
    rec = engine_a.records()[0]
    assert rec.state == "local" and "图片" in rec.detail


def test_duplicate_digest_applied_once(pair):
    engine_a, backend_a, engine_b, backend_b = pair
    item = make_text("只该出现一次")

    backend_a.feed(item)
    engine_a.push_now()
    assert _wait_until(lambda: len(backend_b.written) == 1)

    engine_a.repush(item.digest)          # 强制重发同一条
    time.sleep(0.5)
    assert len(backend_b.written) == 1, "重复内容被应用了两次 → 环抑制第二层失效"
    assert engine_b.stats["dropped"] >= 1


def test_unpaired_engine_records_local_only():
    """没有 psk：能连上但不传内容，记录标成「仅本机」。"""
    port_a, port_b = _free_port(), _free_port()
    cfg_a = ClipboardConfig(port=port_a, peer=f"127.0.0.1:{port_b}", device_id="mac-cccc")
    cfg_b = ClipboardConfig(port=port_b, peer=f"127.0.0.1:{port_a}", device_id="win-dddd")
    backend_a, backend_b = MockBackend(), MockBackend()
    engine_a = SyncEngine(cfg_a, host_label="mac", backend=backend_a)
    engine_b = SyncEngine(cfg_b, host_label="windows", backend=backend_b)
    engine_a.start()
    engine_b.start()
    try:
        _wait_until(lambda: engine_a.state in ("unpaired", "connected"), timeout=6.0)
        backend_a.feed(make_text("未配对时不该传"))
        engine_a.push_now()
        time.sleep(0.5)
        assert not backend_b.written
        rec = engine_a.records()[0]
        assert rec.state == "local"
        assert rec.detail in ("未配对", "对端未连接")
    finally:
        engine_a.stop()
        engine_b.stop()


def test_stale_content_does_not_overwrite_newer():
    """契约 4.5 第三层：ts 更旧的内容不能覆盖更新的。"""
    engine = SyncEngine(ClipboardConfig(), backend=MockBackend(), clock=lambda: 1000.0)
    engine._last_applied_ts = 1000.0

    fresh = make_text("新内容")
    header, payload = P.item_to_message(fresh)
    header["ts"] = 1001.0
    engine._apply_remote(header, payload)
    assert engine._backend.written, "新内容应该被写入"

    stale = make_text("旧内容")
    header2, payload2 = P.item_to_message(stale)
    header2["ts"] = 900.0
    engine._apply_remote(header2, payload2)
    assert [i.text for i in engine._backend.written] == ["新内容"]
    assert engine.stats["dropped"] == 1


def test_records_ring_buffer_and_clear():
    engine = SyncEngine(ClipboardConfig(record_limit=3), backend=MockBackend())
    for i in range(5):
        engine._add(make_text(f"第{i}条"), source="local", state="local")
    records = engine.records()
    assert len(records) == 3
    assert records[0].text == "第4条"     # 最新的在最前
    engine.clear_records()
    assert engine.records() == []


def test_copy_to_local_writes_backend():
    backend = MockBackend()
    engine = SyncEngine(ClipboardConfig(), backend=backend)
    item = make_text("复制回本机")
    engine._add(item, source="remote", state="ok")
    assert engine.copy_to_local(item.digest) is True
    assert backend.written[-1].text == "复制回本机"
    assert engine.copy_to_local("不存在") is False


def test_parse_peer():
    assert parse_peer("", 47823) is None
    assert parse_peer("192.168.1.5", 47823) == ("192.168.1.5", 47823)
    assert parse_peer("192.168.1.5:5000", 47823) == ("192.168.1.5", 5000)
    assert parse_peer("10.0.0.2:notaport", 47823) == ("10.0.0.2", 47823)


def test_status_contains_needed_fields(pair):
    engine_a, _backend_a, _engine_b, _backend_b = pair
    status = engine_a.status()
    for key in ("enabled", "engine", "backend", "device", "peer", "authenticated",
                "records", "sent", "received", "last_sync"):
        assert key in status
    assert status["authenticated"] == "yes"
