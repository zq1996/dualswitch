"""配置加载 / 校验 / 落盘。

配置为单个 JSON 文件，默认位置：
  Windows  %APPDATA%\\dualswitch\\config.json
  macOS    ~/Library/Application Support/dualswitch/config.json
可用环境变量 DUALSWITCH_CONFIG 覆盖，或命令行 --config 指定。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

APP_NAME = "dualswitch"
CONFIG_FILENAME = "config.json"
#: 每次落盘前，把上一份「能解析」的配置留成这个后缀（同目录）
BACKUP_SUFFIX = ".bak"
#: 配置读不懂时（`_degraded`）设备 ID 单独存这个文件，免得为了记 ID 去改用户的配置
DEVICE_ID_FILENAME = "device-id.txt"


class ConfigError(ValueError):
    """配置内容非法。"""


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
def config_dir() -> Path:
    """返回平台约定的配置目录（不保证存在）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def config_path() -> Path:
    """配置文件的最终路径（考虑环境变量覆盖）。"""
    env = os.environ.get("DUALSWITCH_CONFIG")
    if env:
        return Path(env).expanduser()
    return config_dir() / CONFIG_FILENAME


def log_path() -> Path:
    env = os.environ.get("DUALSWITCH_LOG")
    if env:
        return Path(env).expanduser()
    return config_dir() / "logs" / "dualswitch.log"


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class ReadbackConfig:
    """Set 之后的异步回读（非阻塞）。Q2790PC 的 HDMI 通道读数不可靠，故仅用于记录。

    ⚠️ 延迟必须大于**显示器完成切换的时间**。实测 AOC Q2790PC 切一次要 2–3 秒，
    原来 300ms 就开始读，等于在切换过程中往 DDC 总线上插事务 —— 总线是串行的，
    很可能把切换打断（真机上表现为「Set 返回成功、回读也是目标值，但画面不动」）。
    """

    enabled: bool = True
    delay_ms: int = 3000         # 等显示器把切换做完再读（Q2790PC 实测 2–3 秒）
    timeout_ms: int = 8000       # 回读总预算，必须大于 delay_ms，否则一轮都读不到
    interval_ms: int = 400       # 两次回读之间的间隔
    notify_on_unconfirmed: bool = False  # 未确认时是否弹托盘气泡（默认只写日志）


@dataclass
class ClipboardConfig:
    """跨端剪贴板同步（P0 文字 / P1 图片）。契约见 `docs/CLIPBOARD-SYNC-CONTRACT.md`。

    这里只管"怎么连、传什么"，平台差异一律在 `clipboard/{macos,windows}.py` 里。
    """

    enabled: bool = True
    image_enabled: bool = True
    # on_switch: 只在切屏那一刻同步（本次实现）；always: 常驻同步（预留，不实现）
    mode: str = "on_switch"
    port: int = 47823                  # 本机监听端口
    peer_port: int = 0                 # 对端监听端口；0 = 与 port 相同（单机自测时才需要不同）
    discover_port: int = 47824
    peer: str = ""                     # 手填对端地址 "192.168.1.5" 或 "192.168.1.5:47823"
    device_id: str = ""                # 首次运行生成 {host}-{8hex}
    peer_device: str = ""              # 最近一次握手的对端 device_id
    peer_name: str = ""                # 对端主机名（界面上显示）
    paired_at: float = 0.0             # 完成配对的时间戳（配对页显示）
    psk: str = ""                      # 配对后的共享密钥（hex）
    max_image_mb: int = 32
    record_limit: int = 10             # 内存里保留的记录条数，退出即清
    debug_plain: bool = False          # 跳过加密（排障用，写配置时记 warning）
    # ---- v2 设置页（INTERACTIONS §4.1，2026-10-21 新增，旧配置文件默认值兜底）----
    hover_preview: bool = True         # 缩略图悬停 450ms 弹预览窗
    retention_days: int = 30           # 保留历史：7 / 30 / 0=不限
    storage_limit_mb: int = 200        # 存储上限（值显）

    def effective_peer_port(self) -> int:
        return int(self.peer_port) or int(self.port)


@dataclass
class MouseSwitchConfig:
    """切屏时顺带把罗技鼠标的 Easy-Switch 切过去（HID++ CHANGE_HOST 0x1814）。

    默认关闭：需要用户先确认自己的鼠标有几个槽位、本机与对端各占哪个。
    配置错了最坏结果是鼠标失联（只能按鼠标底部的物理键恢复），所以宁可默认不动。

    可行性实测与约束见 `docs/FEASIBILITY-LOGI-EASYSWITCH.md`。
    """

    enabled: bool = False
    local_slot: int = 0     # 本机占用的槽位（1-based，0 = 未配置）
    peer_slot: int = 0      # 对端占用的槽位（1-based，0 = 未配置）
    settle_ms: int = 800    # 切屏后等多久再切鼠标；0 = 不等待
    # 注意：没有「请对端拉回」开关。画面回本机时，本机发不了命令（鼠标在对端），
    # 由对端在它自己的切换路径里推回来——这条链路是自动闭合的，
    # 因为对端代发最终也走 set_input_local → _do_local。


