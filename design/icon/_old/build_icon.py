"""生成「屏幕切换」应用图标。

设计语义：
  · 圆角方砖 = Windows 应用身份（Fluent 图标规范，圆角 22%）
  · 屏幕轮廓 = 显示器本体（这台屏是唯一被控制的设备）
  · 双向箭头 = 输入源在两端之间互换（本工具唯一的动作）
  · 深蓝 + 右下角青光 = 两端：Windows（蓝）/ Mac（青）

产出：
  png/icon-{16,20,24,32,40,48,64,128,256,512}.png
  screen-switch.ico   （Windows 多尺寸，PNG 压缩，Vista+ 可用）
  icon.svg            （矢量母版）

小尺寸（≤32px）自动切「简化字形」：去掉底座、放大屏幕、加粗箭头并拉开间距，
否则支架和箭头的缝隙在 16px 会糊成一团。

用法：python design/icon/build_icon.py
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
PNG_DIR = HERE / "png"

GRID = 512          # 设计栅格：所有几何都写在这个坐标系里
SUPERSAMPLE = 2     # 2 倍超采样后降采样，边缘更干净

WHITE = (255, 255, 255, 255)
BASE_TOP = (61, 107, 245)
BASE_BOTTOM = (23, 52, 160)
GLOW = (13, 176, 164)
RADIUS_RATIO = 0.222


# --------------------------------------------------------------------------- #
# 底：圆角方砖 + 深蓝渐变 + 右下角青光
# --------------------------------------------------------------------------- #
def _tile(n: int) -> Image.Image:
    v = Image.linear_gradient("L").resize((n, n), Image.BILINEAR)
    base = Image.composite(
        Image.new("RGB", (n, n), BASE_BOTTOM),
        Image.new("RGB", (n, n), BASE_TOP),
        v,
    )

    # 右下角径向青光（比整体渐变克制，只在角落发光）
    g = Image.radial_gradient("L").resize((n * 2, n * 2), Image.BICUBIC)
    glow_mask = Image.new("L", (n, n), 0)
    glow_mask.paste(g, (int(n * -0.35), int(n * -0.45)))
    glow_mask = glow_mask.point(lambda p: int(p * 0.62))
    tile = Image.composite(Image.new("RGB", (n, n), GLOW), base, glow_mask)

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, n - 1, n - 1), radius=int(n * RADIUS_RATIO), fill=255
    )
    out = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


# --------------------------------------------------------------------------- #
# 字形：屏幕 + 双向箭头
# --------------------------------------------------------------------------- #
def _glyph(n: int, size: int) -> Image.Image:
    """三档字形：tiny(≤20) 只留箭头 / compact(24–32) 屏幕+箭头 / full(≥40) 加底座。"""
    s = n / GRID
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def sc(v: float) -> int:
        return int(round(v * s))

    if size <= 20:
        # 极小尺寸：屏幕上的一切细节都会糊掉，只留一个「双向互换」箭头
        draw_frame = False
        stroke = 46
        box = radius = 0
        axis = 256
        left, right = 158, 354
        head, head_len = 66, 62
    elif size <= 32:
        # 简化字形：屏幕放大、去掉底座、笔画加粗，箭头与边框留足呼吸
        draw_frame = True
        stroke = 34
        box = (104, 120, 408, 356)
        radius = 34
        axis = 238
        left, right = 186, 326
        head, head_len = 32, 34
    else:
        # 完整字形：屏幕 + 支架 + 居中双向箭头
        draw_frame = True
        stroke = 20
        box = (113, 142, 399, 318)
        radius = 16
        axis = 230
        left, right = 176, 338
        head, head_len = 28, 30

    if draw_frame:
        d.rounded_rectangle(
            tuple(sc(v) for v in box), radius=sc(radius), outline=WHITE, width=sc(stroke)
        )

    if size > 32:
        # 支架：颈 + 底座
        d.rounded_rectangle(tuple(sc(v) for v in (240, 318, 272, 342)), radius=sc(3), fill=WHITE)
        d.rounded_rectangle(tuple(sc(v) for v in (202, 340, 310, 358)), radius=sc(7), fill=WHITE)

    # 双向箭头 ↔：一条横线，两端各一个箭头
    d.line([(sc(left), sc(axis)), (sc(right), sc(axis))], fill=WHITE, width=sc(stroke))
    for tip_x, base_x in ((left - head_len, left), (right + head_len, right)):
        d.line(
            [(sc(base_x), sc(axis - head)), (sc(tip_x), sc(axis)), (sc(base_x), sc(axis + head))],
            fill=WHITE,
            width=sc(stroke),
            joint="curve",
        )
    return layer


def render(size: int) -> Image.Image:
    n = GRID * SUPERSAMPLE
    img = _tile(n)
    glyph = _glyph(n, size)

    # 字形轻微投影，让它从底色上浮起来（小尺寸不加，否则边缘发脏）
    if size <= 32:
        return Image.alpha_composite(img, glyph).resize((size, size), Image.LANCZOS)

    alpha = glyph.split()[3]
    shadow = alpha.filter(ImageFilter.GaussianBlur(n * 0.010))
    shadow = shadow.point(lambda p: int(p * 0.20))
    shadow_layer = Image.new("RGBA", (n, n), (5, 14, 40, 255))
    shadow_layer.putalpha(shadow)
    img = Image.alpha_composite(img, ImageChops.offset(shadow_layer, 0, int(n * 0.006)))
    img = Image.alpha_composite(img, glyph)

    return img.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# ICO：手写容器，才能给每个尺寸塞不同字形
# --------------------------------------------------------------------------- #
def write_ico(path: Path, images: dict[int, Image.Image]) -> None:
    entries, blobs, offset = [], [], 6 + 16 * len(images)
    for size in sorted(images):
        buf = io.BytesIO()
        images[size].save(buf, format="PNG", optimize=True)
        blob = buf.getvalue()
        entries.append(
            struct.pack(
                "<BBBBHHII",
                0 if size >= 256 else size,
                0 if size >= 256 else size,
                0,
                0,
                1,
                32,
                len(blob),
                offset,
            )
        )
        blobs.append(blob)
        offset += len(blob)

    with path.open("wb") as fh:
        fh.write(struct.pack("<HHH", 0, 1, len(images)))
        for entry in entries:
            fh.write(entry)
        for blob in blobs:
            fh.write(blob)


def main() -> None:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256, 512]
    images = {size: render(size) for size in sizes}

    for size, img in images.items():
        img.save(PNG_DIR / f"icon-{size}.png")

    write_ico(HERE / "screen-switch.ico", {s: images[s] for s in sizes if s <= 256})
    print(f"生成 {len(sizes)} 个 PNG -> {PNG_DIR}")
    print(f"生成 ICO -> {HERE / 'screen-switch.ico'}")


if __name__ == "__main__":
    main()
