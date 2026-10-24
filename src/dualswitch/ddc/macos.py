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

2. **m1ddc 的读通道被它自己的校验和 bug 废掉了（2026-10-24 破案）**
   m1ddc 底层 AppleSiliconDDC 的 Get-VCP 请求校验和漏了 0x51 子地址，
   严格显示器直接拒绝 → 表现为 `get input` 恒 `-118`，而 Set 正常。
   **不是「这台屏不响应 DDC 读」**（旧注释的误诊，2026-10-24 实测证伪：
   用 IOAVService 直读按规范拼校验和，17/18 两态都能读到，见
   `ddc/avservice.py`）。所以 `get_input()` 先走 AVService 直读，
   失败才落回 m1ddc 老路。

3. **`display list` 列得出「不在用」的屏**
   屏切到 HDMI 后，USB-C 这条链路的 DCP 代理对象还在，`display list` 照样列出
   这块屏。**但 `set input` 不是假成功**——旧版注释称「打在没在监听的输入口上、
   返回 0 但没生效」，2026-10-24 真机实测**证伪**：画面在 Windows(HDMI/17) 时
   点「本机发送 18」，屏照样切回 Mac；反之亦然。机制：DDC/CI 是随线走的旁路
   通道（USB-C = DP AUX、HDMI = I2C DDC 脚），显示器 MCU 在**每个输入口**上都
   监听，与「当前显示哪一路」无关；且 USB-C 链路在屏切走后保持在线（幻影显示器
   就是它的副作用）。返回 0 = 真生效；不可用的只是**读通道**（-118）。
   详见 docs/SCREEN-OWNERSHIP.md §1.4。

常用命令：
    m1ddc display list
    m1ddc set input 17
    m1ddc display 2 set input 17
    m1ddc get input
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .base import BaseBackend, DDCError, MonitorInfo
from .avservice import AVServiceI2C

log = logging.getLogger("dualswitch.ddc")

# [2] Q2790R3 (4D972D7B-4973-4865-B69A-5DD248A1EFC1)
_DISPLAY_LINE = re.compile(r"^\[(?P<index>\d+)\]\s*(?P<name>.*?)\s*(?:\((?P<uuid>[0-9A-Fa-f-]{36})\))?$")

# 这台 AOC 的 USB-C DDC 处理器会**忽略孤立的一条输入源 Set**：rc=0（m1ddc 甚至
# 回显目标值）但显示器不执行——2026-10-24 实测单发后 12s 纹丝不动，两连发
# （间隔 0.4~2.8s）第二发后 0.5s 内生效；HDMI 侧（对端代发）单发即中，怪癖只在
# USB-C 这条路径。所以本机发送固定两连发。对不需要重复的显示器无害（重复设同一值）。
SET_REPEAT_DELAY_S = 1.2

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


