#!/usr/bin/env python3
"""应用图标：底板 + 白色显示器 + 屏内两支箭头。

几何**逐字取自** `outputs/frset-icon/short-final.html` 里「左右各留 12%」那一版
（箭头组 `translate(512,478) scale(0.8107)`），不做任何改动。
配色与网格参照本机 Frset 的实测参数：画布 1024 / 主体 824 / 圆角 200 /
底 #22113D / 蓝 #2660FF / 琥珀 #FFB200。

用法：
    python3 design/icon/build_icon.py
产物：
    design/icon/icon-master.svg         母版（1024，可直接在浏览器里看）
    design/icon/png/icon-*.png          16…1024 共 11 档
    design/icon/screen-switch.ico
    design/icon/screen-switch.icns
"""

from __future__ import annotations

import struct
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PNG_DIR = HERE / "png"
MASTER_SVG = HERE / "icon-master.svg"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# ---- 画面内容（原样照抄，勿改） ---------------------------------------------
BODY = (
    '<rect x="100" y="100" width="824.0" height="824.0" rx="200" fill="#22113D"/>'
    '<rect x="470.0" y="688.0" width="84" height="36" rx="18.0" fill="#FFFFFF"/>'
    '<rect x="362.0" y="712.0" width="300" height="34" rx="17.0" fill="#FFFFFF"/>'
    '<rect x="192.0" y="278.0" width="640" height="400" rx="46" fill="#FFFFFF"/>'
    '<g transform="translate(512.00,478.00) scale(0.8107)">'
    '<path d="M 200.00 -76.00 L -200.00 -76.00 L -120.33 -142.85" fill="none" '
    'stroke="#2660FF" stroke-width="100" stroke-linecap="round" stroke-linejoin="miter" '
    'stroke-miterlimit="18"/>'
    '<path d="M -200.00 76.00 L 200.00 76.00 L 120.33 142.85" fill="none" '
    'stroke="#FFB200" stroke-width="100" stroke-linecap="round" stroke-linejoin="miter" '
    'stroke-miterlimit="18"/>'
    "</g>"
)


def build_svg(size: int) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 1024 1024">{BODY}</svg>')


def rasterize(sizes: list[int]) -> None:
    """矢量直接栅格化到每档尺寸，保留透明背景。"""
    if not Path(CHROME).exists():
        raise SystemExit(f"找不到 Chrome：{CHROME}")
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        for n in sizes:
            html = tmpd / f"i{n}.html"
            html.write_text(
                "<!doctype html><meta charset=utf-8>"
                "<style>html,body{margin:0;padding:0;background:transparent}"
                f"svg{{display:block}}</style>{build_svg(n)}"
            )
            out = PNG_DIR / f"icon-{n}.png"
            subprocess.run(
                [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
                 "--disable-software-rasterizer", "--disable-dev-shm-usage",
                 "--hide-scrollbars", "--default-background-color=00000000",
                 "--force-device-scale-factor=1", f"--window-size={n},{n}",
                 f"--screenshot={out}", f"file://{html}"],
                check=True, capture_output=True,
            )
            if not out.exists():
                raise SystemExit(f"栅格化失败：{n}px")
    MASTER_SVG.write_text(build_svg(1024))


def write_ico(path: Path, entries: list[tuple[int, Path]]) -> None:
    blobs = [(n, p.read_bytes()) for n, p in entries]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = len(header) + 16 * len(blobs)
    dirs = data = b""
    for n, blob in blobs:
        w = 0 if n >= 256 else n
        dirs += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        data += blob
    path.write_bytes(header + dirs + data)


ICNS_TYPES = (
    ("icp4", 16), ("icp5", 32), ("icp6", 64),
    ("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
    ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512),
)


def write_icns(path: Path, png: dict[int, Path]) -> None:
    body = b""
    for code, px in ICNS_TYPES:
        if px not in png:
            continue
        blob = png[px].read_bytes()
        body += code.encode("ascii") + struct.pack(">I", len(blob) + 8) + blob
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> None:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256, 512, 1024]
    rasterize(sizes)
    print(f"PNG {len(sizes)} 档 -> {PNG_DIR}")
    files = {n: PNG_DIR / f"icon-{n}.png" for n in sizes}
    write_ico(HERE / "screen-switch.ico", [(n, files[n]) for n in sizes if n <= 256])
    print(f"ICO  -> {HERE / 'screen-switch.ico'}")
    write_icns(HERE / "screen-switch.icns", files)
    print(f"ICNS -> {HERE / 'screen-switch.icns'}")


if __name__ == "__main__":
    main()
