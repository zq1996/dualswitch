"""macOS 剪贴板后端专项测试。

契约测试（`test_clipboard_contract.py`）覆盖的是**两端共有的语义**，这里补的是
macOS 独有的几条：TIFF→PNG 转换、敏感标记、HTML 兜底、双写 PNG+TIFF。

⚠️ 这些用例会真的读写系统剪贴板（`QClipboard` 在 offscreen 下是空实现，没法用）。
因此和契约测试一样用环境变量开关，默认跳过，避免每次跑全量测试都冲掉你手里的内容：

    DUALSWITCH_CLIP_TEST=real ~/.workbuddy/binaries/python/envs/default/bin/python \\
        -m pytest tests/test_clipboard_macos.py -q -s -p no:cacheprovider --basetemp=.pytest-tmp

用例结束会把原来的纯文本还回去（图片/富文本无法完整快照，这是已知的取舍）。
"""

from __future__ import annotations

import os
import sys
import time

import pytest

REAL = os.environ.get("DUALSWITCH_CLIP_TEST", "").lower() in ("1", "real", "true", "yes")
pytestmark = [
    pytest.mark.skipif(sys.platform != "darwin", reason="macOS 专用后端"),
    pytest.mark.skipif(not REAL, reason="需要真实剪贴板：设 DUALSWITCH_CLIP_TEST=real 再跑"),
]

AppKit = pytest.importorskip("AppKit")


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture()
def pb():
    """系统剪贴板，用例前后保存 / 恢复纯文本。"""
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    saved = pasteboard.stringForType_(AppKit.NSPasteboardTypeString)
    try:
        yield pasteboard
    finally:
        pasteboard.clearContents()
        if saved:
            pasteboard.declareTypes_owner_([AppKit.NSPasteboardTypeString], None)
            pasteboard.setString_forType_(saved, AppKit.NSPasteboardTypeString)


@pytest.fixture()
def backend():
    from dualswitch.clipboard.macos import MacClipboardBackend

    cb = MacClipboardBackend()
    try:
        yield cb
    finally:
        cb.close()


def _tiny_png() -> bytes:
    """最小合法 PNG（2×2 纯红），不依赖 Pillow。"""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _drain(cb, limit: int = 8):
    out = []
    for _ in range(limit):
        item = cb.poll()
        if item is None:
            break
        out.append(item)
    return out


def _read_after_write(cb, item):
    """写完之后等系统落地再读（第一次 poll 被自写回弹吞掉，属正常）。"""
    cb.write(item)
    got = _drain(cb)
    if not got:
        time.sleep(0.05)
        got = _drain(cb)
    return got


# --------------------------------------------------------------------------- #
# 基本契约
# --------------------------------------------------------------------------- #
def test_backend_identity_and_probe(backend):
    assert backend.name == "macos"
    assert backend.host == "mac"
    info = backend.probe()
    assert info["backend"] == "macos"
    assert "change_count" in info and "types" in info
    assert info["last_kind"] in ("-", "text", "image", "mixed")


def test_write_then_poll_swallows_exactly_once(backend):
    """自写回弹只吞一次：第一次 None，第二次能读回来（往返用例依赖这个形状）。"""
    from dualswitch.clipboard import make_text

    backend.write(make_text("回弹测试-abc"))
    assert backend.poll() is None
    got = _drain(backend)
    if not got:
        time.sleep(0.05)
        got = _drain(backend)
    assert got and got[-1].text == "回弹测试-abc"


def test_text_roundtrip_keeps_unicode_and_newlines(backend):
    from dualswitch.clipboard import make_text

    text = "第一行\n第二行\tTab 🖥️ emoji\n"
    got = _read_after_write(backend, make_text(text))
    assert got, "写入后读不到内容"
    assert got[-1].text == text
    assert got[-1].source == "mac"


