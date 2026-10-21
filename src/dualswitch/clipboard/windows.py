"""Windows 剪贴板后端：ctypes 直调 user32/kernel32，**不依赖 Qt / Pillow**。

契约见 `clipboard/base.py`（冻结），平台差异只允许出现在这个文件里。

三条硬语义（契约第 3.2 节）：

1. `poll()` 只在内容**真的变了**时返回 `ClipItem`；同一内容连续调用返回 `None`
2. `write()` 之后由它引起的那次变更，`poll()` 必须吞掉（环抑制第一道闸）
3. 图片一律 PNG：剪贴板里是 `CF_DIB` 就在这里转，不把 DIB 往上层传

## 「自写回弹只吞一次」

写完之后的**第一次** `poll()` 返回 `None`，第二次开始正常返回刚写进去的内容。
这不是随手写的，是被 `tests/test_clipboard_contract.py` 的两条用例同时钉死的：

- `test_write_then_poll_swallows_echo`：写完立刻 poll 必须是 `None`
- `test_text_roundtrip_unicode`：写完再读回来必须能读到（它先 poll 一次，读不到就
  `sleep(50ms)` 再读一次 —— 第二次必须成功）

真正防止两台机器互相刷屏的是引擎层的 digest 比对（契约 4.5 节第二层），
后端这一层只负责"别把自己刚写的东西当成用户的新复制"。

## Windows 侧三个未知数的实测结论（契约第 6 节 M1 要求）

| # | 问题 | 结论 |
|---|---|---|
| 1 | 资源管理器复制文件时有没有 `CF_HDROP` | **有**，而且**没有**文字/位图可依赖（`CF_UNICODETEXT` 只是文件名）。所以文件记录必须从 `CF_HDROP` 解析路径，见下节 |
| 2 | 图片是 `CF_DIB` 还是 `CF_PNG` | 两种都有：截图工具（Win+Shift+S）给 **`CF_DIB` 32bpp**，浏览器/部分工具额外给注册格式 `PNG`。**优先取 `PNG`（零转换、无损），没有才做 DIB→PNG** |
| 3 | `GetClipboardSequenceNumber` 何时稳定 | 序列号确实**先于数据**变化：读完是空的时候 `sleep(10ms)` 重读一次；仍为空就记下这个序列号不再重试（内容真落地时序列号还会再变一次，不会漏） |

## 文件记录（`CF_HDROP`，2026-10-21 补齐）

资源管理器里 Ctrl+C 一个文件，剪贴板里是 `DROPFILES` 结构 + 双 NUL 结尾的路径列表
（`fWide=1` 时是 UTF-16）。这里把它转成 `make_file()` 的 `ClipItem`：

- **路径优先于位图**：资源管理器复制图片文件时也可能顺手给一份图标/缩略图位图，
  直接用会得到一张「文件图标」而不是图片本身。所以先看 `CF_HDROP`，有文件就按文件记。
- **图片文件抽缩略图**：读文件 → 缩放到最长边 ≤1600px → 重编码 PNG。这样列表里能看到
  内容、预览窗能出图（`record.png is not None` 才给「用默认图片查看器打开」）。
  解码器按 PySide6 → Pillow 顺序找，都没有就只记文件名（文件卡仍然成立）。
- **其它文件不读内容**：只要文件名 + 所在目录 + 字节数。
- **多选只记第一个**：`ClipItem` 是一条内容一个路径（契约冻结，mac 端对多选也是按文本处理），
  这里取第一个并记一条 info，总比整条丢掉强。
- **复制回剪贴板**：`write()` 见到 `item.path` 会同时写 `CF_HDROP`（文件本身）和
  `CF_UNICODETEXT`（文件名）—— 粘到资源管理器里是文件、粘到输入框里是名字，和资源管理器一致。
- **文件不跨端**：`path` 不上网（协议第 274 行明确不传），引擎见到 `path` 且没有图片就只在本机留一条。

## 几个实现上的取舍

- **不做延迟渲染**（`SetClipboardData` 传 NULL 再响应 `WM_RENDERFORMAT`）：那要求有一个
  能收窗口消息的窗口和消息循环，本后端是纯 ctypes、可能跑在任意线程里。立即渲染更简单，
  代价是几 MB 的图会多占一份内存（复制的那一刻，可以接受）。
- **`CF_DIB` 的 alpha**：`BI_RGB` 的 32bpp 第 4 个字节在 Windows 上语义是"保留"，
  多数应用忽略它 → 透明区域会变黑。所以透明图靠 `PNG` 格式保真，`CF_DIB` 只服务老应用；
  截图（alpha 全 255）两边都对。
- **PNG → DIB 需要解码器**：4K 截图 33MB 原始像素，纯 Python 逐字节反滤波要十几秒，
  会把同步线程钉死。所以优先用 PySide6 / Pillow 的 C 实现解码；两者都没有时**只写 PNG 格式**
  （现代应用都认），并记一条 info。反过来 DIB → PNG 是纯 Python 但全程走切片操作
  （`bytes[2::3]` 这类），4K 图也在百毫秒级，不需要任何依赖。
"""

from __future__ import annotations

import ctypes
import logging
import os
import struct
import sys
import threading
import time
import zlib
from ctypes import wintypes
from dataclasses import dataclass, replace

from .base import (
    MAX_IMAGE_BYTES,
    SKIP_MARKERS_WINDOWS,
    ClipItem,
    ClipboardError,
    ImageData,
    make_file,
    make_image,
    make_text,
)

log = logging.getLogger(__name__)

if sys.platform != "win32":  # pragma: no cover - 只有在错误的平台上被导入才会走到
    # `base.create_backend()` 会把 ImportError 当成「后端不可用」降级成 NullBackend
    raise ImportError("clipboard/windows.py 只能在 Windows 上使用")

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
CF_TEXT = 1
CF_BITMAP = 2
CF_DIB = 8
CF_UNICODETEXT = 13
CF_HDROP = 15
CF_DIBV5 = 17

GMEM_MOVEABLE = 0x0002
BI_RGB = 0
BI_BITFIELDS = 3
BI_PNG = 5

#: 注册格式名（`RegisterClipboardFormatW` 的字符串）
FMT_PNG = "PNG"
FMT_HTML = "HTML Format"

