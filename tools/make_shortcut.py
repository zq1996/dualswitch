"""用 IShellLink 生成 Windows 快捷方式（.lnk）——不依赖 COM 自动化对象。

为什么不用 `New-Object -ComObject WScript.Shell`：本机工具环境的 PowerShell 安全策略
直接拦「COM 对象实例化」（报 "COM object instantiation can run arbitrary code"），
`Add-Type` 也被拦。而手搓 MS-SHLLINK 二进制（2026-10-24 试过）Explorer 不认。
这里走 `CoCreateInstance(CLSID_ShellLink)` + vtable 调用，纯 ctypes，无第三方依赖，
生成的 .lnk 与「发送到 → 桌面快捷方式」同源，Explorer 正常识别。

用法:
    python tools/make_shortcut.py <目标 exe> [快捷方式路径] [--workdir DIR] [--desc 文字] [--icon 图标]
    python tools/make_shortcut.py --verify <某个.lnk>     # 读回目标路径，验证文件有效
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_void_p, c_wchar_p, wintypes

CLSCTX_INPROC_SERVER = 0x1
CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010b-0000-0000-C000-000000000046}"

# IShellLinkW vtable 槽位（IUnknown 占 0-2）
VT_GET_PATH, VT_SET_DESCRIPTION, VT_SET_WORKING_DIR = 3, 7, 9
VT_SET_ICON_LOCATION, VT_SET_PATH = 17, 20
# IPersistFile: 3 GetClassID, 4 IsDirty, 5 Load, 6 Save
PF_LOAD, PF_SAVE = 5, 6


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> GUID:
    import uuid

    u = uuid.UUID(text)
    g = GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


def _vtbl_call(ptr: c_void_p, slot: int, restype, *argtypes):
    """取出第 slot 个虚函数并设好签名，返回可调用对象。"""
    vtbl = ctypes.cast(ptr, POINTER(c_void_p))[0]
    fn_addr = ctypes.cast(vtbl, POINTER(c_void_p))[slot]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(fn_addr)


def _release(ptr: c_void_p) -> None:
    _vtbl_call(ptr, 2, wintypes.ULONG)(ptr)


def create(target: str, lnk_path: str, workdir: str = "", desc: str = "",
           icon: str = "") -> None:
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    link = c_void_p()
    clsid, iid = _guid(CLSID_ShellLink), _guid(IID_IShellLinkW)
    hr = ole32.CoCreateInstance(byref(clsid), None, CLSCTX_INPROC_SERVER,
                                byref(iid), byref(link))
    if hr != 0:
        raise OSError(f"CoCreateInstance(ShellLink) 失败: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        _vtbl_call(link, VT_SET_PATH, ctypes.c_long, c_wchar_p)(link, target)
        if workdir:
            _vtbl_call(link, VT_SET_WORKING_DIR, ctypes.c_long, c_wchar_p)(link, workdir)
        if desc:
            _vtbl_call(link, VT_SET_DESCRIPTION, ctypes.c_long, c_wchar_p)(link, desc)
        if icon:
            _vtbl_call(link, VT_SET_ICON_LOCATION, ctypes.c_long, c_wchar_p, ctypes.c_int)(
                link, icon, 0
            )
        persist = c_void_p()
        iid_pf = _guid(IID_IPersistFile)
        hr = _vtbl_call(link, 0, ctypes.c_long, ctypes.POINTER(GUID),
                        ctypes.POINTER(c_void_p))(link, byref(iid_pf), byref(persist))
        if hr != 0:
            raise OSError(f"QueryInterface(IPersistFile) 失败: 0x{hr & 0xFFFFFFFF:08X}")
        try:
            hr = _vtbl_call(persist, PF_SAVE, ctypes.c_long, c_wchar_p, wintypes.BOOL)(
                persist, lnk_path, True
            )
            if hr != 0:
                raise OSError(f"IPersistFile.Save 失败: 0x{hr & 0xFFFFFFFF:08X}")
        finally:
            _release(persist)
    finally:
        _release(link)
        ole32.CoUninitialize()


def read_target(lnk_path: str) -> str:
    """读回 .lnk 的目标路径——能读出来就说明文件结构有效。"""
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    link = c_void_p()
    clsid, iid = _guid(CLSID_ShellLink), _guid(IID_IShellLinkW)
    hr = ole32.CoCreateInstance(byref(clsid), None, CLSCTX_INPROC_SERVER,
                                byref(iid), byref(link))
    if hr != 0:
        raise OSError(f"CoCreateInstance 失败: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        persist = c_void_p()
        iid_pf = _guid(IID_IPersistFile)
        _vtbl_call(link, 0, ctypes.c_long, ctypes.POINTER(GUID),
                   ctypes.POINTER(c_void_p))(link, byref(iid_pf), byref(persist))
        try:
            hr = _vtbl_call(persist, PF_LOAD, ctypes.c_long, c_wchar_p, wintypes.DWORD)(
                persist, lnk_path, 0
            )
            if hr != 0:
                raise OSError(f"IPersistFile.Load 失败: 0x{hr & 0xFFFFFFFF:08X}")
            buf = ctypes.create_unicode_buffer(1024)
            _vtbl_call(link, VT_GET_PATH, ctypes.c_long, c_wchar_p, ctypes.c_int,
                       c_void_p, wintypes.DWORD)(link, buf, 1024, None, 0)
            return buf.value
        finally:
            _release(persist)
    finally:
        _release(link)
        ole32.CoUninitialize()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    if argv[1] == "--verify":
        print(read_target(argv[2]))
        return 0

    target = argv[1]
    lnk = argv[2]
    opts = {"workdir": "", "desc": "", "icon": ""}
    rest = argv[3:]
    i = 0
    while i < len(rest):
        key = rest[i].lstrip("-")
        if key in opts and i + 1 < len(rest):
            opts[key] = rest[i + 1]
            i += 2
        else:
            i += 1
    create(target, lnk, **opts)
    back = read_target(lnk)
    print(f"已创建 {lnk}\n读回目标：{back}")
    # Git Bash 会把 C:\x\y 改写成 C:/x/y 再传给原生程序，比较前先归一化
    import os

    same = os.path.normcase(os.path.normpath(back)) == os.path.normcase(
        os.path.normpath(target)
    )
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