@dataclass
class KeyboardFollowConfig:
    """键盘跟随切屏：画面在哪台，键盘就从哪台输出。

    键盘物理上挂在 Windows（`capture_host`），画面切到 Mac 时把按键转发过去。
    方案与硬伤清单见 `docs/KEYBOARD-BRIDGE.md`。

    默认关闭，理由同鼠标联动：一旦吞键逻辑出错，用户会"打字打到一个看不见的
    窗口里"，影响面比鼠标失联大得多。要先在两端都能看见状态再打开。
    """

    enabled: bool = False
    #: 键盘挂在哪台机器上。目前只支持 windows（捕获端）；mac 那一半没实现。
    capture_host: str = "windows"
    #: position: Alt→⌘、Win→⌥（用户 2026-11-06 选定，Alt+C 就是复制）
    #: semantic: 按名字映射，Win→⌘、Alt→⌥
    mod_map: str = "position"
    #: 紧急停止组合：一键解除吞键，把键盘还给本机。永远不被转发。
    release_chord: str = "ctrl+alt+shift+k"
    #: 换焦点后多少毫秒内丢弃按键。0 = 不丢（默认，丢键比误投更难解释）
    blur_guard_ms: int = 0
    #: 媒体键 / 侧边触控条（Consumer page）是否转发。V1 不做。
    forward_media_keys: bool = False
    #: T_KEYSTATE 心跳频率。它同时是链路存活判据、RTT 采样、按键对账三合一。
    key_state_hz: int = 10
    #: auto | forward | dedupe —— macOS 对注入的 keydown 会不会自动重复，
    #: 决定重复键该转发还是该去重（见方案 §8.2，靠 --probe 实测）。
    repeat_mode: str = "auto"
    #: 进焦点时把对端 CapsLock 对齐到源端，否则会出现"打出来全是大写"。
    align_caps: bool = True


@dataclass
class DisplayPowerConfig:
    """画面不在本机时，把不归本机的外接屏从 WindowServer 里停掉（只有 macOS 生效）。

    显示器输入端切走后 USB-C / DP 链路还在，macOS 会继续为它合成与扫描输出；
    2026-11-06 实测停掉这块屏后系统 CPU 从 23.4% 降到 19.0%（`outputs/measure_ab.py`），
    用户侧表现是「Mac 没干活却发烫」。实现与安全底线见 `display_power.py` 模块注释。

    **默认关**（2026-11-14）：停用之后这块屏在系统里就没有输出对象了，
    DDC 读和写一起失效（`m1ddc` 连屏都枚举不到）。回程全靠「先恢复再发 Set」，
    而恢复在部分状态下会回 1001，屏就醒不过来（用户当晚丢了扩展屏，要拔插线）。
    想省这点电就先看清风险：`dualswitch display off` 手动试一次、确认能恢复再开。
    """

    enabled: bool = False
    #: 画面走后等多久再停。Q2790 一次切换要 2–3 秒，太早停会让回读/归属判断踩空
    off_delay_ms: int = 2500
    #: 停用期间多久读一次 DDC 判断画面是否回来（读会毒化下一条 Set，别设太密）
    probe_interval_ms: int = 1500


