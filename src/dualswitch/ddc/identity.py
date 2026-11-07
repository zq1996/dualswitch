"""显示器身份：厂商 + 产品号 + 型号 + 序列号 + 型号↔DDC 句柄的绑定键。

V3 的 profile 主键是 `厂商:ProductID`（如 `AOC:9987`）。这个模块只回答
「现在接的是哪块屏」，一次调用、毫秒级，**不进轮询**：

* macOS：IOKit 的 `AppleCLCD2` 节点 + `DisplayAttributes.ProductAttributes`。
  `dispN` / `dispextN` 是型号与 `DCPAVServiceProxy` 句柄的共同前缀，
  所以这里返回的 `slot` 可以直接交给 `ddc/avservice.py` 挑句柄。
  内建屏没有 `ProductName`，据此过滤，不靠 VCP 试错。
* Windows：**两级** `EnumDisplayDevicesW` 拿活动屏的 PnP 实例（适配器 → 监视器），
  再从注册表 `...\\Device Parameters\\EDID` 解 128 字节。
  只认活动实例 —— 注册表里有大量已拔掉/重复的历史实例（2026-11-07 实测 6 条）。

  ⚠️ 必须两级枚举：一级（`lpDevice=None`）即使带 `EDD_GET_DEVICE_INTERFACE_NAME`，
  返回的 `DeviceID` 也是**空串**（2026-11-07 本机实测，见 `docs/WINDOWS-VERIFY.md` §0.4）。
  早期版本只做一级枚举，于是 Windows 端永远读不到屏、profile 一直退回 default。

字段口径（Windows 端 2026-11-07 实测）：
`WmiMonitorID.ProductCodeID` 是**十六进制 ASCII 串**（`"2790"` → `0x2790`），
不能直接 `int()`；这里统一走注册表 EDID 的十进制字段，避免两端对不上。
"""

from __future__ import annotations

import logging
import plistlib
import sys
from dataclasses import dataclass

log = logging.getLogger("dualswitch.ddc.identity")

#: 身份读取失败时调用方应当退回 profile 的 default 项，绝不阻断切屏。
_BUILTIN_MFG = "00-10-fa"


@dataclass(frozen=True)
class DisplayIdentity:
    """一块物理显示器的身份。`key` 是 profile 主键。"""

    mfg: str
    product_code: int
    name: str = ""
    serial: str = ""
    year: int = 0
    week: int = 0
    slot: str = ""          # macOS: disp0 / dispext0；Windows: PnP 实例
    pnp_instance: str = ""  # Windows: DISPLAY\AOC2790\7&...
    builtin: bool = False

    def key(self) -> str:
        return f"{self.mfg}:{self.product_code}"

    def label(self) -> str:
        bits = [self.key()]
        if self.name:
            bits.append(self.name)
        if self.slot:
            bits.append(f"slot={self.slot}")
        if self.serial:
            bits.append(f"sn={self.serial}")
        return " ".join(bits)