_FORMAT_NAMES = {
    1: "CF_TEXT",
    2: "CF_BITMAP",
    3: "CF_METAFILEPICT",
    4: "CF_SYLK",
    5: "CF_DIF",
    6: "CF_TIFF",
    7: "CF_OEMTEXT",
    8: "CF_DIB",
    9: "CF_PALETTE",
    10: "CF_PENDATA",
    11: "CF_RIFF",
    12: "CF_WAVE",
    13: "CF_UNICODETEXT",
    14: "CF_ENHMETAFILE",
    15: "CF_HDROP",
    16: "CF_LOCALE",
    17: "CF_DIBV5",
}

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

#: 当图片文件处理的扩展名（抽缩略图用；其它一律按普通文件记录）
IMAGE_FILE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jpe", ".bmp", ".gif", ".webp",
    ".tif", ".tiff", ".ico", ".jfif", ".avif",
}
#: 图片文件缩略图的最长边（够看清，又不会把 payload 撑爆；与 mac 端一致）
PREVIEW_MAX_PX = 1600
#: 读图片文件做缩略图的体积上限：再大就不读了（多半是工程图/素材，读进来只会卡同步线程）
MAX_FILE_READ_BYTES = 64 * 1024 * 1024
#: 像素数上限：1 亿像素的图解码要几秒，直接放弃缩略图
MAX_PREVIEW_PIXELS = 40_000_000

#: `_read_once()` 的第三种返回值：剪贴板被别的进程占着，**这一轮没读到任何东西，
#: 但也不代表"内容为空"**。必须和 None 区分开 —— 混为一谈会把序号记成"已处理"，
#: 那一次复制就永久丢了。
_BUSY = object()

# --------------------------------------------------------------------------- #
# Win32 声明
#
# 所有 restype 必须显式声明：64 位下句柄是 64 位指针，漏声明会被截断成 int32，
# 然后 `SetClipboardData` 拿一个残缺句柄 —— 这个项目在 `GetModuleHandleW` 上
# 已经栽过一次（见 hotkey/windows.py 的注释），这里不重复。
# --------------------------------------------------------------------------- #
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClipboardFormatNameW.restype = ctypes.c_int
_user32.GetClipboardSequenceNumber.argtypes = []
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL
_kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_kernel32.GetLongPathNameW.restype = wintypes.DWORD
# 来源应用：前台窗口 → 进程 exe 路径
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_version = ctypes.WinDLL("version", use_last_error=True)
_version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
_version.GetFileVersionInfoW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID
]
_version.GetFileVersionInfoW.restype = wintypes.BOOL
_version.VerQueryValueW.argtypes = [
    wintypes.LPVOID, wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
]
_version.VerQueryValueW.restype = wintypes.BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: 注册格式 id 缓存（`RegisterClipboardFormatW` 同一个名字返回同一个 id，缓存只为少一次调用）
_format_ids: dict[str, int] = {}


def _sequence() -> int:
    """当前剪贴板序列号（每次内容变化 +1）。0 = 当前窗口站没有剪贴板（服务会话等）。"""
    try:
        return int(_user32.GetClipboardSequenceNumber())
    except Exception:  # pragma: no cover - 只可能在异常会话里发生
        return 0


def _register(name: str) -> int:
    fmt = _format_ids.get(name)
    if fmt is None:
        fmt = int(_user32.RegisterClipboardFormatW(name))
        _format_ids[name] = fmt
    return fmt


def _format_name(fmt: int) -> str:
    """格式 id → 可读名字（`probe()` 与错误信息用）。"""
    if fmt in _FORMAT_NAMES:
        return _FORMAT_NAMES[fmt]
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClipboardFormatNameW(fmt, buf, 256)
    return buf.value if n else f"#{fmt}"


def _open_clipboard(retries: int = 5) -> bool:
    """`OpenClipboard` 会被别的进程短暂占住（复制大文件、Office 存盘时尤其明显）。

    重试节奏 0/5/10/20/40ms，合计约 75ms —— 再长就会顶到 150ms 的轮询周期，
    反而把后面的读取挤掉。拿不到就当这一轮没内容，下一轮再来。
    """
    delay = 0.0
    for attempt in range(max(1, retries)):
        if _user32.OpenClipboard(None):
            return True
        if attempt == retries - 1:
            break
        time.sleep(delay)
        delay = min(delay * 2 or 0.005, 0.04)
    return False


def _enum_formats() -> list[int]:
    """枚举当前剪贴板里所有格式 id（调用方必须已经 OpenClipboard）。"""
    out: list[int] = []
    fmt = 0
    while len(out) < 256:
        fmt = int(_user32.EnumClipboardFormats(fmt))
        if not fmt:
            break
        out.append(fmt)
    return out


