"""常驻入口的菜单数据模型（平台无关）。

真正画图标、收点击的实现分别在：
  · `chrome/win.py` → QtStatusItem（QSystemTrayIcon，双击摊开主界面）
  · `chrome/mac.py` → MacStatusItem（NSStatusBar，单击切换）

这里只定义两端共用的「菜单条目」，`build_menu_entries()` 是纯函数，可以直接单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import theme

PORT = {"windows": "HDMI", "mac": "USB-C"}


@dataclass(frozen=True)
class MenuEntry:
    kind: str = "item"                 # item / sep / label（label = 灰色的只读行）
    title: str = ""
    action: Callable[[], None] | None = None
    enabled: bool = True
    checked: bool = False
    default: bool = False              # 双击图标时触发这一项（pystray 用；Qt 托盘自己处理双击）
    key: str = ""                      # mac 快捷键字符，例如 "q" / ","
    modifier: str = "cmd"              # cmd / cmd_shift


def build_menu_entries(cfg, current: str, hotkey_label: str, actions: dict) -> list[MenuEntry]:
    """生成菜单内容。`current` 是当前画面所在的机器（windows / mac）。"""
    from .chrome import current as chrome

    other = "mac" if current == "windows" else "windows"
    other_title = "Mac" if other == "mac" else "Windows"
    self_title = "Mac" if current == "mac" else "Windows"
    # 双击图标的语义与 Qt 托盘保持一致（`chrome.STATUS_ACTIVATE`）：
    # Windows 上是摊开主界面，所以「打开主界面」是 default；mac 单击即切换，default 是切换项
    double_click_open = chrome().STATUS_ACTIVATE == "show_main"

    return [
        MenuEntry("label", f"当前画面：{self_title}"),
        MenuEntry("item", "打开主界面", actions.get("open"), default=double_click_open),
        MenuEntry("sep"),
        MenuEntry("item", f"切换到 {other_title}（{PORT[other]}）", actions.get("switch"),
                  default=not double_click_open),
        MenuEntry("item", f"强制切到 {self_title}（{PORT[current]}）", actions.get("force")),
        MenuEntry("sep"),
        MenuEntry("item", "探测显示器 / 读取输入源", actions.get("probe")),
        MenuEntry("item", "剪贴板…", actions.get("clipboard"), key="v", modifier="cmd_shift"),
        MenuEntry("item", "打开配置目录", actions.get("config_dir")),
        MenuEntry("item", "查看日志", actions.get("log")),
        MenuEntry("item", "设置…", actions.get("settings"), key=",", modifier="cmd"),
        MenuEntry("sep"),
        MenuEntry("label", f"热键：{theme.pretty_hotkey(hotkey_label)}（{chrome().CLICK_HINT}）"),
        MenuEntry("item", "退出 屏幕切换", actions.get("quit"), key="q", modifier="cmd"),
    ]