def _clean_mfg(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in ("null", "none", _BUILTIN_MFG):
        return ""
    # EDID 的厂商号是三个大写字母；其它形态（内建屏的 00-10-fa）直接判无效
    if len(text) != 3 or not text.isalpha():
        return ""
    return text.upper()


def parse_edid(data: bytes) -> DisplayIdentity | None:
    """解析 128 字节 EDID 基础块。字段位置是 VESA 标准，两端一致。"""
    if len(data) < 128 or data[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    raw = (data[8] << 8) | data[9]
    mfg = "".join(chr(64 + ((raw >> shift) & 0x1F)) for shift in (10, 5, 0))
    mfg = _clean_mfg(mfg)
    if not mfg:
        return None
    product_code = data[10] | (data[11] << 8)
    serial = int.from_bytes(data[12:16], "little")
    name = ""
    # 四个 display descriptor 里找 0xFC（Monitor Name）
    for offset in (54, 72, 90, 108):
        block = data[offset:offset + 18]
        if len(block) == 18 and block[:5] == b"\x00\x00\x00\xfc\x00":
            name = block[5:18].split(b"\n")[0].decode("ascii", "ignore").strip()
            break
    return DisplayIdentity(
        mfg=mfg,
        product_code=product_code,
        name=name,
        serial=str(serial) if serial else "",
        year=1990 + int(data[17]),
        week=int(data[16]),
    )


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #
class _IOKit:
    """惰性加载 IOKit/CoreFoundation 的 ctypes 绑定（只在 macOS 用）。"""

    _instance: "_IOKit | None" = None

    def __init__(self) -> None:
        import ctypes

        self.ctypes = ctypes
        self.iok = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self.cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        iok, cf = self.iok, self.cf

        iok.IOServiceMatching.restype = ctypes.c_void_p
        iok.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iok.IOServiceGetMatchingServices.restype = ctypes.c_int
        iok.IOServiceGetMatchingServices.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iok.IOIteratorNext.restype = ctypes.c_uint32
        iok.IOIteratorNext.argtypes = [ctypes.c_uint32]
        iok.IOObjectRelease.restype = ctypes.c_int
        iok.IOObjectRelease.argtypes = [ctypes.c_uint32]
        iok.IORegistryEntryGetName.restype = ctypes.c_int
        iok.IORegistryEntryGetName.argtypes = [ctypes.c_uint32, ctypes.c_char_p]
        iok.IORegistryEntryGetParentEntry.restype = ctypes.c_int
        iok.IORegistryEntryGetParentEntry.argtypes = [
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iok.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
        iok.IORegistryEntryCreateCFProperty.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]

        cf.CFAllocatorGetDefault.restype = ctypes.c_void_p
        cf.CFAllocatorGetDefault.argtypes = []
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFPropertyListCreateData.restype = ctypes.c_void_p
        cf.CFPropertyListCreateData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_ulong,
            ctypes.c_void_p,
        ]
        cf.CFDataGetLength.restype = ctypes.c_long
        cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        cf.CFDataGetBytePtr.restype = ctypes.c_void_p
        cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]

    @classmethod
    def load(cls) -> "_IOKit | None":
        if cls._instance is not None:
            return cls._instance or None
        try:
            cls._instance = cls()
        except Exception as exc:  # noqa: BLE001 - 老系统 / 符号缺失就整体降级
            log.debug("IOKit 身份读取不可用：%s", exc)
            cls._instance = None  # type: ignore[assignment]
            return None
        return cls._instance

    def parent_name(self, service: int) -> str:
        ctypes = self.ctypes
        parent = ctypes.c_uint32(0)
        if self.iok.IORegistryEntryGetParentEntry(
            service, b"IOService", ctypes.byref(parent)
        ) != 0 or not parent.value:
            return ""
        try:
            buf = ctypes.create_string_buffer(256)
            if self.iok.IORegistryEntryGetName(parent, buf) != 0:
                return ""
            return buf.value.decode("utf-8", "ignore")
        finally:
            self.iok.IOObjectRelease(parent.value)

    def property_dict(self, service: int, name: str) -> dict:
        ctypes = self.ctypes
        key = self.cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)
        if not key:
            return {}
        try:
            value = self.iok.IORegistryEntryCreateCFProperty(service, key, None, 0)
            if not value:
                return {}
            try:
                data = self.cf.CFPropertyListCreateData(None, value, 100, 0, None)
                if not data:
                    return {}
                try:
                    size = self.cf.CFDataGetLength(data)
                    ptr = self.cf.CFDataGetBytePtr(data)
                    if not ptr or size <= 0:
                        return {}
                    parsed = plistlib.loads(ctypes.string_at(ptr, size))
                    return parsed if isinstance(parsed, dict) else {}
                finally:
                    self.cf.CFRelease(data)
            finally:
                self.cf.CFRelease(value)
        finally:
            self.cf.CFRelease(key)


def _read_macos() -> list[DisplayIdentity]:
    lib = _IOKit.load()
    if lib is None:
        return []
    ctypes = lib.ctypes
    matching = lib.iok.IOServiceMatching(b"AppleCLCD2")
    iterator = ctypes.c_uint32(0)
    if lib.iok.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator)) != 0:
        return []

    found: list[DisplayIdentity] = []
    seen: set[str] = set()
    while True:
        service = lib.iok.IOIteratorNext(iterator)
        if not service:
            break
        try:
            slot = lib.parent_name(service)
            if not slot.startswith(("disp", "dispext")) or slot in seen:
                continue
            seen.add(slot)
            attrs = lib.property_dict(service, "DisplayAttributes")
            product = attrs.get("ProductAttributes") if isinstance(attrs, dict) else None
            if not isinstance(product, dict):
                continue
            name = str(product.get("ProductName") or "").strip()
            mfg = _clean_mfg(product.get("ManufacturerID"))
            try:
                product_code = int(product.get("ProductID") or 0)
            except (TypeError, ValueError):
                continue
            # 内建屏没有 ProductName —— 这是最干净的过滤条件
            if not name or not mfg or not product_code:
                continue
            serial = str(
                product.get("AlphanumericSerialNumber")
                or product.get("SerialNumber")
                or ""
            ).strip()
            found.append(
                DisplayIdentity(
                    mfg=mfg,
                    product_code=product_code,
                    name=name,
                    serial=serial,
                    year=int(product.get("YearOfManufacture") or 0),
                    week=int(product.get("WeekOfManufacture") or 0),
                    slot=slot,
                )
            )
        finally:
            lib.iok.IOObjectRelease(service)
    return found


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
#: `\\?\DISPLAY#AOC2790#7&3ae6a500&1&UID524#{guid}` 的前缀
_DEVICE_INTERFACE_PREFIX = "\\\\?\\"