def _hglobal_bytes(handle: int) -> bytes:
    """把剪贴板里的 HGLOBAL 读成 bytes。

    `GlobalSize()` 给的是**分配**大小，可能比实际数据多几个填充字节，
    调用方按格式自己截断（文本找 NUL、PNG 找 IEND、DIB 按 stride 算）。
    """
    if not handle:
        return b""
    size = int(_kernel32.GlobalSize(handle))
    if not size:
        return b""
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        raise ClipboardError(f"GlobalLock 失败（WinError {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.GlobalUnlock(handle)


def _read_dword(fmt: int) -> int | None:
    """读一个注册格式的 DWORD 值（`CanIncludeInClipboardHistory=0` 这类标记）。"""
    handle = _user32.GetClipboardData(fmt)
    if not handle:
        return None
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return int(ctypes.c_uint32.from_address(ptr).value)
    except Exception:  # pragma: no cover - 数据不是 4 字节时读不到，当作没设置
        return None
    finally:
        _kernel32.GlobalUnlock(handle)


def _set_bytes(fmt: int | str, data: bytes) -> None:
    """把 bytes 塞进剪贴板的某个格式（调用方必须已经 OpenClipboard + EmptyClipboard）。"""
    if isinstance(fmt, str):
        fmt = _register(fmt)
    if not fmt:
        raise ClipboardError(f"注册剪贴板格式 {fmt!r} 失败")
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise ClipboardError(f"GlobalAlloc({len(data)}) 失败（WinError {ctypes.get_last_error()}）")
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        err = ctypes.get_last_error()
        _kernel32.GlobalFree(handle)
        raise ClipboardError(f"GlobalLock 失败（WinError {err}）")
    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        _kernel32.GlobalUnlock(handle)
    if not _user32.SetClipboardData(fmt, handle):
        err = ctypes.get_last_error()
        _kernel32.GlobalFree(handle)
        raise ClipboardError(f"写入格式 {_format_name(fmt)} 失败（WinError {err}）")
    # 成功后这块内存归系统所有，**不能** GlobalFree，否则剪贴板里是野指针


# --------------------------------------------------------------------------- #
# 敏感内容标记（纯函数，可单测）
# --------------------------------------------------------------------------- #
def _should_skip(flags: dict[str, int | None]) -> str:
    """按敏感标记判断要不要整条跳过。返回原因（空串 = 正常同步）。

    语义分两种，别混：

    - `ExcludeClipboardContentFromMonitorProcessing`：**出现就跳过**。
      这是密码管理器（1Password / KeePass / Bitwarden）打的"别监听我"标记，值无意义。
    - `CanIncludeInClipboardHistory` / `CanUploadToCloudClipboard`：值是 DWORD，
      `0` 才是"别记 / 别传"。有些应用会写 `1` 明确表示允许，那种不该跳。
    """
    for name in SKIP_MARKERS_WINDOWS:
        if name not in flags:
            continue
        if name == "ExcludeClipboardContentFromMonitorProcessing":
            return name
        if flags[name] == 0:
            return name
    return ""


# --------------------------------------------------------------------------- #
# CF_HTML（"HTML Format"）编解码
#
# 格式：10 位定宽偏移的纯文本头 + HTML 正文。偏移是 **UTF-8 字节**偏移，不是字符数
# —— 中文片段上按字符算会错位，这是这套格式最容易踩的坑。
# --------------------------------------------------------------------------- #
_CF_HTML_HEADER = (
    "Version:0.9\r\n"
    "StartHTML:{sh:010d}\r\n"
    "EndHTML:{eh:010d}\r\n"
    "StartFragment:{sf:010d}\r\n"
    "EndFragment:{ef:010d}\r\n"
)
_CF_HTML_PRE = "<html><body><!--StartFragment-->"
_CF_HTML_POST = "<!--EndFragment--></body></html>"


def _build_cf_html(fragment: str) -> bytes:
    """把一段 HTML 片段包成剪贴板认的 `HTML Format`。"""
    head_len = len(_CF_HTML_HEADER.format(sh=0, eh=0, sf=0, ef=0).encode("utf-8"))
    pre = _CF_HTML_PRE.encode("utf-8")
    body = fragment.encode("utf-8", "replace")
    post = _CF_HTML_POST.encode("utf-8")
    start_html = head_len
    start_frag = start_html + len(pre)
    end_frag = start_frag + len(body)
    end_html = end_frag + len(post)
    header = _CF_HTML_HEADER.format(sh=start_html, eh=end_html, sf=start_frag, ef=end_frag)
    return header.encode("utf-8") + pre + body + post


def _header_offset(raw: bytes, key: str) -> int | None:
    """从头里取一个偏移值（只在前 512 字节里找，正文里出现同名字符串不算）。"""
    head = raw[:512]
    marker = key.encode("ascii") + b":"
    idx = head.find(marker)
    if idx < 0:
        return None
    digits = head[idx + len(marker): idx + len(marker) + 10]
    try:
        return int(digits.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        return None


def _parse_cf_html(raw: bytes) -> str | None:
    """从 `HTML Format` 里取回 HTML 片段。

    优先按 `StartFragment` / `EndFragment` 切（干净，不带 `<html><body>` 外壳）；
    头里没有偏移（少数应用写得随意）就退回"从第一个 `<` 开始"。
    """
    if not raw:
        return None
    start = _header_offset(raw, "StartFragment")
    end = _header_offset(raw, "EndFragment")
    if start is not None and end is not None and 0 <= start < end <= len(raw):
        fragment = raw[start:end].decode("utf-8", "replace").strip()
        return fragment or None
    html_at = raw.find(b"<")
    if html_at < 0:
        return None
    fragment = raw[html_at:].decode("utf-8", "replace").strip()
    return fragment or None


# --------------------------------------------------------------------------- #
# PNG 基础工具
# --------------------------------------------------------------------------- #
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _trim_png(data: bytes) -> bytes:
    """剪掉 `GlobalSize()` 带出来的尾部填充。

    PNG 一定以 IEND 块结束（`00 00 00 00 49 45 4E 44 <crc4>`），后面不可能再有有效数据。
    不剪的话同一张图在两端算出的 digest 不一样，去重和环抑制会一起失效。
    """
    if not data.startswith(_PNG_SIG):
        return data
    end = data.find(b"IEND")
    if end < 0:
        return data
    return data[: end + 8]


def _png_size(png: bytes) -> tuple[int, int] | None:
    """只读 IHDR 拿宽高（不解码像素，微秒级）。"""
    if len(png) < 24 or not png.startswith(_PNG_SIG) or png[12:16] != b"IHDR":
        return None
    width, height = struct.unpack_from(">II", png, 16)
    return (width, height) if width and height else None


def _rgba_to_png(rgba: bytes, width: int, height: int, has_alpha: bool) -> bytes:
    """RGBA/RGB 原始像素 → PNG（filter 0，不做行滤波 —— 切片拼接比逐字节滤波快得多）。"""
    if has_alpha:
        color_type, bpp, body = 6, 4, rgba
    else:
        color_type, bpp = 2, 3
        rgb = bytearray(width * height * 3)
        rgb[0::3] = rgba[0::4]
        rgb[1::3] = rgba[1::4]
        rgb[2::3] = rgba[2::4]
        body = bytes(rgb)
    stride = width * bpp
    raw = b"".join(b"\x00" + body[y * stride: (y + 1) * stride] for y in range(height))
    # 大图降一档压缩级别：4K 截图 33MB 原始数据，级别 6 要一秒多，级别 3 只要三分之一
    level = 6 if len(raw) <= 8_000_000 else 3
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        _PNG_SIG
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, level))
        + _png_chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------- #
# DIB 解析（读方向：CF_DIB → PNG）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Dib:
    """一块 `CF_DIB` 的关键信息（像素数据已切出来，不含行填充以外的多余字节）。"""

    width: int
    height: int
    bit_count: int
    compression: int
    palette: tuple[tuple[int, int, int], ...]
    pixels: bytes
    stride: int
    top_down: bool


