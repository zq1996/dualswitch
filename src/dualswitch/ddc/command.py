"""通用外部命令后端：把切换交给任意 CLI（ddcutil / ddcommando / ControlMyMonitor ...）。

配置示例：
    "ddc_backend": "command",
    "command_template": ["ddcutil", "setvcp", "60", "{value}"],
    "command_get_template": ["ddcutil", "getvcp", "60"]

适用场景：macOS 上不想装 m1ddc、或想复用已有的 DDCommando 二进制；
Linux 上直接用 ddcutil。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .base import BaseBackend, DDCError, MonitorInfo


class CommandBackend(BaseBackend):
    name = "command"

    def __init__(
        self,
        template: list[str],
        get_template: list[str] | None = None,
        vcp_code: int = 0x60,
        monitor_match: str = "",
        monitor_index: int = 0,
        timeout: float = 5.0,
    ):
        super().__init__(vcp_code=vcp_code, monitor_match=monitor_match, monitor_index=monitor_index)
        if not template:
            raise DDCError("command 后端需要配置 command_template")
        self.template = list(template)
        self.get_template = list(get_template or [])
        self.timeout = timeout

    def _render(self, template: list[str], value: int | None) -> list[str]:
        return [part.replace("{value}", str(value)).replace("{vcp}", str(self.vcp_code)) for part in template]

    def _run(self, cmd: list[str]) -> str:
        binary = cmd[0]
        resolved = shutil.which(binary)
        if resolved:
            cmd = [resolved] + cmd[1:]
        elif not Path(binary).expanduser().is_file():
            raise DDCError(f"找不到可执行文件: {binary}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise DDCError(f"命令超时: {' '.join(cmd)}") from exc
        except OSError as exc:
            raise DDCError(f"命令执行失败: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise DDCError(f"命令退出码 {proc.returncode}: {detail or '无输出'}")
        return proc.stdout.strip()

    def list_monitors(self) -> list[MonitorInfo]:
        return [MonitorInfo(index=0, description="command backend")]

    def set_input(self, value: int) -> None:
        self._run(self._render(self.template, int(value)))

    def get_input(self) -> int:
        if not self.get_template:
            raise DDCError("未配置 command_get_template，无法回读")
        out = self._run(self._render(self.get_template, None))
        for token in re.findall(r"\d+", out):
            return int(token)
        raise DDCError(f"命令输出无法解析为输入源值: {out!r}")
