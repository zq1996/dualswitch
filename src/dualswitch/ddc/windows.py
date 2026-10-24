"""Windows DDC/CI 后端：ctypes 直调 dxva2.dll。

设计要点（对应需求文档 3.3 / 3.4 / 3.5）：
* SetVCPFeature 盲发切换；VCP 0x60 读数在 Windows 上真实可用
  （SCREEN-OWNERSHIP.md §1.2 实测 17↔18 严格跟随物理切换），归属判定只认读数。
* 每次调用重新枚举物理显示器并取句柄，用完立即 DestroyPhysicalMonitor。
  原因：切走之后显示器会从系统消失，缓存句柄必然失效；重新枚举只需几毫秒。
* 所有 ctypes 调用都显式声明 argtypes，避免 64 位下句柄被截断。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

from .base import BaseBackend, DDCError, MonitorInfo

if sys.platform != "win32":  # pragma: no cover - 平台守卫
    raise ImportError("dualswitch.ddc.windows 只能在 Windows 上导入")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)


class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [
        ("hPhysicalMonitor", wintypes.HANDLE),
        ("szPhysicalMonitorDescription", wintypes.WCHAR * 128),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)

_user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    _MONITORENUMPROC,
    wintypes.LPARAM,
]
_user32.EnumDisplayMonitors.restype = wintypes.BOOL
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_user32.EnumDisplayDevicesW.restype = wintypes.BOOL

_dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, ctypes.POINTER(wintypes.DWORD)]
_dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
_dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [
    wintypes.HMONITOR,
    wintypes.DWORD,
    ctypes.POINTER(_PHYSICAL_MONITOR),
]
_dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
_dxva2.DestroyPhysicalMonitor.argtypes = [wintypes.HANDLE]
_dxva2.DestroyPhysicalMonitor.restype = wintypes.BOOL
_dxva2.SetVCPFeature.argtypes = [wintypes.HANDLE, wintypes.BYTE, wintypes.DWORD]
_dxva2.SetVCPFeature.restype = wintypes.BOOL
_dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
    wintypes.HANDLE,
    wintypes.BYTE,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
_dxva2.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL


@dataclass
class _Handle:
    info: MonitorInfo
    handle: int


class WindowsBackend(BaseBackend):
    name = "dxva2"

    #: VCP 0x60 读数在 Windows 上可用（SCREEN-OWNERSHIP.md §1.2 实测
    #: 17↔18 跟随物理切换），归属只认它；EnumDisplayMonitors 在画面切走后
    #: 照样枚举得到这块屏（幻影），不能用来判「屏在不在本机」。
    supports_ddc_detect = True

    # ---------------------------------------------------------------- 枚举
    def _enum_hmonitors(self) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []

        def _cb(hmon, hdc, lprc, lparam):  # noqa: ANN001
            info = _MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                found.append((int(hmon), info.szDevice))
            return True

        callback = _MONITORENUMPROC(_cb)
        if not _user32.EnumDisplayMonitors(None, None, callback, 0):
            raise DDCError(f"EnumDisplayMonitors 失败 (WinError {ctypes.get_last_error()})")
        return found

    @staticmethod
    def _device_id(device_name: str) -> str:
        dev = _DISPLAY_DEVICEW()
        dev.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if _user32.EnumDisplayDevicesW(device_name, 0, ctypes.byref(dev), 0):
            return dev.DeviceID or ""
        return ""

    def _open(self) -> list[_Handle]:
        """枚举所有物理显示器并取得句柄；调用方负责释放。"""
        opened: list[_Handle] = []
        index = 0
        for hmon, device_name in self._enum_hmonitors():
            count = wintypes.DWORD(0)
            if not _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
                continue
            if count.value == 0:
                continue
            arr = (_PHYSICAL_MONITOR * count.value)()
            if not _dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
                continue
            for item in arr:
                info = MonitorInfo(
                    index=index,
                    description=item.szPhysicalMonitorDescription or "",
                    device_name=device_name,
                    device_id=self._device_id(device_name),
                )
                opened.append(_Handle(info=info, handle=int(item.hPhysicalMonitor or 0)))
                index += 1
        return opened

    @staticmethod
    def _release(handles: list[_Handle]) -> None:
        for h in handles:
            if h.handle:
                try:
                    _dxva2.DestroyPhysicalMonitor(wintypes.HANDLE(h.handle))
                except Exception:  # pragma: no cover - 释放失败不影响主流程
                    pass

    def list_monitors(self) -> list[MonitorInfo]:
        handles = self._open()
        try:
            return [h.info for h in handles]
        finally:
            self._release(handles)

    def _acquire_one(self) -> _Handle:
        handles = self._open()
        if not handles:
            raise DDCError("未发现物理显示器（可能显示器已切到另一台主机）")
        chosen_info = self.pick([h.info for h in handles])
        chosen = next(h for h in handles if h.info.index == chosen_info.index)
        # 释放其余句柄，只保留选中的
        self._release([h for h in handles if h is not chosen])
        return chosen

    # ---------------------------------------------------------------- Set
    def set_input(self, value: int) -> None:
        value = int(value)
        if not 0 <= value <= 0xFF:
            raise ValueError(f"输入源取值越界: {value}")
        last_error = ""
        for attempt in range(2):  # 显示器刚唤醒时首次调用可能失败，重试一次
            handle = self._acquire_one()
            try:
                ok = _dxva2.SetVCPFeature(
                    wintypes.HANDLE(handle.handle), wintypes.BYTE(self.vcp_code), wintypes.DWORD(value)
                )
                if ok:
                    return
                last_error = f"SetVCPFeature 返回 0 (WinError {ctypes.get_last_error()})"
            finally:
                self._release([handle])
            if attempt == 0:
                time.sleep(0.08)
        raise DDCError(f"{last_error}；显示器={handle.info.label()}")

    # ---------------------------------------------------------------- Get
    def get_input(self) -> int:
        handle = self._acquire_one()
        try:
            pvct = wintypes.DWORD(0)
            cur = wintypes.DWORD(0)
            mx = wintypes.DWORD(0)
            ok = _dxva2.GetVCPFeatureAndVCPFeatureReply(
                wintypes.HANDLE(handle.handle),
                wintypes.BYTE(self.vcp_code),
                ctypes.byref(pvct),
                ctypes.byref(cur),
                ctypes.byref(mx),
            )
            if not ok:
                raise DDCError(f"GetVCPFeature 返回 0 (WinError {ctypes.get_last_error()})")
            if mx.value == 0:
                raise DDCError("GetVCPFeature 返回 max=0，读数不可信")
            return int(cur.value)
        finally:
            self._release([handle])

    def probe_input(self) -> tuple[int, int]:
        """返回 (当前值, max)。仅用于诊断，不作为切换依据。"""
        handle = self._acquire_one()
        try:
            pvct = wintypes.DWORD(0)
            cur = wintypes.DWORD(0)
            mx = wintypes.DWORD(0)
            ok = _dxva2.GetVCPFeatureAndVCPFeatureReply(
                wintypes.HANDLE(handle.handle),
                wintypes.BYTE(self.vcp_code),
                ctypes.byref(pvct),
                ctypes.byref(cur),
                ctypes.byref(mx),
            )
            if not ok:
                raise DDCError(f"GetVCPFeature 返回 0 (WinError {ctypes.get_last_error()})")
            return int(cur.value), int(mx.value)
        finally:
            self._release([handle])
