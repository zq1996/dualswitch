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
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "dualswitch"
CONFIG_FILENAME = "config.json"


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
    """Set 之后的异步回读（非阻塞）。Q2790PC 的 HDMI 通道读数不可靠，故仅用于记录。"""

    enabled: bool = True
    delay_ms: int = 300          # 发出 Set 后首次回读的等待时间（文档要求 250–400ms）
    timeout_ms: int = 1000       # 回读总预算，超时即放弃，不影响主流程
    interval_ms: int = 200       # 两次回读之间的间隔
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
        return asdict(self)


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


def _merge_readback(raw: dict[str, Any], base: ReadbackConfig) -> ReadbackConfig:
    if not isinstance(raw, dict):
        raise ConfigError("readback 必须是对象")
    cfg = copy.deepcopy(base)
    for key, value in raw.items():
        if not hasattr(cfg, key):
            raise ConfigError(f"未知的 readback 字段: {key}")
        setattr(cfg, key, _coerce_bool(value) if isinstance(getattr(cfg, key), bool) else int(value))
    return cfg


def _merge_clipboard(raw: dict[str, Any], base: ClipboardConfig) -> ClipboardConfig:
    if not isinstance(raw, dict):
        raise ConfigError("clipboard 必须是对象")
    cfg = copy.deepcopy(base)
    for key, value in raw.items():
        if not hasattr(cfg, key):
            raise ConfigError(f"未知的 clipboard 字段: {key}")
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


def from_dict(raw: dict[str, Any], base: Config | None = None) -> Config:
    """把 JSON 字典合并到默认配置上，并做严格校验。"""
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是 JSON 对象")
    cfg = copy.deepcopy(base or DEFAULTS)

    for key, value in raw.items():
        if key == "readback":
            cfg.readback = _merge_readback(value, cfg.readback)
            continue
        if key == "clipboard":
            cfg.clipboard = _merge_clipboard(value, cfg.clipboard)
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
        if not hasattr(cfg, key):
            raise ConfigError(f"未知的配置项: {key}")
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
def load(path: str | os.PathLike[str] | None = None, *, strict: bool = True) -> Config:
    """读取配置；文件不存在时返回本平台的默认配置（不落盘）。"""
    target = Path(path).expanduser() if path else config_path()
    if not target.exists():
        return platform_defaults()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON: {target} ({exc})") from exc
    try:
        return from_dict(raw)
    except ConfigError:
        if strict:
            raise
        return platform_defaults()


def save(cfg: Config, path: str | os.PathLike[str] | None = None) -> Path:
    target = Path(path).expanduser() if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    before = cfg.clipboard.device_id
    device_id = ensure_device_id(cfg)
    if not before:
        try:
            save(cfg, path)
        except OSError as exc:  # 配置目录只读时不该让程序起不来
            logging.getLogger(__name__).warning("设备 ID 无法落盘：%s", exc)
    return device_id