def _parse_dib(dib: bytes) -> _Dib | None:
    """解析 `BITMAPINFOHEADER` / `BITMAPV4HEADER` / `BITMAPV5HEADER`。

    只支持 `BI_RGB` / `BI_BITFIELDS` / `BI_PNG`：剪贴板里的图基本只有这几种。
    位深支持 1/4/8（调色板）/24/32。
    """
    if len(dib) < 40:
        return None
    try:
        (header_size, width, raw_height, planes, bit_count, compression,
         size_image, _xppm, _yppm, clr_used, _clr_important) = struct.unpack_from("<IiiHHIIiiII", dib, 0)
    except struct.error:
        return None
    if header_size not in (40, 52, 56, 108, 124):
        return None
    if width <= 0 or raw_height == 0 or planes != 1:
        return None
    if bit_count not in (1, 4, 8, 24, 32):
        return None
    height = abs(raw_height)
    if width > 100_000 or height > 100_000:
        return None
    top_down = raw_height < 0

    if compression == BI_PNG:
        # 像素区直接是一段完整的 PNG 流（少见，但 RDP / 部分驱动会这么给）
        offset = header_size
        end = offset + (size_image or (len(dib) - offset))
        return _Dib(width, height, bit_count, compression, (), dib[offset:end], 0, top_down)

    if compression not in (BI_RGB, BI_BITFIELDS):
        log.debug("不支持的 DIB 压缩方式 %d，跳过", compression)
        return None

    offset = header_size
    if header_size == 40 and compression == BI_BITFIELDS:
        offset += 12                       # 三个颜色掩码紧跟在 40 字节头后面

    palette: tuple[tuple[int, int, int], ...] = ()
    if bit_count <= 8:
        count = clr_used or (1 << bit_count)
        palette_end = offset + count * 4
        if palette_end > len(dib):
            return None
        palette = tuple((dib[i + 2], dib[i + 1], dib[i]) for i in range(offset, palette_end, 4))
        offset = palette_end

    stride = ((width * bit_count + 31) // 32) * 4
    end = offset + stride * height
    if end > len(dib):
        return None
    return _Dib(width, height, bit_count, compression, palette, dib[offset:end], stride, top_down)


def _unpack_indices(row: bytes, width: int, bit_count: int) -> list[int]:
    """调色板位图解包（1/4/8 位）。只用于少见的调色板图，逐像素可接受。"""
    if bit_count == 8:
        return list(row[:width])
    out: list[int] = []
    if bit_count == 4:
        for byte in row:
            out.append(byte >> 4)
            out.append(byte & 0x0F)
            if len(out) >= width:
                break
        return out[:width]
    for byte in row:                        # 1 位
        for shift in (7, 6, 5, 4, 3, 2, 1, 0):
            out.append((byte >> shift) & 0x01)
            if len(out) >= width:
                return out
    return out[:width]


def _dib_to_rgba(dib: _Dib) -> tuple[int, int, bytes, bool] | None:
    """DIB 像素 → RGBA（自上而下）。

    32/24 位全程走切片（`data[2::3]` 这类），是 C 速度；只有 1/4/8 位调色板图逐像素。
    """
    width, height, stride = dib.width, dib.height, dib.stride
    if height <= 0 or width <= 0:
        return None
    rows = [dib.pixels[i * stride: (i + 1) * stride] for i in range(height)]
    if not dib.top_down:
        rows.reverse()                      # DIB 默认是 bottom-up

    if dib.bit_count == 32:
        flat = b"".join(rows)
        rgba = bytearray(width * height * 4)
        rgba[0::4] = flat[2::4]
        rgba[1::4] = flat[1::4]
        rgba[2::4] = flat[0::4]
        alpha = flat[3::4]
        # BI_RGB 的 alpha 字节是"保留位"：全 0 时按不透明处理，否则整张图会全透明；
        # 全 255 说明图本来就不透明，可以省掉 alpha 通道（PNG 体积小一半）。
        # count() 是 C 实现，4K 图也只有几毫秒，别写成 any(...) 逐字节遍历。
        if alpha.count(0) == len(alpha):
            has_alpha = False
            rgba[3::4] = b"\xff" * (width * height)
        else:
            has_alpha = alpha.count(255) != len(alpha)
            rgba[3::4] = alpha
        return width, height, bytes(rgba), has_alpha

    if dib.bit_count == 24:
        flat = b"".join(row[: width * 3] for row in rows)
        rgba = bytearray(width * height * 4)
        rgba[0::4] = flat[2::3]
        rgba[1::4] = flat[1::3]
        rgba[2::4] = flat[0::3]
        rgba[3::4] = b"\xff" * (width * height)
        return width, height, bytes(rgba), False

    table = [bytes((r, g, b, 255)) for (r, g, b) in dib.palette]
    if not table:
        return None
    if width * height > 16_000_000:
        log.warning("调色板位图过大（%d×%d），跳过", width, height)
        return None
    out = bytearray()
    for row in rows:
        out += b"".join(
            table[i] if i < len(table) else b"\x00\x00\x00\xff"
            for i in _unpack_indices(row, width, dib.bit_count)
        )
    return width, height, bytes(out), False


def _dib_to_png(dib: bytes) -> tuple[bytes, int, int] | None:
    """`CF_DIB` → PNG 字节。失败返回 None（上层跳过这张图，不影响文字）。"""
    parsed = _parse_dib(dib)
    if parsed is None:
        return None
    if parsed.compression == BI_PNG:
        png = _trim_png(parsed.pixels)
        size = _png_size(png)
        return (png, size[0], size[1]) if size else None
    converted = _dib_to_rgba(parsed)
    if converted is None:
        return None
    width, height, rgba, has_alpha = converted
    return _rgba_to_png(rgba, width, height, has_alpha), width, height


# --------------------------------------------------------------------------- #
# PNG → DIB（写方向）
# --------------------------------------------------------------------------- #
_PNG_DECODER: str | None = None    # "qt" / "pillow" / ""（都不可用，试过了）


def _decode_with_qt(png: bytes) -> tuple[int, int, bytes] | None:
    from PySide6.QtGui import QImage

    image = QImage()
    if not image.loadFromData(png, "PNG"):
        return None
    image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        return None
    data = bytes(image.constBits())
    need = width * height * 4
    if len(data) < need:
        return None
    return width, height, data[:need]


def _decode_with_pillow(png: bytes) -> tuple[int, int, bytes] | None:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        image.load()
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, rgba.tobytes()


def _png_to_rgba(png: bytes) -> tuple[int, int, bytes] | None:
    """PNG → RGBA，借用 PySide6 / Pillow 的 C 实现。

    为什么不用纯 Python 解码：4K 截图 33MB 原始像素，逐字节反滤波要十几秒，
    会把同步线程钉死。宁可只写 PNG 格式（现代应用都认），也不在这儿卡住。
    """
    global _PNG_DECODER
    if _PNG_DECODER == "":
        return None
    candidates = (_PNG_DECODER,) if _PNG_DECODER else ("qt", "pillow")
    for name in candidates:
        try:
            out = _decode_with_qt(png) if name == "qt" else _decode_with_pillow(png)
        except Exception:  # noqa: BLE001 - 缺库 / 解码失败都退到下一个
            log.debug("PNG 解码（%s）不可用", name, exc_info=True)
            continue
        if out is not None:
            _PNG_DECODER = name
            return out
    if not _PNG_DECODER:
        _PNG_DECODER = ""
        log.info("没有可用的 PNG 解码器（PySide6 / Pillow 都没有），图片只写 PNG、不写 CF_DIB")
    return None


def _rgba_to_bgra_bottom_up(rgba: bytes, width: int, height: int) -> bytes:
    """RGBA 自上而下 → BGRA 自下而上（`CF_DIB` 的像素布局）。"""
    bgra = bytearray(width * height * 4)
    bgra[0::4] = rgba[2::4]
    bgra[1::4] = rgba[1::4]
    bgra[2::4] = rgba[0::4]
    bgra[3::4] = rgba[3::4]
    stride = width * 4
    rows = [bgra[y * stride: (y + 1) * stride] for y in range(height)]
    return b"".join(reversed(rows))


def _build_dib(rgba: bytes, width: int, height: int) -> bytes:
    """拼一块 32bpp `BITMAPINFOHEADER` + 像素。"""
    pixels = _rgba_to_bgra_bottom_up(rgba, width, height)
    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, BI_RGB, len(pixels), 0, 0, 0, 0)
    return header + pixels


