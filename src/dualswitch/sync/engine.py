"""同步引擎：轮询本地剪贴板 → 推送 → 接收 → 写回 → 环抑制。

对外只有 `start / stop / push_now / status / records` 五个入口（契约 6.M2）。
UI 和 `app.py` 不需要知道 TCP、加密、帧格式的存在。

## 线程模型

    socket 读线程 ──on_clip──→ inbox 队列 ──┐
                                            ├─→ worker（唯一碰剪贴板的线程）
    UI 250ms 轮询 ──push_now──→ wake 事件 ──┘

**剪贴板永远只在 worker 线程里读写**：NSPasteboard 不是线程安全的，而且写回大图
要几十毫秒，放在 UI 线程会卡界面。`push_now()` 只 set 一个事件就返回（微秒级），
满足契约 2「同步绝不能阻塞切屏」。

## 环抑制三层（契约 4.5）

| 层 | 在哪 | 做什么 |
|---|---|---|
| 1 | `clipboard/*.py` | `write()` 吞掉自写回弹 |
| 2 | 这里 `_last_applied` | 指纹相同直接丢弃 |
| 3 | 这里 `_last_applied_ts` | 旧内容永不覆盖新内容 |

第 1 层在真机上是"吞一次"，所以每次远端写入后本地 `poll()` 仍可能把我们自己写的
内容读回来一次——那一次就是靠第 2 层丢掉的，**这两层必须同时存在**。
"""

from __future__ import annotations

import logging
import queue
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ..clipboard.base import ClipItem, ClipboardError, create_backend
from . import protocol as P
from .discovery import Discovery
from .transport import LinkError, Transport

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.15
ACK_TIMEOUT_S = 5.0
RESEND_WINDOW_S = 30.0


@dataclass
class ClipRecord:
    """剪贴板页要的一条数据（契约 `UI-DESIGN-CLIPBOARD.md` 第 10 节）。"""

    id: str                      # = ClipItem.digest
    kind: str                    # text | image | mixed
    text: str | None = None
    png: bytes | None = None     # 只在预览时解码
    width: int = 0
    height: int = 0
    source: str = "local"        # local | remote
    peer_name: str = ""
    ts: float = field(default_factory=time.time)
    state: str = "local"         # ok | pend | fail | local
    progress: float = 0.0
    size: int = 0
    detail: str = ""

    @property
    def is_image(self) -> bool:
        return self.png is not None


