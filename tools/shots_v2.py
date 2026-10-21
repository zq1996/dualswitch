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
from PySide6.QtTest import QTest  # noqa: E402
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
    long_text = (
        "验证：全量 pytest 169 例全绿；界面测试补了文件分类、标题、目录、按钮组合、文件卡、"
        "扩展名字形、引擎不发文件、单击复制的真实鼠标事件。文档同步了 INTERACTIONS-v2.md、"
        "UI-MAC2-IMPLEMENTATION.md 与契约第 11 节，原型 index-v2.html 也按新规则改了。"
        "这一段故意写长，用来确认预览窗会把文字完整显示出来：气泡跟着长高，超屏才内部滚动，一个字都不裁。"
    ) * 3
    return [
        ClipRecord(id="l2", kind="text", text=long_text,
                   size=len(long_text.encode()), state="local", app="WorkBuddy"),
        ClipRecord(id="i1", kind="image", png=png_bytes(640, 400, "#83A8FF"),
                   width=640, height=400, size=1_820_000, state="ok", app="截图"),
        ClipRecord(id="t1", kind="text", text="明天下午三点，会议室 B，记得带转接头和 HDMI 线",
                   size=36, state="ok", app="微信", app_id="com.tencent.xinWeChat"),
        ClipRecord(id="l1", kind="text", text="https://github.com/zq1996/dualswitch",
                   size=38, state="ok", source="remote", peer_name="Windows", app="Chrome"),
        ClipRecord(id="c1", kind="text",
                   text="git rebase -i HEAD~3 && git push --force-with-lease origin main",
                   size=64, state="pend", progress=0.58, app="Terminal",
                   app_id="com.apple.Terminal"),
        # 访达复制的「图片文件」：缩略图是图片内容，标题是文件名，元信息给目录
        ClipRecord(id="f1", kind="mixed", text="同景不同高度：湖畔 pagoda 拼贴画.png",
                   png=png_bytes(420, 280, "#F0E6D2"),
                   width=1086, height=1448, size=248 * 1024, file_size=3 * 1048576,
                   state="local", path="/Users/zhqi/Downloads/照片/同景不同高度：湖畔 pagoda 拼贴画.png",
                   app="访达", app_id="com.apple.finder"),
        # 访达复制的普通文件：缩略图是文件瓷砖，标题是文件名
        ClipRecord(id="f2", kind="text", text="ChatGPTInstaller.dmg",
                   size=15, file_size=849 * 1024, state="local",
                   path="/Users/zhqi/Downloads/ChatGPTInstaller.dmg",
                   app="访达", app_id="com.apple.finder"),
        ClipRecord(id="t2", kind="text", text='{ "hotkey": "alt+space", "sync_clipboard": true }',
                   size=62, state="fail", app="VS Code",
                   app_id="com.microsoft.VSCode"),
    ]


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setFont(theme.qfont(theme.APP_FONT_PX))
    if not OUT.exists():
        OUT.mkdir(parents=True)
    cfg = platform_defaults()

    for mode in ("light",):   # 只有浅色（用户 2026-10-21 去掉深色模式）
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
            QTest.qWait(220)
            cp.preview_controller.open_now(row.record, row.ui_kind, row.thumb)
            app.processEvents()
            win.grab().save(str(OUT / "v2-clipboard-hover.png"))
            cp.preview_controller.bubble.grab().save(str(OUT / "v2-preview-bubble.png"))
            cp.preview_controller.hide_now()

            # 文件行 hover：三个按钮组合（截图=预览 / 图片文件=预览+打开 / 文件=打开）
            row._fade_acts(0.0)          # 先把上一步 t1 的 hover 撤掉
            row._set_hover(0.0)
            for rid in ("f1", "f2"):
                crow = cp._rows[rid]
                crow._fade_acts(1.0)
                crow._acts_effect.setOpacity(1.0)
                crow._set_hover(1.0)
            app.processEvents()
            QTest.qWait(220)      # 等淡入动画跑完再截（否则按钮都是半透明）
            win.grab().save(str(OUT / "v2-clipboard-filehover.png"))

            # 文件卡：访达复制的图片文件 / 普通文件（照 Frset 的文件卡）
            for rid, name in (("f1", "v2-preview-fileimg"), ("f2", "v2-preview-filedoc")):
                crow = cp._rows[rid]
                crow._fade_acts(1.0)
                crow._acts_effect.setOpacity(1.0)
                crow._set_hover(1.0)
                cp.preview_controller.open_now(crow.record, crow.ui_kind, crow.thumb)
                app.processEvents()
                win.grab().save(str(OUT / f"{name}.png"))
                cp.preview_controller.bubble.grab().save(str(OUT / f"{name}.png"))
                cp.preview_controller.hide_now()
                crow._fade_acts(0.0)
                crow._set_hover(0.0)

            # 长文本预览：截断 + 省略号（不能压到底栏）
            lrow = cp._rows["l2"]
            cp.preview_controller.open_now(lrow.record, lrow.ui_kind, lrow.thumb)
            app.processEvents()
            cp.preview_controller.bubble.grab().save(str(OUT / "v2-preview-longtext.png"))
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
