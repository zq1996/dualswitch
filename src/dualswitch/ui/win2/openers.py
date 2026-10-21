"""打开方式按类型分派（设计 §6.4，Windows 口径）。

| 类型 | 动作 | 实现 |
|---|---|---|
| 截图（内存图片，无文件） | 预览 | payload 先落临时 PNG → `os.startfile`（通常是「照片」） |
| 图片文件（有 path） | 预览 + 在资源管理器中显示 | `os.startfile(path)` / `explorer /select,` |
| 普通文件 | 在资源管理器中显示 | `explorer /select,` |
| 链接 | 打开链接 | `webbrowser.open` |
| 文字 / 代码 | 仅复制 | 写回剪贴板（调用方处理） |

与 mac2 的差异只有实现：`open -a Preview` → `os.startfile`、`open -R` → `explorer /select,`、
`NSWorkspace.open` → `webbrowser.open`。语义一一对应，动作集合不变。

契约依据：文件不跨端同步（P2），文件条目只会是本机产生的 → 资源管理器语义成立。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import webbrowser
from pathlib import Path

log = logging.getLogger("dualswitch.ui.win2.openers")

URL_RE = re.compile(r"^(https?|ftp)://\S+$", re.IGNORECASE)

#: 行内/预览窗按钮集合（按类型）
ACTIONS_BY_KIND = {
    # 截图没有文件可打开（设计 §6.4：截图只有「预览」）
    "image": ("preview", "copy"),
    "file": ("explorer", "copy"),
    "link": ("openlink", "copy"),
    "text": ("copy",),
    "code": ("copy",),
}


def classify_record(kind: str, text: str | None, path: str = "") -> str:
    """ClipRecord.kind（text/image/mixed）+ path → UI 类型（text/code/image/link/file）。

    **`path` 优先**：从资源管理器复制的文件（含图片文件）都是「文件」卡片；
    `path` 为空才是截图 / 网页图片 / 纯文本。
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
# 系统适配（Windows）——注入替身即可单测
# --------------------------------------------------------------------------- #
class WindowsOpeners:
    """图片→默认看图；文件→资源管理器定位；链接→默认浏览器。

    三个系统调用都可注入（`runner` / `startfile` / `browser`），单测不需要真开窗口。
    """

    def __init__(self, runner=None, startfile=None, browser=None):  # noqa: ANN001
        self._run = runner or subprocess.Popen
        self._startfile = startfile or _os_startfile
        self._browser = browser or webbrowser.open

    # ---- 图片 ----
    def open_image_preview(self, png: bytes, name: str = "clip.png") -> str:
        """把 payload 落临时文件后交给系统默认图片查看器。"""
        target = self._temp_png(png, name)
        self._startfile(str(target))
        return str(target)

    def _temp_png(self, png: bytes, name: str) -> Path:
        d = Path(tempfile.gettempdir()) / "dualswitch-preview"
        d.mkdir(parents=True, exist_ok=True)
        target = d / name
        target.write_bytes(png)
        return target

    def open_image_path(self, path: str) -> str:
        """原图就在本机（资源管理器复制的图片文件）→ 直接交给默认看图。"""
        self._startfile(str(path))
        return str(path)

    # ---- 文件 ----
    def reveal_in_explorer(self, path: str) -> str:
        """在资源管理器中显示（选中该文件）。

        `explorer /select,<path>` 是 Windows 的「定位到文件」；文件已经被删掉时
        退一步打开所在目录，总比什么都不发生强（用户点按钮要有反馈）。
        """
        target = str(path)
        if target and os.path.exists(target):
            self._run(["explorer", "/select,", target])
            return target
        folder = os.path.dirname(target)
        if folder and os.path.isdir(folder):
            self._run(["explorer", folder])
        else:
            log.warning("文件已不存在，无法定位：%s", target)
        return target

    # ---- 链接 ----
    def open_url(self, url: str) -> str:
        self._browser(url)
        return url


def _os_startfile(path: str) -> None:
    """`os.startfile` 只在 Windows 上存在，单独包一层便于测试替换。"""
    os.startfile(path)  # type: ignore[attr-defined]