def _png_to_dib(png: bytes) -> bytes | None:
    """PNG → `CF_DIB`。解码器不可用时返回 None（调用方只写 PNG）。"""
    decoded = _png_to_rgba(png)
    if decoded is None:
        return None
    width, height, rgba = decoded
    if width <= 0 or height <= 0 or width * height > 40_000_000:
        return None
    return _build_dib(rgba, width, height)


# --------------------------------------------------------------------------- #
# CF_HDROP（资源管理器里复制的文件）
#
# DROPFILES 结构（20 字节）：
#   DWORD pFiles;   // 文件列表相对结构体起点的偏移
#   POINT pt;       // 拖放坐标（剪贴板里恒 0）
#   BOOL  fNC;      // 是否来自非客户区
#   BOOL  fWide;    // TRUE = 文件列表是 UTF-16，FALSE = ANSI
# 之后是「路径\0路径\0\0」的双 NUL 结尾列表。
# --------------------------------------------------------------------------- #
def _parse_hdrop(raw: bytes) -> list[str]:
    """`CF_HDROP` 原始字节 → 路径列表（纯函数，可单测）。

    容错两处真实世界的偏差：
      · `pFiles` 越界 / 为 0：按「紧跟 20 字节头」处理，不要直接丢掉整条；
      · `fWide=0`：老应用给 ANSI（`mbcs`）路径，解不出来就当没有。
    """
    if len(raw) < 20:
        return []
    try:
        p_files = int(struct.unpack_from("<I", raw, 0)[0])
        f_wide = int(struct.unpack_from("<i", raw, 16)[0])
    except struct.error:  # pragma: no cover - 长度已判过，理论上到不了
        return []
    if p_files < 20 or p_files >= len(raw):
        p_files = 20 if len(raw) > 20 else len(raw)
    body = raw[p_files:]
    if f_wide:
        text = body.decode("utf-16-le", "replace")
    else:
        try:
            text = body.decode("mbcs", "replace")
        except (LookupError, UnicodeDecodeError):  # pragma: no cover - 非 Windows 才会
            return []
    return [part for part in text.split("\x00") if part.strip()]


def _build_hdrop(paths: list[str]) -> bytes:
    """路径列表 → `CF_HDROP` 字节（写方向：把文件放回剪贴板，粘到资源管理器里就是文件）。"""
    names = "".join(p + "\x00" for p in paths) + "\x00"
    return struct.pack("<Iiiii", 20, 0, 0, 0, 1) + names.encode("utf-16-le")


def _looks_like_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_FILE_EXTS


def _long_path(path: str) -> str:
    """8.3 短路径 → 长路径（`C:\\Users\\ADMINI~1\\…` → `C:\\Users\\Administrator\\…`）。

    有些程序（cmd、部分安装器、老工具）往 `CF_HDROP` 里塞的是短名。短名本身能用，
    但界面上「~\\Downloads」的缩略会失效、同一个文件两次复制的指纹也会不一样。
    """
    if not path or "~" not in path:
        return path
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = int(_kernel32.GetLongPathNameW(path, buf, 32768))
    except Exception:  # noqa: BLE001 - 拿不到就用原路径，不影响记录
        return path
    return buf.value if 0 < n < 32768 else path


