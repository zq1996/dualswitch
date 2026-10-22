"""配对功能单测：状态机、密钥回滚、限流、身份绑定、广播字段。

这里不碰真实系统剪贴板，也不开真实监听端口（Transport 只做内存级验证）；
端到端的两条 TCP 链路在 `test_sync_engine.py` 里已经有覆盖。
"""

from __future__ import annotations

import json
import socket
import threading
import time

from dualswitch.config import ClipboardConfig, from_dict
from dualswitch.sync import protocol as P
from dualswitch.sync.discovery import Discovery
from dualswitch.sync.engine import SyncEngine
from dualswitch.sync.transport import Link, Transport


class FakeTransport:
    def __init__(self, key=None):
        self.key = key
        self.peer = None
        self.expected_peer_device = ""
        self.connected = False
        self.authenticated = False
        self.peer_device = ""
        self.peer_host = ""

    def set_key(self, key, *, drop_links: bool = True):  # noqa: ANN001
        self.key = key
        self.connected = False
        self.authenticated = False

    def set_expected_peer_device(self, device: str) -> None:
        self.expected_peer_device = str(device or "")

    def reconnect_now(self) -> None:
        pass

    def peer_addr(self) -> str:
        return f"{self.peer[0]}:{self.peer[1]}" if self.peer else ""

    def describe(self) -> str:
        return "fake"


def _engine(cfg=None, transport=None, saved=None):  # noqa: ANN001, ANN202
    cfg = cfg or ClipboardConfig(port=47823, device_id="mac-a")
    calls = saved if saved is not None else []
    engine = SyncEngine(
        cfg,
        transport=transport or FakeTransport(),
        save_config=lambda: calls.append(True),
    )
    return engine, cfg, calls


# --------------------------------------------------------------------------- #
# 1. A 端：显示码 → 认证通过 → 落盘
# --------------------------------------------------------------------------- #
def test_begin_pairing_uses_temp_key_without_touching_config():
    engine, cfg, saved = _engine()
    pin = engine.begin_pairing()
    assert len(pin) == 6 and pin.isdigit() and pin[0] != "0"
    assert engine._transport.key == P.derive_key(pin)
    assert cfg.psk == "", "显示配对码阶段不许写 config"
    assert saved == []
    status = engine.pairing_status()
    assert status["state"] == "showing"
    assert status["pin"] == pin
    assert 295 <= status["expires_in"] <= 300


def test_auth_success_persists_pairing_and_binds_device():
    engine, cfg, saved = _engine()
    pin = engine.begin_pairing()
    key = P.derive_key(pin)
    transport = engine._transport
    transport.authenticated = True
    transport.connected = True
    transport.peer_device = "windows-3f2a91c4"
    transport.peer_host = "DESKTOP-8Q2K"
    engine._on_state("connected", "ok")

    assert cfg.psk == key.hex()
    assert cfg.peer_device == "windows-3f2a91c4"
    assert cfg.peer_name == "DESKTOP-8Q2K"
    assert cfg.paired_at > 0
    assert transport.expected_peer_device == "windows-3f2a91c4"
    assert saved, "配对成功必须落盘"
    status = engine.pairing_status()
    assert status["paired"] is True
    assert status["state"] == "paired"
    assert status["outcome"] == "ok"


def test_cancel_restores_previous_key_without_writing():
    old = P.derive_key("111111")
    cfg = ClipboardConfig(port=47823, device_id="mac-a", psk=old.hex())
    transport = FakeTransport(key=old)
    engine, cfg, saved = _engine(cfg=cfg, transport=transport)

    engine.begin_pairing()
    assert transport.key != old
    engine.cancel_pairing()

    assert transport.key == old
    assert cfg.psk == old.hex()
    assert saved == [], "取消配对不许写 config"
    assert engine.pairing_status()["outcome"] == "cancelled"


def test_code_expires_after_five_minutes():
    now = [1000.0]
    cfg = ClipboardConfig(port=47823, device_id="mac-a")
    old = P.derive_key("222222")
    cfg.psk = old.hex()
    transport = FakeTransport(key=old)
    engine = SyncEngine(
        cfg, transport=transport, clock=lambda: now[0], save_config=lambda: None
    )
    pin = engine.begin_pairing()
    now[0] += 301
    status = engine.pairing_status()
    assert status["state"] != "showing"
    assert status["outcome"] == "failed"
    assert "过期" in status["error"]
    assert engine._pair_pin == ""
    assert transport.key == old
    assert cfg.psk == old.hex()


