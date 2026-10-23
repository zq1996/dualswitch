"""Windows 剪贴板后端测试（`clipboard/windows.py`）。

两层，互不依赖：

1. **纯函数层**：DIB ↔ PNG 编解码、`HTML Format` 偏移、敏感标记判定。
   不碰系统剪贴板，任何机器上都能跑，失败直接指向具体函数。
2. **真实剪贴板层**：写入 / 读回 / 自写回弹 / 序列号去重 / `CF_HDROP` / 被占用时抛错。
   会临时占用系统剪贴板，用例结束把**文本**还原（图片不还原：还原图片要重新编码，
   而"跑测试把用户剪贴板里的图弄丢"这件事本身没法优雅处理 —— 别在复制重要图片时跑）。

契约本身由 `tests/test_clipboard_contract.py` 守护，这个文件只测 Windows 侧特有的东西：
DIB 的位深/行序/填充、`HTML Format` 的字节偏移、Win32 敏感标记、`CF_HDROP` → 文件记录。
"""

from __future__ import annotations

import os
import struct
import sys
import time
import zlib

import pytest

if sys.platform != "win32":
    pytest.skip("Windows 专有后端", allow_module_level=True)

from dualswitch.clipboard import windows as win
from dualswitch.clipboard.base import (
    MAX_IMAGE_BYTES,
    ClipItem,
    ClipboardError,
    ImageData,
    make_file,
    make_image,
    make_text,
)


# --------------------------------------------------------------------------- #
# 构造测试数据（手搓，不复用被测代码，避免"自己验自己"）
# --------------------------------------------------------------------------- #
def _dib32(rgba: bytes, width: int, height: int, *, top_down: bool = False,
           header_size: int = 40) -> bytes:
    """RGBA（自上而下）→ 32bpp DIB。默认 bottom-up，和 Windows 原生一致。"""
    stride = width * 4
    rows = []
    for y in range(height):
        row = rgba[y * stride: (y + 1) * stride]
        bgra = bytearray(stride)
        bgra[0::4] = row[2::4]
        bgra[1::4] = row[1::4]
        bgra[2::4] = row[0::4]
        bgra[3::4] = row[3::4]
        rows.append(bytes(bgra))
    if not top_down:
        rows.reverse()
    pixels = b"".join(rows)
    raw_height = -height if top_down else height
    return struct.pack("<IiiHHIIiiII", header_size, width, raw_height, 1, 32, 0,
                       len(pixels), 0, 0, 0, 0) + pixels


def _rgba_rows(width: int, height: int) -> bytes:
    """生成一张可肉眼分辨的图：每个像素的 R 随 x 变、G 随 y 变、B 固定。"""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out += bytes((x * 40 % 256, y * 60 % 256, 128, 255))
    return bytes(out)