def _thumb_with_qt(data: bytes) -> tuple[bytes, int, int] | None:
    """PySide6 解码 → 缩放 → 重编码 PNG。失败返回 None（换下一个解码器）。"""
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage

    image = QImage()
    if not image.loadFromData(data):
        return None
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0 or width * height > MAX_PREVIEW_PIXELS:
        return None
    if max(width, height) > PREVIEW_MAX_PX:
        image = image.scaled(
            PREVIEW_MAX_PX, PREVIEW_MAX_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        width, height = image.width(), image.height()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    if not image.save(buf, "PNG"):
        return None
    return bytes(buf.data()), width, height


def _thumb_with_pillow(data: bytes) -> tuple[bytes, int, int] | None:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        image.load()
        if image.width * image.height > MAX_PREVIEW_PIXELS:
            return None
        image.thumbnail((PREVIEW_MAX_PX, PREVIEW_MAX_PX))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        out = BytesIO()
        image.save(out, "PNG")
        return out.getvalue(), image.width, image.height


def _thumbnail_of_image_file(path: str) -> tuple[bytes, int, int] | None:
    """图片文件 → (png, 宽, 高)。没有解码器 / 太大 / 读不了都返回 None。"""
    try:
        if os.path.getsize(path) > MAX_FILE_READ_BYTES:
            log.debug("图片文件过大，不抽缩略图：%s", path)
            return None
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        log.debug("读图片文件失败（%s）：%s", path, exc)
        return None
    for name, decode in (("qt", _thumb_with_qt), ("pillow", _thumb_with_pillow)):
        try:
            built = decode(data)
        except Exception:  # noqa: BLE001 - 缺库 / 格式不支持都退到下一个
            log.debug("缩略图解码（%s）失败：%s", name, path, exc_info=True)
            continue
        if built is not None:
            return built
    log.debug("没有可用的图片解码器（PySide6 / Pillow），只记文件名：%s", path)
    return None


# --------------------------------------------------------------------------- #
# 来源应用（前台进程 → 显示名 + exe 路径）
#
# v2 剪贴板列表要显示「Windows · 微信 · 3 分钟前 · …」并带 14px exe 图标
# （设计 §6.3）。Windows 上等价于 mac 的 `NSWorkspace.frontmostApplication()`：
# 复制的那一刻前台是谁，就记谁。poll 每 150ms 一次，所以**按 exe 路径缓存显示名**
# （FileDescription 要读版本信息，每次查会把同步线程拖慢）。
# --------------------------------------------------------------------------- #
#: 截图工具：内容本身是图片、前台是截图面板，mac 端统称「截图」，这里对齐
_SCREENSHOT_EXES = {"screenclipper", "snippingtool", "screenclip", "snip"}
_app_name_cache: dict[str, str] = {}


def _process_image_path(pid: int) -> str:
    """进程 id → exe 路径（拿不到返回空串）。"""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value or ""
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def _file_description(path: str) -> str:
    """exe 的 FileDescription（本地化，如「微信」「Windows 资源管理器」）。"""
    if not path:
        return ""
    size_dummy = wintypes.DWORD(0)
    size = _version.GetFileVersionInfoSizeW(path, ctypes.byref(size_dummy))
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not _version.GetFileVersionInfoW(path, 0, size, buf):
        return ""
    ptr = ctypes.c_void_p()
    length = wintypes.UINT(0)
    if not _version.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                                   ctypes.byref(ptr), ctypes.byref(length)):
        return ""
    try:
        lang, codepage = struct.unpack_from("<HH", ctypes.string_at(ptr.value, 4))
    except (ValueError, struct.error):
        return ""
    for block in (f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription",
                  f"\\StringFileInfo\\{lang:04x}04b0\\FileDescription"):
        ptr = ctypes.c_void_p()
        length = wintypes.UINT(0)
        if _version.VerQueryValueW(buf, block, ctypes.byref(ptr), ctypes.byref(length)):
            value = ctypes.wstring_at(ptr.value, length.value)
            return value.split("\x00", 1)[0].strip()
    return ""


def _app_display_name(path: str) -> str:
    """exe 路径 → 列表里显示的应用名（带缓存）。"""
    cached = _app_name_cache.get(path)
    if cached is not None:
        return cached
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.lower() in _SCREENSHOT_EXES:
        name = "截图"
    else:
        name = _file_description(path) or stem
    if len(_app_name_cache) > 128:            # 别让它无限长
        _app_name_cache.clear()
    _app_name_cache[path] = name
    return name


def _foreground_pid() -> int:
    """前台窗口所属进程 id（0 = 没有前台窗口）。"""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:  # noqa: BLE001
        return 0


def _foreground_app() -> tuple[str, str]:
    """(应用名, exe 路径)。取不到 / 前台是我们自己 → ("", "")。

    注意这是**轮询时刻**的前台，不是用户按下 Ctrl+C 的那一刻（本机差 150ms 以内）。
    自己写的剪贴板会被 poll 吞掉，所以不会把「屏幕切换」记成来源。
    """
    try:
        pid = _foreground_pid()
        if not pid or pid == os.getpid():
            return "", ""
        path = _process_image_path(pid)
        if not path:
            return "", ""
        return _app_display_name(path), path
    except Exception:  # noqa: BLE001 - 拿不到来源应用不该影响同步
        log.debug("读取前台应用失败", exc_info=True)
        return "", ""