def test_five_auth_failures_invalidate_code():
    engine, cfg, saved = _engine()
    pin = engine.begin_pairing()
    for _ in range(5):
        engine._on_auth_failed("192.168.1.31", "密钥不匹配")
    status = engine.pairing_status()
    assert status["pin"] == ""
    assert status["outcome"] == "failed"
    assert "失效" in status["error"]
    assert engine._transport.key is None
    assert saved == []


# --------------------------------------------------------------------------- #
# 2. B 端：输入码 → 连接 / 认证
# --------------------------------------------------------------------------- #
def test_pair_with_peer_success_waits_for_authentication():
    engine, cfg, saved = _engine()
    transport = engine._transport

    def drive():
        time.sleep(0.05)
        transport.authenticated = True
        transport.connected = True
        transport.peer_device = "windows-3f2a91c4"
        transport.peer_host = "DESKTOP-8Q2K"
        engine._on_state("connected", "ok")

    threading.Thread(target=drive, daemon=True).start()
    result = engine.pair_with_peer(("192.168.1.31", 47823), "123456", timeout=3.0)
    assert result["ok"] is True
    assert cfg.psk == P.derive_key("123456").hex()
    assert cfg.peer_device == "windows-3f2a91c4"
    assert transport.expected_peer_device == "windows-3f2a91c4"
    assert saved


def test_pair_with_peer_wrong_pin_keeps_old_config():
    old = P.derive_key("111111")
    cfg = ClipboardConfig(port=47823, device_id="mac-a", psk=old.hex())
    transport = FakeTransport(key=old)
    engine, cfg, saved = _engine(cfg=cfg, transport=transport)

    def reject():
        time.sleep(0.05)
        transport.authenticated = False
        transport.connected = False
        engine._on_state("unpaired", "密钥不匹配")

    threading.Thread(target=reject, daemon=True).start()
    result = engine.pair_with_peer(("192.168.1.31", 47823), "654321", timeout=3.0)
    assert result["ok"] is False
    assert result["kind"] == "auth"
    assert cfg.psk == old.hex()
    assert transport.key == old, "失败要换回旧 key，不能把已配对弄丢"
    assert engine.pairing_status()["attempts_left"] == 4


def test_pair_with_peer_timeout_restores_old_key():
    old = P.derive_key("111111")
    cfg = ClipboardConfig(port=47823, device_id="mac-a", psk=old.hex())
    transport = FakeTransport(key=old)
    engine, cfg, saved = _engine(cfg=cfg, transport=transport)

    result = engine.pair_with_peer(("192.168.1.31", 47823), "654321", timeout=0.1)
    assert result["ok"] is False
    assert result["kind"] == "timeout"
    assert transport.key == old
    assert cfg.psk == old.hex()
    assert saved == []
    assert engine.pairing_status()["outcome"] == "failed"


def test_pair_with_peer_stops_after_five_attempts():
    engine, cfg, saved = _engine()
    for _ in range(5):
        engine.pair_with_peer(("1.2.3.4", 47823), "123456", timeout=0.01)
    result = engine.pair_with_peer(("1.2.3.4", 47823), "123456", timeout=0.01)
    assert result["ok"] is False
    assert result["kind"] == "max_attempts"
    assert cfg.psk == ""


# --------------------------------------------------------------------------- #
# 3. 解除配对 / 配置
# --------------------------------------------------------------------------- #
def test_unpair_only_clears_local_state():
    key = P.derive_key("123456")
    cfg = ClipboardConfig(
        port=47823, device_id="mac-a", psk=key.hex(),
        peer_device="win-b", peer_name="DESKTOP", paired_at=123.0,
    )
    transport = FakeTransport(key=key)
    engine, cfg, saved = _engine(cfg=cfg, transport=transport)
    engine.unpair()
    assert cfg.psk == ""
    assert cfg.peer_device == ""
    assert cfg.peer_name == ""
    assert cfg.paired_at == 0.0
    assert transport.key is None
    assert transport.expected_peer_device == ""
    assert saved
    assert engine.pairing_status()["paired"] is False


