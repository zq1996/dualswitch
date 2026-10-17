"""macOS DDC/CI 后端：封装 m1ddc 命令行。

m1ddc 是 waydabber 的 Apple Silicon DDC 工具，通过 IOKit 私有接口发 DDC/CI。
本模块只负责调用与解析，不重复实现 IOKit 逻辑（对应需求文档 8：macOS 封装 m1ddc）。

macOS 与 Windows 的两个关键差异（都是实测踩出来的）：

1. **多屏序号是 1 基的，而且第一块常常是内置屏**
   `m1ddc display list` 输出形如
       [1] (null) (37D8832A-...)
       [2] Q2790R3 (4D972D7B-...)
   内置屏没有名字（(null)），也不能 DDC。不带 `display n` 直接 `m1ddc set input`
   会打到内置屏上，静默失败。所以这里把内置屏/无名屏过滤掉，并把真实序号
   放进 `MonitorInfo.extra["m1ddc_index"]`，发送时自动带上。

2. **读通道可能整体不可用**
   USB-C(DP Alt Mode) 接线下，这台机器的 `get input` / `get luminance` 全部返回
   `-118`（IOKit kIOReturn 类错误），但**写通道正常**。所以读值失败必须报成
   「不可读」而不是「解析失败」，并且绝不能让回读影响切换主流程。

常用命令：
    m1ddc display list
    m1ddc set input 17
    m1ddc display 2 set input 17
    m1ddc get input
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .base import BaseBackend, DDCError, MonitorInfo

# [2] Q2790R3 (4D972D7B-4973-4865-B69A-5DD248A1EFC1)
_DISPLAY_LINE = re.compile(r"^\[(?P<index>\d+)\]\s*(?P<name>.*?)\s*(?:\((?P<uuid>[0-9A-Fa-f-]{36})\))?$")

# 内置屏 / 无名的屏：m1ddc 里名字是 (null)，没有 DDC 通道
_PLACEHOLDER_NAMES = {"", "null", "(null)", "none"}

# Finder 双击 .app 启动时 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin，
# brew 装的 m1ddc 不在里面 —— shutil.which 直接找不到，表现为「切换失败：找不到 m1ddc」。
# 把这些常见位置作为兜底，同时把它们补进子进程的 PATH（m1ddc 自己可能调用其他工具）。
_EXTRA_BIN_DIRS = (
    "/opt/homebrew/bin",   # Apple Silicon 的 brew
    "/usr/local/bin",      # Intel 的 brew
    "/opt/local/bin",      # MacPorts
    "~/.local/bin",
    "~/bin",
)


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
        # PATH 里没有（双击 .app 启动的典型情况）：到常见安装目录里找同名文件
        name = Path(self.m1ddc_path).name
        for folder in _EXTRA_BIN_DIRS:
            folder_path = Path(folder).expanduser()
            candidate = folder_path / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        searched = "、".join(_EXTRA_BIN_DIRS)
        raise DDCError(
            f"找不到 m1ddc 可执行文件（{self.m1ddc_path}）。"
            f"已查找 PATH 与 {searched}。"
            "请先安装：brew install m1ddc，或在配置中把 m1ddc_path 指到绝对路径。"
        )

    def _env(self) -> dict[str, str]:
        """给子进程补上常见 bin 目录，避免 Finder 启动时 PATH 太窄。"""
        env = os.environ.copy()
        extra = [str(Path(d).expanduser()) for d in _EXTRA_BIN_DIRS]
        current = env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        parts = [p for p in current.split(os.pathsep) if p]
        for folder in extra:
            if folder not in parts and Path(folder).is_dir():
                parts.insert(0, folder)
        env["PATH"] = os.pathsep.join(parts)
        return env

    def _run(self, args: list[str], display_args: list[str] | None = None) -> str:
        binary = self._resolve_binary()
        cmd = [binary]
        if display_args:
            cmd += display_args
        cmd += args
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout, env=self._env()
            )
        except subprocess.TimeoutExpired as exc:
            raise DDCError(f"m1ddc 调用超时（{self.timeout}s）: {' '.join(cmd)}") from exc
        except OSError as exc:
            raise DDCError(f"无法执行 m1ddc: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise DDCError(f"m1ddc 退出码 {proc.returncode}: {detail or '无输出'}")
        return proc.stdout.strip()

    def _display_args(self) -> list[str]:
        """算出该发给哪块屏：显式配置优先，否则按显示器名自动解析。

        解析失败（比如此刻屏已经被切走、枚举不到）时退回不带序号的调用，
        由 m1ddc 自己挑默认屏——总比直接抛错、让用户连切回去都做不到好。
        """
        if self.display_index is not None:
            return ["display", str(self.display_index)]
        try:
            monitor = self.pick(self.list_monitors())
        except DDCError:
            return []
        index = monitor.extra.get("m1ddc_index")
        return ["display", str(index)] if index else []

    # ------------------------------------------------------------------ 接口
    def list_monitors(self) -> list[MonitorInfo]:
        try:
            out = self._run(["display", "list"])
        except DDCError as exc:
            message = str(exc)
            # 二进制缺失是硬错误，不能假装有屏（否则状态检测会说「画面在本机」）
            if "找不到 m1ddc" in message or "无法执行" in message:
                raise
            # 老版本 m1ddc 没有 display list 子命令：退回「默认显示器」
            return [MonitorInfo(index=0, description="m1ddc default display", extra={"m1ddc_index": None})]

        monitors: list[MonitorInfo] = []
        for line in out.splitlines():
            text = line.strip()
            if not text:
                continue
            match = _DISPLAY_LINE.match(text)
            if match:
                raw_name = (match.group("name") or "").strip()
                if raw_name.lower() in _PLACEHOLDER_NAMES:
                    continue                      # 内置屏 / 无 DDC 的屏，跳过
                index = int(match.group("index"))  # m1ddc 的序号是 1 基
                name = raw_name
                uuid = match.group("uuid") or ""
            else:
                # 不认识的格式就按整行当名字，序号按出现顺序 +1
                name, uuid, index = text, "", len(monitors) + 1
            monitors.append(
                MonitorInfo(
                    index=len(monitors),              # 本项目内部统一 0 基
                    description=name,
                    device_name=f"display {index}",
                    device_id=uuid or text,
                    extra={"m1ddc_index": index},
                )
            )
        return monitors

    def set_input(self, value: int) -> None:
        self._run(["set", "input", str(int(value))], display_args=self._display_args())

    def get_input(self) -> int:
        out = self._run(["get", "input"], display_args=self._display_args())
        text = out.strip()
        if text.lstrip("-").isdigit():
            number = int(text)
            if number < 0:
                raise DDCError(
                    f"m1ddc 读通道不可用（返回 {number}，IOKit 错误码）。"
                    "这台屏在 USB-C(DP Alt Mode) 下不响应 DDC 读请求，"
                    "写通道正常，切换不受影响；回读校验会显示「未确认」。"
                )
            if 0 <= number <= 0xFF:
                return number
        for token in text.replace(":", " ").split():
            if token.isdigit():
                return int(token)
        raise DDCError(f"m1ddc 输出无法解析为输入源值: {out!r}")

    def read_supported(self) -> bool:
        """探测读通道是否可用（doctor / 界面提示用，不会切屏）。"""
        try:
            self.get_input()
            return True
        except DDCError:
            return False
