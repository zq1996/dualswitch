"""剪贴板抽象层。

`base` 是两端共用的冻结契约；`macos` / `windows` 是各自的平台实现（各自独占文件）。

    from dualswitch.clipboard import create_backend, make_text

    cb = create_backend("auto")
    item = cb.poll()
    if item is not None:
        ...
"""

from __future__ import annotations

from .base import (
    MAX_IMAGE_BYTES,
    MAX_TEXT_BYTES,
    SKIP_MARKERS_MACOS,
    SKIP_MARKERS_WINDOWS,
    ClipItem,
    ClipboardBackend,
    ClipboardError,
    ImageData,
    NullBackend,
    create_backend,
    make_image,
    make_text,
)

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_TEXT_BYTES",
    "SKIP_MARKERS_MACOS",
    "SKIP_MARKERS_WINDOWS",
    "ClipItem",
    "ClipboardBackend",
    "ClipboardError",
    "ImageData",
    "NullBackend",
    "create_backend",
    "make_image",
    "make_text",
]
