"""在无 GUI 交互的沙箱里核对 mac 端行为（不切屏、不发 DDC）。

用法：
    python tools/check_mac_ui.py

做四件事：
1. chrome 契约与 pretty_hotkey 文案
2. 主窗口 / 设置窗口在 mac 模式下能构建，关键控件存在
3. 设置保存走真实文件路径（不用 pytest 的 tmp_path，沙箱里用 build/tmp）
4. 用假 m1ddc 输出验证多屏解析与 ±118 读失败的处理
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from dualswitch.config import Config, load, platform_defaults, save  # noqa: E402
from dualswitch.ddc.base import DDCError  # noqa: E402
from dualswitch.ddc.macos import M1ddcBackend  # noqa: E402
from dualswitch.hotkey.base import parse_hotkey  # noqa: E402
from dualswitch.ui import theme  # noqa: E402
from dualswitch.ui.chrome import current as chrome  # noqa: E402
from dualswitch.ui.settings import SettingsWindow  # noqa: E402
from dualswitch.ui.window import MainWindow  # noqa: E402

OUT = ROOT / "build" / "tmp"
OUT.mkdir(parents=True, exist_ok=True)
ok = True


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {detail}" if detail else ""))
    ok = ok and bool(cond)


print("== chrome 契约 ==")
C = chrome()
check("平台模块", C.NAME == "macos", C.NAME)
check("系统标题栏", C.NATIVE_CHROME is True)
check("内容区设置行", C.INLINE_SETTINGS_ROW is True)
check("设置是窗口内的一页", C.SETTINGS_IN_WINDOW is True)
check("单击图标打开菜单", C.CLICK_HINT == "单击图标打开菜单")
check("字体 SF", C.PALETTE.font_families[0].startswith("SF Pro"))
check("主色系统蓝", C.PALETTE.accent == "#0A7AFF", C.PALETTE.accent)
check("键帽符号", theme.pretty_hotkey("Right Option+Space") == "⌥ Space",
      theme.pretty_hotkey("Right Option+Space"))
check("左 Option 也显示 ⌥", theme.pretty_hotkey("Option+Space") == "⌥ Space",
      theme.pretty_hotkey("Option+Space"))
check("文案里不出现 Alt", "Alt" not in parse_hotkey("alt+space").describe(),
      parse_hotkey("alt+space").describe())
check("图标 icns 优先", theme.app_icon().suffix == ".icns", theme.app_icon().name)
check("菜单栏模板图", theme.asset("menubar.png").exists())

print("\n== 平台默认配置 ==")
d = platform_defaults()
check("mac 默认热键 alt+space（左 Option）", d.hotkey == "alt+space", d.hotkey)
check("mac 默认不写死显示器关键字", d.monitor_match == "", repr(d.monitor_match))
check("两端输入源一致", d.sources == {"windows": 17, "mac": 18}, str(d.sources))
check("权限授权对象可读", bool(C.permission_target()), C.permission_target())

print("\n== 窗口构建 ==")
app = QApplication.instance() or QApplication([])
app.setStyleSheet(theme.qss())
cfg = Config()
win = MainWindow(cfg, "Right Option+Space", "v1.0.0", monitor_name="Q2790R3")
check("无自绘标题栏", win.titlebar is None)
check("状态卡显示外接屏名", "Q2790R3" in win.hero.sub.text(), win.hero.sub.text())
check("键帽显示 ⌥ Space", win.hero.kbd.text() == "⌥ Space", win.hero.kbd.text())
check("有设置行", win.row_settings is not None)
win.show_banner("warn", "热键未启用：缺少「辅助功能」权限", "去授权", lambda: None)
check("横幅可展开", win.height() > theme.WIN_H, f"{win.height()}px")
check("横幅带动作按钮", not win.banner.action.isHidden() and win.banner.action.text() == "去授权",
      win.banner.action.text())
win.clear_banner()

settings = SettingsWindow(cfg, OUT / "config.json", "Right Option+Space", "v1.0.0")
check("设置无自绘标题栏", settings.titlebar is None)
check("严格开关文案", settings.chrome.HOTKEY_COPY["strict_title"] == "只认右 Option")
check("录制按钮显示 ⌥ Space", settings.recorder.text() == "⌥ Space", settings.recorder.text())
check("后端下拉无 dxva2", "dxva2" not in settings.chrome.BACKEND_OPTIONS)
check("四个页签", settings.stack.count() == 4, str(settings.stack.count()))

print("\n== 设置是窗口内的一页（不另开窗口） ==")
embedded = SettingsWindow(
    cfg, OUT / "config.json", "Right Option+Space", "v1.0.0", parent=win, embedded=True
)
win.attach_settings(embedded)
embedded.backRequested.connect(win.show_main_page)
win.show_settings_page()
check("翻到设置页", win.pages.currentWidget() is embedded)
check("窗口标题跟着变", "设置" in win.windowTitle(), win.windowTitle())
check("高度切到设置页", win.height() == theme.SETTINGS_H, f"{win.height()}px")
check(
    "有返回按钮",
    any("返回" in b.text() for b in embedded.findChildren(QPushButton)),
)
embedded.backRequested.emit()
check("返回后是主页面", win.pages.currentWidget() is win.main_page)
check("高度回到主页面", win.height() == theme.WIN_H, f"{win.height()}px")

print("\n== 设置保存（真实文件） ==")
cfg_file = OUT / "config.json"
save(Config(), cfg_file)
settings2 = SettingsWindow(load(cfg_file), cfg_file, "Right Option+Space", "v1.0.0")
settings2.sw_strict.setChecked(True)
settings2._save()
saved = load(cfg_file)
check("保存成功", cfg_file.exists())
check("只认右 Option 落到 ralt", saved.hotkey == "ralt+space", saved.hotkey)
check("mac 不写 hook 后端", saved.hotkey_backend == "auto", saved.hotkey_backend)

print("\n== m1ddc 解析 ==")
backend = M1ddcBackend(monitor_match="", m1ddc_path="m1ddc")
backend._run = lambda args, display_args=None: (  # type: ignore[assignment]
    "[1] (null) (37D8832A-2D66-02CA-B9F7-8F30A301B230)\n"
    "[2] Q2790R3 (4D972D7B-4973-4865-B69A-5DD248A1EFC1)"
    if args == ["display", "list"]
    else ""
)
mons = backend.list_monitors()
check("内置屏被过滤", len(mons) == 1, str([m.description for m in mons]))
check("外接屏名字", mons[0].description == "Q2790R3", mons[0].description)
check("序号 1 基转内部 0 基", mons[0].index == 0 and mons[0].extra["m1ddc_index"] == 2)
check("自动带上 display 2", backend._display_args() == ["display", "2"], str(backend._display_args()))

backend._run = lambda args, display_args=None: "-118"  # type: ignore[assignment]
try:
    backend.get_input()
    check("-118 应报错", False)
except DDCError as exc:
    check("-118 给人话提示", "读通道不可用" in str(exc), str(exc)[:40] + "…")

print("\n== 结果 ==")
print("全部通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
