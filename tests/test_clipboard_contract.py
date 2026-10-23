"""剪贴板后端契约测试：**两端都必须跑通这一份**。

这是并行开发的关键防线 —— macOS 端和 Windows 端各写各的 `clipboard/{macos,windows}.py`，
但行为必须完全一致，否则上层 `sync/engine.py` 会在另一台机器上以奇怪的方式失败。

跑法：

    # 只跑 mock（任何环境都行，快）
    pytest tests/test_clipboard_contract.py -q

    # 连真实后端一起跑（会临时占用系统剪贴板，用前请确保没在复制重要东西，
    # 用例内会尽量保存/恢复文本内容）
    DUALSWITCH_CLIP_TEST=real pytest tests/test_clipboard_contract.py -q -s

Windows 端第一次接上时，先单独跑：

    DUALSWITCH_CLIP_TEST=real python -m pytest tests/test_clipboard_contract.py -q -s

出现的每一项 FAIL 都对应契约第 3 节的一条语义，改实现而不是改测试。
"""

from __future__ import annotations

import os
import sys

import pytest

from dualswitch.clipboard import create_backend, make_image, make_text
from dualswitch.clipboard.base import MAX_IMAGE_BYTES, ClipboardError
from dualswitch.clipboard.mock import MockBackend

REAL = os.environ.get("DUALSWITCH_CLIP_TEST", "").lower() in ("1", "real", "true", "yes")


def _real_backend():
    """真实后端；平台不支持或初始化降级时返回 None。"""
    cb = create_backend("auto")
    if getattr(cb, "name", "none") == "none":
        return None
    return cb


@pytest.fixture(params=["mock"] + (["real"] if REAL else []))
def backend(request):
    if request.param == "mock":
        yield MockBackend()
        return
    cb = _real_backend()
    if cb is None:
        pytest.skip("当前环境没有可用的真实剪贴板后端")
    saved = _snapshot()
    try:
        yield cb
    finally:
        _restore(saved)
        cb.close()


# --------------------------------------------------------------------------- #
# 真实后端的剪贴板保护：用例结束后尽量把原来的文本还回去
# --------------------------------------------------------------------------- #
def _snapshot() -> str | None:
    try:
        if sys.platform == "darwin":
            from AppKit import NSPasteboard, NSPasteboardTypeString

            return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
        if sys.platform == "win32":
            # 用后端自己的读取助手：直接 ctypes.c_wchar_p(GetClipboardData(13)) 在 64 位下
            # 会因为句柄被截断而访问违例（Windows 端接入后实测崩过）。
            from dualswitch.clipboard import windows as _win

            return _win.snapshot_text()
    except Exception:  # noqa: BLE001 - 快照失败不影响用例本身
        return None
    return None


def _restore(text: str | None) -> None:
    if not text:
        return
    try:
        if sys.platform == "darwin":
            from AppKit import NSPasteboard, NSPasteboardTypeString

            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(text, NSPasteboardTypeString)
        elif sys.platform == "win32":
            # 必须走 GlobalAlloc + SetClipboardData；直接塞 Python 缓冲区指针会失败
            from dualswitch.clipboard import windows as _win

            _win.restore_text(text)
    except Exception:  # noqa: BLE001
        pass


def _drain(backend, limit: int = 8) -> list:
    out = []
    for _ in range(limit):
        item = backend.poll()
        if item is None:
            break
        out.append(item)
    return out


def _require_real(backend) -> None:
    """往返类用例只有真实后端有意义：mock 的 write 不产生可读回的内容。"""
    if getattr(backend, "name", "mock") == "mock":
        pytest.skip("依赖真实系统剪贴板；mock 只验证「自写吞掉」语义")


def _require_mock(backend) -> None:
    if getattr(backend, "name", "mock") != "mock":
        pytest.skip("去重语义只在 mock 上可确定性构造（真机要连续改两次剪贴板）")


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def test_poll_empty_returns_none(backend):
    """空剪贴板必须返回 None，而不是空 ClipItem。"""
    for _ in range(3):
        assert backend.poll() is None


def test_write_then_poll_swallows_echo(backend):
    """契约第 3 条：write() 之后由它引起的变更必须被吞掉（环抑制第一道闸）。"""
    backend.write(make_text("契约测试-A"))
    assert backend.poll() is None, "write 后的自写回弹没被吞掉 → 会和对端形成无限循环"


def test_same_content_not_reported_twice(backend):
    """去重下沉：同样的内容连续 poll 只上报一次。"""
    _require_mock(backend)
    first = make_text("契约测试-去重")
    backend.feed(first)
    assert backend.poll() is not None
    assert backend.poll() is None


