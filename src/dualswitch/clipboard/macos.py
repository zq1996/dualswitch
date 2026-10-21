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
import os
import re
import threading
import time
from dataclasses import replace

from .base import (
    SKIP_MARKERS_MACOS,
    ClipItem,
    ClipboardError,
    make_file,
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
            app_name, app_id = self._frontmost_app()
            try:
                item = self._read(app=app_name, app_id=app_id)
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

    def _read(self, app: str = "", app_id: str = "") -> ClipItem | None:
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
        path = ""
        # 先看是不是「访达里复制的文件」：
        #   · 图片文件 → 读文件生成预览图（**优先于**粘贴板里的位图 —— Finder 会顺手
        #     塞一张文件图标的 TIFF，直接用就是一张空白图，这就是「复制图片给我 PNG 空白」）
        #   · 非图片文件 → 不要图标位图，按文本（路径）处理，文件本身不跨端同步
        file_path = self._file_url_path(types)
        if file_path and not os.path.exists(file_path):
            # 解析出来但文件已经不在（删除/移动/引用失效）→ 当普通文本处理，
            # 别再生成一张「打不开的文件卡」
            log.debug("file-url 指向的文件不存在，按文本处理：%s", file_path)
            file_path = ""
        if file_path:
            path = file_path
            # 图片文件才抽缩略图；其它文件不读内容，只要文件名 + 父目录 + 大小
            if self._looks_like_image_file(file_path):
                built = self._preview_of_image_file(file_path)
                if built is not None:
                    png, width, height = built
        else:
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

        # 访达里复制的**文件**：文件名当标题，路径/大小给文件卡用（不上网）
        if path:
            return self._file_item(path, png, width, height, app, app_id)

        if png:
            item = make_image(png, width, height, text=text or None, source=self.host)
            if item is not None:
                return replace(item, ts=time.time(), app=app, app_id=app_id)
            # 图片超限：退化只传文字（`make_image` 已记 warning）
            if not text:
                return None
        if text:
            item = make_text(text, html=markup, source=self.host)
            return replace(item, ts=time.time(), app=app, app_id=app_id)
        return None

    def _file_item(self, path: str, png, width: int, height: int, app: str, app_id: str):  # noqa: ANN001, ANN202
        """把本机文件包成 ClipItem（图片文件带 png，其它只有文件名）。"""
        size = 0
        try:
            size = int(os.path.getsize(path))
        except OSError as exc:
            log.debug("读文件大小失败（%s）：%s", path, exc)
        item = make_file(
            path, png=png, width=width, height=height,
            source=self.host, app=app, app_id=app_id, size=size,
        )
        if item is None and png:
            # 图片超限 → 退化成「只有文件名」的文件记录（`make_image` 已记 warning）
            item = make_file(path, source=self.host, app=app, app_id=app_id, size=size)
        return replace(item, ts=time.time()) if item is not None else None

    # ------------------------------------------------------------------ 来源应用
    @staticmethod
    def _frontmost_app() -> tuple[str, str]:
        """(显示名, bundle id)。截图时系统前台是「截屏」面板，统一叫「截图」。"""
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return "", ""
            name = str(app.localizedName() or "")
            bid = str(app.bundleIdentifier() or "")
            if bid in ("com.apple.screencaptureui", "com.apple.ScreenCaptureUI"):
                return "截图", bid
            return name, bid
        except Exception as exc:  # noqa: BLE001 - 取不到就当没有来源应用
            log.debug("读取前台应用失败：%s", exc)
            return "", ""

    # ------------------------------------------------------------------ 图片文件
    IMAGE_FILE_EXTS = {
        ".png", ".jpg", ".jpeg", ".heic", ".heif", ".gif", ".webp",
        ".tif", ".tiff", ".bmp", ".ico", ".jfif",
    }
    PREVIEW_MAX_PX = 1600          # 预览图最长边（够看清，又不会把 payload 撑爆）

    def _file_url_path(self, types: list[str]) -> str:
        """粘贴板里的 `public.file-url` → 本地路径（不是单文件就返回空）。

        ⚠️ 访达有时给的不是普通路径，而是**文件引用 URL**（`file:///.file/id=6571367.29…`）：
        直接 `urlparse` 会拿到 `/.file/id=6571367.29…`，既打不开也没有扩展名
        （用户 2026-10-21 看到的「id=6571367.29202959 / .file」就是它）。
        必须过 NSURL：`filePathURL()` 会按 inode 解析出真实路径。
        """
        key = "public.file-url"
        if key not in types:
            return ""
        try:
            raw = self._pb.stringForType_(key)
            if not raw:
                return ""
            text = str(raw).strip()
            if not text or "\n" in text:      # 多选文件按文本处理，别猜
                return ""

            path = self._nsurl_file_path(text)
            if path is None:
                # pyobjc 不可用时的兜底：普通 file:// URL 自己解
                from urllib.parse import unquote, urlparse

                parsed = urlparse(text)
                if parsed.scheme != "file":
                    return ""
                path = unquote(parsed.path)
            return path or ""
        except Exception as exc:  # noqa: BLE001
            log.debug("解析 file-url 失败：%s", exc)
            return ""

    @staticmethod
    def _nsurl_file_path(text: str) -> str | None:
        """用 NSURL 解析文件 URL（含 `/.file/id=…` 引用型）→ 真实路径。"""
        try:
            from Foundation import NSURL
        except Exception:  # noqa: BLE001 - 非 mac / 没装 pyobjc
            return None
        url = NSURL.URLWithString_(text)
        if url is None or not url.isFileURL():
            return None
        if url.isFileReferenceURL():
            resolved = url.filePathURL()
            if resolved is not None:
                url = resolved
        return str(url.path() or "")

    def _looks_like_image_file(self, path: str) -> bool:
        return path.lower().endswith(tuple(self.IMAGE_FILE_EXTS))

    def _image_from_file_url(self, types: list[str]):  # noqa: ANN202
        """粘贴板里是图片文件时，读文件生成预览图 → (png, w, h, 原路径)。"""
        path = self._file_url_path(types)
        if not path or not self._looks_like_image_file(path):
            return None
        built = self._preview_of_image_file(path)
        if built is None:
            return None
        png, width, height = built
        return png, width, height, path

    def _preview_of_image_file(self, path: str):  # noqa: ANN202
        """用 ImageIO 抽缩略图（不依赖窗口/绘图上下文，worker 线程安全）。

        访达里复制的图片只有 file-url，没有像素数据；这里把文件读成一张
        最长边 ≤1600 的 PNG 当预览图，列表/预览窗看到的就是图片内容本身。
        返回 (png, 原始宽, 原始高)。
        """
        if not os.path.exists(path):
            return None
        try:
            from AppKit import NSBitmapImageFileTypePNG, NSBitmapImageRep
            from Foundation import NSURL
            from Quartz import (
                CGImageSourceCopyPropertiesAtIndex,
                CGImageSourceCreateThumbnailAtIndex,
                CGImageSourceCreateWithURL,
                kCGImagePropertyPixelHeight,
                kCGImagePropertyPixelWidth,
                kCGImageSourceCreateThumbnailFromImageAlways,
                kCGImageSourceCreateThumbnailWithTransform,
                kCGImageSourceThumbnailMaxPixelSize,
            )
        except Exception as exc:  # noqa: BLE001 - pyobjc 缺 Quartz 时降级
            log.debug("ImageIO 不可用：%s", exc)
            return None

        url = NSURL.fileURLWithPath_(path)
        source = CGImageSourceCreateWithURL(url, None)
        if source is None:
            return None
        props = CGImageSourceCopyPropertiesAtIndex(source, 0, None) or {}
        width = int(props.get(kCGImagePropertyPixelWidth) or 0)
        height = int(props.get(kCGImagePropertyPixelHeight) or 0)
        options = {
            kCGImageSourceCreateThumbnailFromImageAlways: True,
            kCGImageSourceCreateThumbnailWithTransform: True,
            kCGImageSourceThumbnailMaxPixelSize: self.PREVIEW_MAX_PX,
        }
        cg = CGImageSourceCreateThumbnailAtIndex(source, 0, options)
        if cg is None:
            return None
        rep = NSBitmapImageRep.alloc().initWithCGImage_(cg)
        data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        if data is None:
            return None
        return bytes(data), width or int(cg.width()), height or int(cg.height())

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
            NSPasteboardTypeFileURL,
            NSPasteboardTypeHTML,
            NSPasteboardTypePNG,
            NSPasteboardTypeString,
            NSPasteboardTypeTIFF,
        )
        from Foundation import NSData

        # 文件记录：写回「文件 URL」，这样在访达里能直接粘贴出文件本身；
        # 纯文本只放文件名，粘到输入框里毫无意义，所以不写 String 类型。
        file_path = getattr(item, "path", "") or ""
        write_file = bool(file_path) and os.path.exists(file_path)

        types: list[str] = []
        if item.text and not write_file:
            types.append(NSPasteboardTypeString)
        if write_file:
            types.append(NSPasteboardTypeFileURL)
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
                if write_file:
                    from AppKit import NSURL

                    url = NSURL.fileURLWithPath_(file_path)
                    ok = bool(
                        self._pb.setString_forType_(url.absoluteString(), NSPasteboardTypeFileURL)
                    ) and ok
                if item.text and not write_file:
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

    def read_now(self) -> ClipItem | None:
        """无条件读一次（忽略"内容没变"的判断）。

        给 `dualswitch clip get` / `tools/clip_selftest.py` 用：这两处要看的是
        "此刻剪贴板里是什么"，而不是"相对上次有没有变化"。
        契约里的 `poll()` 是给上层轮询用的，语义不同，别混。
        """
        with self._lock:
            try:
                self._last_count = int(self._pb.changeCount())
            except Exception:  # noqa: BLE001
                pass
            item = self._read()
            if item is not None:
                self._last_digest = item.digest
                self._last_kind = item.kind
            return item

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