def test_image_written_as_png_and_tiff(backend, pb):
    """图片必须双写 PNG + TIFF：浏览器只认 PNG，部分老应用只认 TIFF。"""
    from dualswitch.clipboard import make_image

    png = _tiny_png()
    item = make_image(png, 2, 2)
    assert item is not None
    backend.write(item)

    types = list(pb.types() or [])
    assert AppKit.NSPasteboardTypePNG in types, f"没写 PNG：{types}"
    assert AppKit.NSPasteboardTypeTIFF in types, f"没写 TIFF：{types}"


def test_image_roundtrip_is_png_with_size(backend):
    from dualswitch.clipboard import make_image

    item = make_image(_tiny_png(), 2, 2)
    assert item is not None
    got = _read_after_write(backend, item)
    assert got, "写入图片后读不到内容"
    img = got[-1].image
    assert img is not None
    assert img.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert (img.width, img.height) == (2, 2)
    assert got[-1].kind == "image"


def test_tiff_only_source_is_converted_to_png(backend, pb):
    """外部应用只放 TIFF 时（Qt 自己写图片就是这样），读回来必须是 PNG。"""
    tiff = _png_to_tiff(_tiny_png())
    assert tiff, "构造 TIFF 失败"
    pb.clearContents()
    pb.declareTypes_owner_([AppKit.NSPasteboardTypeTIFF], None)
    pb.setData_forType_(_nsdata(tiff), AppKit.NSPasteboardTypeTIFF)

    got = _drain(backend)
    assert got, "只有 TIFF 时读不到内容"
    img = got[-1].image
    assert img is not None, "TIFF 没被转成图片"
    assert img.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert (img.width, img.height) == (2, 2)


def test_sensitive_marker_skips_everything(backend, pb):
    """密码管理器标记：整条跳过，既不传也不记录。"""
    pb.clearContents()
    pb.declareTypes_owner_(
        [AppKit.NSPasteboardTypeString, "org.nspasteboard.ConcealedType"], None
    )
    pb.setString_forType_("hunter2", AppKit.NSPasteboardTypeString)
    pb.setString_forType_("", "org.nspasteboard.ConcealedType")

    assert backend.poll() is None, "带 ConcealedType 的内容不该被读到"
    assert backend.probe()["sensitive"] == "yes"


def test_html_only_degrades_to_plain_text(backend, pb):
    """只有 HTML 没有纯文本时，兜底剥标签，不能让整条内容丢掉。"""
    pb.clearContents()
    pb.declareTypes_owner_([AppKit.NSPasteboardTypeHTML], None)
    pb.setString_forType_(
        "<b>加粗</b><p>第二段</p>", AppKit.NSPasteboardTypeHTML
    )

    got = _drain(backend)
    assert got, "只有 HTML 时读不到内容"
    item = got[-1]
    assert item.kind == "text"
    assert "加粗" in (item.text or "")
    assert "第二段" in (item.text or "")
    assert item.html and "<b>" in item.html


def test_write_empty_item_raises(backend):
    from dualswitch.clipboard.base import ClipItem, ClipboardError

    # 构造一个「合法但没有可写内容」的对象不容易（ClipItem 会自洽校验），
    # 直接绕过校验构造一个空壳来验证 write 的防御分支。
    empty = object.__new__(ClipItem)
    object.__setattr__(empty, "kind", "text")
    object.__setattr__(empty, "text", None)
    object.__setattr__(empty, "html", None)
    object.__setattr__(empty, "image", None)
    object.__setattr__(empty, "ts", 0.0)
    object.__setattr__(empty, "source", "")
    object.__setattr__(empty, "digest", "x")
    with pytest.raises(ClipboardError):
        backend.write(empty)


def test_close_is_idempotent(backend):
    backend.close()
    backend.close()


# --------------------------------------------------------------------------- #
def _nsdata(raw: bytes):
    from Foundation import NSData

    return NSData.dataWithBytes_length_(raw, len(raw))


def _png_to_tiff(png: bytes) -> bytes | None:
    rep = AppKit.NSBitmapImageRep.imageRepWithData_(_nsdata(png))
    if rep is None:
        return None
    out = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypeTIFF, {})
    return bytes(out) if out is not None else None
