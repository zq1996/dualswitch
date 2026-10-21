"""应用图标（来源应用那一栏用，设计 §2「来源应用图标」）。

剪贴板列表的元信息是 `Windows · 微信 · 3 分钟前 · 36 B · 纯文本`，其中「微信」希望带
应用图标；截图这类没有应用的，就只写文字（"截图"）。

Windows 做法：`app_id` 在 Windows 后端里就是**进程 exe 路径** →
`QFileIconProvider` 取图标 → 按 DPR 超采样成 QPixmap。
取不到就返回 None（调用方只画文字），**不抛异常**。

> 设计 §6.9 已写明：Windows 后端目前不填 `app / app_id`，所以这一栏会自动退化为
> 纯文字（不占位、不留空）。后端补齐后这里直接生效，UI 不用改。
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QFileInfo, QSize
from PySide6.QtGui import QPixmap

log = logging.getLogger("dualswitch.ui.win2.apps")

_cache: dict[tuple[str, int], QPixmap | None] = {}


def app_icon(app_id: str, size: int = 14) -> QPixmap | None:
    """按 exe 路径取应用图标；拿不到返回 None。"""
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
        from PySide6.QtWidgets import QApplication, QFileIconProvider
    except Exception:  # noqa: BLE001 - 非 Windows / 缺 Qt
        return None

    path = app_id
    if not os.path.isabs(path) or not os.path.exists(path):
        # 允许直接传 exe 名（后端将来可能只给 basename）：按 PATH 找一次
        resolved = _which(path)
        if not resolved:
            return None
        path = resolved
    try:
        icon = QFileIconProvider().icon(QFileInfo(path))
        if icon.isNull():
            return None
        app = QApplication.instance()
        ratio = 2.0
        if app is not None and app.primaryScreen() is not None:
            ratio = float(app.primaryScreen().devicePixelRatio()) or 1.0
        px = max(1, int(round(size * ratio)))
        pm = icon.pixmap(QSize(px, px))
        if pm.isNull():
            return None
        pm.setDevicePixelRatio(ratio)
        return pm
    except Exception as exc:  # noqa: BLE001
        log.debug("取应用图标失败（%s）：%s", app_id, exc)
        return None


def _which(name: str) -> str:
    """exe 名 → 绝对路径（不启动进程，只查 PATH）。"""
    if not name:
        return ""
    from shutil import which

    found = which(name)
    return found or ""


def clear_cache() -> None:
    _cache.clear()
