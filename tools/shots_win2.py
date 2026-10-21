#!/usr/bin/env python
"""Windows2 界面出图（不出现在屏幕上、不碰真实剪贴板、不发 DDC）。

用法：
    python tools/shots_win2.py

产出 `build/win2-shots/win2-*.png`（设计 §10.4 的清单）：
    win2-switch / win2-switch-unknown / win2-clipboard / win2-preview-text /
    win2-preview-file / win2-settings / win2-confirm / win2-tray-menu
另加三张辅助图：win2-clipboard-hover（行内动作 + 选中态）、win2-settings-bottom
（高级组）、win2-tray-menu-unknown（未检测到时的置灰行）。

用来和 `design/windows2/index.html` 逐块对照版面。

> **为什么不用 `QT_QPA_PLATFORM=offscreen`**：offscreen 插件的字体库是空的
> （`QFontDatabase.families() == 0`，设 `QT_QPA_FONTDIR` 也只认单文件字体，
> 读不了 `msyh.ttc`），中文全是豆腐块。改用正常 windows 平台 +
> `WA_DontShowOnScreen`：窗口不映射到屏幕，但字体、DPR、QSS 全是真实环境。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.pop("QT_QPA_PLATFORM", None)      # 见模块说明：要真字体，不能用 offscreen

from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from dualswitch.config import SOURCE_UNKNOWN, platform_defaults  # noqa: E402
from dualswitch.sync.engine import ClipRecord  # noqa: E402
from dualswitch.ui.win2 import theme, tray  # noqa: E402
from dualswitch.ui.win2.clipboard_page import ClipboardPage  # noqa: E402
from dualswitch.ui.win2.runner import ConfirmOverlay  # noqa: E402
from dualswitch.ui.win2.settings_page import SettingsPage  # noqa: E402
from dualswitch.ui.win2.switch_page import SwitchPage  # noqa: E402
from dualswitch.ui.win2.window import MainWindow  # noqa: E402

OUT = ROOT / "build" / "win2-shots"

HOTKEY = "Alt+Space"


def png_bytes(w: int, h: int, color: str) -> bytes:
    """造一张「图片」payload：底色 + 一个白色圆角块，够看出 cover 裁切。"""
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
        return {"authenticated": "yes", "peer_device": "dualswitch-mac"}

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
        "验证：全量 pytest 全绿；界面测试补了文件分类、标题、目录、按钮组合、文件卡、"
        "扩展名字形、引擎不发文件、单击复制的真实鼠标事件。这一段故意写长，"
        "用来确认预览窗会把文字完整显示出来：气泡跟着长高，超屏才内部滚动，一个字都不裁。"
    ) * 3
    home = os.path.expanduser("~")
    return [
        ClipRecord(id="t1", kind="text", text="今晚 8 点在 3 号会议室过一下 v2 的验收清单，别忘了带转接头",
                   size=96, state="ok", app="微信", app_id=r"C:\Program Files\Tencent\WeChat\WeChat.exe"),
        ClipRecord(id="i1", kind="image", png=png_bytes(640, 400, "#83A8FF"),
                   width=640, height=400, size=1_820_000, state="ok", app="截图"),
        ClipRecord(id="f1", kind="mixed", text="湖畔的清晨.png",
                   png=png_bytes(420, 280, "#F0E6D2"),
                   width=1086, height=1448, size=248 * 1024, file_size=3 * 1048576,
                   state="local", path=os.path.join(home, "Downloads", "照片", "湖畔的清晨.png"),
                   app="资源管理器", app_id=r"C:\Windows\explorer.exe"),
        ClipRecord(id="l1", kind="text", text="https://github.com/zq1996/dualswitch",
                   size=38, state="ok", source="remote", peer_name="Mac", app="Chrome"),
        # 长 URL：折行 + 气泡阴影的回归样本（用户 2026-10-21 截图里只显示半截的那条）
        ClipRecord(id="l3", kind="text",
                   text="https://www.bilibili.com/?spm_id_from=333.1007.0.0",
                   size=48, state="local", app="Chrome"),
        ClipRecord(id="c1", kind="text",
                   text="git rebase -i HEAD~3 && git push --force-with-lease origin main",
                   size=64, state="pend", progress=0.58, app="Windows 终端",
                   app_id=r"C://Windows//System32//cmd.exe"),
        ClipRecord(id="f2", kind="text", text="ChatGPTInstaller.exe",
                   size=18, file_size=849 * 1024, state="local",
                   path=os.path.join(home, "Downloads", "ChatGPTInstaller.exe"),
                   app="资源管理器", app_id=r"C:\Windows\explorer.exe"),
        ClipRecord(id="t2", kind="text", text='{ "hotkey": "alt+space", "sync_clipboard": true }',
                   size=62, state="fail", app="VS Code"),
        ClipRecord(id="l2", kind="text", text=long_text,
                   size=len(long_text.encode()), state="local", app="WorkBuddy"),
        # Emoji：字体回退（Segoe UI Emoji）+ 标题截断不能切碎 ZWJ 序列
        ClipRecord(id="e1", kind="text",
                   text="上线啦 🎉✅🚀 家庭 👨\u200d👩\u200d👧\u200d👦 国旗 🇨🇳 肤色 👍🏽",
                   size=64, state="ok", app="微信"),
    ]


def build_window(app, cfg, *, unknown: bool = False, peer: bool = True, records_list=None):
    win = MainWindow(cfg, HOTKEY, "v1.0.0", "AOC Q2790PC")
    win.setAttribute(Qt.WA_DontShowOnScreen, True)      # 不映射到屏幕，只渲染
    sp = SwitchPage(cfg, HOTKEY, "AOC Q2790PC")
    cp = ClipboardPage(FakeEngine(records_list if records_list is not None else records()))
    st = SettingsPage(cfg, ROOT / "build" / "win2-shots" / "cfg.json")
    win.attach_pages(sp, cp, st)
    win.show()
    if unknown:
        sp.set_state(SOURCE_UNKNOWN)
        sp.set_remote_available(peer)
        win.set_side_status("对端离线 · 仅本机" if not peer else "已连接 Mac", ok=peer)
        win.set_footer("对端未连接" if not peer else "已连接 Mac · 剪贴板已开启", ok=peer)
    else:
        sp.set_state("windows")
        sp.set_note("上次切换 63.9 ms · 回读已确认", ok=True)
        win.set_side_status("已连接 Mac", ok=True)
        win.set_footer("已连接 Mac · 剪贴板已开启", ok=True)
    app.processEvents()
    return win, sp, cp, st


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setFont(theme.qfont(theme.APP_FONT_PX))
    theme.set_appearance("light")
    app.setStyleSheet(theme.qss())
    cfg = platform_defaults()
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- 1/2 切换页（正常 / 未检测到）----
    win, sp, cp, st = build_window(app, cfg)
    win.show_page(0)
    app.processEvents()
    win.grab().save(str(OUT / "win2-switch.png"))

    win.deleteLater()
    win2, sp2, _, _ = build_window(app, cfg, unknown=True, peer=False)
    win2.show_page(0)
    app.processEvents()
    win2.grab().save(str(OUT / "win2-switch-unknown.png"))
    win2.deleteLater()

    # ---- 3 剪贴板页 ----
    win, sp, cp, st = build_window(app, cfg)
    win.show_page(1)
    cp.refresh()
    app.processEvents()
    win.grab().save(str(OUT / "win2-clipboard.png"))

    # 3b 行内动作 + 键盘选中态（辅助图）
    cp._select("i1")
    row = cp._rows["i1"]
    row._fade_acts(1.0)
    row._acts_effect.setOpacity(1.0)
    app.processEvents()
    win.grab().save(str(OUT / "win2-clipboard-hover.png"))
    cp._select(None)
    row._fade_acts(0.0)

    # ---- 4/5 预览气泡（文字 / 文件卡 / 长链接）----
    for rid, name in (("t1", "win2-preview-text"), ("f1", "win2-preview-file"),
                      ("l3", "win2-preview-link")):
        crow = cp._rows[rid]
        cp.preview_controller.open_now(crow.record, crow.ui_kind, crow.thumb)
        app.processEvents()
        cp.preview_controller.bubble.grab().save(str(OUT / f"{name}.png"))
        cp.preview_controller.hide_now()

    # ---- 5b emoji：列表 + 预览（顺带在真字体环境里核对字形）----
    from PySide6.QtGui import QFontDatabase, QFontMetrics

    fm = QFontMetrics(theme.qfont(theme.FS_ROW))
    missing = [hex(cp_) for cp_ in (0x1F389, 0x2705, 0x1F44D, 0x1F468)
               if not fm.inFontUcs4(cp_)]
    print("字体库：", len(QFontDatabase.families()), "个；emoji 缺字形：", missing or "无")
    erow = cp._rows["e1"]
    cp.preview_controller.open_now(erow.record, erow.ui_kind, erow.thumb)
    app.processEvents()
    cp.preview_controller.bubble.grab().save(str(OUT / "win2-preview-emoji.png"))
    cp.preview_controller.hide_now()

    # ---- 6 设置页（顶 / 底）----
    win.show_page(2)
    app.processEvents()
    win.grab().save(str(OUT / "win2-settings.png"))
    # 滚到底看「高级」组
    area = st.findChild(QScrollArea)
    if area is not None:
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        app.processEvents()
        win.grab().save(str(OUT / "win2-settings-bottom.png"))

    # ---- 7 清空确认遮罩 ----
    overlay = ConfirmOverlay(win)
    overlay.ask(8, lambda: None)
    app.processEvents()
    win.grab().save(str(OUT / "win2-confirm.png"))
    overlay.cancel()

    # ---- 8 托盘菜单 ----
    noop = lambda: None  # noqa: E731
    actions = {"open": noop, "switch": noop, "force": noop, "clipboard": noop,
               "settings": noop, "quit": noop, "remote_ready": lambda: True}
    entries = tray.build_menu_entries(cfg, "windows", HOTKEY, actions)
    menu = tray.TrayMenu()
    menu.setAttribute(Qt.WA_DontShowOnScreen, True)
    menu.rebuild(entries)
    menu.resize(theme.MENU_W, 8 * theme.MENU_ROW_H + 4 * 8 + 12)
    menu.show()
    app.processEvents()
    menu.grab().save(str(OUT / "win2-tray-menu.png"))

    # 8b hover 态：主色实底 + 白字 + 键帽反白
    rows = menu.findChildren(tray.MenuRow)
    switch_row = [r for r in rows if r.entry.title.startswith("切换到")][0]
    switch_row._hover = True
    app.processEvents()
    menu.grab().save(str(OUT / "win2-tray-menu-hover.png"))
    switch_row._hover = False
    menu.close()

    # 未检测到态的菜单（行 3/4 置灰）
    entries2 = tray.build_menu_entries(cfg, SOURCE_UNKNOWN, HOTKEY, {"remote_ready": lambda: False})
    menu2 = tray.TrayMenu()
    menu2.setAttribute(Qt.WA_DontShowOnScreen, True)
    menu2.rebuild(entries2)
    menu2.resize(theme.MENU_W, 8 * theme.MENU_ROW_H + 4 * 8 + 12)
    menu2.show()
    app.processEvents()
    menu2.grab().save(str(OUT / "win2-tray-menu-unknown.png"))
    menu2.close()

    # ---- 9 空态（双态：真空 / 筛选无结果）----
    win.show_page(1)
    empty = ClipboardPage(FakeEngine([]))
    empty.setAttribute(Qt.WA_DontShowOnScreen, True)
    empty.resize(592, 470)
    empty.show()
    app.processEvents()
    empty.grab().save(str(OUT / "win2-clipboard-empty.png"))
    empty.close()

    # ---- 10 emoji：短表情 / 长 ZWJ 串 / emoji 文件名 ----
    home = os.path.expanduser("~")
    emoji_recs = [
        ClipRecord(id="em1", kind="text", text="🎉 上线 ✅ 🚀 🇨🇳 👍🏽", size=32,
                   state="ok", app="微信"),
        ClipRecord(id="em2", kind="text",
                   text=("👨\u200d👩\u200d👧\u200d👦 " * 10).strip(), size=64,
                   state="ok", app="微信"),
        ClipRecord(id="em3", kind="text", text="截图 🎉 2026.png", size=20, file_size=2048,
                   state="local", path=os.path.join(home, "Pictures", "截图 🎉 2026.png")),
    ]
    ep = ClipboardPage(FakeEngine(emoji_recs))
    ep.setAttribute(Qt.WA_DontShowOnScreen, True)
    ep.resize(592, 360)
    ep.show()
    ep.refresh()
    app.processEvents()
    ep.grab().save(str(OUT / "win2-clipboard-emoji.png"))
    ep.close()

    win.close()
    win.deleteLater()
    print("已出图到", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
