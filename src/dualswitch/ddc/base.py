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


class BaseBackend:
    """提供默认实现与统一的 VCP 记录，便于子类复用。"""

    name = "base"

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
