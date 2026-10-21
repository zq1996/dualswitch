"""打开方式按类型分派（INTERACTIONS §3.3）。

| 类型 | 动作 | 实现 |
|---|---|---|
| 截图（内存图片，无文件） | 预览 | Preview.app（payload 先落临时 PNG）；**没有「打开文件」** |
| 图片文件（访达复制） | 预览 + 打开文件 | 预览开原图；打开文件 = 在访达中显示 |
| 普通文件 | 打开文件 | 在访达中显示；**没有「预览」** |
| 链接 | 打开链接 | `NSWorkspace.open` |
| 文字 | 仅复制 | 写回剪贴板（调用方处理） |

契约依据：文件不跨端同步（P2），文件条目只会是本机产生的 → 访达语义成立；
图片可能来自对端内存 PNG payload → 用「看图」打开临时文件。
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("dualswitch.ui.mac2.openers")

URL_RE = re.compile(r"^(https?|ftp)://\S+$", re.IGNORECASE)

#: 行内/预览窗按钮集合（按类型）
ACTIONS_BY_KIND = {
    # 截图没有文件可打开（用户 2026-10-21：截图有预览、没有打开文件）
    "image": ("preview", "copy"),
    "file": ("finder", "copy"),
    "link": ("openlink", "copy"),
    "text": ("copy",),
    "code": ("copy",),
}


def classify_record(kind: str, text: str | None, path: str = "") -> str:
    """ClipRecord.kind（text/image/mixed）+ path → UI 类型（text/code/image/link/file）。

    **`path` 优先**：从访达复制的文件（含图片文件）都是「文件」卡片；
    `path` 为空才是截图 / 网页图片 / 纯文本。这是 Frset 的分类法，用户 2026-10-21 指定。
    """
    if path:
        return "file"
    if kind in ("image", "mixed"):
        return "image"
    body = (text or "").strip()
    if body and URL_RE.match(body):
        return "link"
    if _looks_like_code(body):
        return "code"
    return "text"


def _looks_like_code(text: str) -> bool:
    """粗判代码：等宽语法信号（花括号 / shebang / 赋值行 / 常见命令行）。"""
    if not text or len(text) > 4000:
        return False
    head = text[:400]
    signals = (
        head.startswith("#!"),
        "def " in head and ":" in head,
        "function " in head,
        "=> " in head or " -> " in head,
        "{" in head and "}" in head and ":" in head,
        head.startswith(("git ", "cd ", "npm ", "pip ", "docker ", "sudo ", "ls ", "curl ")),
        "\n" in head and any(line.strip().startswith(("//", "# ", "/*", "* ")) for line in head.splitlines()),
    )
    return sum(1 for s in signals if s) >= 1


def actions_for(ui_kind: str) -> tuple[str, ...]:
    return ACTIONS_BY_KIND.get(ui_kind, ("copy",))


# --------------------------------------------------------------------------- #
# 系统适配（macOS）——注入替身即可单测
# --------------------------------------------------------------------------- #
class MacOpeners:
    """图片→Preview.app；文件→访达定位；链接→默认浏览器。"""

    def __init__(self, runner=None):  # noqa: ANN001 - 可注入 subprocess 替身
        self._run = runner or subprocess.Popen

    # ---- 图片 ----
    def open_image_preview(self, png: bytes, name: str = "clip.png") -> str:
        """把 payload 落临时文件后交给 Preview.app（系统看图，不是访达）。"""
        target = self._temp_png(png, name)
        self._run(["open", "-a", "Preview", str(target)])
        return str(target)

    def _temp_png(self, png: bytes, name: str) -> Path:
        d = Path(tempfile.gettempdir()) / "dualswitch-preview"
        d.mkdir(parents=True, exist_ok=True)
        target = d / name
        target.write_bytes(png)
        return target

    def open_image_path(self, path: str) -> str:
        """原图就在本机（访达复制的图片文件）→ 直接交给 Preview，不落临时文件。"""
        self._run(["open", "-a", "Preview", str(path)])
        return str(path)

    # ---- 文件 ----
    def reveal_in_finder(self, path: str) -> str:
        """在访达中显示（选中该文件）。`open -R` 即 NSWorkspace 的 activateFileViewerSelecting。"""
        self._run(["open", "-R", str(path)])
        return str(path)

    # ---- 链接 ----
    def open_url(self, url: str) -> str:
        self._run(["open", str(url)])
        return url