@dataclass
class Config:
    # ---- 热键 ----
    hotkey: str = "alt+space"
    # auto: Windows 用 RegisterHotKey；macOS 用 CGEventTap
    # register: 强制 RegisterHotKey（不区分左右 Alt）
    # hook: Windows 低级键盘钩子（严格右 Alt，可吞掉按键）
    hotkey_backend: str = "auto"
    swallow_hotkey: bool = True   # 仅 hook 后端有效：吞掉按键，避免弹出窗口系统菜单
    debounce_ms: int = 800        # 热键去抖，防止长按 / 连击

    # ---- 显示器 ----
    vcp_code: int = 0x60          # MCCS: Input Source Select
    # AOC Q2790PC 实测值：17 = HDMI（Windows），18 = USB-C（Mac），15 = DP。
    # 注意：27 是常见 MCCS 里 USB-C 的取值，但这块屏直接拒绝 27（实测），不要照抄网上的 27。
    sources: dict[str, int] = field(default_factory=lambda: {"windows": 17, "mac": 18})
    this_host: str = "auto"       # auto | windows | mac
    switch_mode: str = "other"    # other: 按一次切到另一台；toggle: 在两侧之间翻转
    monitor_match: str = "AOC"    # 物理显示器匹配子串（匹配 device_id / 设备名 / 描述）
    monitor_index: int = 0        # 匹配不到时使用第 N 个物理显示器
    ddc_backend: str = "auto"     # auto | dxva2 | m1ddc | command | mock
    m1ddc_path: str = "m1ddc"
    m1ddc_display_index: int | None = None   # m1ddc display <n> ...；None 表示单显示器直连
    command_template: list[str] = field(default_factory=list)  # 例: ["ddcutil","setvcp","60","{value}"]
    command_get_template: list[str] = field(default_factory=list)
    dry_run: bool = False         # 只记录不真发（便于演练）

    # ---- 运行时 ----
    readback: ReadbackConfig = field(default_factory=ReadbackConfig)
    tray: bool = True
    notify: bool = True
    log_level: str = "INFO"

    # ---- v2 设置页（INTERACTIONS §4.1 / §0.2，2026-10-21 新增）----
    switch_policy: str = "standard"    # standard: 先本地后请对端代发；force: 直接发 Set
    appearance: str = "system"         # system | light | dark（§0.2 明暗主题）
    clipboard_hotkey: str = "cmd+shift+v"   # 呼出剪贴板页（键帽 ⌘⇧V）

    # ---- 剪贴板同步 ----
    clipboard: ClipboardConfig = field(default_factory=ClipboardConfig)

    # ---- 鼠标联动（切屏时顺带切 Easy-Switch）----
    mouse_switch: MouseSwitchConfig = field(default_factory=MouseSwitchConfig)

    # ---- 键盘跟随（画面在哪台，键盘就从哪台输出）----
    keyboard_follow: KeyboardFollowConfig = field(default_factory=KeyboardFollowConfig)

    # ---- 外接屏省电（画面不在本机时不再为它渲染）----
    display_power: DisplayPowerConfig = field(default_factory=DisplayPowerConfig)

    # ---- 载入期元数据（不落盘）----
    #: 本版本不认识的字段，原样留着、落盘时写回。**不能丢**：老版本程序读到新配置
    #: 时把这些字段扔掉再存盘，等于替用户删配置（2026-11-06 事故）。
    _extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    #: 载入时整份配置读不懂、已降到默认值。这个状态下 `save()` 拒绝覆盖原文件。
    _degraded: bool = field(default=False, repr=False, compare=False)

    # ---- 派生属性 ----
    def effective_host(self) -> str:
        """本机在 sources 中的身份。"""
        if self.this_host in ("windows", "mac"):
            return self.this_host
        return "windows" if sys.platform == "win32" else "mac"

    def other_host(self) -> str:
        return "mac" if self.effective_host() == "windows" else "windows"

    def target_value(self, target: str) -> int:
        try:
            return int(self.sources[target])
        except KeyError as exc:  # pragma: no cover - 校验阶段已拦截
            raise ConfigError(f"sources 中缺少 {target!r}") from exc

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("_extra", {}) or {}
        data.pop("_degraded", None)
        for key, value in extra.items():
            current = data.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                # 只补本版本没有的子字段，认识的字段以内存里的值为准
                for sub_key, sub_value in value.items():
                    current.setdefault(sub_key, sub_value)
            else:
                data.setdefault(key, value)
        return data


DEFAULTS = Config()


def platform_defaults() -> Config:
    """按平台补齐默认值的配置副本。

    macOS 与 Windows 的差异只在「开箱即用」的几个字段上，业务字段（sources /
    readback / debounce）保持完全一致，否则两端配置文件会互相看不懂：

    | 字段 | Windows | macOS | 原因 |
    |---|---|---|---|
    | hotkey | alt+space | alt+space（= ⌥ Space，左 Option） | mac 键盘上没有 Alt 键，⌥ 才是用户会去按的那个；右 Option 需要刻意伸手指 |
    | monitor_match | AOC | 空 | mac 的显示器列表里只有外接屏（内置屏被过滤），空 = 取第一台 |
    | m1ddc_display_index | — | None | 由后端按显示器名自动解析，不写死序号 |
    """
    cfg = copy.deepcopy(DEFAULTS)
    if sys.platform == "darwin":
        # alt = Option（左）；想只认右 Option 的用户在设置里打开「只认右 Option」，
        # 那条路径会把 hotkey 改写成 ralt+space（见 settings._with_strict_modifier）
        cfg.hotkey = "alt+space"
        cfg.monitor_match = ""
    return cfg


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
_VALID_HOSTS = ("windows", "mac")

