#!/usr/bin/env python3
"""从 design/icon/png/icon-*.png 生成 macOS 应用图标 screen-switch.icns。

为什么不用 sips + iconutil：那套要建临时 .iconset 目录再删掉，在沙箱/CI 里经常被拦，
而且对多版本图标列表的映射规则藏在脚本里。ICNS 本身就是一个「类型 + 长度 + PNG」的
简单容器，直接写 80 行比调两个系统命令更可控，也不依赖 macOS。

用法：
    python design/icon/build_icns.py
产物：
    design/icon/screen-switch.icns
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    raise SystemExit("需要 pillow：pip install pillow")

HERE = Path(__file__).resolve().parent
SRC = HERE / "png"
OUT = HERE / "screen-switch.icns"
MASTER = SRC / "icon-512.png"

# ICNS 类型 → 像素尺寸
#   icp4/icp5  16/32（老系统）
#   ic07/ic08/ic09/ic10   128/256/512/1024
#   ic11/ic12/ic13/ic14   @2x 变体：32/64/256/512
TYPES = [
    ("icp4", 16),
    ("icp5", 32),
    ("ic11", 32),
    ("ic12", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic13", 256),
    ("ic09", 512),
    ("ic14", 512),
    ("ic10", 1024),
]


def _png_bytes(size: int) -> bytes:
    """取指定尺寸的 PNG：已有尺寸就直读，没有就用 512 母版 Lanczos 缩放。"""
    candidate = SRC / f"icon-{size}.png"
    if candidate.exists():
        return candidate.read_bytes()
    if not MASTER.exists():
        raise SystemExit(f"缺少母版 {MASTER}，请先跑 python design/icon/build_icon.py")
    from io import BytesIO

    with Image.open(MASTER) as img:
        resized = img.convert("RGBA").resize((size, size), Image.LANCZOS)
        buf = BytesIO()
        resized.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def build() -> Path:
    elements = []
    for kind, size in TYPES:
        data = _png_bytes(size)
        elements.append(kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data)

    body = b"".join(elements)
    payload = b"icns" + struct.pack(">I", len(body) + 8) + body
    OUT.write_bytes(payload)
    return OUT


def main() -> int:
    if not MASTER.exists():
        print(f"缺少 {MASTER}", file=sys.stderr)
        return 1
    path = build()
    print(f"已生成：{path}（{path.stat().st_size // 1024} KB，{len(TYPES)} 档尺寸）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
