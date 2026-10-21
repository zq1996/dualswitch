"""应用图标（来源应用那一栏用）。

剪贴板列表的元信息是 `Mac · 微信 · 3 分钟前 · 36 B · 纯文本`，其中「微信」希望带
应用图标；截图这类没有应用的，就只写文字（"截图"）。

做法：`app_id`（bundle id）→ `NSWorkspace` 找到 .app 路径 → 取图标 → NSImage/TIFF
→ QPixmap。取不到就返回 None（调用方只画文字），**不抛异常**。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

log = logging.getLogger("dualswitch.ui.mac2.apps")

_cache: dict[tuple[str, int], QPixmap | None] = {}


def app_icon(app_id: str, size: int = 13) -> QPixmap | None:
    """按 bundle id 取应用图标；拿不到返回 None。"""
    if not app_id:
        return None
    key = (app_id, int(size))
    if key in _cache:
        return _cache[key]
    pix = _load_icon(app_id, int(size))
    _cache[key] = pix
    return pix


def _load_icon(app_id: str, size: int) -> QPixmap | None:
    try:
        from AppKit import NSWorkspace
    except Exception:  # noqa: BLE001 - 非 mac / 缺 pyobjc
        return None
    try:
        workspace = NSWorkspace.sharedWorkspace()
        url = workspace.URLForApplicationWithBundleIdentifier_(app_id)
        if url is None:
            return None
        image = workspace.iconForFile_(url.path())
        if image is None:
            return None
        data = image.TIFFRepresentation()
        if data is None:
            return None
        img = QImage.fromData(bytes(data), "TIFF")
        if img.isNull():
            return None
        # 按屏幕 DPR 超采样，Retina 下才不会糊成一团
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        ratio = float(app.primaryScreen().devicePixelRatio()) if app and app.primaryScreen() else 2.0
        px = max(1, int(round(size * ratio)))
        scaled = QPixmap.fromImage(img).scaled(
            px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        scaled.setDevicePixelRatio(ratio)
        return scaled
    except Exception as exc:  # noqa: BLE001
        log.debug("取应用图标失败（%s）：%s", app_id, exc)
        return None


def clear_cache() -> None:
    _cache.clear()
