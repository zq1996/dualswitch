"""把用户提供的「双屏切换」插画抠成透明背景，并生成全套图标。

源图：白色底 JPG（1254×1254，非透明）。

抠图不用「泛洪」，改用亮度 + 饱和度双判据，原因是这张图机身是浅蓝白、
箭头更是接近纯白，单纯按白底泛洪会把机身一起吃掉：

    w = clamp((252 - L) / 26, 0, 1)     # 越白越接近 1
    g = clamp(S / 16, 0, 1)             # 有颜色（饱和度）越接近 1
    alpha = 255 * (1 - w * (1 - g))

  · 纯白背景  L≈254,S≈0   → alpha 0
  · 机身浅蓝  L≈238,S≈18  → alpha 255（被饱和度保住）
  · 柔和投影  L≈235,S≈3   → alpha ≈120（半透明，阴影自然淡出）
  · 深色屏幕  L≈40        → alpha 255

然后收缩 1px + 羽化去白边，按内容边界裁正方形居中，再降采样到各尺寸。

用法：
  python design/icon/build_icon.py            # 由 source/ 母版重新出全套
  python design/icon/build_icon.py --cutout   # 重新抠图（用 --src 指定源图）

依赖：Pillow。
"""

from __future__ import annotations

import argparse
import io
import struct
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
PNG_DIR = HERE / "png"
SOURCE_DIR = HERE / "source"
MASTER = SOURCE_DIR / "screen-switch.png"
DEFAULT_SRC = Path(
    r"C:\Users\Administrator\.workbuddy-ai\clipboard-images"
    r"\clipboard-2026-10-17T08-45-21-921Z-cba9a7e4.jpg"
)

FLOOD_THRESH = 42     # 已废弃，保留常量供旧参数说明
WHITE_REF = 252       # 视为纯白的亮度基准
WHITE_SPAN = 26       # 从白到「不透明」的亮度跨度
SAT_SPAN = 16         # 饱和度达到该值即视为「有内容」
EDGE_MARGIN = 0.02    # 裁切后四周留白比例
SMALL_MAX = 24        # ≤ 该尺寸改用简化版（见 render_small）


def _alpha_from_whiteness(src: Image.Image) -> Image.Image:
    """亮度 + 饱和度双判据求 alpha：纯白透明、浅蓝机身保留、投影半透明。"""
    r, g, b = src.split()
    lum = src.convert("L")

    # 饱和度代理：max - min
    hi = ImageChops.lighter(ImageChops.lighter(r, g), b)
    lo = ImageChops.darker(ImageChops.darker(r, g), b)
    sat = ImageChops.subtract(hi, lo)

    w = lum.point(lambda p: int(max(0.0, min(1.0, (WHITE_REF - p) / WHITE_SPAN)) * 255))
    g_ = sat.point(lambda p: int(max(0.0, min(1.0, p / SAT_SPAN)) * 255))

    # alpha = 1 - (1-d)*(1-g)：纯白为 0，有颜色的浅色机身直接拉满，投影落在中间
    inv = ImageChops.multiply(
        ImageChops.invert(w), ImageChops.invert(g_)
    )
    return ImageChops.invert(inv)


def cutout(src_path: Path, out_path: Path) -> Image.Image:
    src = Image.open(src_path).convert("RGB")
    w, h = src.size

    alpha = _alpha_from_whiteness(src)

    # 收缩 + 羽化，压掉贴边白晕
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.0))

    out = src.convert("RGBA")
    out.putalpha(alpha)

    # 按内容边界裁正方形并居中
    bbox = alpha.point(lambda p: 255 if p > 8 else 0).getbbox()
    x0, y0, x1, y1 = bbox
    side = max(x1 - x0, y1 - y0)
    half = side // 2 + int(side * EDGE_MARGIN)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top = int(cx - half), int(cy - half)

    canvas = Image.new("RGBA", (half * 2, half * 2), (0, 0, 0, 0))
    crop = out.crop(
        (max(left, 0), max(top, 0), min(left + half * 2, w), min(top + half * 2, h))
    )
    canvas.paste(crop, (max(0, -left), max(0, -top)))

    master = canvas.resize((1024, 1024), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    master.save(out_path)
    return master


def render(master: Image.Image, size: int) -> Image.Image:
    img = master.resize((size, size), Image.LANCZOS)
    if size <= 128:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=2))
    return img


# --------------------------------------------------------------------------- #
# 小尺寸专用简化版（16 / 20 / 24）
# --------------------------------------------------------------------------- #
# 母版里的两条浅蓝箭头在这个尺寸下只有 1px 宽、且接近纯白，缩下去就成了一团灰雾，
# 反而把两块屏幕糊掉。小尺寸按图标设计的常规做法做减法：只留「两块屏幕 + 对角位移」，
# 去掉箭头、高光与内屏渐变，并把几何对齐到像素网格。
SMALL_BODY_BACK = (233, 239, 249, 255)
SMALL_BODY_FRONT = (246, 250, 255, 255)
SMALL_BORDER = (170, 189, 217, 255)
SMALL_SCREEN_DARK = (43, 48, 60, 255)
SMALL_SCREEN_BLUE = (46, 138, 240, 255)


def render_small(size: int) -> Image.Image:
    """≤24px 用：两块屏幕的对角构图，4 倍超采样后降采样。"""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def rrect(x0, y0, x1, y1, radius, fill, outline=None, width=0):
        d.rounded_rectangle(
            [x0 * s, y0 * s, x1 * s, y1 * s],
            radius=radius * s,
            fill=fill,
            outline=outline,
            width=width,
        )

    edge = max(1, round(s / 24))

    # 后屏（深色），左上
    rrect(0.055, 0.135, 0.615, 0.585, 0.065, SMALL_BODY_BACK, SMALL_BORDER, edge)
    rrect(0.105, 0.185, 0.565, 0.505, 0.035, SMALL_SCREEN_DARK)

    # 前屏（蓝色），右下，压住后屏一角
    rrect(0.365, 0.395, 0.945, 0.845, 0.065, SMALL_BODY_FRONT, SMALL_BORDER, edge)
    rrect(0.415, 0.445, 0.895, 0.765, 0.035, SMALL_SCREEN_BLUE)

    return img.resize((size, size), Image.LANCZOS)


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
                0, 0, 1, 32, len(blob), offset,
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="源图（白底 JPG/PNG）")
    ap.add_argument("--cutout", action="store_true", help="重新抠图")
    args = ap.parse_args()

    if args.cutout or not MASTER.exists():
        master = cutout(Path(args.src), MASTER)
        print(f"抠图完成 -> {MASTER}")
    else:
        master = Image.open(MASTER).convert("RGBA")

    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256, 512]
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    images = {
        s: (render_small(s) if s <= SMALL_MAX else render(master, s)) for s in sizes
    }
    for size, img in images.items():
        img.save(PNG_DIR / f"icon-{size}.png")

    write_ico(HERE / "screen-switch.ico", {s: images[s] for s in sizes if s <= 256})
    print(f"生成 {len(sizes)} 档 PNG -> {PNG_DIR}")
    print(f"生成 ICO -> {HERE / 'screen-switch.ico'}")


if __name__ == "__main__":
    main()
