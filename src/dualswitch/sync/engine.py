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
import os
import queue
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ..clipboard.base import ClipItem, ClipboardError, create_backend
from . import protocol as P
from .discovery import Discovery, computer_name
from .transport import LinkError, Transport

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.15
ACK_TIMEOUT_S = 5.0
RESEND_WINDOW_S = 30.0
# 多久问一次对端「你看得到这块屏吗」。太密浪费带宽，太稀归属判定会滞后；
# 5 秒对切屏（人眼 1~2 秒完成）足够，且和 controller 的 2 秒轮询错开。
PEER_STATE_INTERVAL_S = 5.0
PAIRING_TTL_S = 300.0          # 显示端配对码有效期 5 分钟
PAIRING_CONNECT_TIMEOUT_S = 15.0
PAIRING_MAX_ATTEMPTS = 5


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
    app: str = ""                # 来源应用名（截图 = "截图"）
    app_id: str = ""             # 来源应用 bundle id
    path: str = ""               # 来自本机文件时的原始路径（文件卡用）
    file_size: int = 0           # 文件本身的字节数（列表显示用；size 仍是入库字节数）

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
        switch_executor: Callable[[int], "tuple[bool, str]"] | None = None,
        vcp_code: int = 0x60,
        save_config: Callable[[], None] | None = None,
        version: str = "",
    ) -> None:
        self.cfg = cfg
        self.host_label = host_label or "mac"
        self._clock = clock
        self._backend = backend
        self._transport = transport
        self._discovery: Discovery | None = None
        self._owns_transport = transport is None
        self._save_config = save_config
        self.version = version

        # 远端代发切屏（ISSUE-002）：对端来到的请求交给它，返回值是 (ok, detail)
        self._switch_executor = switch_executor
        self._vcp_code = int(vcp_code)
        self._switch_waiters: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._switch_seen: dict[str, tuple[float, dict[str, Any]]] = {}
        self.switch_stats = {"requested": 0, "served": 0, "failed": 0}

        # ---- 画面归属联合判定（T_STATE / 0x32，2026-10-23）----
        # 本机「现在能不能枚举到这块屏」由 CLI 注入（引擎不碰硬件）；
        # 对端最近一次上报的结果缓存下来，供 controller.classify() 判定归属。
        self._monitor_probe: Callable[[], bool] | None = None
        self._peer_sees: bool | None = None      # None = 没问过 / 对端太旧 / 已失效
        self._peer_sees_at = 0.0
        self._peer_state_waiters: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._peer_state_asked = 0.0

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

        # 暂停记录（剪贴板页 ⏸）：只停「本机采集」，收发与列表都照常。
        # 暂停期间照常 poll 后端并丢弃结果 —— 否则恢复第一次 poll 会把暂停期间
        # 复制的内容当成新内容补发出去。
        self._paused = False

        self.stats = {"sent": 0, "received": 0, "dropped": 0, "failed": 0}
        self.last_error = ""
        self.last_sync_at = 0.0
        self.state = "idle"
        # 剪贴板内容同步是否开启。与「链路是否建立」解耦：关掉内容同步时
        # 连接照样建（配对 / 对端代发切屏都靠它），只是不读不写剪贴板。
        self._clip_on = bool(self.cfg.enabled)
        self._last_reason = ""

        # ---- 配对（INTERACTIONS-v2 §12.6）----
        # role: "" 空闲 / "show" 本机显示码 / "enter" 本机输入码
        self._pair_role = ""
        self._pair_pin = ""
        self._pair_key: bytes | None = None
        self._pair_prev_key: bytes | None = None
        self._pair_prev_peer: tuple[str, int] | None = None
        self._pair_prev_expected = ""
        self._pair_prev_psk = ""
        self._pair_prev_device = ""
        self._pair_prev_name = ""
        self._pair_prev_paired_at = 0.0
        self._pair_expires = 0.0
        self._pair_attempts: dict[str, int] = {}
        self._pair_event = threading.Event()
        self._pair_ok = False
        self._pair_error = ""
        self._pair_error_kind = ""
        self._pair_outcome = ""          # "" | ok | failed | cancelled
        self._pair_target: dict[str, Any] = {}

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._running:
            return
        clip_on = bool(self.cfg.enabled)
        self._clip_on = clip_on
        if not clip_on:
            # 剪贴板内容同步关掉 ≠ 链路关掉：配对状态、对端代发切屏都走这条连接。
            # 以前这里直接 return，于是「只要切屏、不要剪贴板」的用法连代发一起废掉，
            # 界面上还只显示「对端离线」，用户完全看不出是开关导致的。
            log.info("剪贴板内容同步未启用（clipboard.enabled=false），仅建链供配对 / 对端代发切屏")
        else:
            if self._backend is None:
                try:
                    self._backend = create_backend("auto")
                except Exception as exc:  # noqa: BLE001 - 后端坏了不能拖垮链路
                    log.warning("创建剪贴板后端失败：%s", exc)
                    self._backend = None
            if getattr(self._backend, "name", "none") == "none":
                # 后端不可用也不 return：剪贴板降级为不可用，链路与代发继续
                self._backend = None
                self.last_error = "没有可用的剪贴板后端，仅保留链路"
                log.warning("剪贴板后端不可用：内容同步关闭，链路与对端代发继续可用")

        if self._transport is None:
            self._transport = self._build_transport()
        self._transport.start()
        if self.cfg.psk and self.cfg.debug_plain:
            log.warning("clipboard.debug_plain 已开启：剪贴板内容在局域网内明文传输")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="clip-engine", daemon=True)
        self._thread.start()
        self._running = True
        log.info(
            "链路已启动（%s，监听 %s）",
            f"剪贴板后端 {self._backend.name}" if self._backend is not None else "仅链路，剪贴板内容同步关闭",
            self.cfg.port,
        )

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
            self._transport = None        # 重新 start 时要按最新配置重建
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
        display_name = computer_name(self.host_label)
        transport = Transport(
            device_id=self.device_id,
            listen_port=int(self.cfg.port),
            host_label=display_name,
            key=key,
            peer=peer,
            on_clip=self._on_remote_clip,
            on_ack=self._on_ack,
            on_switch=self._on_switch_request,
            on_switch_ack=self._on_switch_ack,
            on_peer_state=self._on_peer_state_request,
            on_peer_state_ack=self._on_peer_state_ack,
            on_state=self._on_state,
            on_auth_failed=self._on_auth_failed,
            expected_peer_device=self.cfg.peer_device,
        )
        # 发现必须常开：手填 peer 只用于「立即先拨一次」，不能变成永久地址依赖。
        # 例外：跑测试 / 自测脚本时必须能关掉。测试里的引擎用默认 discover_port=47824，
        # 一起就会向真实网段广播自己的临时端口，把同一个网段里正在运行的那台机器的
        # 拨号目标改成一个马上关闭的死端口（BUG-003，实测把对端刷了 200+ 轮 timed out）。
        # tests/conftest.py 里有 autouse fixture 打开这个开关。
        no_discover = os.environ.get("DUALSWITCH_NO_DISCOVER", "").strip().lower() in ("1", "true", "yes", "on")
        self._discovery = Discovery(
            device_id=self.device_id,
            host_label=display_name,
            service_port=int(self.cfg.port),
            discover_port=0 if no_discover else int(self.cfg.discover_port),
            platform="macos" if self.host_label == "mac" else "windows",
            version=self.version,
            on_peer=self._on_peer_found,
        )
        self._discovery.paired = bool(self.cfg.psk)
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
        # 配对中不允许发现结果改拨号目标，否则可能连到第三台、把配对流程搅乱
        if self._pair_pin:
            return
        device = str(info.get("device") or "")
        # 已配对：只认记住的那台；广播里的其他设备最多显示在列表里，不拨号
        if self.cfg.peer_device and device != self.cfg.peer_device:
            return
        if self._transport.peer == (ip, port):
            return
        log.info("发现对端 %s（%s:%s）", info.get("host") or "?", ip, port)
        self._transport.peer = (ip, port)

    def _on_auth_failed(self, ip: str, reason: str) -> None:
        """显示端限流：同一来源 IP 认证失败 5 次 → 当前配对码立即作废。

        2026-10-23（BUG-007，用户报「出码直接提示失效」）：**只能对"猜码"计数**。
        真正输错 PIN 的人一定带着某个 key，失败形态是帧层 HMAC 校验不过
        （`FrameReader` 抛 `ProtocolError`，链路直接 close），**根本走不到这个回调**。
        能走到这里的几乎全是「对端还没配对，正在自动重连」——它的 reason 是
        「本端未配对 / 对端未配对」。把它计进限流 = 出码端 1~2 秒内自杀：
        对端每 200ms 重连一次，5 次就到上限。所以这类 reason 一律不计。
        """
        with self._lock:
            if not self._pair_pin or self._pair_role != "show":
                return
            if "未配对" in str(reason or ""):
                log.debug("忽略「%s」类认证失败（对端未配对的重连，不是猜码）：%s", reason, ip)
                return
            count = int(self._pair_attempts.get(ip, 0)) + 1
            self._pair_attempts[ip] = count
            if count >= PAIRING_MAX_ATTEMPTS:
                self._pair_error = "配对码已失效，请让对方重新显示配对码"
                self._pair_error_kind = "max_attempts"
                should_cancel = True
            else:
                should_cancel = False
        if should_cancel:
            log.warning("配对码因来自 %s 的 %d 次认证失败作废", ip, count)
            self.cancel_pairing(errors=True)

    def _on_state(self, state: str, detail: str) -> None:
        self.state = state
        if state in ("error", "unpaired"):
            self.last_error = detail
        if state == "connected":
            self.last_error = ""
            # 连上后立刻把当前剪贴板同步过去一次？不。切屏时才推，避免"一启动就覆盖对端"。
            self._pair_on_connected()
        elif state == "unpaired" and self._pair_role == "enter" and self._pair_pin:
            self._pair_fail("密钥不匹配（PIN 不正确或对端不在配对状态）", "auth")
        elif state == "error" and self._pair_role == "enter" and self._pair_pin:
            self._pair_fail(detail or "连接失败", "network")

    def _pair_on_connected(self) -> None:
        """任一端认证通过后落盘。A 端在状态回调里走到这里，B 端在等待线程里也会走。"""
        transport = self._transport
        if transport is None or not self._pair_pin or not transport.authenticated:
            return
        self._complete_pairing(transport.peer_device, transport.peer_host)

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

    # ------------------------------------------------------------------ 配对
    @property
    def pairing_active(self) -> bool:
        return bool(self._pair_pin)

    def begin_pairing(self) -> str:
        """A 端：生成 6 位码并切到临时 key，返回要显示的 PIN。

        临时 key 只进 Transport 的内存，不写 config；取消/超时会换回原 key。
        """
        transport = self._transport
        if transport is None:
            raise RuntimeError("剪贴板同步未启动")
        with self._lock:
            if (
                self._pair_role == "show"
                and self._pair_pin
                and self._clock() < self._pair_expires
            ):
                return self._pair_pin                # 再点一次只沿用，不叠加
        self.cancel_pairing()

        pin = P.random_pin()
        key = P.derive_key(pin)
        with self._lock:
            self._pair_prev_key = bytes.fromhex(self.cfg.psk) if self.cfg.psk else None
            self._pair_prev_peer = transport.peer
            self._pair_prev_expected = transport.expected_peer_device
            self._pair_role = "show"
            self._pair_pin = pin
            self._pair_key = key
            self._pair_expires = self._clock() + PAIRING_TTL_S
            self._pair_attempts.clear()
            self._pair_error = ""
            self._pair_error_kind = ""
            self._pair_outcome = ""
            self._pair_target = {}
            self._pair_event.clear()
        # 重新配对时先解绑旧设备，否则新设备即使 PIN 正确也会被身份绑定挡掉
        transport.set_expected_peer_device("")
        transport.set_key(key)
        if self._discovery is not None:
            self._discovery.set_status(pairing=True, paired=bool(self.cfg.psk))
        log.info("开始显示配对码（5 分钟内有效，只能成功一次）")
        return pin

    def cancel_pairing(self, *, errors: bool = False) -> None:
        """丢弃临时 key，恢复原 key；本次不落盘。"""
        with self._lock:
            was_active = bool(self._pair_role or self._pair_pin)
            prev_key = self._pair_prev_key
            prev_peer = self._pair_prev_peer
            prev_expected = self._pair_prev_expected
            self._pair_role = ""
            self._pair_pin = ""
            self._pair_key = None
            self._pair_expires = 0.0
            self._pair_attempts.pop("self", None)
            self._pair_event.set()       # 叫醒可能还在等 15s 的输入码线程
            self._pair_ok = False
            if errors:
                self._pair_outcome = "failed"
            elif was_active:
                self._pair_outcome = "cancelled"
            if was_active:
                self._pair_error = self._pair_error or ""
        if not was_active:
            return
        transport = self._transport
        if transport is not None:
            transport.set_key(prev_key)
            transport.set_expected_peer_device(prev_expected)
            if prev_key is not None and prev_peer is not None:
                transport.peer = prev_peer
        if self._discovery is not None:
            self._discovery.set_status(pairing=False, paired=bool(self.cfg.psk))
        log.info("已取消配对（临时密钥丢弃）")

    def pair_with_peer(self, addr, pin: str, timeout: float = PAIRING_CONNECT_TIMEOUT_S) -> dict[str, Any]:
        """B 端：用 PIN 派生 key，连 `addr` 并等双向认证（阻塞到成功/失败/超时）。

        调用方应放到后台线程，UI 线程只用 `pairing_status()` 轮询结果。
        """
        pin = str(pin).strip()
        transport = self._transport
        if transport is None:
            return {"ok": False, "kind": "disabled", "reason": "剪贴板同步未启动"}
        if not (len(pin) == 6 and pin.isdigit()):
            return {"ok": False, "kind": "bad_pin", "reason": "配对码必须是 6 位数字"}
        with self._lock:
            used = int(self._pair_attempts.get("self", 0))
            if used >= PAIRING_MAX_ATTEMPTS:
                return {
                    "ok": False,
                    "kind": "max_attempts",
                    "reason": "配对码已失效，请让对方重新显示配对码",
                }
            self._pair_attempts["self"] = used + 1
            self._pair_prev_key = bytes.fromhex(self.cfg.psk) if self.cfg.psk else None
            self._pair_prev_peer = transport.peer
            self._pair_prev_expected = transport.expected_peer_device
            self._pair_role = "enter"
            self._pair_pin = pin
            self._pair_key = P.derive_key(pin)
            self._pair_expires = self._clock() + float(timeout)
            self._pair_error = ""
            self._pair_error_kind = ""
            self._pair_outcome = ""
            self._pair_ok = False
            self._pair_event.clear()
            target = dict(self._pair_target)
            target.update({"addr": _addr_text(addr)})
            self._pair_target = target
            key = self._pair_key

        # 配对时先解除旧的身份绑定，否则新设备即使 PIN 正确也会被当成「别的设备」
        transport.set_expected_peer_device("")
        transport.set_key(key)
        transport.peer = addr
        transport.reconnect_now()

        self._pair_event.wait(float(timeout))
        with self._lock:
            ok = self._pair_ok
            reason = self._pair_error
            kind = self._pair_error_kind
            still_waiting = bool(self._pair_pin and self._pair_role == "enter")
            peer_device = self.cfg.peer_device
            peer_name = self.cfg.peer_name
        if ok:
            return {
                "ok": True, "kind": "", "reason": "",
                "peer_device": peer_device, "peer_name": peer_name,
                "addr": _addr_text(addr),
            }
        if still_waiting:
            kind = kind or "timeout"
            reason = reason or "连接超时"
            self._pair_fail(f"{_addr_text(addr)} 没有响应，请检查两台电脑是否在同一局域网、防火墙是否放行", "timeout")
        return {"ok": False, "kind": kind or "failed", "reason": reason or "配对失败",
                "addr": _addr_text(addr)}

    def unpair(self) -> None:
        """解除配对：只清本机，不向对端发消息。"""
        self.cancel_pairing()
        self.cfg.psk = ""
        self.cfg.peer_device = ""
        self.cfg.peer_name = ""
        self.cfg.paired_at = 0.0
        transport = self._transport
        if transport is not None:
            transport.set_expected_peer_device("")
            transport.set_key(None)
        if self._discovery is not None:
            self._discovery.set_status(pairing=False, paired=False)
        self.last_error = "已解除配对"
        self._save()
        log.info("已解除配对（仅清本机）")

    def pairing_status(self) -> dict[str, Any]:
        """UI 用的配对快照：状态、PIN、倒计时、剩余尝试次数。"""
        self._expire_pairing()
        transport = self._transport
        with self._lock:
            role = self._pair_role
            pin = self._pair_pin
            expires = self._pair_expires
            attempts_left = max(0, PAIRING_MAX_ATTEMPTS - int(self._pair_attempts.get("self", 0)))
            outcome = self._pair_outcome
            error = self._pair_error
            kind = self._pair_error_kind
            target = dict(self._pair_target)
        if role == "show" and pin:
            state = "showing"
        elif role == "enter" and pin:
            state = "connecting"
        elif self.cfg.psk:
            state = "paired"
        else:
            state = "idle"
        connected = bool(transport and transport.connected and transport.authenticated)
        mismatch = bool(self.cfg.psk and self.state == "unpaired" and not connected)
        addr = ""
        if transport is not None:
            addr = transport.peer_addr()
        return {
            "state": state,
            "outcome": outcome,
            "error": error,
            "error_kind": kind,
            "pin": pin,
            "expires_in": max(0.0, expires - self._clock()) if pin else 0.0,
            "attempts_left": attempts_left,
            "paired": bool(self.cfg.psk),
            "legacy": bool(self.cfg.psk and not self.cfg.peer_device),
            "mismatch": mismatch,
            "connected": connected,
            "peer_device": self.cfg.peer_device or (transport.peer_device if transport else ""),
            "peer_name": self.cfg.peer_name or (transport.peer_host if transport else ""),
            "paired_at": float(self.cfg.paired_at or 0.0),
            "addr": target.get("addr") or addr,
            "last_sync_at": self.last_sync_at,
        }

    def set_config_saver(self, saver: Callable[[], None] | None) -> None:
        """UI 会把保存动作换成「保存当前 runner.cfg」（设置页保存后对象会换新）。"""
        self._save_config = saver

    def discovered_peers(self) -> list[dict[str, Any]]:
        if self._discovery is None:
            return []
        return self._discovery.alive_peers()

    def refresh_discovery(self) -> None:
        if self._discovery is not None:
            self._discovery.announce_now()

    def _expire_pairing(self) -> None:
        with self._lock:
            if not self._pair_pin or self._pair_role != "show":
                return
            if self._clock() < self._pair_expires:
                return
            self._pair_error = "配对码已过期，请重新生成"
            self._pair_error_kind = "expired"
        self.cancel_pairing(errors=True)

    def _pair_fail(self, reason: str, kind: str) -> None:
        """结束一次输入码尝试：清临时 key、恢复原配对，并让等待线程醒来。"""
        with self._lock:
            if self._pair_role != "enter" or not self._pair_pin:
                return
            prev_key = self._pair_prev_key
            prev_peer = self._pair_prev_peer
            prev_expected = self._pair_prev_expected
            self._pair_error = reason
            self._pair_error_kind = kind
            self._pair_outcome = "failed"
            self._pair_role = ""
            self._pair_pin = ""
            self._pair_key = None
            self._pair_expires = 0.0
            self._pair_event.set()
        transport = self._transport
        if transport is not None:
            transport.set_key(prev_key)
            transport.set_expected_peer_device(prev_expected)
            if prev_key is not None and prev_peer is not None:
                transport.peer = prev_peer
        if self._discovery is not None:
            self._discovery.set_status(pairing=False, paired=bool(self.cfg.psk))

    def _complete_pairing(self, peer_device: str, peer_name: str) -> None:
        with self._lock:
            if not self._pair_pin or not self._pair_key:
                return
            key = self._pair_key
            self._pair_role = ""
            self._pair_pin = ""
            self._pair_key = None
            self._pair_expires = 0.0
            self._pair_outcome = "ok"
            self._pair_ok = True
            self._pair_error = ""
            self._pair_error_kind = ""
            self.cfg.psk = key.hex()
            self.cfg.peer_device = str(peer_device or "")
            self.cfg.peer_name = str(peer_name or "")
            self.cfg.paired_at = time.time()
            self._pair_event.set()
        transport = self._transport
        if transport is not None:
            transport.set_expected_peer_device(self.cfg.peer_device)
        if self._discovery is not None:
            self._discovery.set_status(pairing=False, paired=True)
        self._save()
        log.info("配对成功：%s（%s）", self.cfg.peer_name or "?", self.cfg.peer_device or "?")

    def _save(self) -> None:
        if self._save_config is None:
            return
        try:
            self._save_config()
        except Exception:  # noqa: BLE001 - 落盘失败不该把配对流程搞崩
            log.exception("配对信息落盘失败")

    # ------------------------------------------------------------ 远端代发切屏
    @property
    def switch_ready(self) -> bool:
        """这条链路现在能不能替本机切屏（连上且已配对）。"""
        transport = self._transport
        return bool(transport and transport.connected and transport.authenticated)

    def set_switch_executor(self, executor: Callable[[int], "tuple[bool, str]"] | None) -> None:
        """挂上"替对端发 DDC Set"的实现（由 CLI 注入，引擎不碰硬件）。"""
        self._switch_executor = executor

    def request_switch(self, value: int, *, timeout: float = 3.0) -> tuple[bool, str]:
        """请对端代发一次 VCP Set。返回 (ok, detail)，**阻塞到对端回执或超时**。

        调用方要放在后台线程（runner 的切换线程），不要卡 UI / 热键回调。
        """
        transport = self._transport
        if transport is None or not self.switch_ready:
            return False, "对端未连接或未配对"
        req = secrets.token_hex(8)
        waiter = threading.Event()
        box: dict[str, Any] = {}
        with self._lock:
            self._switch_waiters[req] = (waiter, box)
        try:
            transport.send_switch(
                {"vcp": self._vcp_code, "value": int(value), "req": req}
            )
        except (LinkError, OSError) as exc:
            with self._lock:
                self._switch_waiters.pop(req, None)
            self.switch_stats["failed"] += 1
            return False, f"发送失败：{exc}"
        self.switch_stats["requested"] += 1
        answered = waiter.wait(timeout)
        with self._lock:
            self._switch_waiters.pop(req, None)
        if not answered:
            self.switch_stats["failed"] += 1
            return False, "对端未响应（可能版本过旧或链路已断）"
        ok = bool(box.get("ok"))
        detail = str(box.get("detail") or ("" if ok else "对端拒绝"))
        if not ok:
            self.switch_stats["failed"] += 1
        log.info("远端代发切屏 → %s（%s）", "ok" if ok else "失败", detail or "-")
        return ok, detail

    def _on_switch_request(self, header: dict[str, Any]) -> None:
        """socket 读线程：对端要我们替它切屏。DDC 调用放到小线程里，别堵读循环。"""
        try:
            req = str(header.get("req") or "")
            vcp = int(header.get("vcp", -1))
            value = int(header.get("value", -1))
        except (TypeError, ValueError):
            log.warning("收到非法的 SWITCH 帧：%r", header)
            return
        if not req:
            log.warning("SWITCH 帧缺 req，丢弃（无法回执）")
            return
        # 幂等：同一 req 10 秒内重复到达，直接重发上次的结果
        with self._lock:
            seen = self._switch_seen.get(req)
            cached = dict(seen[1]) if seen and self._clock() - seen[0] < 10.0 else None
        if cached is not None:
            self._transport_switch_ack(cached)
            return
        threading.Thread(
            target=self._serve_switch, args=(req, vcp, value), name="remote-switch", daemon=True
        ).start()

    def _serve_switch(self, req: str, vcp: int, value: int) -> None:
        ok, detail = False, ""
        if vcp != self._vcp_code:
            detail = f"VCP 0x{vcp:02X} 与本机配置（0x{self._vcp_code:02X}）不一致"
        elif self._switch_executor is None:
            detail = "本机未挂载切屏实现"
        else:
            try:
                ok, detail = self._switch_executor(value)
            except Exception as exc:  # noqa: BLE001 - 对端请求不能把引擎搞崩
                ok, detail = False, f"{type(exc).__name__}: {exc}"
        ack = {"req": req, "ok": bool(ok), "detail": detail}
        with self._lock:
            self._switch_seen[req] = (self._clock(), ack)
            if len(self._switch_seen) > 32:      # 只留最近一小把，防止无限增长
                oldest = sorted(self._switch_seen.items(), key=lambda kv: kv[1][0])
                for key, _ in oldest[: len(self._switch_seen) - 32]:
                    self._switch_seen.pop(key, None)
        if ok:
            self.switch_stats["served"] += 1
        else:
            self.switch_stats["failed"] += 1
        log.info("替对端切屏 VCP 0x%02X=%d → %s（%s）", vcp, value, "ok" if ok else "失败", detail or "-")
        self._transport_switch_ack(ack)

    def _transport_switch_ack(self, ack: dict[str, Any]) -> None:
        if self._transport is not None:
            self._transport.send_switch_ack(ack)

    def _on_switch_ack(self, header: dict[str, Any]) -> None:
        req = str(header.get("req") or "")
        with self._lock:
            entry = self._switch_waiters.get(req)
        if entry is None:
            log.debug("收到无人等待的 SWITCH_ACK：%s", req[:8])
            return
        waiter, box = entry
        box.update(header)
        waiter.set()

    # ------------------------------------------------------------ 画面归属联合判定
    def set_monitor_probe(self, probe: Callable[[], bool] | None) -> None:
        """挂上「本机现在能不能枚举到这块屏」（由 CLI 注入，引擎不碰硬件）。"""
        self._monitor_probe = probe

    def peer_sees_monitor(self, *, max_age: float = 20.0) -> bool | None:
        """对端最近一次上报的「我能不能看到这块屏」。

        返回 `None` 表示**问不出来**：没连上、对端还是旧版本（收到 0x32 直接
        忽略）、或者上报已经过期。调用方必须把 `None` 当成「没有这条证据」，
        回落到原来的判据 —— 这样两端不同版本并存时行为不会变差。
        """
        if not self.switch_ready or self._peer_sees is None:
            return None
        if self._clock() - self._peer_sees_at > max_age:
            return None
        return self._peer_sees

    def request_peer_state(self, *, timeout: float = 1.0) -> bool | None:
        """主动问一次对端（阻塞到回执或超时）。超时 / 旧版本一律返回 None。"""
        transport = self._transport
        if transport is None or not self.switch_ready:
            return None
        req = secrets.token_hex(8)
        waiter = threading.Event()
        box: dict[str, Any] = {}
        with self._lock:
            self._peer_state_waiters[req] = (waiter, box)
        try:
            transport.send_state({"req": req})
        except (LinkError, OSError):
            with self._lock:
                self._peer_state_waiters.pop(req, None)
            return None
        answered = waiter.wait(timeout)
        with self._lock:
            self._peer_state_waiters.pop(req, None)
        if not answered:
            return None
        sees = box.get("sees")
        return None if sees is None else bool(sees)

    def _on_peer_state_request(self, header: dict[str, Any]) -> None:
        """对端问「你看得到这块屏吗」。枚举要几毫秒，放小线程里别堵读循环。"""
        req = str(header.get("req") or "")
        if not req:
            return
        threading.Thread(
            target=self._serve_peer_state, args=(req,), name="peer-state", daemon=True
        ).start()

    def _serve_peer_state(self, req: str) -> None:
        sees = False
        if self._monitor_probe is not None:
            try:
                sees = bool(self._monitor_probe())
            except Exception:  # noqa: BLE001 - 探测失败按「看不到」处理
                sees = False
        if self._transport is not None:
            self._transport.send_state_ack({"req": req, "sees": sees})

    def _on_peer_state_ack(self, header: dict[str, Any]) -> None:
        req = str(header.get("req") or "")
        with self._lock:
            sees = header.get("sees")
            if sees is not None:
                self._peer_sees = bool(sees)
                self._peer_sees_at = self._clock()
            entry = self._peer_state_waiters.get(req)
        if entry is None:
            return                      # 心跳式询问没人等，正常
        waiter, box = entry
        box.update(header)
        waiter.set()

    def _maybe_ask_peer_state(self) -> None:
        """心跳式询问：发出去就不管，回执到了更新缓存。"""
        now = self._clock()
        if now - self._peer_state_asked < PEER_STATE_INTERVAL_S:
            return
        self._peer_state_asked = now
        transport = self._transport
        if transport is None or not self.switch_ready:
            return
        try:
            transport.send_state({"req": secrets.token_hex(8)})
        except (LinkError, OSError):
            return

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

    def set_paused(self, paused: bool) -> None:
        """暂停 / 恢复**本机采集**（不关同步、不影响接收对端内容）。"""
        self._paused = bool(paused)
        log.info("剪贴板记录已%s", "暂停" if self._paused else "恢复")

    @property
    def paused(self) -> bool:
        return self._paused

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
                self._expire_old()
                self._maybe_ask_peer_state()
            except Exception:  # noqa: BLE001 - worker 挂了等于同步死了，必须兜住
                log.exception("同步 worker 出现未捕获异常")

    def _pump_local(self) -> None:
        if not self._clip_on or self._backend is None:
            return
        try:
            item = self._backend.poll()
        except Exception as exc:  # noqa: BLE001
            log.debug("读剪贴板失败：%s", exc)
            return
        if item is None:
            return
        if self._paused:
            # 消费掉 changeCount，避免恢复后把暂停期间复制的内容补发出去
            log.debug("已暂停记录，丢弃一条本机剪贴板内容")
            return
        if self.pairing_active:
            # 配对中临时 key 还没转正：不把本机内容按临时 key 发出去
            log.debug("配对中，暂不推送本机剪贴板")
            return
        self._send(item)

    def _send(self, item: ClipItem, *, force: bool = False) -> None:
        if self.pairing_active:
            return
        if item.digest == self._last_applied_digest:
            self.stats["dropped"] += 1
            log.debug("丢弃自写回弹（%s）", item.digest[:8])
            return
        # 访达里复制的**文件**：文件本身同步不过去（契约 P2），只在本机留一条。
        # 图片文件带 png，按图片照常同步（内容能过去，文件身份留在本机）。
        if item.path and item.image is None:
            self._add(item, source="local", state="local", detail="文件本身不跨端同步")
            log.debug("本机文件记录（不发送）：%s", item.describe())
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

    def _expire_old(self) -> None:
        """按「保留历史」设置清理过期条目（设置页 7 天 / 30 天 / 不限）。

        每 60 秒最多扫一次：记录数量本来就少（record_limit 上限），全量比时间戳
        比引入索引简单得多。保留 0 天 = 不限，直接跳过。
        """
        days = int(getattr(self.cfg, "retention_days", 0) or 0)
        if days <= 0:
            return
        now = self._clock()
        if now - getattr(self, "_last_expire_at", 0.0) < 60.0:
            return
        self._last_expire_at = now
        deadline = now - days * 86400
        with self._lock:
            stale = [r.id for r in self._records if r.ts < deadline]
            if not stale:
                return
            for rid in stale:
                self._items.pop(rid, None)
                self._pending.pop(rid, None)
            keep = [r for r in self._records if r.ts >= deadline]
            self._records.clear()
            self._records.extend(keep)
        log.info("按保留 %d 天清理了 %d 条过期记录", days, len(stale))

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
        if self.pairing_active:
            return
        digest = str(header.get("digest") or "")
        transport = self._transport
        if not self._clip_on or self._backend is None:
            # 本机剪贴板同步关闭（或后端不可用）：不写入，但要回执，
            # 否则对端会当成没收到而反复重发
            if transport:
                transport.send_ack(digest, False, "本机剪贴板同步已关闭")
            return
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
            app=item.app,
            app_id=item.app_id,
            path=item.path,
            file_size=item.file_size,
        )
        with self._lock:
            # 同一条内容重复出现时**不新增行**：更新状态 + 时间，并提到最前面
            # （剪贴板列表按「最近用过」排序，重复复制应该往顶上走，而不是留在原位）。
            match = next((r for r in self._records if r.id == rec.id), None)
            if match is not None:
                match.state = state
                match.detail = detail
                match.ts = max(match.ts, rec.ts)
                match.app = match.app or rec.app
                match.app_id = match.app_id or rec.app_id
                self._items[rec.id] = item
                try:
                    self._records.remove(match)
                except ValueError:      # 并发下已被别处移除
                    pass
                self._records.appendleft(match)
                return match
            self._records.appendleft(rec)
            self._items[rec.id] = item
        self._enforce_storage_limit()
        return rec

    def _enforce_storage_limit(self) -> None:
        """总大小超过「存储上限」时丢掉最旧的记录（设置页可改，默认 200MB）。

        至少留一条：刚复制进来的那条不能被自己挤掉。
        """
        limit_mb = int(getattr(self.cfg, "storage_limit_mb", 0) or 0)
        if limit_mb <= 0:
            return
        limit = limit_mb * 1048576
        with self._lock:
            total = sum(max(0, r.size) for r in self._records)
            while total > limit and len(self._records) > 1:
                dropped = self._records.pop()      # 最旧
                total -= max(0, dropped.size)
                self._items.pop(dropped.id, None)
                self._pending.pop(dropped.id, None)
                log.info("超出存储上限 %dMB，丢弃最旧一条（%s）", limit_mb, dropped.id[:8])

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


def _addr_text(addr) -> str:
    """(ip, port) / "ip:port" → 界面显示用的 `ip:port`。"""
    if isinstance(addr, tuple) and len(addr) >= 2:
        return f"{addr[0]}:{int(addr[1])}"
    return str(addr or "")


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