def pnp_from_device_id(raw: str) -> str:
    """设备接口名 → PnP 实例 ID（`DISPLAY\\AOC2790\\7&3ae6a500&1&UID524`）。

    这个字符串与 WMI `InstanceName`、注册表 `Enum\\DISPLAY` 下的键名**逐字相同**，
    也是「型号 ↔ DDC 句柄」的绑定键。不是显示器接口就返回空串。

    注意：不要用 `lstrip("\\\\?\\\\")` —— 那是按**字符集**剥，不是按前缀剥。
    """
    text = str(raw or "").strip()
    if text.startswith(_DEVICE_INTERFACE_PREFIX):
        text = text[len(_DEVICE_INTERFACE_PREFIX):]
    parts = text.split("#")
    if len(parts) < 3 or parts[0].upper() != "DISPLAY":
        return ""
    if not parts[1] or not parts[2]:
        return ""
    return "DISPLAY\\" + parts[1] + "\\" + parts[2]


def _read_windows() -> list[DisplayIdentity]:
    import ctypes
    import winreg
    from ctypes import wintypes

    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplayDevicesW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL

    def _enum(parent: str | None, flags: int) -> list[_DISPLAY_DEVICEW]:
        items: list[_DISPLAY_DEVICEW] = []
        index = 0
        while True:
            dev = _DISPLAY_DEVICEW()
            dev.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(parent, index, ctypes.byref(dev), flags):
                break
            items.append(dev)
            index += 1
        return items

    # 一级：适配器（`\\\\.\\DISPLAY1` …）。这一级的 DeviceID 是 PCI 路径或空串，
    # 拿不到显示器接口，必须再往下枚举一层。
    adapters = [dev.DeviceName for dev in _enum(None, 0)]

    found: list[DisplayIdentity] = []
    seen: set[str] = set()
    for adapter in adapters:
        # 二级：该适配器下的监视器，EDD_GET_DEVICE_INTERFACE_NAME = 0x1
        for mon in _enum(adapter, 0x1):
            if mon.StateFlags & 0x8:  # DISPLAY_DEVICE_MIRRORING_DRIVER
                continue
            pnp = pnp_from_device_id(mon.DeviceID)
            if not pnp or pnp in seen:
                continue
            seen.add(pnp)
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    f"SYSTEM\\CurrentControlSet\\Enum\\{pnp}\\Device Parameters",
                )
                try:
                    edid, _ = winreg.QueryValueEx(key, "EDID")
                finally:
                    winreg.CloseKey(key)
            except OSError:
                continue
            ident = parse_edid(bytes(edid))
            if ident is None:
                continue
            found.append(
                DisplayIdentity(
                    mfg=ident.mfg,
                    product_code=ident.product_code,
                    name=ident.name or (mon.DeviceString or "").strip(),
                    serial=ident.serial,
                    year=ident.year,
                    week=ident.week,
                    slot=pnp,
                    pnp_instance=pnp,
                )
            )
    return found


# --------------------------------------------------------------------------- #
# 公共入口
# --------------------------------------------------------------------------- #
def read_displays() -> list[DisplayIdentity]:
    """返回所有**外接**显示器身份；读不到返回空列表（绝不抛给调用方）。"""
    try:
        if sys.platform == "darwin":
            return _read_macos()
        if sys.platform == "win32":
            return _read_windows()
    except Exception as exc:  # noqa: BLE001 - 身份识别失败只降级，不阻断
        log.debug("读取显示器身份失败：%s", exc, exc_info=True)
    return []


def read_current() -> DisplayIdentity | None:
    """当前优先使用的外接屏。多块屏时：先 `dispext0`，否则第一块。"""
    displays = read_displays()
    if not displays:
        return None
    for item in displays:
        if item.slot.startswith("dispext0"):
            return item
    return displays[0]