# --------------------------------------------------------------------------- #
# 后端
# --------------------------------------------------------------------------- #
class WindowsClipboardBackend:
    """Win32 剪贴板后端。`poll()` 由引擎以 ~150ms 间隔调用，`write()` 在同步线程里调。"""

    name = "windows"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 启动时把当前序列号记为基线：程序启动**之前**就躺在剪贴板里的内容不算"新复制"，
        # 否则第一次 poll 就会把陈旧内容推给对端（也正好满足契约测试
        # `test_poll_empty_returns_none` 对「空/无变化 → None」的要求）。
        self._last_seq = _sequence()
        self._last_digest = ""
        self._last_kind = ""
        # 写完之后"要吞掉的那一次"的序列号；吞掉后立即清空 —— 只吞一次
        self._swallow_seq: int | None = None
        self._closed = False

    # ------------------------------------------------------------------ 读
    def poll(self) -> ClipItem | None:
        if self._closed:
            return None
        seq = _sequence()
        if not seq:                                  # 没有剪贴板的会话（服务 / 无窗口站）
            return None

        # ① 自写回弹：只吞这一次
        if self._swallow_seq is not None:
            if seq == self._swallow_seq:
                self._swallow_seq = None
                # 注意**不要**在这里更新 `_last_seq`：更新了就等于告诉下一次 poll
                # 「这个序号处理过了」，刚写进去的内容永远读不回来
                # （契约测试 test_text_roundtrip_unicode 正是卡这一点）。
                log.debug("poll：吞掉自己刚写入的那次变更（seq=%d）", seq)
                return None
            self._swallow_seq = None                 # 写完又被别人改了，标记作废

        # ② 序号没变 = 内容没变
        if seq == self._last_seq:
            return None

        with self._lock:
            item = self._read_once()

        if item is _BUSY:
            # 被别的程序占着：**不记序号**，下一轮（150ms 后）再试
            return None

        if item is None:
            # 经典竞态：序列号已经变了，数据还没落地。等 10ms 再看一眼，
            # 仍为空就把这个序号记下（内容真落地时序号会再变一次，不会漏）。
            time.sleep(0.01)
            with self._lock:
                item = self._read_once()
            if item is _BUSY:
                return None
            if item is None:
                self._last_seq = seq
                return None

        self._last_seq = seq
        if item.digest == self._last_digest:
            return None
        self._last_digest = item.digest
        self._last_kind = item.kind
        log.debug("poll：读到 %s（%s）", item.describe(), item.digest[:12])
        return item

    def _read_once(self) -> ClipItem | object | None:
        """在**一次** OpenClipboard 会话里把内容读全（格式枚举与读取必须在同一个会话）。

        返回 `_BUSY`（打不开）/ `None`（打开了但没有可同步的内容）/ `ClipItem`。

        文件（`CF_HDROP`）优先：有文件就只读文本兜底，**不读位图** —— 资源管理器
        复制图片文件时可能带一张图标位图，读了就会把「文件」错当成「截图」。
        缩略图要解码整张图，放在关掉剪贴板之后做，别让别的程序等我们。
        """
        files: list[str] = []
        if not _open_clipboard():
            log.debug("poll：剪贴板被占用，这一轮跳过")
            return _BUSY
        try:
            formats = _enum_formats()
            if not formats:
                return None

            flags: dict[str, int | None] = {}
            for name in SKIP_MARKERS_WINDOWS:
                fmt = _format_ids.get(name) or _user32.RegisterClipboardFormatW(name)
                _format_ids[name] = int(fmt)
                if fmt in formats:
                    flags[name] = _read_dword(fmt)
            reason = _should_skip(flags)
            if reason:
                log.debug("poll：命中敏感标记 %s，跳过", reason)
                return None

            app, app_id = _foreground_app()
            files = self._read_files(formats)
            if files:
                # 文件不在了（删除/移动）时用文件名兜底，别整条丢掉
                text = self._read_text(formats)
                html = None
                image = None
            else:
                html = self._read_html(formats)
                text = self._read_text(formats)
                image = self._read_image(formats)
        except Exception:  # noqa: BLE001 - 读失败不能让轮询线程挂掉
            log.warning("读取剪贴板失败，这一轮跳过", exc_info=True)
            return _BUSY
        finally:
            _user32.CloseClipboard()

        if files:
            item = self._file_item(files, app=app, app_id=app_id)
            if item is not None:
                return item
            log.debug("CF_HDROP 里的文件都不在了，退回文本：%s", files[:3])

        if image is not None:
            png, width, height = image
            if text:
                # 图 + 附带文字（浏览器复制带说明的图、Excel 选区带图表）：按 mixed 传
                return ClipItem(kind="mixed", text=text, html=html, app=app, app_id=app_id,
                                image=ImageData(png=png, width=width, height=height))
            item = make_image(png, width, height)     # 超限时返回 None（不抛）
            return replace(item, app=app, app_id=app_id) if item else None
        if text:
            item = make_text(text, html=html)
            return replace(item, app=app, app_id=app_id)
        # 只有不支持的类型（自定义格式 / 只有 CF_BITMAP 等）
        return None

    def _read_files(self, formats: list[int]) -> list[str]:
        """`CF_HDROP` → 路径列表（调用方必须已经 OpenClipboard）。"""
        if CF_HDROP not in formats:
            return []
        handle = _user32.GetClipboardData(CF_HDROP)
        if not handle:
            return []
        try:
            paths = _parse_hdrop(_hglobal_bytes(handle))
        except ClipboardError:
            log.debug("读 CF_HDROP 失败", exc_info=True)
            return []
        if len(paths) > 1:
            log.info("复制了 %d 个文件，只记录第一个：%s", len(paths), paths[0])
        return paths

    def _file_item(self, paths: list[str], app: str = "",
                   app_id: str = "") -> ClipItem | None:
        """路径列表 → 文件记录（图片文件带缩略图）。文件都不在返回 None。"""
        existing = [p for p in paths if p and os.path.exists(p)]
        if not existing:
            return None
        path = _long_path(existing[0])
        try:
            size = int(os.path.getsize(path))
        except OSError as exc:
            log.debug("读文件大小失败（%s）：%s", path, exc)
            size = 0

        png = b""
        width = height = 0
        if _looks_like_image_file(path):
            built = _thumbnail_of_image_file(path)
            if built is not None:
                png, width, height = built
        item = make_file(
            path, png=png or None, width=width, height=height, size=size,
            app=app, app_id=app_id,
        )
        if item is None and png:
            # 缩略图超限（`make_image` 已记 warning）→ 退化成「只有文件名」的文件记录
            item = make_file(path, size=size, app=app, app_id=app_id)
        return item


    def _read_text(self, formats: list[int]) -> str | None:
        if CF_UNICODETEXT in formats:
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if handle:
                raw = _hglobal_bytes(handle)
                value = raw.decode("utf-16-le", "replace").split("\x00", 1)[0]
                if value:
                    return value
        if CF_TEXT in formats:
            handle = _user32.GetClipboardData(CF_TEXT)
            if handle:
                raw = _hglobal_bytes(handle).split(b"\x00", 1)[0]
                if raw:
                    return raw.decode("mbcs", "replace")
        return None

    def _read_html(self, formats: list[int]) -> str | None:
        fmt = _format_ids.get(FMT_HTML) or _user32.RegisterClipboardFormatW(FMT_HTML)
        _format_ids[FMT_HTML] = int(fmt)
        if fmt not in formats:
            return None
        handle = _user32.GetClipboardData(fmt)
        if not handle:
            return None
        return _parse_cf_html(_hglobal_bytes(handle))

    def _read_image(self, formats: list[int]) -> tuple[bytes, int, int] | None:
        # 优先注册格式 PNG：零转换、无损，而且 digest 与源端完全一致
        fmt = _format_ids.get(FMT_PNG) or _user32.RegisterClipboardFormatW(FMT_PNG)
        _format_ids[FMT_PNG] = int(fmt)
        if fmt in formats:
            handle = _user32.GetClipboardData(fmt)
            if handle:
                png = _trim_png(_hglobal_bytes(handle))
                size = _png_size(png)
                if size and len(png) <= MAX_IMAGE_BYTES:
                    return png, size[0], size[1]
        for cf in (CF_DIBV5, CF_DIB):
            if cf not in formats:
                continue
            handle = _user32.GetClipboardData(cf)
            if not handle:
                continue
            converted = _dib_to_png(_hglobal_bytes(handle))
            if converted is not None:
                return converted
        return None

    # ------------------------------------------------------------------ 写
    def write(self, item: ClipItem) -> None:
        if self._closed:
            raise ClipboardError("剪贴板后端已关闭")
        payloads: list[tuple[int, bytes]] = []

        if item.html:
            payloads.append((_register(FMT_HTML), _build_cf_html(item.html)))
        if item.text:
            payloads.append((CF_UNICODETEXT, (item.text + "\x00").encode("utf-16-le")))
        # 文件记录：把路径放回剪贴板，粘到资源管理器里就是那个文件。
        # 同时保留上面的纯文本（文件名），粘到输入框里仍然是名字 —— 和资源管理器一致。
        if item.path and os.path.exists(item.path):
            payloads.append((CF_HDROP, _build_hdrop([item.path])))
        if item.image is not None:
            payloads.append((_register(FMT_PNG), item.image.png))
            dib = _png_to_dib(item.image.png)
            if dib is not None:
                payloads.append((CF_DIB, dib))
            else:
                log.debug("PNG→DIB 不可用，本次只写 PNG 格式")

        if not payloads:
            raise ClipboardError("没有可写入剪贴板的内容")

        with self._lock:
            try:
                if not _open_clipboard():
                    raise ClipboardError("剪贴板被其它程序占用，写入失败")
                try:
                    if not _user32.EmptyClipboard():
                        raise ClipboardError(f"清空剪贴板失败（WinError {ctypes.get_last_error()}）")
                    for fmt, data in payloads:
                        _set_bytes(fmt, data)
                finally:
                    _user32.CloseClipboard()
            finally:
                # 成功失败都要记：写到一半失败时剪贴板里已经是半成品了，
                # 不记的话下一次 poll 会把这份半成品当成"用户刚复制的"推给对端。
                self._swallow_seq = _sequence() or None

        log.debug("写入剪贴板：%s（%d 种格式）", item.describe(), len(payloads))

    # ------------------------------------------------------------------ 诊断
    def probe(self) -> dict[str, str]:
        """自检信息（<50ms，不做任何转换，UI 状态轮询会调它）。"""
        info = {
            "backend": self.name,
            "seq": str(_sequence()),
            "last_kind": self._last_kind or "-",
            "swallow_seq": str(self._swallow_seq if self._swallow_seq is not None else "-"),
        }
        if self._closed:
            info["state"] = "closed"
            return info
        if not _open_clipboard(retries=2):
            info["state"] = "busy"                  # 被别的程序占着，属正常现象
            return info
        try:
            formats = _enum_formats()
            names = [_format_name(f) for f in formats]
            info["state"] = "ok"
            if names:
                info["formats"] = ", ".join(names[:16]) + ("…" if len(names) > 16 else "")
            else:
                info["formats"] = "空"
            info["has_text"] = "yes" if CF_UNICODETEXT in formats else "no"
            png_fmt = _format_ids.get(FMT_PNG, 0)
            image_formats = [f for f in (CF_DIB, CF_DIBV5, png_fmt) if f]
            info["has_image"] = "yes" if any(f in formats for f in image_formats) else "no"
            info["has_files"] = "yes" if CF_HDROP in formats else "no"
            if CF_HDROP in formats:
                # 排障用：「复制了文件但列表没东西」时先看这里解析出几个路径
                try:
                    handle = _user32.GetClipboardData(CF_HDROP)
                    info["files"] = str(len(_parse_hdrop(_hglobal_bytes(handle)))) if handle else "0"
                except Exception:  # noqa: BLE001 - 诊断信息不值得让 probe 失败
                    info["files"] = "?"
            info["png_decoder"] = _decoder_hint()
        finally:
            _user32.CloseClipboard()
        return info

    def close(self) -> None:
        """释放资源。本后端不持有 OS 资源（每次都成对 Open/Close），可重复调用。"""
        self._closed = True


