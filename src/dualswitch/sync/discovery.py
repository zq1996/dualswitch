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
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

MAGIC = "ds-clip"
ANNOUNCE_INTERVAL_S = 5.0
PEER_TTL_S = 20.0            # 超过这么久没再广播的设备不再显示（换 Wi-Fi 后不残留）


def computer_name(fallback: str = "") -> str:
    """本机电脑名（配对页列表里显示的名字），取不到就回落 device_id / 平台名。"""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["scutil", "--get", "ComputerName"],
                capture_output=True, text=True, timeout=1.0,
            )
            name = (out.stdout or "").strip()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform == "win32":
        name = (os.environ.get("COMPUTERNAME") or "").strip()
        if name:
            return name
    try:
        name = socket.gethostname().strip()
        if name and name.lower() != "localhost":
            return name.split(".")[0]
    except OSError:
        pass
    return fallback


class Discovery:
    def __init__(
        self,
        *,
        device_id: str,
        host_label: str,
        service_port: int,
        discover_port: int = 47824,
        platform: str = "",
        version: str = "",
        on_peer: Callable[[str, int, dict[str, Any]], None] | None = None,
    ) -> None:
        self.device_id = device_id
        self.host_label = host_label
        self.service_port = int(service_port)
        self.discover_port = int(discover_port)
        self.platform = platform
        self.version = version
        self.pairing = False          # 本机正在显示配对码
        self.paired = False           # 本机已有配对对象
        self._on_peer = on_peer or (lambda ip, port, info: None)
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.peers: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._sock is not None:
            return
        # discover_port<=0 表示「不要发现」：自测 / e2e 脚本起的临时引擎必须走这条，
        # 否则它会向真实的 47824 广播一个随机服务端口，把正在运行的对端拨号目标改成
        # 一个马上就会关掉的死端口 —— 对端会每 3 秒重连一次、连到天亮（BUG-003）。
        if self.discover_port <= 0:
            log.debug("发现服务已关闭（discover_port=%s），只能靠手填 peer", self.discover_port)
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
        while not self._stop.is_set():
            self.announce_now()
            self._stop.wait(ANNOUNCE_INTERVAL_S)

    def announce_now(self) -> None:
        """立刻广播一次（配对页「重新搜索」/ 状态变化时不用等 5s）。"""
        self._announce(
            {
                "ds": MAGIC,
                "v": 1,
                "device": self.device_id,
                "host": self.host_label,
                "port": self.service_port,
                "ts": round(time.time(), 3),
                # 配对页要显示的增量字段；旧实现不认就直接忽略。
                "plat": self.platform,
                "ver": self.version,
                "pairing": bool(self.pairing),
                "paired": bool(self.paired),
            }
        )

    def set_status(self, *, pairing: bool | None = None, paired: bool | None = None) -> None:
        """更新广播里的配对状态，并立刻重播一次（对端列表 1.5s 内就能看到）。"""
        changed = False
        if pairing is not None and bool(pairing) != self.pairing:
            self.pairing = bool(pairing)
            changed = True
        if paired is not None and bool(paired) != self.paired:
            self.paired = bool(paired)
            changed = True
        if changed:
            self.announce_now()

    def alive_peers(self, max_age: float = PEER_TTL_S) -> list[dict[str, Any]]:
        """按最近一次广播倒序返回还活着的设备（本机已排除）。"""
        now = time.time()
        dead = [dev for dev, info in self.peers.items()
                if now - float(info.get("seen", 0.0)) > max_age]
        for dev in dead:
            self.peers.pop(dev, None)
        return sorted(self.peers.values(), key=lambda i: float(i.get("seen", 0.0)), reverse=True)

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
            info["seen"] = time.time()
            self.peers[device] = info
            try:
                self._on_peer(ip, port, info)
            except Exception:  # noqa: BLE001
                log.exception("on_peer 回调出错")


__all__ = ["Discovery", "computer_name"]