#: 画面归属的「第三态」：既不在本机、也不在另一台 —— 因为**根本没枚举到外接屏**。
#: 控制器与界面共用这一个字面量：曾经把「枚举不到」直接当成「在另一台」，
#: 结果没接显示器时界面硬说「当前 Windows」（ISSUE-001）。
SOURCE_UNKNOWN = "unknown"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    raise ConfigError(f"无法解析布尔值: {value!r}")


def _merge_readback(
    raw: dict[str, Any],
    base: ReadbackConfig,
    *,
    unknown: dict[str, Any] | None = None,
) -> ReadbackConfig:
    if not isinstance(raw, dict):
        raise ConfigError("readback 必须是对象")
    cfg = copy.deepcopy(base)
    for key, value in raw.items():
        if not hasattr(cfg, key) or key.startswith("_"):
            if unknown is None:
                raise ConfigError(f"未知的 readback 字段: {key}")
            unknown[key] = value
            continue
        setattr(cfg, key, _coerce_bool(value) if isinstance(getattr(cfg, key), bool) else int(value))
    return cfg


def _merge_clipboard(
    raw: dict[str, Any],
    base: ClipboardConfig,
    *,
    unknown: dict[str, Any] | None = None,
) -> ClipboardConfig:
    if not isinstance(raw, dict):
        raise ConfigError("clipboard 必须是对象")
    cfg = copy.deepcopy(base)
    for key, value in raw.items():
        if not hasattr(cfg, key) or key.startswith("_"):
            if unknown is None:
                raise ConfigError(f"未知的 clipboard 字段: {key}")
            unknown[key] = value
            continue
        current = getattr(cfg, key)
        if isinstance(current, bool):
            setattr(cfg, key, _coerce_bool(value))
        elif isinstance(current, float):
            setattr(cfg, key, float(value))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(cfg, key, int(value))
        elif value is None:
            setattr(cfg, key, "")
        else:
            setattr(cfg, key, str(value))
    return cfg


def _merge_mouse_switch(
    raw: dict[str, Any],
    base: MouseSwitchConfig,
    *,
    unknown: dict[str, Any] | None = None,
) -> MouseSwitchConfig:
    if not isinstance(raw, dict):
        raise ConfigError("mouse_switch 必须是对象")
    cfg = copy.deepcopy(base)
    for key, value in raw.items():
        if not hasattr(cfg, key) or key.startswith("_"):
            if unknown is None:
                raise ConfigError(f"未知的 mouse_switch 字段: {key}")
            unknown[key] = value
            continue
        current = getattr(cfg, key)
        if isinstance(current, bool):
            setattr(cfg, key, _coerce_bool(value))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(cfg, key, int(value))
        else:
            setattr(cfg, key, value)
    return cfg


def _merge_keyboard_follow(
    raw: dict[str, Any],
    base: KeyboardFollowConfig,
    *,
    unknown: dict[str, Any] | None = None,
) -> KeyboardFollowConfig:
    if not isinstance(raw, dict):
        raise ConfigError("keyboard_follow 必须是对象")
    cfg = copy.deepcopy(base)
    for key, value in raw.items():
        if not hasattr(cfg, key) or key.startswith("_"):
            if unknown is None:
                raise ConfigError(f"未知的 keyboard_follow 字段: {key}")
            unknown[key] = value
            continue
        current = getattr(cfg, key)
        if isinstance(current, bool):
            setattr(cfg, key, _coerce_bool(value))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(cfg, key, int(value))
        else:
            setattr(cfg, key, value)
    return cfg


def _merge_display_power(
    raw: dict[str, Any],
    base: DisplayPowerConfig,
    *,
    unknown: dict[str, Any] | None = None,
) -> DisplayPowerConfig:
    if not isinstance(raw, dict):
        raise ConfigError("display_power 必须是对象")
    cfg = copy.deepcopy(base)
    for key, value in raw.items():
        if not hasattr(cfg, key) or key.startswith("_"):
            if unknown is None:
                raise ConfigError(f"未知的 display_power 字段: {key}")
            unknown[key] = value
            continue
        current = getattr(cfg, key)
        if isinstance(current, bool):
            setattr(cfg, key, _coerce_bool(value))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(cfg, key, int(value))
        else:
            setattr(cfg, key, value)
    return cfg


