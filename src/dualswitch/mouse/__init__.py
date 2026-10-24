"""外设联动：目前只有罗技鼠标的 Easy-Switch 主机切换。"""
from __future__ import annotations

from .logi_host import MouseSwitcher, MouseSwitchOutcome

__all__ = ["MouseSwitcher", "MouseSwitchOutcome"]
