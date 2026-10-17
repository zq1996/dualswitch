"""DDC 后端工厂：按配置与平台选择实现。"""

from __future__ import annotations

import sys

from .base import BaseBackend, DDCError, DisplayBackend, MonitorInfo
from .mock import MockBackend

__all__ = ["BaseBackend", "DDCError", "DisplayBackend", "MonitorInfo", "MockBackend", "create_backend"]


def create_backend(cfg) -> DisplayBackend:  # noqa: ANN001 - 避免循环导入
    """根据 cfg.ddc_backend 返回后端实例。"""
    kind = cfg.ddc_backend
    common = {
        "vcp_code": cfg.vcp_code,
        "monitor_match": cfg.monitor_match,
        "monitor_index": cfg.monitor_index,
    }

    if kind == "mock":
        return MockBackend(**common)

    if kind == "command":
        from .command import CommandBackend

        return CommandBackend(
            template=cfg.command_template,
            get_template=cfg.command_get_template,
            **common,
        )

    if kind == "auto":
        kind = "dxva2" if sys.platform == "win32" else "m1ddc"

    if kind == "dxva2":
        if sys.platform != "win32":
            raise DDCError("dxva2 后端只能在 Windows 上使用")
        from .windows import WindowsBackend

        return WindowsBackend(**common)

    if kind == "m1ddc":
        if sys.platform != "darwin":
            raise DDCError("m1ddc 后端只能在 macOS 上使用")
        from .macos import M1ddcBackend

        return M1ddcBackend(
            m1ddc_path=cfg.m1ddc_path,
            display_index=cfg.m1ddc_display_index,
            **common,
        )

    raise DDCError(f"未知的 ddc_backend: {kind}")
