"""DDC/CI 后端抽象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class DDCError(RuntimeError):
    """DDC/CI 通信失败（找不到显示器、调用返回失败、命令行退出码非 0 等）。"""


@dataclass
class MonitorInfo:
    """一个物理显示器。"""

    index: int
    description: str = ""
    device_name: str = ""      # Windows: \\.\DISPLAY1；macOS: 序号字符串
    device_id: str = ""        # Windows: MONITOR\AOC2790\{...}
    extra: dict = field(default_factory=dict)

    def matches(self, needle: str) -> bool:
        if not needle:
            return True
        low = needle.lower()
        return any(low in (s or "").lower() for s in (self.description, self.device_name, self.device_id))

    def label(self) -> str:
        parts = [p for p in (self.description, self.device_name, self.device_id) if p]
        return " | ".join(parts) or f"monitor#{self.index}"


@runtime_checkable
class DisplayBackend(Protocol):
    """切换后端需要实现的最小接口。"""

    name: str

    def list_monitors(self) -> list[MonitorInfo]:
        ...

    def set_input(self, value: int) -> None:
        """盲发 VCP Set。失败抛 DDCError。"""

    def get_input(self) -> int:
        """读取当前输入源。不可读时抛 DDCError。"""

    def close(self) -> None:
        ...


def probe_active_monitors(backend) -> list[MonitorInfo]:  # noqa: ANN001 - 避免循环导入
    """枚举「此刻真正在输出画面」的显示器。

    `list_monitors()` 只能回答「这块屏还挂在总线上吗」。macOS 的 m1ddc 走 IOKit
    的 DCP 代理，屏切到 HDMI 之后代理还在，**照样列得出这块屏** —— 于是
    「画面在本机」永远为真，`_try_remote` 永远不触发（ISSUE-003 的根因之一）。

    后端能给出更硬的证据时用它的（macOS 用 AppKit 的当前显示配置）；
    给不出来（返回 None）就退回 `list_monitors()`，行为与改动前完全一致 ——
    老后端 / 缺 pyobjc 的机器不会因此变差。
    """
    probe = getattr(backend, "active_monitors", None)
    if callable(probe):
        try:
            monitors = probe()
        except Exception:  # noqa: BLE001 - 探测失败退回保守枚举
            monitors = None
        if monitors is not None:
            return monitors
    return backend.list_monitors()


class BaseBackend:
    """提供默认实现与统一的 VCP 记录，便于子类复用。"""

    name = "base"
    #: 「显示器亲口报当前输入」的归属判定开关（controller._read_host()）。
    #: 只有读通道真实可用的后端才开（m1ddc 走 IOAVService 直读）；
    #: mock / Windows 后端不开 —— 否则测试与误报会把推断链整个带歪。
    supports_ddc_detect = False

    def __init__(self, vcp_code: int = 0x60, monitor_match: str = "", monitor_index: int = 0):
        self.vcp_code = int(vcp_code)
        self.monitor_match = monitor_match or ""
        self.monitor_index = int(monitor_index)
        self.last_monitor: MonitorInfo | None = None

    # 子类实现 -------------------------------------------------------------
    def list_monitors(self) -> list[MonitorInfo]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def set_input(self, value: int) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def get_input(self) -> int:  # pragma: no cover - 抽象
        raise NotImplementedError

    def active_monitors(self) -> list[MonitorInfo] | None:
        """此刻真正在输出画面的显示器。返回 None = 本后端分不出「挂着」和「在用」。

        默认不实现：绝大多数后端（Windows 的 EnumDisplayMonitors）本来就只枚举
        有活动信号的屏，`list_monitors()` 已经是答案。macOS 必须覆盖它 ——
        见 `dualswitch.ddc.macos.M1ddcBackend.active_monitors`。
        """
        return None

    def close(self) -> None:
        return None

    # 公共逻辑 -------------------------------------------------------------
    def pick(self, monitors: list[MonitorInfo]) -> MonitorInfo:
        """按 monitor_match 子串优先匹配，其次按 monitor_index 兜底。"""
        if not monitors:
            raise DDCError("未发现任何物理显示器")
        if self.monitor_match:
            for mon in monitors:
                if mon.matches(self.monitor_match):
                    self.last_monitor = mon
                    return mon
        idx = min(max(self.monitor_index, 0), len(monitors) - 1)
        self.last_monitor = monitors[idx]
        return monitors[idx]