def from_dict(
    raw: dict[str, Any], base: Config | None = None, *, keep_unknown: bool = True
) -> Config:
    """把 JSON 字典合并到默认配置上，并做严格校验。

    本版本不认识的字段**不算错**，统一收进 `_extra`，落盘时原样写回。这条是硬要求：
    旧版本程序读到新字段就放弃整份配置、再用默认值覆盖，等于替用户删配置
    （2026-11-06 事故：鼠标槽位 2⇄1、配对 psk、device_id 一起没了）。

    真正非法的取值（类型错、越界）仍然抛 `ConfigError`；`keep_unknown=False` 只
    用于「我要知道这份配置里有没有我不认识的键」的显式检查场景。
    """
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是 JSON 对象")
    cfg = copy.deepcopy(base or DEFAULTS)
    extras: dict[str, Any] = {}

    for key, value in raw.items():
        if key in ("readback", "clipboard", "mouse_switch", "keyboard_follow", "display_power"):
            section: dict[str, Any] = {}
            slot = None if not keep_unknown else section
            if key == "readback":
                cfg.readback = _merge_readback(value, cfg.readback, unknown=slot)
            elif key == "clipboard":
                cfg.clipboard = _merge_clipboard(value, cfg.clipboard, unknown=slot)
            elif key == "mouse_switch":
                cfg.mouse_switch = _merge_mouse_switch(value, cfg.mouse_switch, unknown=slot)
            elif key == "display_power":
                cfg.display_power = _merge_display_power(value, cfg.display_power, unknown=slot)
            else:
                cfg.keyboard_follow = _merge_keyboard_follow(
                    value, cfg.keyboard_follow, unknown=slot
                )
            if section:
                extras[key] = section
            continue
        if key == "sources":
            if not isinstance(value, dict):
                raise ConfigError("sources 必须是对象，如 {\"windows\": 17, \"mac\": 18}")
            merged = dict(cfg.sources)
            for host, code in value.items():
                if host not in _VALID_HOSTS:
                    raise ConfigError(f"sources 中出现未知主机: {host!r}")
                merged[host] = int(code)
            cfg.sources = merged
            continue
        if not hasattr(cfg, key) or key.startswith("_"):
            if not keep_unknown:
                raise ConfigError(f"未知的配置项: {key}")
            extras[key] = value
            continue
        current = getattr(cfg, key)
        if isinstance(current, bool):
            setattr(cfg, key, _coerce_bool(value))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(cfg, key, int(value))
        elif key == "m1ddc_display_index":
            setattr(cfg, key, None if value in (None, "", "null") else int(value))
        elif key == "command_template" or key == "command_get_template":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(f"{key} 必须是字符串数组")
            setattr(cfg, key, list(value))
        else:
            setattr(cfg, key, value)

    cfg._extra = extras
    validate(cfg)
    return cfg


def _validate_clipboard(cb: ClipboardConfig) -> None:
    if not (1 <= int(cb.port) <= 65535):
        raise ConfigError("clipboard.port 必须在 1..65535 之间")
    if cb.peer_port and not (1 <= int(cb.peer_port) <= 65535):
        raise ConfigError("clipboard.peer_port 必须在 1..65535 之间（0 表示与 port 相同）")
    if not (1 <= int(cb.discover_port) <= 65535):
        raise ConfigError("clipboard.discover_port 必须在 1..65535 之间")
    if cb.mode not in ("on_switch", "always"):
        raise ConfigError("clipboard.mode 只能是 on_switch / always")
    if not (1 <= int(cb.max_image_mb) <= 128):
        raise ConfigError("clipboard.max_image_mb 必须在 1..128 之间")
    if not (1 <= int(cb.record_limit) <= 100):
        raise ConfigError("clipboard.record_limit 必须在 1..100 之间")
    if int(cb.retention_days) < 0:
        raise ConfigError("clipboard.retention_days 不能为负（0 = 不限）")
    if not (1 <= int(cb.storage_limit_mb) <= 4096):
        raise ConfigError("clipboard.storage_limit_mb 必须在 1..4096 之间")
    if cb.psk and not _is_hex(cb.psk):
        raise ConfigError("clipboard.psk 必须是十六进制字符串")


def _validate_mouse_switch(ms: MouseSwitchConfig) -> None:
    """校验鼠标联动配置。

    只在 enabled 时强制要求槽位：关闭状态下允许留空，方便用户先写一半。
    配错的代价是鼠标失联（只能按物理键恢复），所以宁可在这里拦住。
    """
    if int(ms.settle_ms) < 0:
        raise ConfigError("mouse_switch.settle_ms 不能为负")
    if not ms.enabled:
        return
    for name in ("local_slot", "peer_slot"):
        slot = int(getattr(ms, name))
        if not (1 <= slot <= 3):
            raise ConfigError(
                f"mouse_switch.{name} 必须在 1..3 之间（当前 {slot}）；"
                "请先确认鼠标底部的 Easy-Switch 灯位"
            )
    if int(ms.local_slot) == int(ms.peer_slot):
        raise ConfigError("mouse_switch.local_slot 与 peer_slot 不能相同，否则切换无意义")


