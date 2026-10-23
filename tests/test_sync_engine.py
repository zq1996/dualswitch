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
from dualswitch.clipboard.base import make_file
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


def test_file_record_stays_local(pair, tmp_path):
    """文件记录只在本机留一条，**不发给对端**（`path` 不上网，契约 P2）。"""
    engine_a, backend_a, _engine_b, backend_b = pair
    target = tmp_path / "报告.pdf"
    target.write_bytes(b"%PDF-1.4 fake")
    item = make_file(str(target), size=target.stat().st_size)
    assert item is not None

    backend_a.feed(item)
    engine_a.push_now()
    time.sleep(0.4)

    assert not backend_b.written, "文件本身不该跨端"
    rec = engine_a.records()[0]
    assert rec.path == str(target)
    assert rec.file_size == target.stat().st_size
    assert rec.state == "local"


def test_image_file_syncs_image_but_not_path(pair, tmp_path):
    """图片文件：图片照常同步（对端能看到图），路径只留在本机。"""
    engine_a, backend_a, engine_b, backend_b = pair
    png = b"\x89PNG\r\n\x1a\n" + bytes(64)
    target = tmp_path / "湖畔.png"
    target.write_bytes(png)
    item = make_file(str(target), png=png, width=8, height=8, size=len(png))
    assert item is not None and item.image is not None

    backend_a.feed(item)
    engine_a.push_now()

    assert _wait_until(lambda: bool(backend_b.written)), "图片文件里的图没有同步过去"
    assert backend_b.written[-1].image is not None
    assert backend_b.written[-1].path == "", "路径不该上网"
    assert engine_b.records()[0].path == ""


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


def test_discovery_disabled_when_port_not_positive():
    """`discover_port<=0` 必须彻底不广播（BUG-003）。

    自测 / e2e 脚本在**真机上**跑临时引擎时，如果它们照样向 47824 广播一个随机服务端口，
    正在运行的对端就会把拨号目标改成那个马上关闭的端口，日志每 3 秒刷一次 timed out。
    这里直接断言"没有 socket、没有线程"，比抓包更稳。
    """
    from dualswitch.sync.discovery import Discovery

    for port in (0, -1):
        disc = Discovery(device_id="mac-test", host_label="mac", service_port=47823, discover_port=port)
        disc.start()
        assert disc._sock is None, f"discover_port={port} 时不该建 socket"
        assert disc._threads == [], f"discover_port={port} 时不该起线程"

        # 广播入口也要是空操作，而不是往一个非法端口 sendto
        disc.announce_now()  # 不抛异常即通过
        disc.stop()


def test_no_discover_env_disables_broadcast(monkeypatch):
    """`DUALSWITCH_NO_DISCOVER=1` 时引擎不得起发现（BUG-003 的进程级开关）。"""
    import socket as _socket

    from dualswitch.clipboard.mock import MockBackend

    monkeypatch.setenv("DUALSWITCH_NO_DISCOVER", "1")
    port = _free_port()
    cfg = ClipboardConfig(port=port, psk="00" * 32, device_id="mac-nodiscover")
    cfg.discover_port = 47824                     # 故意保留默认值
    engine = SyncEngine(cfg, host_label="mac", backend=MockBackend())
    try:
        engine.start()
        disc = engine._discovery
        assert disc is not None
        assert disc.discover_port == 0, "环境变量没生效"
        assert disc._sock is None, "不该建发现 socket"
        assert disc._threads == [], "不该起广播线程"
    finally:
        engine.stop()


def test_unpaired_engine_does_not_dial_out():
    """没有 psk（key=None）就不该主动外连（BUG-006）。

    未配对的机器没有任何东西能同步，连上去只会制造一次认证失败，
    然后 200ms 再连一次 —— 对端出配对码时会被打到「配对码已失效」。
    """
    from dualswitch.sync.transport import Transport

    port = _free_port()
    transport = Transport(
        device_id="mac-nokey", listen_port=_free_port(), host_label="mac",
        key=None, peer=("127.0.0.1", port),
    )
    transport.start()
    try:
        time.sleep(1.2)
        assert transport._state == "idle", f"未配对却在拨号：state={transport._state}"
    finally:
        transport.stop()


def test_auth_fail_waits_grow_and_reset():
    """认证失败后的重连等待要指数退避，握手成功要复位（BUG-006）。"""
    from dualswitch.sync.transport import (
        AUTH_FAIL_MAX_WAIT_S, AUTH_FAIL_MIN_WAIT_S, Transport,
    )

    transport = Transport(
        device_id="mac-backoff", listen_port=_free_port(), host_label="mac",
        key=b"\x00" * 32, peer=None,
    )
    assert transport._auth_fail_wait == AUTH_FAIL_MIN_WAIT_S

    class _Link:
        peer_addr = ("127.0.0.1", 12345)

    waits = []
    for _ in range(8):
        transport._handle_auth_failed(_Link(), "对端未配对")
        waits.append(transport._auth_fail_wait)
    assert waits[0] > AUTH_FAIL_MIN_WAIT_S, f"第一次失败就该开始退避：{waits}"
    assert waits == sorted(waits), f"等待时间必须单调不降：{waits}"
    assert max(waits) <= AUTH_FAIL_MAX_WAIT_S

    transport._handle_ready(type("L", (), {"authenticated": True, "peer_device": "", "peer_host": "",
                                           "describe": lambda self: "x"})())
    assert transport._auth_fail_wait == AUTH_FAIL_MIN_WAIT_S, "握手成功要复位"
