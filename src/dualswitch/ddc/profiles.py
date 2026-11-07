"""显示器 profile：端口语义、码表、内置表、匹配与降级。

结构（详见 `docs/PROFILE-AND-ROLES-DESIGN.md`）：

    profile.ports:  port -> VCP 数值      （随屏变，标定得来）
    config.wiring:  role -> port          （随人变，手选）

`port` 用语义名，不再用「windows/mac」：`hdmi1 / hdmi2 / dp / usb_c / dvi / vga`。
这样双 Windows 也不会把「对端」说成 Mac，换屏时码表也只需要换 profile。

**降级原则：不认识这块屏就用 `default`（由旧 `sources` 迁移而来），绝不阻断切屏。**
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

DEFAULT_KEY = "default"

#: 展示顺序（也是切换页端口行的顺序）。新增端口只往这里加，别改已有名字。
PORT_ORDER = ("hdmi1", "hdmi2", "dp", "usb_c", "dvi", "vga")

PORT_LABELS = {
    "hdmi1": "HDMI 1",
    "hdmi2": "HDMI 2",
    "dp": "DisplayPort",
    "usb_c": "USB-C",
    "dvi": "DVI",
    "vga": "VGA",
}

_ALIASES = {
    "hdmi": "hdmi1",
    "hdmi-1": "hdmi1",
    "hdmi1": "hdmi1",
    "hdmi-2": "hdmi2",
    "hdmi2": "hdmi2",
    "dp": "dp",
    "displayport": "dp",
    "display-port": "dp",
    "usb-c": "usb_c",
    "usbc": "usb_c",
    "usb_c": "usb_c",
    "type-c": "usb_c",
    "typec": "usb_c",
    "dvi": "dvi",
    "vga": "vga",
}

ROLES = ("local", "peer")

#: 切换页每行高度（端口行 2/3/4 行时窗口按此增高；设计稿 §6.0 实测值）
ROW_H = 58


def normalize_port(name: object) -> str:
    """把用户输入的各种写法收敛成标准端口名。未知返回空串。"""
    key = str(name or "").strip().lower()
    return _ALIASES.get(key, "")


def port_label(name: str) -> str:
    return PORT_LABELS.get(normalize_port(name), str(name or ""))


def valid_port(name: object) -> bool:
    return bool(normalize_port(name))


#: 本机平台 → 旧语义下默认接的口。只在「没有 wiring」时兜底。
def _legacy_port(host: str) -> str:
    return "hdmi1" if host == "windows" else "usb_c"


def other_host(host: str) -> str:
    return "mac" if host == "windows" else "windows"


@dataclass
class MonitorProfile:
    key: str = DEFAULT_KEY
    name: str = ""
    vcp: int = 0x60
    ports: dict[str, int] = field(default_factory=dict)
    readback: str = "full"  # full / unreliable / none
    serial: str = ""
    year: int = 0
    week: int = 0
    calibrated_at: str = ""
    notes: str = ""
    builtin: bool = False

    def ordered_ports(self) -> list[str]:
        known = [p for p in PORT_ORDER if p in self.ports]
        extra = [p for p in self.ports if p not in PORT_ORDER]
        return known + sorted(extra)

    def value(self, port: str) -> int | None:
        port = normalize_port(port)
        if not port or port not in self.ports:
            return None
        return int(self.ports[port])

    def items(self) -> list[tuple[str, int]]:
        return [(p, int(self.ports[p])) for p in self.ordered_ports()]

    def is_calibrated(self) -> bool:
        return self.key != DEFAULT_KEY and bool(self.ports)


#: 内置表：自己实测过的屏。用户表（`config.monitors`）优先于这里。
#: 数据来源见 `docs/MONITOR-PROFILE.md` §5 与 `docs/WINDOWS-VERIFY.md` §0.2。
BUILTIN_PROFILES: dict[str, MonitorProfile] = {
    "AOC:9987": MonitorProfile(
        key="AOC:9987",
        name="U27N3G6R4B",
        ports={"hdmi1": 17, "hdmi2": 17, "dp": 15, "usb_c": 100},
        readback="unreliable",
        notes="max 恒报 14 不可用于校验；macOS 读成功率约 50%，切走后句柄会返陈旧值",
        builtin=True,
    ),
    "AOC:10128": MonitorProfile(
        key="AOC:10128",
        name="Q2790R3",
        # 用户 2026-11-07 确认：这块屏只有 3 个输入口（HDMI / DP / USB-C）。
        # HDMI=17、USB-C=18 是实测；DP=15 是 MCCS 标准值（尚未逐口标定，P2 会覆盖）。
        ports={"hdmi1": 17, "dp": 15, "usb_c": 18},
        readback="unreliable",
        notes="3 口屏：HDMI=17、DP=15（标准值，待标定）、USB-C=18",
        builtin=True,
    ),
}


def default_profile(sources: dict[str, int], local_host: str) -> MonitorProfile:
    """由旧 `sources{windows,mac}` 造一个 default profile。

    旧结构里 Windows=HDMI、Mac=USB-C 是写死的，所以这里能把两个值无损搬进
    语义端口；换屏后如果命中内置表，会改用内置表的值。
    """
    peer = other_host(local_host)
    ports = {
        _legacy_port(local_host): int(sources.get(local_host, 17)),
        _legacy_port(peer): int(sources.get(peer, 18)),
    }
    return MonitorProfile(
        key=DEFAULT_KEY,
        name="默认配置（未识别的屏）",
        ports=ports,
        readback="unreliable",
        notes="由旧 sources 迁移而来；识别到具体型号后会自动改用对应 profile",
    )


def default_wiring(local_host: str) -> dict[str, str]:
    peer = other_host(local_host)
    return {"local": _legacy_port(local_host), "peer": _legacy_port(peer)}


# --------------------------------------------------------------------------- #
# 解析 / 合并
# --------------------------------------------------------------------------- #
_FIELDS = {
    "key",
    "name",
    "vcp",
    "ports",
    "readback",
    "serial",
    "year",
    "week",
    "calibrated_at",
    "notes",
    "builtin",
}


def profile_from_dict(key: str, raw: Any) -> tuple[MonitorProfile, dict[str, Any]]:
    """解析一个 profile；返回 (profile, 本版本不认识的子字段)。

    未知子字段交给 `config._extra` 原样保存 —— 与 2026-11-06 的字段保留规则一致。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"profile {key!r} 必须是对象")
    extra: dict[str, Any] = {}
    values: dict[str, Any] = {}
    for name, value in raw.items():
        if name not in _FIELDS:
            extra[name] = value
            continue
        values[name] = value

    ports: dict[str, int] = {}
    raw_ports = values.get("ports", {})
    if not isinstance(raw_ports, dict):
        raise ValueError(f"profile {key!r} 的 ports 必须是对象")
    for name, value in raw_ports.items():
        port = normalize_port(name)
        if not port:
            raise ValueError(f"profile {key!r} 里有未知端口: {name!r}")
        code = int(value)
        if not 0 <= code <= 0xFF:
            raise ValueError(f"profile {key!r} 的 {port} 必须在 0..255 之间")
        ports[port] = code

    readback = str(values.get("readback", "full") or "full")
    if readback not in ("full", "unreliable", "none"):
        raise ValueError(f"profile {key!r} 的 readback 只能是 full/unreliable/none")

    vcp = int(values.get("vcp", 0x60))
    if not 0 <= vcp <= 0xFF:
        raise ValueError(f"profile {key!r} 的 vcp 必须在 0..255 之间")

    profile = MonitorProfile(
        key=str(values.get("key", key) or key),
        name=str(values.get("name", "") or ""),
        vcp=vcp,
        ports=ports,
        readback=readback,
        serial=str(values.get("serial", "") or ""),
        year=int(values.get("year", 0) or 0),
        week=int(values.get("week", 0) or 0),
        calibrated_at=str(values.get("calibrated_at", "") or ""),
        notes=str(values.get("notes", "") or ""),
        builtin=bool(values.get("builtin", False)),
    )
    return profile, extra