def _validate_keyboard_follow(kf: KeyboardFollowConfig, hotkey: str) -> None:
    """校验键盘跟随配置。

    两条是"配错就会很难受"的，必须在这里拦住：

    * `release_chord` 和切屏 `hotkey` 相同 —— 按一下既要切屏又要停转发，
      用户会以为自己把键盘按坏了。
    * `capture_host` 写成 mac —— 捕获端这一半还没实现，配了就是静默不工作。
    """
    if kf.capture_host not in ("windows", "mac"):
        raise ConfigError("keyboard_follow.capture_host 只能是 windows / mac")
    if kf.mod_map not in ("position", "semantic"):
        raise ConfigError("keyboard_follow.mod_map 只能是 position / semantic")
    if kf.repeat_mode not in ("auto", "forward", "dedupe"):
        raise ConfigError("keyboard_follow.repeat_mode 只能是 auto / forward / dedupe")
    if not (1 <= int(kf.key_state_hz) <= 50):
        raise ConfigError("keyboard_follow.key_state_hz 必须在 1..50 之间")
    if int(kf.blur_guard_ms) < 0:
        raise ConfigError("keyboard_follow.blur_guard_ms 不能为负")
    if kf.enabled:
        if kf.capture_host == "mac":
            raise ConfigError(
                "keyboard_follow.capture_host=mac 暂不支持：捕获端只实现了 Windows"
            )
        if not str(kf.release_chord).strip():
            raise ConfigError("keyboard_follow.enabled=true 时 release_chord 不能为空")
        if str(kf.release_chord).strip().lower() == str(hotkey).strip().lower():
            raise ConfigError(
                "keyboard_follow.release_chord 不能和切屏热键相同（按一下会又切屏又停转发）"
            )


def _validate_display_power(dp: DisplayPowerConfig) -> None:
    if int(dp.off_delay_ms) < 0:
        raise ConfigError("display_power.off_delay_ms 不能为负")
    if not (200 <= int(dp.probe_interval_ms) <= 10000):
        raise ConfigError("display_power.probe_interval_ms 必须在 200..10000 之间")


def _is_hex(value: str) -> bool:
    try:
        bytes.fromhex(value)
    except (TypeError, ValueError):
        return False
    return True


def ensure_device_id(cfg: Config) -> str:
    """确保 `clipboard.device_id` 存在并返回它（首次运行时按 `{host}-{8hex}` 生成）。

    不用 uuid4 的带横线格式：设备 ID 会出现在日志与界面里，短一点好读。
    """
    cb = cfg.clipboard
    if cb.device_id:
        return cb.device_id
    cb.device_id = f"{cfg.effective_host()}-{secrets.token_hex(4)}"
    return cb.device_id


def validate(cfg: Config) -> None:
    if not (0 <= int(cfg.vcp_code) <= 0xFF):
        raise ConfigError("vcp_code 必须在 0..255 之间")
    for host in _VALID_HOSTS:
        if host not in cfg.sources:
            raise ConfigError(f"sources 必须同时包含 windows 与 mac（缺少 {host}）")
        if not (0 <= int(cfg.sources[host]) <= 0xFF):
            raise ConfigError(f"sources.{host} 必须在 0..255 之间")
    if cfg.sources["windows"] == cfg.sources["mac"]:
        raise ConfigError("sources.windows 与 sources.mac 不能相同，否则切换无效果")
    if cfg.this_host not in ("auto",) + _VALID_HOSTS:
        raise ConfigError("this_host 只能是 auto / windows / mac")
    if cfg.switch_mode not in ("other", "toggle"):
        raise ConfigError("switch_mode 只能是 other / toggle")
    if cfg.hotkey_backend not in ("auto", "register", "hook"):
        raise ConfigError("hotkey_backend 只能是 auto / register / hook")
    if cfg.switch_policy not in ("standard", "force"):
        raise ConfigError("switch_policy 只能是 standard / force")
    if cfg.appearance not in ("system", "light", "dark"):
        raise ConfigError("appearance 只能是 system / light / dark")
    if cfg.ddc_backend not in ("auto", "dxva2", "m1ddc", "command", "mock"):
        raise ConfigError("ddc_backend 只能是 auto / dxva2 / m1ddc / command / mock")
    if cfg.ddc_backend == "command" and "{value}" not in " ".join(cfg.command_template):
        raise ConfigError("ddc_backend=command 时 command_template 必须包含 {value} 占位符")
    if cfg.readback.delay_ms < 0 or cfg.readback.timeout_ms < 0 or cfg.readback.interval_ms < 0:
        raise ConfigError("readback 的时间参数不能为负")
    if cfg.debounce_ms < 0:
        raise ConfigError("debounce_ms 不能为负")
    _validate_clipboard(cfg.clipboard)
    _validate_mouse_switch(cfg.mouse_switch)
    _validate_keyboard_follow(cfg.keyboard_follow, cfg.hotkey)
    _validate_display_power(cfg.display_power)
    if not str(cfg.hotkey).strip():
        raise ConfigError("hotkey 不能为空")
    # 延迟导入，避免 config 模块反向依赖 hotkey 包
    from .hotkey.base import HotkeyError, parse_hotkey

    try:
        parse_hotkey(cfg.hotkey)
    except HotkeyError as exc:
        raise ConfigError(f"hotkey 无法解析: {exc}") from exc
    try:
        parse_hotkey(cfg.clipboard_hotkey)
    except HotkeyError as exc:
        raise ConfigError(f"clipboard_hotkey 无法解析: {exc}") from exc


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
# 载入期发现的问题。此时日志系统通常还没初始化（`_load_config` 早于
# `_setup_logging`），先攒着，由 cli 在日志就绪后统一打出来。
LOAD_WARNINGS: list[str] = []


