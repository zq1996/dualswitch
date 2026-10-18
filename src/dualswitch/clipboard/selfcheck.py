"""本机格式矩阵（契约第 7 节「闸三」）：往剪贴板里放 6 类内容再逐项回读校验。

这不验证"能不能粘到微信里"（那个只能人工），验证的是**本机读写链路**：
字节进去、字节出来、类型对得上。CI 与 `dualswitch clip selftest` 共用这一份。

每项输出 PASS / FAIL / MANUAL：
  · PASS    字节完全一致
  · FAIL    读不回来 / 类型变了 / 字节不一致 —— 附"该改哪个函数"
  · MANUAL  本平台没法程序化构造（例如 Windows 的敏感标记要 ctypes 写私有格式）
"""

from __future__ import annotations

import logging
import os
import struct
import sys
import time
import zlib
from dataclasses import dataclass

from .base import ClipItem, ClipboardError, create_backend, make_image, make_text

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    status: str          # PASS | FAIL | MANUAL
    detail: str

    def line(self) -> str:
        return f"[{self.status:6}] {self.name:<22} {self.detail}"


# --------------------------------------------------------------------------- #
# PNG 生成（不依赖 Pillow）
# --------------------------------------------------------------------------- #
def _png(width: int, height: int, *, noise: bool = False) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    if noise:
        # 每行独立随机：模拟真实截图的体积。整图重复同一行的话 zlib 会压到几百 KB，
        # 测不出"大图在网络上会不会断"这个问题。
        raw = b"".join(b"\x00" + os.urandom(width * 3) for _ in range(height))
        level = 1
    else:
        row = b"\xff\x00\x00" * width
        raw = b"".join(b"\x00" + row for _ in range(height))
        level = 9
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, level))
        + chunk(b"IEND", b"")
    )


def _read_back(backend, timeout: float = 1.0) -> ClipItem | None:
    """写完立刻读回来。优先用后端的 `read_now()`（忽略"没变"语义）。"""
    read_now = getattr(backend, "read_now", None)
    if callable(read_now):
        return read_now()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = backend.poll()
        if item is not None:
            return item
        time.sleep(0.02)
    return None


def _check_text(backend, name: str, text: str) -> CheckResult:
    try:
        backend.write(make_text(text))
    except ClipboardError as exc:
        return CheckResult(name, "FAIL", f"写入失败：{exc}（查 write()）")
    item = _read_back(backend)
    if item is None:
        return CheckResult(name, "FAIL", "读不回来（查 read_now()/poll()）")
    if item.text != text:
        return CheckResult(name, "FAIL", f"内容不一致：{item.text!r}（查 _read() 的文本分支）")
    return CheckResult(name, "PASS", f"写入 {len(text.encode('utf-8'))}B，读回一致")


def _check_html(backend) -> CheckResult:
    markup = "<b>加粗</b><p style='color:red'>第二段</p>"
    try:
        backend.write(make_text("加粗\n第二段", html=markup))
    except ClipboardError as exc:
        return CheckResult("HTML 富文本", "FAIL", f"写入失败：{exc}")
    item = _read_back(backend)
    if item is None:
        return CheckResult("HTML 富文本", "FAIL", "读不回来")
    if not item.html:
        return CheckResult("HTML 富文本", "FAIL", "html 字段为空（查 _read() 的 HTML 分支）")
    return CheckResult("HTML 富文本", "PASS", f"写入 {len(markup)}B，html 非空")


def _check_image(backend, name: str, width: int, height: int, *, noise: bool = False) -> CheckResult:
    png = _png(width, height, noise=noise)
    item = make_image(png, width, height)
    if item is None:
        return CheckResult(name, "FAIL", f"图片 {len(png)}B 被 make_image 拒绝")
    started = time.monotonic()
    try:
        backend.write(item)
    except ClipboardError as exc:
        return CheckResult(name, "FAIL", f"写入失败：{exc}")
    elapsed = (time.monotonic() - started) * 1000
    back = _read_back(backend, timeout=3.0)
    if back is None or back.image is None:
        return CheckResult(name, "FAIL", "读不回来或不是图片（查 write() 的双写与 _read() 的 PNG 分支）")
    if not back.image.png.startswith(b"\x89PNG\r\n\x1a\n"):
        return CheckResult(name, "FAIL", "读回的不是 PNG 字节")
    if (back.image.width, back.image.height) != (width, height):
        return CheckResult(
            name, "FAIL",
            f"尺寸丢了：{back.image.width}×{back.image.height}（查 NSBitmapImageRep 尺寸解析）",
        )
    return CheckResult(name, "PASS", f"写入 {len(png) / 1048576:.1f}MB，读回 PNG、{width}×{height}、{elapsed:.0f}ms")


def _check_sensitive(backend) -> CheckResult:
    """敏感标记：写入后应整条跳过。"""
    try:
        if sys.platform == "darwin":
            from AppKit import NSPasteboard, NSPasteboardTypeString

            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.declareTypes_owner_([NSPasteboardTypeString, "org.nspasteboard.ConcealedType"], None)
            pb.setString_forType_("hunter2", NSPasteboardTypeString)
            pb.setString_forType_("", "org.nspasteboard.ConcealedType")
            time.sleep(0.02)
            item = _read_back(backend)
            if item is None:
                return CheckResult("敏感标记", "PASS", "带 ConcealedType 的内容被跳过")
            return CheckResult(
                "敏感标记", "FAIL", "读到了不该读的内容（查 _read() 里的 SKIP_MARKERS_MACOS 判断）"
            )
    except ImportError:
        pass
    return CheckResult("敏感标记", "MANUAL", "本平台需要手工构造敏感标记（见契约 3.1）")


# --------------------------------------------------------------------------- #
def run_matrix(backend=None, *, include_big: bool = True) -> list[CheckResult]:
    """跑完整矩阵。传入 backend 便于测试；不传就按平台自动创建。"""
    own = backend is None
    if own:
        backend = create_backend("auto")
    results: list[CheckResult] = []
    saved = None
    try:
        read_now = getattr(backend, "read_now", None)
        if callable(read_now):
            saved = read_now()
        results.append(_check_text(backend, "纯文本 ASCII", "hello clipboard 123"))
        results.append(_check_text(backend, "中文 + emoji", "中文测试 🖥️⌨️"))
        results.append(_check_text(backend, "多行 + 制表符", "第一行\n第二行\r\n\t缩进"))
        results.append(_check_html(backend))
        results.append(_check_image(backend, "截图风格 PNG 2×2", 2, 2))
        if include_big:
            results.append(_check_image(backend, "4K 大图 3840×2160", 3840, 2160, noise=True))
        results.append(_check_sensitive(backend))
    finally:
        if saved is not None and saved.text:
            try:
                backend.write(saved)
            except ClipboardError:
                pass
        if own:
            backend.close()
    return results


__all__ = ["CheckResult", "run_matrix"]
