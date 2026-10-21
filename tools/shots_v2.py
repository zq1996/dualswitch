#!/usr/bin/env python
"""v2 界面出图（offscreen 渲染，不弹窗、不碰真实剪贴板）。

用法：
    QT_QPA_PLATFORM=offscreen python tools/shots_v2.py

产出 `outputs/v2-*.png`：切换页 / 剪贴板页（明暗）/ 设置页 / 预览气泡 / 空态。
用来和 `design/redesign/index-v2.html` 逐像素对照。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dualswitch.config import platform_defaults  # noqa: E402
from dualswitch.sync.engine import ClipRecord  # noqa: E402
from dualswitch.ui.mac2 import icons, theme  # noqa: E402
from dualswitch.ui.mac2.clipboard_page import ClipboardPage  # noqa: E402
from dualswitch.ui.mac2.settings_page import SettingsPage  # noqa: E402
from dualswitch.ui.mac2.switch_page import SwitchPage  # noqa: E402
from dualswitch.ui.mac2.window import MainWindow  # noqa: E402

OUT = ROOT / "outputs"


def png_bytes(w: int, h: int, color: str) -> bytes:
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(color))
    p = QPainter(img)
    p.setPen(QColor("#FFFFFF"))
    p.setBrush(QColor("#4E87FF"))
    p.drawRoundedRect(24, 24, w - 48, h - 48, 12, 12)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


class FakeEngine:
    def __init__(self, records):
        self._records = list(records)

    def records(self):
        return list(self._records)

    def status(self):
        return {"authenticated": "yes"}

    def copy_to_local(self, _rid):
        return True

    def repush(self, _rid):
        pass

    def drop_record(self, rid):
        self._records = [r for r in self._records if r.id != rid]

    def clear_records(self):
        self._records = []


def records() -> list[ClipRecord]:
    return [
        ClipRecord(id="i1", kind="image", png=png_bytes(96, 64, "#83A8FF"),
                   width=96, height=64, size=1_820_000, state="ok"),
        ClipRecord(id="t1", kind="text", text="明天下午三点，会议室 B，记得带转接头和 HDMI 线",
                   size=36, state="ok"),
        ClipRecord(id="l1", kind="text", text="https://github.com/zq1996/dualswitch",
                   size=38, state="ok", source="remote", peer_name="Windows"),
        ClipRecord(id="c1", kind="text",
                   text="git rebase -i HEAD~3 && git push --force-with-lease origin main",
                   size=64, state="pend", progress=0.58),
        ClipRecord(id="f1", kind="text", text="需求评审纪要 v3.docx",
                   detail="~/Documents/需求评审纪要 v3.docx", size=248 * 1024, state="local"),
        ClipRecord(id="t2", kind="text", text='{ "hotkey": "alt+space", "sync_clipboard": true }',
                   size=62, state="fail"),
    ]


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setFont(theme.qfont(theme.APP_FONT_PX))
    if not OUT.exists():
        OUT.mkdir(parents=True)
    cfg = platform_defaults()

    for mode in ("light", "dark"):
        theme.set_appearance(mode)
        icons.clear_cache()
        app.setStyleSheet(theme.qss())

        win = MainWindow(cfg, "Right Option+Space", "v1.0.0", "Q2790R3")
        sp = SwitchPage(cfg, "Right Option+Space", "Q2790R3")
        cp = ClipboardPage(FakeEngine(records()))
        st = SettingsPage(cfg, ROOT / "build" / "tmp" / "cfg.json")
        win.attach_pages(sp, cp, st)
        win.show()
        win.set_side_status("已连接 Windows", True)
        win.set_footer("对端在线 · Windows", True)
        app.processEvents()

        suffix = "" if mode == "light" else "-dark"

        win.show_page(0)
        app.processEvents()
        win.grab().save(str(OUT / f"v2-switch{suffix}.png"))

        win.show_page(1)
        cp.refresh()
        app.processEvents()
        win.grab().save(str(OUT / f"v2-clipboard{suffix}.png"))

        win.show_page(2)
        app.processEvents()
        win.grab().save(str(OUT / f"v2-settings{suffix}.png"))

        if mode == "light":
            # 悬停态：行内动作 + 预览气泡
            win.show_page(1)
            app.processEvents()
            row = cp._rows["t1"]
            row._fade_acts(1.0)
            row._acts_effect.setOpacity(1.0)
            row._set_hover(1.0)
            app.processEvents()
            cp.preview_controller.open_now(row.record, row.ui_kind, row.thumb)
            app.processEvents()
            win.grab().save(str(OUT / "v2-clipboard-hover.png"))
            cp.preview_controller.bubble.grab().save(str(OUT / "v2-preview-bubble.png"))
            cp.preview_controller.hide_now()

            # 空态
            empty_page = ClipboardPage(FakeEngine([]))
            empty_page.resize(592, 500)
            empty_page.show()
            app.processEvents()
            empty_page.grab().save(str(OUT / "v2-empty.png"))
            empty_page.close()

        win.close()
        win.deleteLater()

    print("已出图到", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
