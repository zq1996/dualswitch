"""测试 / 演练用的假后端：把调用记录在内存里，不碰硬件。"""

from __future__ import annotations

from .base import BaseBackend, DDCError, MonitorInfo


class MockBackend(BaseBackend):
    name = "mock"

    def __init__(
        self,
        vcp_code: int = 0x60,
        monitor_match: str = "",
        monitor_index: int = 0,
        current: int = 17,
        fail_set: bool = False,
        fail_get: bool = False,
        readback_value: int | None = None,
        monitors: list[MonitorInfo] | None = None,
    ):
        super().__init__(vcp_code=vcp_code, monitor_match=monitor_match, monitor_index=monitor_index)
        self.current = current
        self.fail_set = fail_set
        self.fail_get = fail_get
        self.readback_value = readback_value  # 非 None 时模拟"回读值与目标不符"
        self.set_calls: list[int] = []
        self.get_calls = 0
        self._monitors = monitors if monitors is not None else [
            MonitorInfo(
                index=0,
                description="Generic PnP Monitor",
                device_name="\\\\.\\DISPLAY1",
                device_id="MONITOR\\AOC2790\\{mock}",
            )
        ]

    def list_monitors(self) -> list[MonitorInfo]:
        return list(self._monitors)

    def set_input(self, value: int) -> None:
        self.set_calls.append(int(value))
        if self.fail_set:
            raise DDCError("mock: 模拟 Set 失败")
        self.current = int(value)

    def get_input(self) -> int:
        self.get_calls += 1
        if self.fail_get:
            raise DDCError("mock: 模拟读取失败（Q2790PC HDMI 通道乱码）")
        if self.readback_value is not None:
            return self.readback_value
        return self.current
