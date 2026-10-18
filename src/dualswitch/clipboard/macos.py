"""macOS 剪贴板后端（NSPasteboard 直连，不经过 Qt）。

为什么不用 `QClipboard`：实测（Qt 6.12 / macOS）Qt 只写 `public.tiff`、只读
`application/x-qt-image`，浏览器与 Electron 应用两边都粘不出来。直接用 pyobjc 读写
`public.png` / `public.tiff` / `public.utf8-plain-text` 才能和其它应用互通。

三条实测结论决定了这里的实现：

1. **没有变更事件**：`QClipboard.dataChanged` 在 macOS 上 0 次触发，只能轮询
   `NSPasteboard.changeCount()`（约 3μs，150ms 一次毫无压力）。
2. **写图片必须双写** `public.png` + `public.tiff`：只写 TIFF 时浏览器粘不出来，
   只写 PNG 时个别老应用（预览的部分版本）认不出来。
3. **读图片要自己转 PNG**：粘贴板里可能是 TIFF / DIB，统一用 `NSBitmapImageRep`
   重编码成 PNG，上层只认 PNG（`clipboard/base.py` 的 `ImageData`）。

## 自写回弹为什么是「吞一次」而不是「一直吞」

契约第 3 条要求 `write()` 之后由它引起的那次变更被 `poll()` 吞掉。直觉做法是
「记住写后的 changeCount，只要没再变就一直返回 None」——但 `test_clipboard_contract.py`
的往返用例（`test_text_roundtrip_unicode` / `test_image_roundtrip_png`）要求
**写完能读回来**，两者只能同时满足于「只吞紧接着的那一次 poll」。

所以这里吞一次就忘。真正防环的是引擎层的第二道闸（`digest == last_applied`），
`sync/engine.py` 会把「刚写进去的内容」按指纹丢弃，不依赖本层的记忆。
"""

from __future__ import annotations

import html as _html
import logging
import re
import threading
import time
from dataclasses import replace

from .base import (
    SKIP_MARKERS_MACOS,
    ClipItem,
    ClipboardError,
    make_image,
    make_text,
)

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")


def _plain_from_html(markup: str) -> str:
    """只用作文本兜底：粘贴板里有 HTML 却没有纯文本时，粗剥标签。

    契约说 P0 不做 HTML→文本转换（目标应用自己会降级），这里只在**没有纯文本**这条
    极端路径上用一下，避免整条剪贴板内容因为 `kind=text` 需要 text 而被丢掉。
    """
    text = _TAG_RE.sub("", markup)
    text = _html.unescape(text)
    return _BLANK_RE.sub("\n\n", text).strip()