def _decoder_hint() -> str:
    """`probe()` 用：现在能不能把 PNG 转成 `CF_DIB`。

    只查 `find_spec`（不 import），因为 `probe()` 有 50ms 预算，而 import PySide6
    本身就要上百毫秒。
    """
    if _PNG_DECODER:
        return f"{_PNG_DECODER}（已用）"
    if _PNG_DECODER == "":
        return "无（图片只写 PNG）"
    import importlib.util

    for name, module in (("qt", "PySide6"), ("pillow", "PIL")):
        try:
            if importlib.util.find_spec(module) is not None:
                return f"{name}（可用）"
        except (ImportError, ValueError):  # pragma: no cover - 半装状态才会走到
            continue
    return "无（图片只写 PNG）"


# --------------------------------------------------------------------------- #
# 测试 / 诊断用的小工具（不参与同步链路）
# --------------------------------------------------------------------------- #
def snapshot_text() -> str | None:
    """读当前剪贴板里的文本。只读，不改变序列号。

    `tests/test_clipboard_contract.py` 用它保存/还原用户剪贴板，`tools/` 里的自测脚本
    也可以直接用 —— 那段逻辑以前是测试文件里内联的 `ctypes.c_wchar_p(GetClipboardData(13))`，
    64 位下句柄没声明 restype 会被截断成 int32，再当指针解引用就是访问违例（实测崩过一次）。
    """
    if not _open_clipboard(retries=2):
        return None
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        raw = _hglobal_bytes(handle)
    except ClipboardError:
        return None
    finally:
        _user32.CloseClipboard()
    return raw.decode("utf-16-le", "replace").split("\x00", 1)[0] or None


def restore_text(text: str) -> bool:
    """把一段文本写回剪贴板（测试收尾还原用）。返回是否成功。

    注意必须走 `GlobalAlloc` + `SetClipboardData`：直接把 Python 缓冲区的指针丢给
    `SetClipboardData` 会失败（它不是 HGLOBAL），剪贴板反而被清空。
    """
    if not text:
        return False
    try:
        if not _open_clipboard():
            return False
        try:
            if not _user32.EmptyClipboard():
                return False
            _set_bytes(CF_UNICODETEXT, (text + "\x00").encode("utf-16-le"))
        finally:
            _user32.CloseClipboard()
    except ClipboardError:
        return False
    return True


__all__ = ["WindowsClipboardBackend", "restore_text", "snapshot_text"]