def test_text_roundtrip_unicode(backend):
    """中文 / emoji / 换行 / 制表符都要原样往返。"""
    _require_real(backend)
    samples = [
        "中文测试：屏幕切换",
        "emoji 🖥️⌨️🖱️",
        "多行\n第二行\r\n第三行\t带制表符",
        "",
    ]
    for s in samples:
        if not s:
            continue
        backend.write(make_text(s))
        got = _drain(backend)
        # 真实后端在写入后立即读，可能因为系统还没落地而读不到，重试一次
        if not got:
            import time

            time.sleep(0.05)
            got = _drain(backend)
        assert got, f"写入后读不到内容：{s!r}"
        assert got[-1].text == s, f"往返不一致：{s!r} -> {got[-1].text!r}"


def test_image_roundtrip_png(backend):
    """图片必须能写进去再读出来，且仍是 PNG（不是 TIFF / DIB / QImage）。"""
    _require_real(backend)
    png = _tiny_png()
    item = make_image(png, 2, 2)
    assert item is not None
    backend.write(item)
    got = _drain(backend)
    if not got:
        import time

        time.sleep(0.1)
        got = _drain(backend)
    assert got, "写入图片后读不到内容"
    img = got[-1].image
    assert img is not None, "读回来的不是图片"
    assert img.png.startswith(b"\x89PNG\r\n\x1a\n"), "读回来的不是 PNG 字节"
    assert (img.width, img.height) == (2, 2), f"尺寸丢了：{img.width}×{img.height}"


def test_oversize_image_rejected_by_helper():
    """超大图片在构造阶段就被拒掉，不进网络。"""
    huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_BYTES + 1)
    assert make_image(huge) is None


def test_probe_returns_required_keys(backend):
    """probe() 是 doctor / 自测脚本的数据源，键必须齐。"""
    info = backend.probe()
    assert isinstance(info, dict)
    assert "backend" in info
    assert info["backend"] == backend.name


def test_write_failure_raises_clipboard_error():
    """写入失败必须抛 ClipboardError，不能静默 —— 否则 UI 会以为同步成功了。"""
    mb = MockBackend()
    mb.fail_next_write = True
    with pytest.raises(ClipboardError):
        mb.write(make_text("会失败"))


def test_digest_is_stable_and_content_only():
    """指纹只跟内容有关：换 ts / source 不改变 digest，否则去重和环抑制全废。"""
    a = make_text("同样的内容", source="mac")
    b = make_text("同样的内容", source="windows")
    assert a.digest == b.digest
    assert make_text("不同内容").digest != a.digest


def test_clipitem_rejects_inconsistent_kind():
    """kind 与内容不符要在构造时就炸，别等到对端。"""
    from dualswitch.clipboard.base import ClipItem

    with pytest.raises(ValueError):
        ClipItem(kind="text")
    with pytest.raises(ValueError):
        ClipItem(kind="image")
    with pytest.raises(ValueError):
        ClipItem(kind="mixed", text="只有文字")


def test_create_backend_degrades_gracefully():
    """后端不可用时降级成 NullBackend，绝不抛。"""
    cb = create_backend("none")
    assert cb.name == "none"
    assert cb.poll() is None
    with pytest.raises(ClipboardError):
        cb.write(make_text("x"))
    assert create_backend("根本不存在的后端").name == "none"


# --------------------------------------------------------------------------- #
def _tiny_png() -> bytes:
    """最小合法 PNG（2×2 纯色），不依赖 Pillow。"""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)   # 2×2, 8bit, truecolor
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def test_make_image_fills_dimensions_from_png_header():
    """不传宽高时从 PNG 头解析（BUG-005）。

    `clip send --image` / `tools/*_serve.py --send-image` 都不传宽高，帧里就是 0×0，
    对端预览按比例布局会退化。尺寸本来就在 IHDR 里。
    """
    from dualswitch.clipboard.base import png_size

    png = _png(320, 200) if "_png" in globals() else None
    if png is None:                                   # 这个文件里没有现成的构造器
        import struct
        import zlib

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        raw = b"".join(b"\x00" + bytes((9, 9, 9)) * 320 for _ in range(200))
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", 320, 200, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw, 1))
               + chunk(b"IEND", b""))

    item = make_image(png)
    assert (item.image.width, item.image.height) == (320, 200)
    assert png_size(png) == (320, 200)
    assert png_size(b"not a png") == (0, 0)

    explicit = make_image(png, 10, 20)                # 显式传值仍以调用方为准
    assert (explicit.image.width, explicit.image.height) == (10, 20)