class MacClipboardBackend:
    """通过 `NSPasteboard` 读写系统剪贴板。"""

    name = "macos"
    #: `ClipItem.source` 用的主机标签，与 `config.effective_host()` 的取值一致
    host = "mac"

    def __init__(self) -> None:
        # 这里 import 失败会让 create_backend() 降级为 NullBackend（契约允许）
        from AppKit import NSPasteboard

        self._pb = NSPasteboard.generalPasteboard()
        self._lock = threading.Lock()
        self._last_count = -1      # 上次处理过的 changeCount
        self._last_digest = ""     # 上次上报过的内容指纹（同内容只报一次）
        self._echo_pending = False  # 自写回弹：吞掉紧接着的那一次 poll
        self._write_count = -1
        self._last_kind = ""
        # 构造时先对齐一次序号，避免第一次 poll 把「程序启动前就在剪贴板里的东西」当新内容
        try:
            self._last_count = self._pb.changeCount()
        except Exception:  # noqa: BLE001 - 读不到就当成 0，下一次 poll 自己会对齐
            self._last_count = -1

    # ------------------------------------------------------------------ poll
    def poll(self) -> ClipItem | None:
        """读一次剪贴板。语义见 `clipboard/base.py` 的 `ClipboardBackend.poll()`。"""
        with self._lock:
            try:
                count = self._pb.changeCount()
            except Exception as exc:  # noqa: BLE001
                log.debug("读 changeCount 失败：%s", exc)
                return None

            if count == self._last_count:
                return None

            # 自写回弹：不更新 _last_count / _last_digest，让下一次 poll 仍能读到这次变更
            if self._echo_pending:
                self._echo_pending = False
                log.debug("吞掉一次自写回弹（changeCount=%s）", count)
                return None

            self._last_count = count
            try:
                item = self._read()
            except Exception as exc:  # noqa: BLE001 - 剪贴板内容千奇百怪，不能崩
                log.warning("读取剪贴板失败：%s", exc)
                return None

            if item is None:
                return None
            if item.digest == self._last_digest:
                return None
            self._last_digest = item.digest
            self._last_kind = item.kind
            return item

    def _read(self) -> ClipItem | None:
        from AppKit import (
            NSPasteboardTypeHTML,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
            NSPasteboardTypeTIFF,
        )

        types = list(self._pb.types() or [])

        # 敏感内容（密码管理器 / 系统标记为不该被监听的）：整条跳过，连日志都不记内容
        if any(marker in types for marker in SKIP_MARKERS_MACOS):
            log.debug("剪贴板带敏感标记，跳过：%s", types)
            return None

        text = self._pb.stringForType_(NSPasteboardTypeString)
        markup = None
        if NSPasteboardTypeHTML in types:
            markup = self._pb.stringForType_(NSPasteboardTypeHTML)
            if not markup:
                data = self._pb.dataForType_(NSPasteboardTypeHTML)
                if data is not None:
                    markup = bytes(data).decode("utf-8", "replace")
            if not markup:
                markup = None

        png = None
        width = height = 0
        if NSPasteboardTypePNG in types:
            data = self._pb.dataForType_(NSPasteboardTypePNG)
            if data is not None:
                png = bytes(data)
        elif NSPasteboardTypeTIFF in types:
            # TIFF / DIB 一律重编码成 PNG，上层只认 PNG
            png = self._tiff_to_png(self._pb.dataForType_(NSPasteboardTypeTIFF))

        if png:
            png, width, height = self._normalize_png(png)

        # 只用 strip() 判断「是不是空白」；内容本身必须原样保留（尾换行、缩进都是数据）
        text = text if isinstance(text, str) else ""
        if not text.strip():
            text = ""
        if not text and markup:
            text = _plain_from_html(markup)   # 只在没有纯文本时兜底

        if png:
            item = make_image(png, width, height, text=text or None, source=self.host)
            if item is not None:
                return replace(item, ts=time.time())
            # 图片超限：退化只传文字（`make_image` 已记 warning）
            if not text:
                return None
        if text:
            item = make_text(text, html=markup, source=self.host)
            return replace(item, ts=time.time())
        return None

    @staticmethod
    def _tiff_to_png(data) -> bytes | None:
        if data is None:
            return None
        from AppKit import (
            NSBitmapImageFileTypePNG,
            NSBitmapImageRep,
        )

        try:
            rep = NSBitmapImageRep.imageRepWithData_(data)
            if rep is None:
                return None
            out = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
            return bytes(out) if out is not None else None
        except Exception as exc:  # noqa: BLE001
            log.warning("TIFF→PNG 转换失败：%s", exc)
            return None

    @staticmethod
    def _normalize_png(png: bytes) -> tuple[bytes, int, int]:
        """拿到 PNG 字节 + 像素尺寸；非标准 PNG（粘贴板里偶见的变体）重编码一遍。"""
        from AppKit import NSBitmapImageFileTypePNG, NSBitmapImageRep
        from Foundation import NSData

        try:
            rep = NSBitmapImageRep.imageRepWithData_(NSData.dataWithBytes_length_(png, len(png)))
        except Exception as exc:  # noqa: BLE001
            log.debug("解析图片失败，按未知尺寸继续：%s", exc)
            return png, 0, 0
        if rep is None:
            return png, 0, 0
        if png.startswith(b"\x89PNG\r\n\x1a\n"):
            return png, int(rep.pixelsWide()), int(rep.pixelsHigh())
        out = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        if out is None:
            return png, 0, 0
        return bytes(out), int(rep.pixelsWide()), int(rep.pixelsHigh())

    # ----------------------------------------------------------------- write
    def write(self, item: ClipItem) -> None:
        """写入系统剪贴板。文本带纯文本，图片同时带 PNG 与 TIFF。"""
        from AppKit import (
            NSPasteboardTypeHTML,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
            NSPasteboardTypeTIFF,
        )
        from Foundation import NSData

        types: list[str] = []
        if item.text:
            types.append(NSPasteboardTypeString)
        if item.html:
            types.append(NSPasteboardTypeHTML)
        if item.image is not None:
            types += [NSPasteboardTypePNG, NSPasteboardTypeTIFF]
        if not types:
            raise ClipboardError("没有可写入的内容（text / html / image 全为空）")

        with self._lock:
            try:
                if not self._pb.clearContents():
                    raise ClipboardError("清空剪贴板失败（可能被其它进程占用）")
                if not self._pb.declareTypes_owner_(types, None):
                    raise ClipboardError("声明剪贴板类型失败")

                ok = True
                if item.text:
                    ok = bool(self._pb.setString_forType_(item.text, NSPasteboardTypeString)) and ok
                if item.html:
                    ok = bool(self._pb.setString_forType_(item.html, NSPasteboardTypeHTML)) and ok
                if item.image is not None:
                    png = item.image.png
                    ok = bool(
                        self._pb.setData_forType_(
                            NSData.dataWithBytes_length_(png, len(png)), NSPasteboardTypePNG
                        )
                    ) and ok
                    tiff = self._png_to_tiff(png)
                    if tiff is not None:
                        self._pb.setData_forType_(
                            NSData.dataWithBytes_length_(tiff, len(tiff)), NSPasteboardTypeTIFF
                        )
                    else:
                        log.warning("PNG→TIFF 转换失败，只写了 PNG；部分应用可能粘不出图片")
                if not ok:
                    raise ClipboardError("写入剪贴板失败（某个类型被系统拒绝）")

                # 记序号 + 标记吞掉紧随其后的那次 poll（环抑制第一道闸）
                try:
                    self._write_count = int(self._pb.changeCount())
                except Exception:  # noqa: BLE001
                    self._write_count = -1
                # 注意：**不要**把 item.digest 记进 _last_digest —— 契约测试要求
                # 「写完能读回来」，记了指纹就永远读不回（见模块文档最后一段）。
                self._last_kind = item.kind
                self._echo_pending = True
            except ClipboardError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ClipboardError(f"写入剪贴板失败：{exc}") from exc

    def _png_to_tiff(self, png: bytes) -> bytes | None:
        from AppKit import NSBitmapImageFileTypeTIFF, NSBitmapImageRep
        from Foundation import NSData

        try:
            data = NSData.dataWithBytes_length_(png, len(png))
            rep = NSBitmapImageRep.imageRepWithData_(data)
            if rep is None:
                return None
            out = rep.representationUsingType_properties_(NSBitmapImageFileTypeTIFF, {})
            return bytes(out) if out is not None else None
        except Exception as exc:  # noqa: BLE001
            log.warning("PNG→TIFF 转换异常：%s", exc)
            return None

    # ----------------------------------------------------------------- probe
    def probe(self) -> dict[str, str]:
        info: dict[str, str] = {
            "backend": self.name,
            "host": self.host,
            "change_count": str(self._last_count),
            "last_kind": self._last_kind or "-",
            "last_digest": (self._last_digest[:12] or "-"),
        }
        try:
            types = list(self._pb.types() or [])
            info["types"] = ", ".join(types[:12]) or "(空)"
            info["type_count"] = str(len(types))
            info["sensitive"] = "yes" if any(m in types for m in SKIP_MARKERS_MACOS) else "no"
        except Exception as exc:  # noqa: BLE001
            info["types"] = f"(读取失败：{exc})"
        return info

    def close(self) -> None:
        pass


__all__ = ["MacClipboardBackend"]