def test_paired_at_roundtrips_through_config():
    cfg = from_dict({"clipboard": {"paired_at": 1234.5, "psk": "00" * 32}})
    assert cfg.clipboard.paired_at == 1234.5


# --------------------------------------------------------------------------- #
# 4. 身份绑定（Link 层）
# --------------------------------------------------------------------------- #
def _tcp_pair():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    client = socket.create_connection(srv.getsockname(), timeout=2.0)
    conn, _addr = srv.accept()
    srv.close()
    return conn, client


def _link(sock, key, expected="", failed=None):  # noqa: ANN001, ANN202
    return Link(
        sock,
        key=key,
        device_id="mac-a",
        host_label="mac",
        direction="in",
        on_frame=lambda link, frame: None,
        on_ready=lambda link: None,
        on_closed=lambda link, reason: None,
        expected_peer_device=expected,
        on_auth_failed=failed,
    )


def test_link_rejects_other_device_even_with_correct_key():
    key = P.derive_key("123456")
    left, right = _tcp_pair()
    failures = []
    link = _link(left, key, expected="win-b", failed=lambda l, r: failures.append(r))
    link._dispatch(P.Frame(P.T_HELLO, P.hello_header("win-other", "OTHER", key)))

    frames = P.FrameReader(key).feed(right.recv(65536))
    assert frames and frames[0].type == P.T_HELLO_OK
    assert frames[0].header["ok"] is False
    assert "另一台" in frames[0].header["reason"]
    assert failures and "另一台" in failures[0]
    assert not link.alive
    right.close()


def test_link_accepts_bound_device():
    key = P.derive_key("123456")
    left, right = _tcp_pair()
    link = _link(left, key, expected="win-b")
    link._dispatch(P.Frame(P.T_HELLO, P.hello_header("win-b", "DESKTOP", key)))

    frames = P.FrameReader(key).feed(right.recv(65536))
    assert frames[0].header["ok"] is True
    assert link.authenticated is True
    link.close("测试结束", notify=False)
    right.close()


# --------------------------------------------------------------------------- #
# 5. Transport.set_key 会断开旧连接
# --------------------------------------------------------------------------- #
def test_transport_set_key_drops_old_links():
    class DummyLink:
        alive = True
        def __init__(self):
            self.closed = ""

        def close(self, reason, notify=True):  # noqa: ANN001
            self.closed = reason
            self.alive = False

    old = P.derive_key("111111")
    new = P.derive_key("222222")
    transport = Transport(device_id="mac-a", listen_port=0, key=old)
    dummy = DummyLink()
    transport._links[id(dummy)] = dummy
    transport._link = dummy
    transport.authenticated = True

    transport.set_key(new)
    assert transport.key == new
    assert dummy.closed == "配对密钥切换"
    assert transport.authenticated is False
    assert transport._link is None


# --------------------------------------------------------------------------- #
# 6. 广播字段
# --------------------------------------------------------------------------- #
def test_discovery_broadcast_adds_pairing_fields():
    d = Discovery(
        device_id="mac-a", host_label="zq-mac-mini", service_port=47823,
        platform="macos", version="1.0.0",
    )

    class Sock:
        def __init__(self):
            self.sent = []

        def sendto(self, raw, addr):  # noqa: ANN001
            self.sent.append(raw)

    sock = Sock()
    d._sock = sock
    d.pairing = True
    d.paired = False
    d.announce_now()
    info = json.loads(sock.sent[-1].decode("utf-8"))
    assert info["host"] == "zq-mac-mini"
    assert info["plat"] == "macos"
    assert info["ver"] == "1.0.0"
    assert info["pairing"] is True
    assert info["paired"] is False

    d.paired = True
    d.announce_now()
    assert json.loads(sock.sent[-1].decode("utf-8"))["paired"] is True


def test_discovery_peer_ttl():
    d = Discovery(device_id="a", host_label="a", service_port=1)
    d.peers["old"] = {"device": "old", "seen": time.time() - 60}
    d.peers["new"] = {"device": "new", "seen": time.time()}
    alive = d.alive_peers(max_age=20)
    assert [p["device"] for p in alive] == ["new"]
    assert "old" not in d.peers
