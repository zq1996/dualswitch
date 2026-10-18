"""平台外观契约（Windows / macOS 各一份实现）。

## 为什么要有这一层

两端界面共用 90% 的内容：状态卡、两台设备、四个设置页、250ms 状态轮询。
真正不同的只有「窗口谁画、常驻图标在哪、几个文案怎么说」这几件事。

把这些差异全部收进 `chrome/win.py` / `chrome/mac.py` 之后：

* 改 Windows 界面 → 只动 `chrome/win.py`
* 改 macOS 界面 → 只动 `chrome/mac.py`
* `theme.py` / `window.py` / `settings.py` / `runner.py` 只在「契约要变」时才动

这比在共享文件里写 `if IS_MAC:` 更抗合并：两个人分别改两端时，几乎不会碰同一个文件。

## 契约

每个 chrome 模块（win.py / mac.py）必须提供下列模块级常量：

| 名字 | 类型 | 含义 |
|---|---|---|
| `NAME` | str | 平台名，写日志用 |
| `PALETTE` | `Palette` | 颜色 / 字体 / 字号 |
| `NATIVE_CHROME` | bool | 窗口外框与标题栏是否由系统绘制 |
| `ROOT_QSS` / `STATUSBAR_QSS` / `QSS_EXTRA` | str | QSS 片段（窗口外框、底部状态栏圆角、其它微调） |
| `BODY_TOP` | int | 内容区上边距 |
| `INLINE_SETTINGS_ROW` | bool | 主窗口内容区里是否放一行「设置…」 |
| `CLICK_HINT` | str | 「单击图标切换」/「双击图标切换」 |
| `REOPEN_ON_ACTIVATE` | bool | 激活应用时若窗口收起就自动打开（mac Dock 行为） |
| `DOCK_FOLLOWS_WINDOW` | bool | 窗口收起时程序坞图标是否跟着消失（mac 是，Windows 无此概念） |
| `HOTKEY_COPY` | dict | 热键页四个文案（严格修饰键 / 吞键 / 常驻 / 说明） |
| `BACKEND_OPTIONS` | list[str] | 高级页 DDC 后端下拉选项 |
| `STRICT_BACKEND` | str | 勾选「只认右 Alt/Option」时写进配置的 hotkey_backend |
| `MATCH_DESC` | str | 显示器页「匹配关键字」的说明 |
| `SETTINGS_ROW_HINT` | str | 主窗口设置行的右侧提示（`⌘ ,` / 空） |

以及下列函数：

| 函数 | 返回 | 说明 |
|---|---|---|
| `titlebar(title, show_settings)` | `QWidget \\| None` | 自绘标题栏；返回 None 表示用系统标题栏 |
| `build_status_item(icon, on_click)` | 状态项对象 | 托盘 / 菜单栏，接口见 `ui.statusitem.MenuEntry` |
| `build_app_menu(runner)` | `QMenuBar \\| None` | macOS 全局菜单栏；Windows 返回 None |
| `activate(window)` | None | 把窗口抢到前台（macOS 需要显式 activate） |
| `set_dock_visible(visible)` | None | 显示/隐藏程序坞图标（mac 切 `NSApplicationActivationPolicy`；Windows 空实现） |
| `notify(status, title, message, icon)` | None | 系统通知；没有可用通道时降级为写日志 |
| `accessibility_granted(prompt=False)` | bool | 是否已拿到「辅助功能」权限（Windows 恒 True） |
| `request_accessibility()` | bool | 弹系统授权框 + 打开设置面板，返回当前是否已授权 |
| `open_accessibility_settings()` | None | 直接打开系统的「辅助功能」面板 |
| `permission_target()` | str | 该为谁打勾（脚本运行 = 终端名，打包后 = 「屏幕切换」，Windows 空串） |

> `accessibility_*` 这组是 macOS 专属的痛点：CGEventTap 没有「辅助功能」权限就建不起来，
> 而 TCC 只会把**主动请求过**的可执行文件写进列表 —— 必须调用
> `AXIsProcessTrustedWithOptions(prompt=True)` 让系统弹一次框，否则用户在设置页里根本找不到本应用。
> Windows 侧实现成恒真的空壳，共享代码因此可以无条件调用。

`build_status_item()` 返回的常驻图标对象要实现这些方法（两端同名）：

| 方法 | 说明 |
|---|---|
| `set_tooltip(text)` / `set_menu(entries)` / `show()` / `hide()` | 常规操作，`entries` 见 `ui.statusitem.MenuEntry` |
| `is_visible()` | 图标当前是否可见（注册失败时为 False） |
| `is_available()` | 这台机器有没有托盘 / 菜单栏；**为 False 时不能把窗口藏进后台**，否则没有入口 |
| `geometry()` | 图标在屏幕上的位置，只给排障日志用，拿不到返回空串 |
| `reassert()` | Windows 专用：重新向系统注册一次图标（Explorer 重启后图标会丢） |


chrome 模块**不要**在模块顶层 import `..theme` / `..widgets`：`theme` 需要 import chrome
才能拿到配色，顶层互相 import 会成环。需要控件时在函数内 import。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Palette:
    """一个平台的配色与字体。字段名与 theme 里的 token 一一对应。"""

    accent: str
    accent_deep: str
    accent_soft: str
    primary_top: str
    primary_bottom: str
    primary_top_hover: str
    primary_bottom_hover: str
    primary_top_down: str
    primary_bottom_down: str
    font_families: tuple[str, ...]
    body_font_size: float
    app_font_point: float
    key_glyphs: dict[str, str] = field(default_factory=dict)
