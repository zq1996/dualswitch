"""UDP 广播发现 + 手填 IP 兜底。

双系统单显示器的场景里，两台机器总是在同一个局域网，所以"知道对方 IP"这件事
不值得让用户去做——能广播就广播，广播不通（企业 WiFi 常拦广播）再让用户手填，
手填的值写在 `config.clipboard.peer`，一次就好。

发现只负责"告诉拨号线程对端在哪"，不做任何握手与认证：

    Discovery --(UDP 47824 广播 {"ds":1,"device","host","port"})--> 对端
            <-- 同样一份 -->  on_peer(addr, info) → transport.peer = (ip, port)

报文里故意不带任何剪贴板内容。**发现阶段不传数据**，数据只走认证过的 TCP。
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

MAGIC = "ds-clip"
ANNOUNCE_INTERVAL_S = 5.0


class Discovery:
    def __init__(
        self,
        *,
        device_id: str,
        host_label: str,
        service_port: int,
        discover_port: int = 47824,
        on_peer: Callable[[str, int, dict[str, Any]], None] | None = None,
    ) -> None:
        self.device_id = device_id
        self.host_label = host_label
        self.service_port = int(service_port)
        self.discover_port = int(discover_port)
        self._on_peer = on_peer or (lambda ip, port, info: None)
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.peers: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._sock is not None:
            return
        self._stop.clear()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", self.discover_port))
            sock.settimeout(0.5)
        except OSError as exc:
            log.warning("UDP 发现不可用（%s），只能用 config.clipboard.peer 手填对端", exc)
            return
        self._sock = sock
        for target, name in ((self._listen_loop, "clip-discover-rx"), (self._announce_loop, "clip-discover-tx")):
            th = threading.Thread(target=target, name=name, daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        for th in self._threads:
            th.join(timeout=1.0)
        self._threads.clear()

    # ------------------------------------------------------------------ 收发
    def _announce(self, info: dict[str, Any]) -> None:
        if self._sock is None:
            return
        raw = json.dumps(info, separators=(",", ":")).encode("utf-8")
        try:
            self._sock.sendto(raw, ("<broadcast>", self.discover_port))
        except OSError as exc:
            log.debug("广播失败：%s", exc)

    def _announce_loop(self) -> None:
        info = {
            "ds": MAGIC,
            "v": 1,
            "device": self.device_id,
            "host": self.host_label,
            "port": self.service_port,
            "ts": 0,
        }
        while not self._stop.is_set():
            info["ts"] = round(time.time(), 3)
            self._announce(info)
            self._stop.wait(ANNOUNCE_INTERVAL_S)

    def _listen_loop(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                raw, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                info = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(info, dict) or info.get("ds") != MAGIC:
                continue
            device = str(info.get("device") or "")
            if not device or device == self.device_id:
                continue          # 自己的广播
            ip = str(addr[0])
            port = int(info.get("port") or self.service_port)
            info["ip"] = ip
            self.peers[device] = info
            try:
                self._on_peer(ip, port, info)
            except Exception:  # noqa: BLE001
                log.exception("on_peer 回调出错")


__all__ = ["Discovery"]