def _tiny_png(width: int = 2, height: int = 2) -> bytes:
    """最小合法 PNG（纯红），不依赖 Pillow。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (win._PNG_SIG + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- #
# 真实剪贴板操作（测试自用的小工具）
# --------------------------------------------------------------------------- #
def _clip_write(entries: list[tuple[int | str, bytes]]) -> None:
    """按顺序写多个格式（一次 OpenClipboard 会话内）。"""
    assert win._open_clipboard(), "打不开剪贴板，测试环境异常"
    try:
        assert win._user32.EmptyClipboard()
        for fmt, data in entries:
            win._set_bytes(fmt, data)
    finally:
        win._user32.CloseClipboard()


def _clip_read_text() -> str | None:
    if not win._open_clipboard():
        return None
    try:
        handle = win._user32.GetClipboardData(win.CF_UNICODETEXT)
        if not handle:
            return None
        return win._hglobal_bytes(handle).decode("utf-16-le", "replace").split("\x00", 1)[0]
    finally:
        win._user32.CloseClipboard()


def _hdrop(paths: list[str]) -> bytes:
    """`CF_HDROP`：20 字节 DROPFILES 头 + 双 NUL 结尾的 UTF-16 路径列表。"""
    head = struct.pack("<Iiiii", 20, 0, 0, 0, 1)   # pFiles, x, y, fNC, fWide
    body = "".join(p + "\x00" for p in paths) + "\x00"
    return head + body.encode("utf-16-le")


@pytest.fixture(autouse=True)
def restore_clipboard():
    """每个用例前后尽量保住用户剪贴板里的文本（图片无法无损还原，见模块说明）。"""
    saved = _clip_read_text()
    yield
    if saved:
        try:
            _clip_write([(win.CF_UNICODETEXT, (saved + "\x00").encode("utf-16-le"))])
        except Exception:  # noqa: BLE001 - 还原失败不该让用例变红
            pass


@pytest.fixture
def backend():
    cb = win.WindowsClipboardBackend()
    try:
        yield cb
    finally:
        cb.close()


def _drain(backend, limit: int = 8) -> list:
    out = []
    for _ in range(limit):
        item = backend.poll()
        if item is None:
            break
        out.append(item)
    return out


def _next_item(backend) -> ClipItem | None:
    """读下一条内容。数据落地比序列号慢一点，读不到就等 50ms 再试一次。"""
    items = _drain(backend, limit=1)
    if items:
        return items[0]
    time.sleep(0.05)
    items = _drain(backend, limit=1)
    return items[0] if items else None


def _read_back(backend) -> ClipItem | None:
    """写入之后读回内容：第一次 poll 一定是被吞掉的自写回弹，第二次才是内容。"""
    assert backend.poll() is None, "自写回弹没被吞掉 → 会和对端形成无限循环"
    return _next_item(backend)


# --------------------------------------------------------------------------- #
# 一、纯函数：DIB 解析
# --------------------------------------------------------------------------- #
def test_parse_dib_32bpp_bottom_up():
    rgba = _rgba_rows(3, 2)
    dib = _dib32(rgba, 3, 2)
    parsed = win._parse_dib(dib)
    assert parsed is not None
    assert (parsed.width, parsed.height) == (3, 2)
    assert parsed.bit_count == 32
    assert parsed.top_down is False
    assert parsed.stride == 12
    got = win._dib_to_rgba(parsed)
    assert got is not None
    width, height, out, has_alpha = got
    assert (width, height) == (3, 2)
    assert out == rgba, "bottom-up 行序还原错了"
    assert has_alpha is False, "alpha 全 255 时应判为不透明"


def test_parse_dib_top_down():
    rgba = _rgba_rows(2, 3)
    dib = _dib32(rgba, 2, 3, top_down=True)
    parsed = win._parse_dib(dib)
    assert parsed is not None and parsed.top_down is True
    assert win._dib_to_rgba(parsed)[2] == rgba, "top-down（负高度）行序没处理"


def test_parse_dib_32bpp_all_zero_alpha_is_opaque():
    """`BI_RGB` 的 32bpp alpha 是保留位，全 0 时要按不透明处理，否则整张图全透明。"""
    rgba = bytearray(_rgba_rows(2, 2))
    rgba[3::4] = b"\x00" * 4
    parsed = win._parse_dib(_dib32(bytes(rgba), 2, 2))
    width, height, out, has_alpha = win._dib_to_rgba(parsed)
    assert has_alpha is False
    assert out[3::4] == b"\xff" * 4


def test_parse_dib_32bpp_real_alpha_is_kept():
    rgba = bytearray(_rgba_rows(2, 2))
    rgba[3::4] = b"\x00\x80\xff\x10"
    parsed = win._parse_dib(_dib32(bytes(rgba), 2, 2))
    width, height, out, has_alpha = win._dib_to_rgba(parsed)
    assert has_alpha is True
    assert out[3::4] == b"\x00\x80\xff\x10"


def test_parse_dib_24bpp_with_stride_padding():
    """24bpp 每行按 4 字节对齐：宽 3 时 9 字节数据 + 3 字节填充。"""
    width, height = 3, 2
    stride = 12
    top = bytes([0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0x00])      # 红 绿 蓝
    bottom = bytes([0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x80, 0x80, 0x80])   # 白 黑 灰
    pixels = bottom + b"\x00" * 3 + top + b"\x00" * 3
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0,
                      len(pixels), 0, 0, 0, 0) + pixels
    parsed = win._parse_dib(dib)
    assert parsed is not None and parsed.stride == stride
    width, height, rgba, _ = win._dib_to_rgba(parsed)
    assert width == 3 and height == 2
    assert rgba[0:4] == bytes((255, 0, 0, 255)), "第一行第一列应是红色（行填充没剥掉）"
    assert rgba[4:8] == bytes((0, 255, 0, 255))
    assert rgba[8:12] == bytes((0, 0, 255, 255))
    assert rgba[12:16] == bytes((255, 255, 255, 255)), "第二行应是白色"


def test_parse_dib_8bpp_palette():
    width, height = 3, 1
    palette = bytes([0, 0, 255, 0]) + bytes([0, 255, 0, 0])   # BGRX：红、绿
    indices = bytes([0, 1, 0, 0])                              # 3 个索引 + 1 字节行填充
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 8, 0,
                      len(indices), 0, 0, 2, 0) + palette + indices
    parsed = win._parse_dib(dib)
    assert parsed is not None and parsed.palette[0] == (255, 0, 0)
    width, height, rgba, _ = win._dib_to_rgba(parsed)
    assert rgba[0:4] == bytes((255, 0, 0, 255))
    assert rgba[4:8] == bytes((0, 255, 0, 255))
    assert rgba[8:12] == bytes((255, 0, 0, 255))


@pytest.mark.parametrize("bad", [
    b"",
    b"\x00" * 39,
    struct.pack("<IiiHHIIiiII", 40, 0, 4, 1, 32, 0, 0, 0, 0, 0, 0),          # 宽 0
    struct.pack("<IiiHHIIiiII", 40, 4, 4, 2, 32, 0, 0, 0, 0, 0, 0),          # planes != 1
    struct.pack("<IiiHHIIiiII", 40, 4, 4, 1, 32, 4, 0, 0, 0, 0, 0),          # BI_JPEG
    struct.pack("<IiiHHIIiiII", 40, 4, 4, 1, 32, 0, 0, 0, 0, 0, 0),          # 像素数据不够
])
def test_parse_dib_rejects_garbage(bad):
    assert win._parse_dib(bad) is None


# --------------------------------------------------------------------------- #
# 二、纯函数：PNG 编解码
# --------------------------------------------------------------------------- #
def test_png_size_reads_ihdr():
    assert win._png_size(_tiny_png(7, 5)) == (7, 5)
    assert win._png_size(b"not a png") is None


def test_trim_png_removes_global_alloc_padding():
    """`GlobalSize()` 会多给几个填充字节，不剪掉 digest 就和源端对不上。"""
    png = _tiny_png()
    assert win._trim_png(png + b"\x00" * 6) == png
    assert win._trim_png(png) == png
    assert win._trim_png(b"junk") == b"junk"


def test_dib_to_png_roundtrip_pixels():
    rgba = _rgba_rows(5, 4)
    png, width, height = win._dib_to_png(_dib32(rgba, 5, 4))
    assert (width, height) == (5, 4)
    assert png.startswith(win._PNG_SIG)
    assert win._png_size(png) == (5, 4)


def test_dib_to_png_keeps_alpha():
    rgba = bytearray(_rgba_rows(2, 2))
    rgba[3::4] = b"\x00\x40\x80\xff"
    png, _, _ = win._dib_to_png(_dib32(bytes(rgba), 2, 2))
    assert png[25] == 6, "带 alpha 的图应写成 PNG colortype 6（RGBA）"


def test_dib_to_png_bi_png_passthrough():
    """`BI_PNG` 压缩的 DIB 里直接就是 PNG 流，应原样取出。"""
    png = _tiny_png(3, 3)
    dib = struct.pack("<IiiHHIIiiII", 40, 3, 3, 1, 32, win.BI_PNG, len(png), 0, 0, 0, 0) + png
    out = win._dib_to_png(dib)
    assert out is not None and out[0] == png


def test_png_to_dib_needs_decoder_or_skips():
    """没有解码器时 `_png_to_dib` 返回 None（写路径只写 PNG），不能抛。"""
    if win._png_to_rgba(_tiny_png()) is None:
        assert win._png_to_dib(_tiny_png()) is None
    else:
        dib = win._png_to_dib(_tiny_png(3, 2))
        assert dib is not None
        assert win._png_size(win._dib_to_png(dib)[0]) == (3, 2)


def test_png_to_dib_matches_pixels():
    if win._png_to_rgba(_tiny_png()) is None:
        pytest.skip("没有 PNG 解码器（PySide6 / Pillow 都没装）")
    rgba = _rgba_rows(4, 3)
    png = win._rgba_to_png(rgba, 4, 3, False)
    dib = win._png_to_dib(png)
    assert dib is not None
    width, height, back, _ = win._dib_to_rgba(win._parse_dib(dib))
    assert (width, height) == (4, 3)
    assert back == rgba, "PNG → DIB → RGBA 像素不一致"


def test_rgba_to_png_with_and_without_alpha():
    rgba = _rgba_rows(2, 2)
    opaque = win._rgba_to_png(rgba, 2, 2, False)
    alpha = win._rgba_to_png(rgba, 2, 2, True)
    assert opaque[25] == 2 and alpha[25] == 6


def test_dib_bytes_are_readable_by_other_decoders():
    """独立验证：DIB 补上 14 字节 BMP 文件头后，Pillow（另一套实现）要能解开。

    这条不是"自己验自己"：Pillow 的 BMP 解码器和我们的 `_parse_dib` 毫无关系，
    它能解出同样的像素，说明写进剪贴板的 `CF_DIB` 对 Word / Chrome 这些应用也是合法的。
    """
    pytest.importorskip("PIL")
    from io import BytesIO

    from PIL import Image

    rgba = _rgba_rows(5, 4)
    dib = win._build_dib(rgba, 5, 4)
    # BMP 文件头 14 字节 + BITMAPINFOHEADER 40 字节 = 像素起点 54
    bmp = struct.pack("<2sIHHI", b"BM", 14 + len(dib), 0, 0, 54) + dib
    with Image.open(BytesIO(bmp)) as image:
        image.load()
        assert image.size == (5, 4)
        assert image.convert("RGBA").tobytes() == rgba


# --------------------------------------------------------------------------- #
# 三、纯函数：HTML Format
# --------------------------------------------------------------------------- #
def test_cf_html_roundtrip_chinese():
    fragment = '<b>中文测试</b> &amp; emoji 🖥️'
    raw = win._build_cf_html(fragment)
    assert win._parse_cf_html(raw) == fragment
    # 偏移必须是字节偏移：中文 3 字节、emoji 4 字节，按字符算就会切错
    start = win._header_offset(raw, "StartFragment")
    end = win._header_offset(raw, "EndFragment")
    assert raw[start:end].decode("utf-8") == fragment
    assert end - start == len(fragment.encode("utf-8")) > len(fragment)


def test_cf_html_header_length_is_stable():
    """10 位定宽偏移：不同长度的片段，头长度必须一样，否则算偏移要迭代。"""
    a = win._build_cf_html("x")
    b = win._build_cf_html("y" * 5000)
    assert win._header_offset(a, "StartHTML") == win._header_offset(b, "StartHTML")


def test_cf_html_fallback_without_header():
    raw = "<i>裸 HTML</i>".encode("utf-8")
    assert win._parse_cf_html(raw) == "<i>裸 HTML</i>"
    assert win._parse_cf_html(b"") is None
    assert win._parse_cf_html(b"no html here") is None


def test_cf_html_broken_offsets_fall_back():
    raw = b"Version:0.9\r\nStartFragment:999999\r\nEndFragment:1\r\n<p>x</p>"
    assert win._parse_cf_html(raw) == "<p>x</p>"


# --------------------------------------------------------------------------- #
# 四、纯函数：敏感标记
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flags,expected", [
    ({}, ""),
    ({"ExcludeClipboardContentFromMonitorProcessing": 1}, "ExcludeClipboardContentFromMonitorProcessing"),
    ({"ExcludeClipboardContentFromMonitorProcessing": None}, "ExcludeClipboardContentFromMonitorProcessing"),
    ({"CanIncludeInClipboardHistory": 0}, "CanIncludeInClipboardHistory"),
    ({"CanIncludeInClipboardHistory": 1}, ""),          # 明确允许 → 不该跳
    ({"CanUploadToCloudClipboard": 0}, "CanUploadToCloudClipboard"),
    ({"CanUploadToCloudClipboard": 1}, ""),
    ({"CF_UNICODETEXT": 0}, ""),                        # 不认识的键一律不管
])
def test_should_skip(flags, expected):
    assert win._should_skip(flags) == expected


def test_format_name():
    assert win._format_name(win.CF_UNICODETEXT) == "CF_UNICODETEXT"
    assert win._format_name(win.CF_HDROP) == "CF_HDROP"
    assert win._format_name(win._register(win.FMT_HTML)) == "HTML Format"
    assert win._format_name(0x8FFF).startswith("#")     # 未注册的私有格式


# --------------------------------------------------------------------------- #
# 五、真实剪贴板：文本
# --------------------------------------------------------------------------- #
def test_first_poll_is_none_after_construction(backend):
    """构造时取序列号做基线：启动前就在剪贴板里的内容不算"新复制"。"""
    for _ in range(3):
        assert backend.poll() is None


def test_read_now_sees_content_that_predates_the_backend():
    """`read_now()` 必须无视「内容没变」，读到进程启动前就在剪贴板里的内容。

    2026-10-23 联调暴露的 bug：macOS 后端有 `read_now()`，Windows 后端漏了，于是
    `dualswitch clip get` 落到 `poll()` 上 —— `poll()` 构造时把当前序列号记成基线，
    所以「不是本进程写的」内容一律读不到，`clip get` 永远打印「剪贴板为空」。
    现场证据：对端同步过来的文本已在系统剪贴板里（`Get-Clipboard` 能读到 27 字），
    `clip get` 却报空。这条用例把「另一个进程先写好、新后端再读」这条路钉住。
    """
    # 先用**另一个**后端实例写进去，模拟「内容早就在剪贴板里」
    writer = win.WindowsClipboardBackend()
    try:
        writer.write(make_text("对端同步过来的文本 🖥️"))
    finally:
        writer.close()

    fresh = win.WindowsClipboardBackend()      # 新实例：基线序列号 = 当前序列号
    try:
        assert fresh.poll() is None, "poll 按契约就该返回 None（内容没变）"
        item = fresh.read_now()
        assert item is not None, "read_now 读不到已有内容 → clip get 会报「剪贴板为空」"
        assert item.kind == "text"
        assert item.text == "对端同步过来的文本 🖥️"
    finally:
        fresh.close()


@pytest.mark.parametrize("sample", [
    "纯文本 ASCII",
    "中文 + emoji 🖥️⌨️",
    "多行\n第二行\r\n第三行\t制表符",
    "  前后空格要保留  ",
])
def test_text_roundtrip(backend, sample):
    backend.write(make_text(sample))
    item = _read_back(backend)
    assert item is not None, "写入后读不到内容"
    assert item.kind == "text"
    assert item.text == sample


def test_self_write_swallowed_only_once(backend):
    """契约第 3 条：写后第一次 poll 吞掉，第二次正常返回（只吞一次）。"""
    backend.write(make_text("只吞一次"))
    assert backend.poll() is None
    item = _next_item(backend)
    assert item is not None and item.text == "只吞一次"


def test_same_text_twice_reported_once(backend):
    """同样的内容再写一次（序列号变了但 digest 没变）→ 不重复上报。"""
    backend.write(make_text("重复内容"))
    first = _read_back(backend)
    assert first is not None
    backend.write(make_text("重复内容"))
    assert backend.poll() is None                       # 自写回弹
    assert backend.poll() is None, "digest 没变却又上报了一次 → 对端会被刷屏"


def test_html_roundtrip(backend):
    fragment = "<b>加粗</b>中文 &amp; 🖥️"
    backend.write(make_text("加粗中文 🖥️", html=fragment))
    item = _read_back(backend)
    assert item is not None
    assert item.text == "加粗中文 🖥️"
    assert item.html == fragment, "HTML 片段在往返中丢了或被加了外壳"


# --------------------------------------------------------------------------- #
# 六、真实剪贴板：图片
# --------------------------------------------------------------------------- #
def test_image_roundtrip_keeps_exact_png(backend):
    """写进去的 PNG 必须原样读回来（字节级），否则两端 digest 永远对不上。"""
    png = win._rgba_to_png(_rgba_rows(6, 4), 6, 4, False)
    item = make_image(png, 6, 4)
    assert item is not None
    backend.write(item)
    got = _read_back(backend)
    assert got is not None and got.image is not None
    assert got.kind == "image"
    assert got.image.png == png
    assert (got.image.width, got.image.height) == (6, 4)


def test_dib_only_image_is_converted_to_png(backend):
    """截图工具只给 CF_DIB 时，后端要自己转成 PNG（契约：上层只认 PNG）。"""
    rgba = _rgba_rows(4, 3)
    _clip_write([(win.CF_DIB, _dib32(rgba, 4, 3))])
    item = _next_item(backend)
    assert item is not None and item.image is not None
    assert item.image.png.startswith(win._PNG_SIG)
    assert (item.image.width, item.image.height) == (4, 3)
    assert win._png_size(item.image.png) == (4, 3)


def test_text_and_image_become_mixed(backend):
    png = win._rgba_to_png(_rgba_rows(3, 3), 3, 3, False)
    backend.write(ClipItem(kind="mixed", text="附带说明",
                           image=ImageData(png=png, width=3, height=3)))
    got = _read_back(backend)
    assert got is not None
    assert got.kind == "mixed"
    assert got.text == "附带说明"
    assert got.image is not None and got.image.png == png


def test_oversize_image_not_written_by_helper():
    """32MB 上限由 base 层拦，后端不重复判断（这里只确认常量没被改坏）。"""
    assert MAX_IMAGE_BYTES == 32 * 1024 * 1024


# --------------------------------------------------------------------------- #
# 七、真实剪贴板：敏感标记 / 文件 / 诊断
# --------------------------------------------------------------------------- #
def test_skip_marker_excludes_content(backend):
    """密码管理器打的标记必须整条跳过（连占位都不留）。"""
    _clip_write([
        (win.CF_UNICODETEXT, "my-secret-password\x00".encode("utf-16-le")),
        ("ExcludeClipboardContentFromMonitorProcessing", struct.pack("<I", 1)),
    ])
    assert backend.poll() is None
    # 对照组：同样的文本、不带标记，必须能读到（否则这条用例可能因为别的原因"通过"）
    _clip_write([(win.CF_UNICODETEXT, "普通内容\x00".encode("utf-16-le"))])
    item = _next_item(backend)
    assert item is not None and item.text == "普通内容"


def test_can_include_in_history_zero_excludes(backend):
    _clip_write([
        (win.CF_UNICODETEXT, "别记我\x00".encode("utf-16-le")),
        ("CanIncludeInClipboardHistory", struct.pack("<I", 0)),
    ])
    assert backend.poll() is None


def test_can_include_in_history_one_allows(backend):
    """值为 1 = 应用明确说"可以记"，不该被当成敏感内容丢掉。"""
    _clip_write([
        (win.CF_UNICODETEXT, "可以记\x00".encode("utf-16-le")),
        ("CanIncludeInClipboardHistory", struct.pack("<I", 1)),
    ])
    item = _next_item(backend)
    assert item is not None and item.text == "可以记"


def test_hdrop_file_becomes_local_file_record(backend, tmp_path):
    """资源管理器复制文件 → 文件记录（路径 + 文件名 + 字节数），不再被当成空内容丢掉。"""
    target = tmp_path / "说明.txt"
    target.write_text("hi", encoding="utf-8")
    _clip_write([(win.CF_HDROP, _hdrop([str(target)]))])

    item = backend.poll()
    assert item is not None, "CF_HDROP 没有被解析成记录"
    assert item.kind == "text"
    assert item.path == str(target)
    assert item.text == "说明.txt"
    assert item.file_size == target.stat().st_size
    assert item.image is None, "普通文件不该带图片"


def test_hdrop_image_file_gets_thumbnail(backend, tmp_path):
    """图片文件要抽缩略图：列表能看到内容、预览窗能给「用默认图片查看器打开」。"""
    target = tmp_path / "湖畔 🎉.png"
    target.write_bytes(_tiny_png(6, 4))
    _clip_write([(win.CF_HDROP, _hdrop([str(target)]))])

    item = backend.poll()
    assert item is not None
    assert item.kind == "mixed", "图片文件 = 文件名 + 缩略图"
    assert item.path == str(target)
    assert item.text == "湖畔 🎉.png"
    assert item.image is not None
    assert item.image.png.startswith(win._PNG_SIG)
    assert (item.image.width, item.image.height) == (6, 4)


def test_hdrop_missing_file_falls_back_to_text(backend, tmp_path):
    """文件已被删掉：不要留一条点不开的文件卡，退回文件名文本。"""
    missing = tmp_path / "已经删了.pdf"
    _clip_write([
        (win.CF_HDROP, _hdrop([str(missing)])),
        (win.CF_UNICODETEXT, "已经删了.pdf\x00".encode("utf-16-le")),
    ])
    item = backend.poll()
    assert item is not None
    assert item.path == "", "不存在的路径不该留在记录里"
    assert item.text == "已经删了.pdf"


def test_hdrop_multi_select_takes_first(backend, tmp_path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    _clip_write([(win.CF_HDROP, _hdrop([str(first), str(second)]))])
    item = backend.poll()
    assert item is not None and item.path == str(first)


def test_file_item_can_be_copied_back(backend, tmp_path):
    """点文件行的「复制」要把**文件**放回剪贴板（只写文件名等于复制了个寂寞）。"""
    target = tmp_path / "报告.pdf"
    target.write_bytes(b"%PDF-1.4 fake")
    item = make_file(str(target), size=target.stat().st_size)
    assert item is not None

    backend.write(item)
    info = backend.probe()
    if info.get("state") == "ok":
        assert info["has_files"] == "yes", "写完剪贴板里应该有 CF_HDROP"

    back = _read_back(backend)
    assert back is not None
    assert back.path == str(target)
    assert back.text == "报告.pdf", "纯文本仍是文件名（粘到输入框里的行为）"


def test_build_hdrop_roundtrip():
    paths = [r"C:\a\b.txt", r"C:\c\湖畔.png"]
    assert win._parse_hdrop(win._build_hdrop(paths)) == paths


def test_probe_reports_file_count(backend, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("a", encoding="utf-8")
    _clip_write([(win.CF_HDROP, _hdrop([str(target)]))])
    info = backend.probe()
    if info.get("state") != "ok":
        pytest.skip(f"剪贴板被占用（state={info.get('state')}）")
    assert info["has_files"] == "yes"
    assert info["files"] == "1"


# --------------------------------------------------------------------------- #
# 七、CF_HDROP 解析（纯函数）
# --------------------------------------------------------------------------- #
def test_parse_hdrop_wide():
    raw = _hdrop([r"C:\a\b.txt", r"C:\c\d.png"])
    assert win._parse_hdrop(raw) == [r"C:\a\b.txt", r"C:\c\d.png"]


def test_parse_hdrop_ansi():
    head = struct.pack("<Iiiii", 20, 0, 0, 0, 0)      # fWide = 0
    body = ("C:\\a\\b.txt\x00\x00").encode("mbcs")
    assert win._parse_hdrop(head + body) == ["C:\\a\\b.txt"]


def test_parse_hdrop_broken_offset_uses_header():
    head = struct.pack("<Iiiii", 0, 0, 0, 0, 1)       # pFiles = 0（少数实现这么写）
    body = "C:\\x.txt\x00\x00".encode("utf-16-le")
    assert win._parse_hdrop(head + body) == ["C:\\x.txt"]


def test_parse_hdrop_short_or_empty():
    assert win._parse_hdrop(b"") == []
    assert win._parse_hdrop(b"\x00" * 8) == []


# --------------------------------------------------------------------------- #
# 九、来源应用（前台进程 → 显示名 + exe 路径）
# --------------------------------------------------------------------------- #
def test_file_description_reads_localized_names():
    """`FileDescription` 是本地化名字（「Windows 资源管理器」「记事本」），
    比 exe 主名（explorer/notepad）更适合放进列表。"""
    assert win._file_description(r"C://Windows//explorer.exe") == "Windows 资源管理器"
    assert win._file_description(r"C://Windows//System32//notepad.exe") == "记事本"
    assert win._file_description("") == ""


def test_app_display_name_falls_back_to_stem(monkeypatch):
    monkeypatch.setattr(win, "_file_description", lambda path: "")
    assert win._app_display_name(r"C://app//zzq.exe") == "zzq"


def test_screenshot_tools_are_named_screenshot():
    """截图工具（Win+Shift+S）的前台是截图面板，按 mac 口径统称「截图」。"""
    assert win._app_display_name(r"C://Windows//SystemApps//ScreenClipper.exe") == "截图"


def test_foreground_app_skips_own_process(monkeypatch):
    monkeypatch.setattr(win, "_foreground_pid", lambda: os.getpid())
    assert win._foreground_app() == ("", ""), "自己不能当来源应用"


def test_foreground_app_reads_process_image(monkeypatch):
    monkeypatch.setattr(win, "_foreground_pid", lambda: 4242)
    monkeypatch.setattr(win, "_process_image_path", lambda pid: r"C://Windows//explorer.exe")
    assert win._foreground_app() == ("Windows 资源管理器", r"C://Windows//explorer.exe")


def test_foreground_app_blank_when_no_window(monkeypatch):
    monkeypatch.setattr(win, "_foreground_pid", lambda: 0)
    assert win._foreground_app() == ("", "")


def test_probe_required_keys(backend):
    info = backend.probe()
    assert info["backend"] == "windows"
    assert info["state"] in ("ok", "busy")
    assert isinstance(info["seq"], str)
    assert isinstance(info["formats"], str)


def test_probe_reports_png_format_after_image_write(backend):
    png = _tiny_png()
    item = make_image(png, 2, 2)
    assert item is not None
    backend.write(item)
    info = backend.probe()
    if info.get("state") != "ok":
        # 别的程序（Office 存盘、复制大文件、剪贴板管理器）正好占着剪贴板时
        # probe 只能报 busy，连 formats 都没有 —— 这不是后端的问题，跳过而不是误报
        pytest.skip(f"剪贴板被占用（state={info.get('state')}），无法验证格式")
    assert info["has_image"] == "yes"
    assert "PNG" in info["formats"]


def test_probe_is_fast(backend):
    """`probe()` 会被 UI 状态轮询调用，必须 <50ms（契约 3.2）。"""
    start = time.perf_counter()
    backend.probe()
    assert (time.perf_counter() - start) < 0.05


# --------------------------------------------------------------------------- #
# 八、真实剪贴板：异常与生命周期
# --------------------------------------------------------------------------- #
def test_write_raises_when_clipboard_busy(monkeypatch, backend):
    """打不开剪贴板时写入必须抛 ClipboardError（不能静默，否则 UI 会以为同步成功）。

    这里用 monkeypatch 而不是真开一个"占位线程"：实测**同一进程内不同线程之间
    `OpenClipboard` 并不互斥**（holder 线程占着时主线程照样能打开），占位线程模拟不出
    "被别的进程占用"。真实占用只能靠第二个进程，那种场景留给真机联调。
    """
    monkeypatch.setattr(win, "_open_clipboard", lambda *a, **k: False)
    with pytest.raises(ClipboardError):
        backend.write(make_text("拿不到剪贴板"))


def test_busy_poll_returns_none_without_losing_content(monkeypatch, backend):
    """打不开时 poll 只返回 None，而且**不能把序号记成已处理** —— 否则那次复制永久丢。"""
    _clip_write([(win.CF_UNICODETEXT, "占位期间的内容\x00".encode("utf-16-le"))])
    monkeypatch.setattr(win, "_open_clipboard", lambda *a, **k: False)
    assert backend.poll() is None
    monkeypatch.undo()
    item = _next_item(backend)
    assert item is not None and item.text == "占位期间的内容"


def test_close_is_idempotent():
    cb = win.WindowsClipboardBackend()
    cb.close()
    cb.close()
    assert cb.poll() is None
    with pytest.raises(ClipboardError):
        cb.write(make_text("已关闭"))


def test_empty_item_cannot_be_constructed():
    """空内容在数据模型层就炸（`ClipItem.__post_init__`），轮不到写剪贴板。"""
    with pytest.raises(ValueError):
        ClipItem(kind="text", text="")


def test_snapshot_and_restore_text_helpers():
    """契约测试用它保存/还原剪贴板；还原必须走 GlobalAlloc，不能直接塞 Python 缓冲区。"""
    assert win.restore_text("还原用文本") is True
    assert win.snapshot_text() == "还原用文本"
    assert win.restore_text("") is False