def resolve_active(cfg, identity=None) -> MonitorProfile:  # noqa: ANN001
    """给配置挂上「当前屏」的 profile，并修好 wiring 里失效的端口。

    选择顺序：用户表（`config.monitors`）> 内置表 > default。
    识别不到屏 / 屏没标定过都不影响切屏，只是会走 default。
    """
    key = ""
    if identity is not None:
        try:
            key = identity.key()
        except Exception:  # noqa: BLE001 - 身份对象异常按「未识别」处理
            key = ""

    profile: MonitorProfile | None = None
    if key and key in cfg.monitors:
        profile = copy.deepcopy(cfg.monitors[key])
    elif key and key in BUILTIN_PROFILES:
        profile = copy.deepcopy(BUILTIN_PROFILES[key])
    elif DEFAULT_KEY in cfg.monitors:
        profile = copy.deepcopy(cfg.monitors[DEFAULT_KEY])
    else:
        profile = default_profile(cfg.sources, cfg.effective_host())

    if identity is not None:
        if not profile.name:
            profile.name = str(getattr(identity, "name", "") or "")
        if not profile.serial:
            profile.serial = str(getattr(identity, "serial", "") or "")
        if not profile.year:
            profile.year = int(getattr(identity, "year", 0) or 0)
        if not profile.week:
            profile.week = int(getattr(identity, "week", 0) or 0)

    cfg._active_profile = profile
    cfg._active_identity = identity
    cfg.repair_wiring()
    cfg.sync_sources()
    return profile
