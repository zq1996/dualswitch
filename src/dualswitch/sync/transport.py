"""对称 TCP 传输：两端都监听、也都主动连，没有 server / client 之分。

为什么对称：契约 4.1 定的。好处是两端代码几乎一样，联调时不用先判"到底哪边该监听"。
代价是两端可能同时连上对方，所以这里有一条明确的取舍规则：

    **已经 ready 的连接就是赢家，新来的连接一律关掉。**

一旦某条连接握手成功，它就一直用下去，直到心跳超时或对端断开；那时两条线程
（监听 / 主动连）各自按退避重试，谁先连上谁就是新赢家。这样不会出现两条链路互相顶掉
的振荡，而"多一条空闲连接"这种状态最多持续到下一次握手完成。

一条连接的职责划分：
    Link         一帧一帧地收发，负责 HELLO 握手、心跳、加解密计数
    Transport    监听 + 拨号 + 重连退避 + 选一个赢家，向上抛 `on_clip` / `on_ack` / `on_state`

⚠️ `on_clip` / `on_ack` 是在 **socket 读线程**里回调的，回调里不要做耗时操作
（写剪贴板、解码大图都不行）。`sync/engine.py` 会把它转成队列交给自己的工作线程。
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any, Callable

from . import protocol as P

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 4.0
RECV_TIMEOUT_S = 0.5
HELLO_TIMEOUT_S = 6.0
HEARTBEAT_INTERVAL_S = 12.0
HEARTBEAT_TIMEOUT_S = 40.0
RECONNECT_INTERVAL_S = 3.0
#: 认证失败后的重连间隔：从 0.2s 指数涨到 5s（BUG-006）。
#: 以前是固定 0.2s —— 认证失败时链路 200ms 就重来一次，2 秒 20 次，
#: 日志刷屏、CPU 空转，还会把对端的「配对码失败次数」打满（详见 engine._on_auth_failed）。
AUTH_FAIL_MIN_WAIT_S = 0.2
AUTH_FAIL_MAX_WAIT_S = 5.0
MAX_FRAME_READ = 1 << 20  # 单次 recv 上限；大图靠多轮 recv 拼


class LinkError(RuntimeError):
    pass


class Link:
    """一条已完成（或正在完成）HELLO 握手的 TCP 连接。"""

    def __init__(
        self,
        sock: socket.socket,
        *,
        key: bytes | None,
        device_id: str,
        host_label: str,
        direction: str,
        on_frame: Callable[["Link", P.Frame], None],
        on_ready: Callable[["Link"], None],
        on_closed: Callable[["Link", str], None],
        expected_peer_device: str = "",
        on_auth_failed: Callable[["Link", str], None] | None = None,
    ) -> None:
        self.sock = sock
        self.key = key
        self.device_id = device_id
        self.host_label = host_label
        self.direction = direction          # "in" | "out"
        # 配对后的身份绑定：只接受 HELLO.device 等于它的连接
        self.expected_peer_device = expected_peer_device
        self._on_auth_failed = on_auth_failed
        self.peer_addr = _peer_addr(sock)
        self.peer_device = ""
        self.peer_host = ""
        self.authenticated = False

        self._on_frame = on_frame
        self._on_ready = on_ready
        self._on_closed = on_closed

        self._reader = P.FrameReader(key)
        self._send_lock = threading.Lock()
        self._send_counter = 0
        self._alive = True
        self._stop = threading.Event()      # close() 置位：让读写 / 心跳线程立刻醒，别睡满间隔
        self._ready = False
        self._last_rx = time.monotonic()
        self._closed_reason = ""
        self._thread: threading.Thread | None = None
        self._beat: threading.Thread | None = None

    # ------------------------------------------------------------------ 状态
    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def idle_s(self) -> float:
        return time.monotonic() - self._last_rx

    def describe(self) -> str:
        who = self.peer_host or "对端"
        dev = self.peer_device or "?"
        tag = "" if self.authenticated else "（未配对）"
        return f"{who}{tag} {dev} {self.peer_addr[0]}:{self.peer_addr[1]}/{self.direction}"

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        # 先发 HELLO 再起线程：反过来的话读线程可能先收到对端 HELLO 并触发选举，
        # 把这条还没发 HELLO 的连接判负关掉，`send` 就会撞上"连接已关闭"。
        try:
            self.send(P.T_HELLO, P.hello_header(self.device_id, self.host_label, self.key))
        except (OSError, LinkError) as exc:
            self.close(f"发 HELLO 失败：{exc}", notify=False)
            return
        self._thread = threading.Thread(
            target=self._read_loop, name=f"clip-link-{self.direction}", daemon=True
        )
        self._thread.start()
        self._beat = threading.Thread(
            target=self._beat_loop, name=f"clip-beat-{self.direction}", daemon=True
        )
        self._beat.start()

    def close(self, reason: str = "主动断开", *, notify: bool = True) -> None:
        if not self._alive:
            return
        self._alive = False
        self._stop.set()                     # 先叫醒睡眠中的心跳 / 读线程
        self._closed_reason = reason
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        if notify:
            self._on_closed(self, reason)

    # ------------------------------------------------------------------ 收发
    def send(self, msg_type: int, header: dict[str, Any], payload: bytes = b"") -> None:
        with self._send_lock:
            if not self._alive:
                raise LinkError("连接已关闭")
            counter = self._send_counter
            self._send_counter += 1
            raw = P.encode_frame(msg_type, header, payload, key=self.key, counter=counter)
            self.sock.sendall(raw)

    def _read_loop(self) -> None:
        self.sock.settimeout(RECV_TIMEOUT_S)
        hello_deadline = time.monotonic() + HELLO_TIMEOUT_S
        while self._alive:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                if not self._ready and time.monotonic() > hello_deadline:
                    self.close("握手超时")
                    return
                continue
            except OSError as exc:
                self.close(f"读取失败：{exc}")
                return
            if not chunk:
                self.close("对端断开")
                return
            self._last_rx = time.monotonic()
            try:
                frames = self._reader.feed(chunk)
            except P.ProtocolError as exc:
                self.close(f"协议错误：{exc}")
                return
            for frame in frames:
                if not self._alive:
                    return
                try:
                    self._dispatch(frame)
                except Exception:  # noqa: BLE001 - 单帧处理失败不该断整条连接
                    log.exception("处理 %s 帧时出错", frame.type_name)

    def _dispatch(self, frame: P.Frame) -> None:
        if frame.type == P.T_HELLO:
            ok, reason = P.verify_hello(frame.header, self.key)
            self.peer_device = str(frame.header.get("device") or "")
            self.peer_host = str(frame.header.get("host") or "")
            if ok and self.expected_peer_device and self.peer_device != self.expected_peer_device:
                # 密钥对了但不是已配对的那台：契约只支持一对一
                ok, reason = False, "已与另一台设备配对"
            self.authenticated = ok
            if self.device_id and self.peer_device == self.device_id:
                self.close("连到了自己")
                return
            self.send(
                P.T_HELLO_OK,
                {
                    "device": self.device_id,
                    "host": self.host_label,
                    "ok": ok,
                    "reason": reason,
                },
            )
            if not ok:
                log.warning("对端 %s 未通过认证：%s", self.peer_device or "?", reason)
                if self._on_auth_failed is not None:
                    try:
                        self._on_auth_failed(self, reason)
                    except Exception:  # noqa: BLE001
                        log.exception("on_auth_failed 回调出错")
                # 先让对方收到 HELLO_OK 里的原因，再断开（未认证的连接没有用）
                self.close(reason or "认证失败")
                return
            self._mark_ready()
            return

        if frame.type == P.T_HELLO_OK:
            ok = bool(frame.header.get("ok"))
            reason = str(frame.header.get("reason") or "未知原因")
            self.peer_device = str(frame.header.get("device") or self.peer_device)
            self.peer_host = str(frame.header.get("host") or self.peer_host)
            self.authenticated = ok
            if not ok:
                log.warning("对端拒绝了握手：%s", reason)
            self._mark_ready()
            if not ok:
                # 对端拒绝（密钥不一致 / 它已配了别的设备）—— 这条连接不必留着
                self.close(reason)
            return

        if frame.type == P.T_PING:
            self.send(P.T_PONG, {"ts": frame.header.get("ts", 0)})
            return

        if frame.type == P.T_PONG:
            return

        if frame.type == P.T_ERROR:
            log.warning("对端报错 %s：%s", frame.header.get("code"), frame.header.get("msg"))
            return

        self._on_frame(self, frame)

    def _mark_ready(self) -> None:
        if self._ready:
            return
        self._ready = True
        self._on_ready(self)

    def _beat_loop(self) -> None:
        while self._alive:
            # 用 Event.wait 而不是 time.sleep：否则 close() 之后还得再睡满半个心跳
            # 才退出，`Transport.stop()` 的 join 会超时，留一个引用着已关闭 socket 的
            # 活线程 —— 解释器退出时踩它，直接 access violation
            if self._stop.wait(HEARTBEAT_INTERVAL_S / 2):
                return
            if not self._alive:
                return
            if self.idle_s > HEARTBEAT_TIMEOUT_S:
                self.close(f"心跳超时（{self.idle_s:.0f}s 没有数据）")
                return
            if self.idle_s >= HEARTBEAT_INTERVAL_S:
                try:
                    self.send(P.T_PING, {"ts": time.time()})
                except (OSError, LinkError) as exc:
                    self.close(f"心跳发送失败：{exc}")
                    return


def _peer_addr(sock: socket.socket) -> tuple[str, int]:
    try:
        addr = sock.getpeername()
        return (str(addr[0]), int(addr[1]))
    except OSError:
        return ("?", 0)


class Transport:
    """监听 + 拨号 + 维持一条可用连接。线程安全。"""

    def __init__(
        self,
        *,
        device_id: str,
        listen_port: int,
        host_label: str = "",
        key: bytes | None = None,
        peer: tuple[str, int] | None = None,
        on_clip: Callable[[dict[str, Any], bytes], None] | None = None,
        on_ack: Callable[[dict[str, Any]], None] | None = None,
        on_switch: Callable[[dict[str, Any]], None] | None = None,
        on_switch_ack: Callable[[dict[str, Any]], None] | None = None,
        on_peer_state: Callable[[dict[str, Any]], None] | None = None,
        on_peer_state_ack: Callable[[dict[str, Any]], None] | None = None,
        on_state: Callable[[str, str], None] | None = None,
        on_auth_failed: Callable[[str, str], None] | None = None,
        expected_peer_device: str = "",
    ) -> None:
        self.device_id = device_id
        self.listen_port = int(listen_port)
        self.host_label = host_label
        self.key = key
        self.peer = peer
        self.expected_peer_device = expected_peer_device
        self._on_clip = on_clip or (lambda header, payload: None)
        self._on_ack = on_ack or (lambda header: None)
        self._on_switch = on_switch or (lambda header: None)
        self._on_switch_ack = on_switch_ack or (lambda header: None)
        self._on_peer_state = on_peer_state or (lambda header: None)
        self._on_peer_state_ack = on_peer_state_ack or (lambda header: None)
        self._on_state = on_state or (lambda state, detail: None)
        self._on_auth_failed = on_auth_failed or (lambda ip, reason: None)

        self._lock = threading.Lock()
        self._link: Link | None = None
        self._links: dict[int, Link] = {}
        self._running = False
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._state = "idle"
        self.last_error = ""
        self.peer_device = ""
        self.peer_host = ""
        self.authenticated = False
        #: 认证失败后的等待（指数退避，BUG-006）。握手成功即复位。
        self._auth_fail_wait = AUTH_FAIL_MIN_WAIT_S

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()
        try:
            self._listener = self._open_listener()
        except OSError as exc:
            self._set_state("error", f"监听 {self.listen_port} 失败：{exc}")
            self._listener = None

        for target, name in ((self._accept_loop, "clip-accept"), (self._dial_loop, "clip-dial")):
            th = threading.Thread(target=target, name=name, daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        with self._lock:
            links = list(self._links.values())
            self._links.clear()
            self._link = None
        for link in links:
            link.close("本端停止", notify=False)
        for th in self._threads:
            th.join(timeout=1.5)
        self._threads.clear()
        self._set_state("idle", "已停止")

    def _open_listener(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.listen_port))
        sock.listen(2)
        sock.settimeout(0.5)
        return sock

    # ------------------------------------------------------------------ 状态
    @property
    def connected(self) -> bool:
        link = self._link
        return bool(link and link.alive and link.ready)

    @property
    def state(self) -> str:
        return self._state

    def describe(self) -> str:
        link = self._link
        if self.connected and link is not None:
            return link.describe()
        if self.peer:
            return f"未连接（对端 {self.peer[0]}:{self.peer[1]}）"
        return "未连接（等对端来连）"

    def peer_addr(self) -> str:
        """当前尝试连接 / 已连接的对端地址（配对页失败态要显示它）。"""
        link = self._link
        if self.connected and link is not None:
            return f"{link.peer_addr[0]}:{link.peer_addr[1]}"
        if self.peer:
            return f"{self.peer[0]}:{self.peer[1]}"
        return ""

    def set_key(self, key: bytes | None, *, drop_links: bool = True) -> None:
        """切换共享密钥。配对时先换成临时 PIN key，取消/失败再换回来。

        `drop_links=True` 会主动断开旧连接，让监听/拨号线程用新 key 重新握手；
        没有这一步，线上那条用旧 key 认证的连接会一直活着，临时 key 永远不生效。
        """
        with self._lock:
            self.key = key
            links = list(self._links.values()) if drop_links else []
            if drop_links:
                self._links.clear()
                self._link = None
                self.authenticated = False
                self.peer_device = ""
                self.peer_host = ""
        for link in links:
            link.close("配对密钥切换", notify=False)
        if drop_links:
            self._set_state("idle", "配对密钥已更新")

    def set_expected_peer_device(self, device_id: str) -> None:
        """配对后的身份绑定；空串 = 接受任何知道密钥的设备（旧 CLI 配置）。"""
        self.expected_peer_device = str(device_id or "")

    def _set_state(self, state: str, detail: str = "") -> None:
        changed = state != self._state or detail != self.last_error
        self._state = state
        self.last_error = detail
        if changed:
            log.info("剪贴板连接状态 → %s%s", state, f"（{detail}）" if detail else "")
            try:
                self._on_state(state, detail)
            except Exception:  # noqa: BLE001
                log.exception("on_state 回调出错")

    # ------------------------------------------------------------------ 线程
    def _accept_loop(self) -> None:
        while self._running and not self._stop.is_set():
            sock = self._listener
            if sock is None:
                return
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(CONNECT_TIMEOUT_S)
            self._install(self._make_link(conn, "in"), "in")

    def _dial_loop(self) -> None:
        backoff = 1.0
        while self._running and not self._stop.is_set():
            with self._lock:
                busy = bool(self._links)
            # 没配对就**不主动外连**：没有 key 的机器没有任何东西能同步，
            # 连上去只会制造一次认证失败，然后 200ms 再来一遍（BUG-006）。
            if self.connected or busy or not self.peer or self.key is None:
                self._stop.wait(RECONNECT_INTERVAL_S)
                continue
            host, port = self.peer
            conn = None
            self._set_state("connecting", f"{host}:{port}")
            try:
                conn = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)
            except OSError as exc:
                self._set_state("idle", f"连接 {host}:{port} 失败：{exc}")
                self._wait_backoff(backoff)
                backoff = min(backoff * 1.6, RECONNECT_INTERVAL_S * 2)
                continue
            backoff = 1.0
            conn.settimeout(RECV_TIMEOUT_S)
            self._install(self._make_link(conn, "out"), "out")
            # 认证失败的链路会被立刻 close，这里等的是**下一次重连的间隔**：
            # 固定 0.2s 会让未配对的对端 2 秒被重连 20 次。
            self._stop.wait(self._auth_fail_wait)

    def _wait_backoff(self, seconds: float) -> None:
        self._stop.wait(min(seconds, RECONNECT_INTERVAL_S))

    def _make_link(self, sock: socket.socket, direction: str) -> Link:
        return Link(
            sock,
            key=self.key,
            device_id=self.device_id,
            host_label=self.host_label,
            direction=direction,
            on_frame=self._handle_frame,
            on_ready=self._handle_ready,
            on_closed=self._handle_closed,
            expected_peer_device=self.expected_peer_device,
            on_auth_failed=self._handle_auth_failed,
        )

    def _install(self, link: Link, direction: str) -> bool:
        """新连接一律先接进来，再由 `_elect()` 决定谁留谁走。

        为什么不在这一步就拒绝：两端同时拨号时，各自"先 ready 的那条"并不一致，
        谁先拒绝谁就会把对面刚建好的连接踢掉 —— 实测表现为两条链路互相顶死、永远连不上。
        选举规则必须是**两端算出来同一个答案**的：拨号方 device_id 小的一方胜出。
        """
        with self._lock:
            self._links[id(link)] = link
        link.start()
        return True

    def _elect(self) -> None:
        with self._lock:
            candidates = [l for l in self._links.values() if l.alive and l.ready]
            if not candidates:
                self._link = None
                return

            def dialer_id(l: Link) -> str:
                # 拨号方是谁：out 是我们自己，in 是对端
                return self.device_id if l.direction == "out" else (l.peer_device or "")

            def rank(l: Link) -> tuple[int, str, int]:
                did = dialer_id(l)
                # 认不出拨号方时排最后；同 id 再按方向定序，保证两端完全一致
                return (0, did, 0 if l.direction == "out" else 1) if did else (1, "", 0)

            best = min(candidates, key=rank)
            self._link = best
            losers = [l for l in candidates if l is not best]
        for l in losers:
            log.info("关闭重复连接 %s（保留 %s）", l.describe(), best.describe())
            l.close("被更优连接取代", notify=False)

    # ------------------------------------------------------------------ 回调
    def _handle_ready(self, link: Link) -> None:
        # 只要有一条链路握手成功就复位退避（不看它最后是不是选举赢家）：
        # 能握手说明对端可达且密钥一致，再按失败指数等待就是白等。
        if getattr(link, "authenticated", False):
            self._auth_fail_wait = AUTH_FAIL_MIN_WAIT_S
        self._elect()
        if self._link is not link:
            return                      # 自己就是被取代的那条，状态交给赢家去报
        self.peer_device = link.peer_device
        self.peer_host = link.peer_host
        self.authenticated = link.authenticated
        if link.authenticated:
            self._set_state("connected", link.describe())
        else:
            self._set_state("unpaired", "两端密钥不一致或尚未配对")

    def _handle_auth_failed(self, link: Link, reason: str) -> None:
        ip = str(link.peer_addr[0])
        # 指数退避：0.2 → 0.4 → 0.8 → … → 5s，握手成功后复位（在 _handle_ready 里）
        self._auth_fail_wait = min(max(self._auth_fail_wait * 2, AUTH_FAIL_MIN_WAIT_S), AUTH_FAIL_MAX_WAIT_S)
        try:
            self._on_auth_failed(ip, reason)
        except Exception:  # noqa: BLE001
            log.exception("on_auth_failed 回调出错")

    def _handle_closed(self, link: Link, reason: str) -> None:
        with self._lock:
            self._links.pop(id(link), None)
            if self._link is link:
                self._link = None
        self._elect()
        if not self._running:
            return
        current = self._link
        if current is not None:
            self._handle_ready(current)
            return
        # 断了就是没连上：`authenticated` 必须跟着翻回 False，
        # 否则界面会显示「已配对」而实际上一条消息也发不出去（排查起来极费时间）
        self.authenticated = False
        self._set_state("idle", f"连接断开：{reason}")

    def _handle_frame(self, link: Link, frame: P.Frame) -> None:
        if frame.type == P.T_ONECLIP:
            self._on_clip(frame.header, frame.payload)
            return
        if frame.type == P.T_ACK:
            self._on_ack(frame.header)
            return
        if frame.type == P.T_SWITCH:
            self._on_switch(frame.header)
            return
        if frame.type == P.T_SWITCH_ACK:
            self._on_switch_ack(frame.header)
            return
        if frame.type == P.T_STATE:
            self._on_peer_state(frame.header)
            return
        if frame.type == P.T_STATE_ACK:
            self._on_peer_state_ack(frame.header)
            return
        log.debug("忽略未知帧 %s", frame)

    # ------------------------------------------------------------------ 发送
    def send_clip(self, item) -> None:
        """发送一条剪贴板内容。未连接 / 未认证时抛 `LinkError`（调用方转成"仅本机"）。"""
        link = self._link
        if link is None or not link.alive or not link.ready:
            raise LinkError("未连接")
        if not link.authenticated:
            raise LinkError("未配对")
        header, payload = P.item_to_message(item)
        link.send(P.T_ONECLIP, header, payload)

    def send_ack(self, digest: str, ok: bool, detail: str = "") -> None:
        link = self._link
        if link is None or not link.alive:
            return
        try:
            link.send(P.T_ACK, {"digest": digest, "ok": bool(ok), "detail": detail})
        except (OSError, LinkError) as exc:
            log.debug("ACK 发送失败（连接已断）：%s", exc)

    # ------------------------------------------------------------------ 远端代发切屏
    def send_switch(self, header: dict[str, Any]) -> None:
        """请求对端代发 DDC Set。未连接 / 未认证时抛 `LinkError`。"""
        link = self._link
        if link is None or not link.alive or not link.ready:
            raise LinkError("未连接")
        if not link.authenticated:
            raise LinkError("未配对")
        link.send(P.T_SWITCH, header)

    def send_switch_ack(self, header: dict[str, Any]) -> None:
        link = self._link
        if link is None or not link.alive:
            return
        try:
            link.send(P.T_SWITCH_ACK, header)
        except (OSError, LinkError) as exc:
            log.debug("SWITCH_ACK 发送失败（连接已断）：%s", exc)

    def send_state(self, header: dict[str, Any]) -> None:
        """问对端「你现在能不能枚举到这块屏」。未连接 / 未认证时抛 `LinkError`。"""
        link = self._link
        if link is None or not link.alive or not link.ready:
            raise LinkError("未连接")
        if not link.authenticated:
            raise LinkError("未配对")
        link.send(P.T_STATE, header)

    def send_state_ack(self, header: dict[str, Any]) -> None:
        link = self._link
        if link is None or not link.alive:
            return
        try:
            link.send(P.T_STATE_ACK, header)
        except (OSError, LinkError) as exc:
            log.debug("STATE_ACK 发送失败（连接已断）：%s", exc)

    def reconnect_now(self) -> None:
        """让拨号线程立刻重试一次（用户点了「重试」）。"""
        link = self._link
        if link is not None and not link.ready:
            link.close("重试", notify=False)
        self._stop.wait(0)


__all__ = ["Link", "LinkError", "Transport"]
