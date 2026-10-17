"""macOS DDC/CI 后端：封装 m1ddc 命令行。

m1ddc 是 waydabber 的 Apple Silicon DDC 工具，通过 IOKit 私有接口发 DDC/CI。
本模块只负责调用与解析，不重复实现 IOKit 逻辑（对应需求文档 8：macOS 封装 m1ddc）。

常用命令：
    m1ddc display list
    m1ddc set input 17
    m1ddc display 1 set input 17
    m1ddc get input
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import BaseBackend, DDCError, MonitorInfo


class M1ddcBackend(BaseBackend):
    name = "m1ddc"

    def __init__(
        self,
        vcp_code: int = 0x60,
        monitor_match: str = "",
        monitor_index: int = 0,
        m1ddc_path: str = "m1ddc",
        display_index: int | None = None,
        timeout: float = 3.0,
    ):
        super().__init__(vcp_code=vcp_code, monitor_match=monitor_match, monitor_index=monitor_index)
        self.m1ddc_path = m1ddc_path or "m1ddc"
        self.display_index = display_index
        self.timeout = timeout

    # ------------------------------------------------------------------ 内部
    def _resolve_binary(self) -> str:
        candidate = Path(self.m1ddc_path).expanduser()
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(self.m1ddc_path)
        if found:
            return found
        raise DDCError(
            f"找不到 m1ddc 可执行文件（{self.m1ddc_path}）。"
            "请先安装：brew install m1ddc，或在配置中把 m1ddc_path 指到绝对路径。"
        )

    def _run(self, args: list[str]) -> str:
        binary = self._resolve_binary()
        cmd = [binary]
        if self.display_index is not None:
            cmd += ["display", str(self.display_index)]
        cmd += args
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise DDCError(f"m1ddc 调用超时（{self.timeout}s）: {' '.join(cmd)}") from exc
        except OSError as exc:
            raise DDCError(f"无法执行 m1ddc: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise DDCError(f"m1ddc 退出码 {proc.returncode}: {detail or '无输出'}")
        return proc.stdout.strip()

    # ------------------------------------------------------------------ 接口
    def list_monitors(self) -> list[MonitorInfo]:
        try:
            out = self._run(["display", "list"])
        except DDCError:
            # 老版本 m1ddc 没有 display list，退回单显示器
            return [MonitorInfo(index=0, description="m1ddc default display")]
        monitors: list[MonitorInfo] = []
        for line in out.splitlines():
            text = line.strip()
            if not text:
                continue
            monitors.append(
                MonitorInfo(
                    index=len(monitors),
                    description=text,
                    device_name=text.split()[0] if text.split() else "",
                    device_id=text,
                )
            )
        return monitors or [MonitorInfo(index=0, description="m1ddc default display")]

    def set_input(self, value: int) -> None:
        self._run(["set", "input", str(int(value))])

    def get_input(self) -> int:
        out = self._run(["get", "input"])
        for token in out.replace(":", " ").split():
            if token.isdigit():
                return int(token)
        raise DDCError(f"m1ddc 输出无法解析为输入源值: {out!r}")