def take_load_warnings() -> list[str]:
    """取走并清空载入期警告（调用方负责写日志）。"""
    global LOAD_WARNINGS
    out, LOAD_WARNINGS = LOAD_WARNINGS, []
    return out


def _device_id_sidecar(target: Path) -> Path:
    return target.with_name(DEVICE_ID_FILENAME)


def _read_device_id_sidecar(target: Path) -> str:
    try:
        return _device_id_sidecar(target).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _quarantine_unreadable(target: Path, exc: Exception) -> Path | None:
    """把读不懂的配置另存一份，再让调用方用默认值继续。

    2026-10-30 事故：旧版程序（Config 里还没有 mouse_switch 字段）读新配置，
    `from_dict` 抛「未知的配置项」，而 `load(strict=False)` 把这个异常吞成
    `platform_defaults()`；紧接着 `ensure_device_id_persisted` 又把这份默认配置
    **写回同一个文件** —— 用户的槽位、配对 psk、device_id 全没了，日志里一个字都没有。
    所以：先备份原件，且必须留下能被看见的警告。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = target.with_name(f"{target.name}.unreadable-{stamp}")
    n = 1
    while dest.exists():  # 同一秒内多次失败也别互相覆盖
        dest = target.with_name(f"{target.name}.unreadable-{stamp}-{n}")
        n += 1
    try:
        shutil.copy2(target, dest)
    except OSError:
        LOAD_WARNINGS.append(
            f"配置文件无法解析（{exc}），且备份失败；本次以默认配置启动，"
            f"原文件仍在 {target}，请先手工修好再启动。"
        )
        return None
    LOAD_WARNINGS.append(
        f"配置文件无法解析（{exc}）；原件已备份到 {dest.name}，本次以默认配置启动，"
        "**且不会覆盖原文件**。常见原因：用旧版本程序读含新字段的配置。"
    )
    return dest


def load(path: str | os.PathLike[str] | None = None, *, strict: bool = True) -> Config:
    """读取配置；文件不存在时返回本平台的默认配置（不落盘）。

    `strict=False` 是运行期路径：
    * 本版本不认识的字段**原样保留**（`_extra`），落盘时写回，不算错；
    * 真的读不懂（类型/取值非法）才降到默认值，此时先备份原件并打上
      `_degraded`，后续 `save()` 拒绝覆盖原文件 —— 备份只是最后的兜底，
      用户不该为了找回配置去翻 `.unreadable-*` 文件。

    `strict=True` 只影响「读不懂」的处理：直接抛给调用方（`doctor` 用）。
    """
    target = Path(path).expanduser() if path else config_path()
    if not target.exists():
        return platform_defaults()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {target} ({exc})") from exc
    try:
        cfg = from_dict(raw)
    except ConfigError as exc:
        if strict:
            raise
        backup = _quarantine_unreadable(target, exc)
        cfg = platform_defaults()
        # **这份是"借来用"的默认配置**：标记成只读，`save()` 一律拒绝落盘。
        # 2026-10-30 那次只做了备份，紧接着 `ensure_device_id_persisted` 又把默认值
        # 写回同一个文件 —— 备份在、用户的配置没了。2026-11-06 又复现了一次
        # （旧包读不懂 keyboard_follow → 配对 psk / 鼠标槽位被抹）。
        cfg._unreadable = True
        cfg._unreadable_backup = str(backup) if backup else ""
        # 远端（macOS 侧）为同一个事故做的第二套标记：`_degraded` 是 dataclass 字段，
        # 语义与 `_unreadable` 一致（`save()` 都拒绝覆盖），并存互不干扰 ——
        # 合并时两条都留着，任何一边的守卫用例都还能过。
        # 设备 ID 走 sidecar 小文件：既不污染用户的配置，又保证重启后对端还认得你。
        cfg._degraded = True
        cfg.clipboard.device_id = _read_device_id_sidecar(target)
        return cfg
    if cfg._extra:
        LOAD_WARNINGS.append(
            "配置里有本版本不认识的字段（"
            + ", ".join(sorted(cfg._extra))
            + "），已原样保留、不会写掉。若这是旧版本程序在读新配置，"
            "请先升级再用它改配置。"
        )
    return cfg


def _backup_before_overwrite(target: Path) -> None:
    """覆盖前把上一份**能解析**的配置留成 `config.json.bak`。

    2026-11-06 事故：旧版程序把整份配置写成默认值，槽位 / 配对 / 设备 ID 全丢，
    一个可回滚的副本都没有。备份放在每次落盘之前 —— 旧版程序不认这个文件、
    也不会碰它，所以恰恰是它造成的覆盖能从 `.bak` 里捞回来。
    """
    if not target.exists():
        return
    try:
        json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 现文件已经坏了：别拿它盖掉上一份好备份
        return
    try:
        shutil.copy2(target, target.with_name(target.name + BACKUP_SUFFIX))
    except OSError as exc:
        logging.getLogger(__name__).warning("配置备份失败：%s", exc)


def save(cfg: Config, path: str | os.PathLike[str] | None = None) -> Path:
    """落盘。

    **命令行临时覆盖不写回文件**（2026-11-06 踩过）：`--dry-run` / `--log-level` /
    `--no-tray` 只改运行中的对象。以前它们会被下一次"界面里点个开关"顺手写进
    config.json —— 用户下次正常启动时程序还在演练模式，界面上写着「[演练] 未真正
    发送指令」，看着像坏了。`cli._load_config()` 把盘上的原值记进
    `cfg._cli_overrides`，这里按原值写回。
    """
    target = Path(path).expanduser() if path else config_path()
    # 两套「读不懂」标记（Windows 侧 `_unreadable` / macOS 侧 `_degraded`）走同一条
    # 拒绝路径 —— 消息里必须带「拒绝覆盖」，否则调用方（界面 / CLI）没法区分
    # 「这次是读不懂、不敢写」和「写失败」。
    if getattr(cfg, "_unreadable", False) or getattr(cfg, "_degraded", False):
        backup = getattr(cfg, "_unreadable_backup", "") or ""
        raise ConfigError(
            "配置文件读不懂（取值非法），本次运行拒绝覆盖它"
            + (f"，原件已备份到 {backup}" if backup else "，原件备份见同目录 config.json.unreadable-*")
            + "。请修好配置或删掉它再启动。"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.to_dict()
    # 命令行临时覆盖（--dry-run / --log-level / --no-tray）按盘上原值写回，
    # 免得「界面里点个开关」把它们顺手固化进 config.json（2026-11-06 事故）。
    for key, original in (getattr(cfg, "_cli_overrides", None) or {}).items():
        if key in data:
            data[key] = original
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    # 覆盖前先留一份可回滚的 .bak（旧版程序不认它，正好躲过它的覆盖）
    _backup_before_overwrite(target)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)   # 原子替换：中途崩溃也不会留下半截配置
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return target


def ensure_config_file(path: str | os.PathLike[str] | None = None) -> tuple[Path, bool]:
    """确保配置文件存在，返回 (路径, 是否新建)。新建时顺手把设备 ID 定下来。"""
    target = Path(path).expanduser() if path else config_path()
    if target.exists():
        return target, False
    cfg = platform_defaults()
    ensure_device_id(cfg)
    save(cfg, target)
    return target, True


def ensure_device_id_persisted(
    cfg: Config, path: str | os.PathLike[str] | None = None
) -> str:
    """保证设备 ID 已生成并**落盘**（跨重启必须不变，否则对端会把你当新设备）。"""
    target = Path(path).expanduser() if path else config_path()
    before = cfg.clipboard.device_id
    device_id = ensure_device_id(cfg)
    if before:
        return device_id
    # 两种「读不懂」的标记都要拦：`_degraded`（macOS 侧加的 dataclass 字段）
    # 和 `_unreadable`（Windows 侧加的动态属性），语义相同。
    if getattr(cfg, "_degraded", False) or getattr(cfg, "_unreadable", False):
        # 配置读不懂时整份都不回写（见 load），设备 ID 单独存一个小文件：
        # 既不污染用户的配置，又保证重启后对端还认得你。
        try:
            _device_id_sidecar(target).write_text(device_id + "\n", encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).warning("设备 ID 无法落盘：%s", exc)
        return device_id
    try:
        save(cfg, target)
    except OSError as exc:  # 配置目录只读时不该让程序起不来
        logging.getLogger(__name__).warning("设备 ID 无法落盘：%s", exc)
    return device_id