def find_m1ddc(name: str = "m1ddc") -> str | None:
    """在 PATH 与常见安装目录里找 m1ddc，找不到返回 None。

    PATH 优先（终端里跑就是这种情况）；找不到再扫 brew / MacPorts 目录 ——
    双击 .app 启动时 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin。
    """
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    base = Path(name).name
    for folder in _EXTRA_BIN_DIRS:
        candidate = Path(folder).expanduser() / base
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class M1ddcBackend(BaseBackend):
    name = "m1ddc"
    # controller._read_host() 的开关：只有声明支持直读的后端才参与
    # 「显示器亲口报输入」的归属判定（mock / Windows 后端不开）。
    supports_ddc_detect = True

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
        self._av: AVServiceI2C | None = None

    # ------------------------------------------------------------------ 内部
    def _resolve_binary(self) -> str:
        found = find_m1ddc(self.m1ddc_path)
        if found:
            return found
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

    def active_monitors(self) -> list[MonitorInfo] | None:
        """列出「此刻真正在本机输出画面」的外接屏。

        **为什么不能复用 `list_monitors()`**：它走 `m1ddc display list`，底层是
        IOKit 的 DCP 代理。屏切到 HDMI 之后 USB-C 链路的代理对象还挂在总线上，
        `m1ddc` 照样把它列出来 —— 于是本机永远判「画面在我这儿」，
        「请对端代发」那条路一次都走不到（ISSUE-003 的根因之一）。

        这里叠**两道**过滤，缺一不可：

        1. `NSScreen.screens()` —— 当前显示配置里的屏（拿名字，给 monitor_match 用）；
        2. `CGDisplayIsActive(id)` —— 真的在扫描输出。

        ⚠️ 只做第 1 道不够：**「在线」不等于「在用」**。屏切到 HDMI 之后，
        macOS 常常仍把这块屏留在在线列表里（NSScreen 照列），只是不再往它扫画面；
        这时 `CGDisplayIsActive` 会翻成 False。用户 2026-10-23 报的
        「初始显示屏幕在 Mac、实际在 Windows」就是漏了这一道。

        返回 None 表示「问不出来」（没装 pyobjc / 拿不到显示名 / 拿不到 id），
        调用方要退回 `list_monitors()`。宁可退回旧行为，也不能给出假的「在/不在」。
        """
        try:
            from AppKit import NSScreen
            from Quartz import CGDisplayIsActive, CGDisplayIsBuiltin
        except Exception as exc:  # noqa: BLE001 - 缺 pyobjc 时降级
            log.debug("无法判断活动显示器（pyobjc 缺失）：%s", exc)
            return None
        try:
            screens = list(NSScreen.screens())
        except Exception as exc:  # noqa: BLE001 - 无窗口服务器（ssh / launchd）时也会失败
            log.debug("NSScreen.screens() 不可用：%s", exc)
            return None

        monitors: list[MonitorInfo] = []
        for position, screen in enumerate(screens):
            number = None
            try:
                number = int(screen.deviceDescription().objectForKey_("NSScreenNumber"))
            except Exception:  # noqa: BLE001
                number = None
            if number is None:
                # 拿不到 CGDirectDisplayID 就分不出内置屏 —— 那会把 MacBook 自己的屏
                # 当成「外接屏还在」，比不判断更糟。整轮作废，交给调用方退回旧判据。
                log.debug("NSScreen 缺少 NSScreenNumber，本轮活动探测作废")
                return None
            try:
                if CGDisplayIsBuiltin(number):
                    continue
                if not CGDisplayIsActive(number):
                    # 在线但不在扫描输出：屏切到 HDMI 之后就是这个样子。
                    log.debug("屏 %s 在线但未激活（画面不在本机）", number)
                    continue
            except Exception:  # noqa: BLE001
                return None
            try:
                name = str(screen.localizedName() or "")
            except Exception:  # noqa: BLE001 - 10.15 以下没有 localizedName
                name = ""
            if not name:
                # 名字是 monitor_match 唯一的匹配依据（mac 默认 match 为空 = 取第一台），
                # 空名字会让「有没有这块屏」永远判错，直接作废这一轮。
                log.debug("NSScreen 取不到显示名，本轮活动探测作废")
                return None
            monitors.append(
                MonitorInfo(
                    index=len(monitors),
                    description=name,
                    device_name=f"screen {position}",
                    device_id=name,
                )
            )
        return monitors

    def set_input(self, value: int) -> None:
        # Set 必须与读互斥（同一把 I2C 锁）：显示器的 DDC 一次只能处理一个
        # 事务，回读/轮询的 Get 与 Set 重叠时 Set 会被显示器吃掉。
        if self._av is None:
            self._av = AVServiceI2C()
        def _send() -> None:
            self._run(["set", "input", str(int(value))], display_args=self._display_args())
        def _double_send() -> None:
            _send()
            time.sleep(SET_REPEAT_DELAY_S)
            _send()          # 第二条：USB-C 路径的显示器固件才肯执行（见 SET_REPEAT_DELAY_S 注释）
        self._av.run_exclusive(_double_send)

    def get_input(self) -> int:
        # 先走 IOAVService 直读：m1ddc 的 Get-VCP 校验和有 bug（漏 0x51），
        # 严格显示器会拒绝 → -118。直读在画面切到对端时照样能读到当前输入
        # （USB-C 的 DP AUX 链路不随画面切换断），实测 17/18 两态 1~2 次成功。
        if self._av is None:
            self._av = AVServiceI2C()
        value = self._av.get_vcp(self.vcp_code)
        if value is not None:
            return value

        # 直读不可用（Intel 机 / 老系统 / 链路真断了）才落回 m1ddc 老路。
        out = self._run(["get", "input"], display_args=self._display_args())
        text = out.strip()
        if text.lstrip("-").isdigit():
            number = int(text)
            if number < 0:
                raise DDCError(
                    f"m1ddc 读通道不可用（返回 {number}，IOKit 错误码），"
                    "且 IOAVService 直读也没成功。已知 m1ddc 的 Get-VCP 校验和"
                    "有 bug（漏 0x51 子地址，见 ddc/avservice.py），直读都失败"
                    "说明链路确实不通（拔线 / 对端独占 / 系统过旧）；"
                    "写通道正常，切换不受影响，回读校验会显示「未确认」。"
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
