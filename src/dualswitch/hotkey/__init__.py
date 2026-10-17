"""热键后端工厂。"""

from __future__ import annotations

import sys

from .base import Hotkey, HotkeyBackend, HotkeyError, parse_hotkey
from .mock import ManualHotkeyBackend

__all__ = [
    "Hotkey",
    "HotkeyBackend",
    "HotkeyError",
    "ManualHotkeyBackend",
    "parse_hotkey",
    "create_hotkey",
    "resolve_backend_name",
]


def resolve_backend_name(cfg) -> str:  # noqa: ANN001
    """把 auto 解析成具体后端名。"""
    hk = parse_hotkey(cfg.hotkey)
    name = cfg.hotkey_backend
    if name == "auto":
        if sys.platform == "win32":
            return "hook" if hk.strict_right_alt else "register"
        if sys.platform == "darwin":
            return "quartz"
        return "manual"
    if name == "register" and sys.platform != "win32":
        raise HotkeyError("register 后端只适用于 Windows")
    if name == "hook" and sys.platform != "win32":
        raise HotkeyError("hook 后端只适用于 Windows")
    return name


def create_hotkey(cfg, callback) -> HotkeyBackend:  # noqa: ANN001
    """根据配置创建并返回**未启动**的热键后端。"""
    hk = parse_hotkey(cfg.hotkey)
    name = resolve_backend_name(cfg)

    if name == "manual":
        return ManualHotkeyBackend(hk, callback)

    if name == "register":
        from .windows import RegisterHotkeyBackend

        return RegisterHotkeyBackend(hk, callback)

    if name == "hook":
        from .windows import LowLevelHookBackend

        return LowLevelHookBackend(hk, callback, swallow=cfg.swallow_hotkey)

    if name == "quartz":
        from .macos import QuartzEventTapBackend

        return QuartzEventTapBackend(hk, callback, swallow=cfg.swallow_hotkey)

    raise HotkeyError(f"未知的热键后端: {name}")
