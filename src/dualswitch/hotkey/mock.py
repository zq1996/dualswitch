"""测试用热键后端：不注册任何系统钩子，由测试代码手动触发。"""

from __future__ import annotations

from .base import Hotkey


class ManualHotkeyBackend:
    name = "manual"

    def __init__(self, hotkey: Hotkey, callback):
        self.hotkey = hotkey
        self.callback = callback
        self.error: str | None = None
        self.started = False
        self.presses = 0

    def start(self, timeout: float = 1.0) -> None:  # noqa: ARG002
        self.started = True

    def stop(self) -> None:
        self.started = False

    def describe(self) -> str:
        return self.hotkey.describe()

    def press(self) -> None:
        """模拟一次按键。"""
        self.presses += 1
        self.callback()
