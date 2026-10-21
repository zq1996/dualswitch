#!/usr/bin/env python
"""把开源图标库的 SVG 抽成 `iconlib.py`（内联路径数据，运行时不联网）。

来源（均为可商用的开源许可，署名见生成文件头）：
  · Lucide    —— ISC，24×24 线性描边图标，UI 控件用
  · Simple Icons —— CC0，品牌 logo（Apple / Windows），平台角标用

用法（需要联网，只在换图标时跑一次）：
    python tools/gen_iconlib.py --src /tmp/icon-src

`--src` 目录下按 `<name>.svg` 放好文件；下载命令见文件末尾。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "dualswitch" / "ui" / "mac2" / "iconlib.py"

LUCIDE = [
    "arrow-left-right", "clipboard", "settings", "search", "pause", "play",
    "trash-2", "copy", "image", "file-text", "link-2", "folder-open", "x",
    "external-link", "check", "monitor", "app-window", "power", "download",
    "chevron-right", "eye", "scan-eye", "folder", "type", "code",
    # 文件卡片 / 文件缩略图（按扩展名分派：普通 / 图片 / 压缩包）
    "file", "file-image", "file-archive", "file-down", "hard-drive",
]

FILLED = {
    "apple": "apple.svg",
    "windows": "windows.svg",
}

LICENSE = '''"""图标路径数据（自动生成，勿手改）。

两份开源图标库的内联副本，运行时不联网、不依赖外部资源：

  · **Lucide** —— https://lucide.dev · ISC License
    24×24 线性描边图标（`stroke="currentColor"`），渲染时由 `icons.py` 注入颜色。
  · **Simple Icons** —— https://simpleicons.org · CC0 1.0
    24×24 实心品牌 logo（Apple / Windows 平台标识）。

生成脚本：`tools/gen_iconlib.py`。
"""
'''


def inner(svg: str) -> str:
    body = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    body = re.sub(r"^.*?<svg\b[^>]*>", "", body, flags=re.S)
    body = re.sub(r"</svg>\s*$", "", body, flags=re.S)
    body = re.sub(r"<title>.*?</title>", "", body, flags=re.S)
    body = body.strip()
    body = re.sub(r"\s+", " ", body)
    body = re.sub(r">\s+<", "><", body)
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="放 svg 的目录")
    ap.add_argument("--lucide-dir", default="lucide")
    args = ap.parse_args()
    src = Path(args.src)

    lines = [LICENSE, "", "LUCIDE: dict[str, str] = {"]
    for name in LUCIDE:
        path = src / args.lucide_dir / f"{name}.svg"
        if not path.exists():
            raise SystemExit(f"缺少 {path}")
        lines.append(f'    "{name}": {inner(path.read_text(encoding="utf-8"))!r},')
    lines += ["}", "", "FILLED: dict[str, str] = {"]
    for name, filename in FILLED.items():
        path = src / filename
        if not path.exists():
            raise SystemExit(f"缺少 {path}")
        lines.append(f'    "{name}": {inner(path.read_text(encoding="utf-8"))!r},')
    lines += ["}", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {OUT}（Lucide {len(LUCIDE)} 个 + 品牌 {len(FILLED)} 个）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