class SyncEngine:
    def __init__(
        self,
        cfg,                       # config.ClipboardConfig
        *,
        host_label: str = "",
        backend=None,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self.host_label = host_label or "mac"
        self._clock = clock
        self._backend = backend
        self._transport = transport
        self._discovery: Discovery | None = None
        self._owns_transport = transport is None

        self._lock = threading.RLock()
        self._records: deque[ClipRecord] = deque(maxlen=max(1, int(cfg.record_limit or 10)))
        self._items: dict[str, ClipItem] = {}
        self._sent: dict[str, float] = {}
        self._pending: dict[str, float] = {}
        self._inbox: queue.Queue = queue.Queue(maxsize=64)
        self._manual: deque[str] = deque()
        self._outbox: deque[ClipItem] = deque()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

        # 环抑制
        self._last_applied_digest = ""
        self._last_applied_ts = float(self._clock())

        self.stats = {"sent": 0, "received": 0, "dropped": 0, "failed": 0}
        self.last_error = ""
        self.last_sync_at = 0.0
        self.state = "idle"
        self._last_reason = ""

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._running:
            return
        if not self.cfg.enabled:
            self.state = "disabled"
            log.info("剪贴板同步未启用（clipboard.enabled=false）")
            return
        if self._backend is None:
            self._backend = create_backend("auto")
        if getattr(self._backend, "name", "none") == "none":
            self.state = "error"
            self.last_error = "没有可用的剪贴板后端"
            log.warning("剪贴板后端不可用，同步功能关闭")
            return

        if self._transport is None:
            self._transport = self._build_transport()
        self._transport.start()
        if self.cfg.psk and self.cfg.debug_plain:
            log.warning("clipboard.debug_plain 已开启：剪贴板内容在局域网内明文传输")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="clip-engine", daemon=True)
        self._thread.start()
        self._running = True
        log.info("剪贴板同步已启动（后端 %s，监听 %s）", self._backend.name, self.cfg.port)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._transport is not None and self._owns_transport:
            self._transport.stop()
        if self._discovery is not None:
            self._discovery.stop()
            self._discovery = None
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:  # noqa: BLE001
                pass
        self.state = "idle"

    # ------------------------------------------------------------------ 构网
    def _build_transport(self) -> Transport:
        key = bytes.fromhex(self.cfg.psk) if self.cfg.psk else None
        peer = parse_peer(self.cfg.peer, self.cfg.effective_peer_port())
        transport = Transport(
            device_id=self.device_id,
            listen_port=int(self.cfg.port),
            host_label=self.host_label,
            key=key,
            peer=peer,
            on_clip=self._on_remote_clip,
            on_ack=self._on_ack,
            on_state=self._on_state,
        )
        if peer is None:
            # 没手填对端就靠广播找；找到谁连谁（本工具只有"另一台"）
            self._discovery = Discovery(
                device_id=self.device_id,
                host_label=self.host_label,
                service_port=int(self.cfg.port),
                discover_port=int(self.cfg.discover_port),
                on_peer=self._on_peer_found,
            )
            self._discovery.start()
        return transport

    @property
    def device_id(self) -> str:
        if not self.cfg.device_id:
            self.cfg.device_id = f"{self.host_label}-{secrets.token_hex(4)}"
        return self.cfg.device_id

    def _on_peer_found(self, ip: str, port: int, info: dict[str, Any]) -> None:
        if self._transport is None:
            return
        if self._transport.peer == (ip, port):
            return
        log.info("发现对端 %s（%s:%s）", info.get("host") or "?", ip, port)
        self._transport.peer = (ip, port)

    def _on_state(self, state: str, detail: str) -> None:
        self.state = state
        if state in ("error", "unpaired"):
            self.last_error = detail
        if state == "connected":
            self.last_error = ""
            # 连上后立刻把当前剪贴板同步过去一次？不。切屏时才推，避免"一启动就覆盖对端"。

    # ------------------------------------------------------------------ 对外接口
    def push_now(self, reason: str = "hotkey") -> None:
        """切屏前调用：必须 <5ms 返回，只叫醒 worker。"""
        self._last_reason = reason
        self._wake.set()

    def status(self) -> dict[str, str]:
        transport = self._transport
        backend_info = {}
        if self._backend is not None:
            try:
                backend_info = self._backend.probe()
            except Exception as exc:  # noqa: BLE001
                backend_info = {"backend": "error", "reason": str(exc)}
        return {
            "enabled": "yes" if self.cfg.enabled else "no",
            "engine": self.state,
            "backend": str(backend_info.get("backend", "-")),
            "device": self.cfg.device_id or "-",
            "psk": "yes" if self.cfg.psk else "no",
            "peer": (transport.describe() if transport else "-"),
            "peer_device": (transport.peer_device if transport else "") or "-",
            "authenticated": "yes" if (transport and transport.authenticated) else "no",
            "port": str(self.cfg.port),
            "records": str(len(self._records)),
            "last_sync": (time.strftime("%H:%M:%S", time.localtime(self.last_sync_at)) if self.last_sync_at else "-"),
            "sent": str(self.stats["sent"]),
            "received": str(self.stats["received"]),
            "dropped": str(self.stats["dropped"]),
            "error": self.last_error or "-",
            "types": str(backend_info.get("types", "-")),
        }

    def records(self) -> list[ClipRecord]:
        with self._lock:
            return list(self._records)

    def record(self, record_id: str) -> ClipRecord | None:
        with self._lock:
            for rec in self._records:
                if rec.id == record_id:
                    return rec
        return None

    def item(self, record_id: str) -> ClipItem | None:
        return self._items.get(record_id)

    def repush(self, record_id: str) -> None:
        """重发一条（面板上的「重发」/失败药丸）。"""
        with self._lock:
            self._manual.append(record_id)
            if record_id in self._pending:
                self._pending.pop(record_id, None)
        self._wake.set()

    def push_item(self, item: ClipItem) -> None:
        """手工推一条，不经过本机剪贴板（`dualswitch clip send` 用）。"""
        with self._lock:
            self._outbox.append(item)
        self._wake.set()

    def copy_to_local(self, record_id: str) -> bool:
        """把某条记录写回本机剪贴板（面板上的「复制到本机」/双击）。"""
        item = self._items.get(record_id)
        if item is None or self._backend is None:
            return False
        try:
            self._backend.write(item)
            log.info("已复制到本机剪贴板：%s", item.describe())
            return True
        except ClipboardError as exc:
            self.last_error = str(exc)
            log.warning("复制到本机失败：%s", exc)
            return False

    def clear_records(self) -> None:
        with self._lock:
            self._records.clear()
            self._items.clear()

    def drop_record(self, record_id: str) -> None:
        """只从列表里删掉一条（不动系统剪贴板，也不通知对端）。"""
        with self._lock:
            for rec in list(self._records):
                if rec.id == record_id:
                    self._records.remove(rec)
                    break
            self._items.pop(record_id, None)
            self._pending.pop(record_id, None)

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(POLL_INTERVAL_S)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self._drain_inbox()
                self._pump_local()
                self._flush_manual()
                self._expire_pending()
            except Exception:  # noqa: BLE001 - worker 挂了等于同步死了，必须兜住
                log.exception("同步 worker 出现未捕获异常")

    def _pump_local(self) -> None:
        if self._backend is None:
            return
        try:
            item = self._backend.poll()
        except Exception as exc:  # noqa: BLE001
            log.debug("读剪贴板失败：%s", exc)
            return
        if item is None:
            return
        self._send(item)

    def _send(self, item: ClipItem, *, force: bool = False) -> None:
        if item.digest == self._last_applied_digest:
            self.stats["dropped"] += 1
            log.debug("丢弃自写回弹（%s）", item.digest[:8])
            return
        last = self._sent.get(item.digest)
        if not force and last is not None and self._clock() - last < RESEND_WINDOW_S:
            self.stats["dropped"] += 1
            return
        if item.image is not None and not self.cfg.image_enabled:
            self._add(item, source="local", state="local", detail="图片同步已关闭")
            return

        transport = self._transport
        if transport is None or not transport.authenticated:
            self._add(item, source="local", state="local",
                      detail="未配对" if transport and transport.connected else "对端未连接")
            log.debug("本机记录（不发送）：%s", item.describe())
            return
        try:
            transport.send_clip(item)
        except LinkError as exc:
            self._add(item, source="local", state="local", detail=str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.stats["failed"] += 1
            self._add(item, source="local", state="fail", detail=f"发送失败：{exc}")
            return

        self._sent[item.digest] = self._clock()
        rec = self._add(item, source="local", state="pend", detail="等待对端确认")
        self._pending[rec.id] = self._clock()
        self.stats["sent"] += 1
        log.info("已推送 %s（%s）", item.describe(), item.digest[:8])

    def _flush_manual(self) -> None:
        with self._lock:
            outbox = list(self._outbox)
            self._outbox.clear()
        for item in outbox:
            self._send(item, force=True)

        while True:
            try:
                record_id = self._manual.popleft()
            except IndexError:
                return
            item = self._items.get(record_id)
            if item is None:
                continue
            transport = self._transport
            try:
                if transport is None:
                    raise LinkError("未连接")
                transport.send_clip(item)
            except LinkError as exc:
                self._set_state(record_id, "fail", detail=str(exc))
                continue
            self._sent[item.digest] = self._clock()
            self._pending[record_id] = self._clock()
            self._set_state(record_id, "pend", detail="重发中")
            self.stats["sent"] += 1

    def _expire_pending(self) -> None:
        now = self._clock()
        for record_id, at in list(self._pending.items()):
            if now - at < ACK_TIMEOUT_S:
                continue
            self._pending.pop(record_id, None)
            self.stats["failed"] += 1
            self._set_state(record_id, "fail", detail="等待对端确认超时")

    # ------------------------------------------------------------------ 收
    def _on_remote_clip(self, header: dict[str, Any], payload: bytes) -> None:
        """socket 读线程回调：只入队，不碰剪贴板。"""
        try:
            self._inbox.put_nowait((header, payload))
        except queue.Full:
            self.stats["dropped"] += 1
            log.warning("接收队列已满，丢弃一条")
        self._wake.set()

    def _drain_inbox(self) -> None:
        while True:
            try:
                header, payload = self._inbox.get_nowait()
            except queue.Empty:
                return
            self._apply_remote(header, payload)

    def _apply_remote(self, header: dict[str, Any], payload: bytes) -> None:
        digest = str(header.get("digest") or "")
        transport = self._transport
        try:
            item = P.message_to_item(header, payload)
        except (ValueError, P.ProtocolError) as exc:
            log.warning("对端内容无法解析：%s", exc)
            if transport:
                transport.send_ack(digest, False, f"内容非法：{exc}")
            return

        if digest and digest == self._last_applied_digest:
            self.stats["dropped"] += 1
            if transport:
                transport.send_ack(digest, True, "重复内容，已忽略")
            return
        if item.ts < self._last_applied_ts - 1e-3:
            self.stats["dropped"] += 1
            log.debug("丢弃过期内容（ts %.3f < %.3f）", item.ts, self._last_applied_ts)
            if transport:
                transport.send_ack(digest, False, "内容比本机旧，已忽略")
            return
        if item.image is not None and not self.cfg.image_enabled:
            if transport:
                transport.send_ack(digest, False, "对端关闭了图片同步")
            return

        try:
            self._backend.write(item)
        except ClipboardError as exc:
            self.stats["failed"] += 1
            self._add(item, source="remote", state="fail", detail=str(exc))
            log.warning("写入本机剪贴板失败：%s", exc)
            if transport:
                transport.send_ack(digest, False, f"写入失败：{exc}")
            return

        self._last_applied_digest = digest or item.digest
        self._last_applied_ts = max(self._last_applied_ts, item.ts)
        self._add(item, source="remote", state="ok", detail="已写入本机剪贴板")
        self.stats["received"] += 1
        self.last_sync_at = self._clock()
        log.info("已同步到本机：%s（%s）", item.describe(), (digest or item.digest)[:8])
        if transport:
            transport.send_ack(digest, True, "ok")

    def _on_ack(self, header: dict[str, Any]) -> None:
        record_id = str(header.get("digest") or "")
        ok = bool(header.get("ok"))
        detail = str(header.get("detail") or "")
        with self._lock:
            self._pending.pop(record_id, None)
        if ok:
            self._set_state(record_id, "ok", detail="对端已确认")
            self.last_sync_at = self._clock()
        else:
            self.stats["failed"] += 1
            self._set_state(record_id, "fail", detail=detail or "对端拒绝")
            self.last_error = detail or "对端拒绝"

    # ------------------------------------------------------------------ 记录
    def _add(self, item: ClipItem, *, source: str, state: str, detail: str = "") -> ClipRecord:
        rec = ClipRecord(
            id=item.digest,
            kind=item.kind,
            text=item.text,
            png=item.image.png if item.image is not None else None,
            width=item.image.width if item.image is not None else 0,
            height=item.image.height if item.image is not None else 0,
            source=source,
            peer_name=self._peer_name(),
            ts=item.ts,
            state=state,
            progress=1.0 if state == "ok" else 0.0,
            size=item.wire_size(),
            detail=detail,
        )
        with self._lock:
            # 同一条内容重复出现时更新状态，不要堆两行
            for existing in self._records:
                if existing.id == rec.id:
                    existing.state = state
                    existing.detail = detail
                    existing.ts = max(existing.ts, rec.ts)
                    self._items[rec.id] = item
                    return existing
            self._records.appendleft(rec)
            self._items[rec.id] = item
        return rec

    def _set_state(self, record_id: str, state: str, detail: str = "") -> None:
        with self._lock:
            for rec in self._records:
                if rec.id == record_id:
                    rec.state = state
                    rec.detail = detail
                    rec.progress = 1.0 if state == "ok" else (0.0 if state in ("fail", "local") else 0.5)
                    return

    def _peer_name(self) -> str:
        transport = self._transport
        if transport is None:
            return ""
        return transport.peer_host or ""


def parse_peer(text: str, default_port: int) -> tuple[str, int] | None:
    """`"192.168.1.5"` / `"192.168.1.5:47823"` → (host, port)；空串 → None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.count(":") == 1:
        host, _, port = text.partition(":")
        try:
            return host.strip(), int(port)
        except ValueError:
            log.warning("clipboard.peer 端口非法：%r，改用 %d", port, default_port)
            return host.strip(), default_port
    return text, int(default_port)


__all__ = ["ClipRecord", "SyncEngine", "parse_peer"]
