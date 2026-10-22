# 平台分层契约（Windows / macOS）

> 2026-10-17 立 · 适用 `src/dualswitch/ui/`
> **这份文档是给"另一端的 AI / 另一个人"看的**：动界面代码前先读第 1 节和第 5 节。

---

## 0. 一句话规则

> **界面代码两端各一套，物理分开：改界面只动 `ui/mac/**` 或 `ui/win/**` 里自己那棵。
> 只有「外观差异」这种一眼可见的东西还留在 `ui/chrome/win.py` / `ui/chrome/mac.py`。**

2026-10-20 之前是"共享文件 + chrome 字段"：两端都改 `theme.py` / `clipboard_page.py`，
每加一个平台差异就多一个冲突点。5 次合并的统计结果是 **UI 层两端撞了 14 次，
业务后台只撞了 1 次** —— 后台之所以不冲突，是因为 `clipboard/windows.py` 只归 Windows、
`sync/*` 只归 macOS，**一个文件一个作者**。于是界面也照这条边界拆开：

| | 拆之前 | 拆之后 |
|---|---|---|
| 改剪贴板页 | 两端改同一个 `clipboard_page.py`（528+/273-） | 各改各的 `ui/{mac,win}/clipboard_page.py` |
| 改 QSS | 两端改同一个 `theme.py` | 各改各的 |
| 冲突概率 | 每次合并 3–9 个文件重叠 | **0**（剩下一行选路，两端都不需要改） |
| 代价 | — | 约 2200 行重复代码；**跨端一致性靠文档 + 各自的自测，不靠代码** |

> 因此：**两端视觉要一致的地方，写进 `docs/UI-DESIGN.md` / `docs/UI-DESIGN-MAC.md`；
> 在一端修了一个 bug，先判断另一端是否同样存在**（两棵树不会自动同步）。
> 用例层面已经钉住"公开接口必须对齐"（`tests/test_chrome_contract.py::test_ui_trees_expose_the_same_api`），
> 但**行为**上的差异是允许的、也是刻意保留的。

为什么要这样：两端原本在同一批文件里写 `if IS_MAC:`。Windows 和 Mac 由两个 AI（或两段时间）
并行开发时，每加一个平台差异就多一个冲突点，merge 时大面积重叠。拆完之后两端各占一个文件。

---

## 1. 我要改 X，应该动哪个文件

> 路径里的 `{tree}` = 自己那棵树（`mac` 或 `win`）。

| 想改的东西 | 动这里 | 别动 |
|---|---|---|
| Windows 主色 / 字体 / 标题栏按钮 / 托盘双击语义 | `chrome/win.py` | 任何一棵树的 theme、widgets |
| mac 主色 / SF 字体 / 菜单栏图标与菜单 / ⌘ 快捷键 | `chrome/mac.py` | 任何一棵树的 theme、widgets |
| 「严格区分左右 Alt」这类**同一个开关、两端不同文案** | 两端 chrome 的 `HOTKEY_COPY` | settings |
| DDC 后端下拉里有哪些选项 | 两端 chrome 的 `BACKEND_OPTIONS` | settings |
| 状态卡 / 设备卡 / 列表行 / 灯 / 开关的外观 | `ui/{tree}/widgets.py`（**本端**） | 另一端那棵 |
| 四个设置页的**结构**（分组、行顺序） | `ui/{tree}/settings.py`（本端） | 另一端那棵 |
| 主窗口的**结构**（谁在上面、几行列表） | `ui/{tree}/window.py`（本端） | 另一端那棵 |
| 250ms 状态轮询、热键重建、探针/诊断弹窗 | `ui/{tree}/runner.py`（本端） | 另一端那棵 |
| 菜单条目的文字与顺序 | `ui/{tree}/statusitem.py::build_menu_entries`（本端） | 另一端那棵 |
| 剪贴板页（列表 / 预览 / 双击语义 / 看图方式） | `ui/{tree}/clipboard_page.py`（本端） | 另一端那棵 |
| 切换编排、DDC、热键、配置 | `app.py` / `ddc/*` / `hotkey/*` / `config.py` | `ui/` 里任何东西 |

判断标准：**「只有一端需要不同」→ chrome 字段；「这一端独有的实现」→ 自己那棵树；
「两端必须一模一样」→ 写进设计文档，两边各自实现。**

---

## 2. 目录

```
src/dualswitch/
├── clipboard_feed.py       没有同步引擎时的本机记录源（mac 写，win 只读）
├── ui/
│   ├── __init__.py         唯一接缝：platform_ui() 按平台返回 mac / win 包（冻结）
│   ├── chrome/             ← 平台外观契约（两端各一个文件，互不相干）
│   │   ├── base.py         契约定义（Palette 数据类 + 本文档的代码版）
│   │   ├── win.py          Windows：自绘标题栏 + 托盘双击 + Win 文案/配色
│   │   └── mac.py          macOS：系统标题栏 + 菜单栏 NSStatusItem + 应用菜单 + Mac 文案/配色
│   ├── mac/                ← **macOS 整套界面，只归 macOS 端改**
│   │   ├── theme.py        组装 token 与 QSS（配色来自 chrome.PALETTE）
│   │   ├── widgets.py      Led / DeviceCard / HeroCard / ListRow / Combo / ToggleSwitch …
│   │   ├── window.py       主窗口骨架（系统标题栏）
│   │   ├── settings.py     设置页（内嵌为主窗口的一页）
│   │   ├── statusitem.py   菜单条目（MenuEntry + build_menu_entries）
│   │   ├── clipboard_page.py  剪贴板页（双击预览图 → 系统看图软件）
│   │   ├── runner.py       接线：QApplication + 菜单栏 + 热键 + SwitchController + 轮询
│   │   └── focus_style.py  焦点虚线框治理（mac 侧未安装，保留模块以便接口对齐）
│   ├── win/                ← **Windows 整套界面，只归 Windows 端改**（同名八件，实现独立）
│   │   └── …（clipboard_page 走应用内全屏预览 + ←/→ 翻页；设置页用 page_header）
│   ├── {theme,widgets,window,settings,statusitem,clipboard_page,runner,focus_style}.py
│   │                       兼容垫片：只做 `sys.modules` 换绑，**冻结，谁都不许加实现**
│   └── assets/            windows-logo(-dim).png / apple-logo(-dim).png / menubar.png /
│                          screen-switch.ico + screen-switch.icns（运行时副本，由
│                          `design/icon/build_icon.py` 自动同步，别手改）
```

为什么垫片还在：`tests/` 与 `tools/` 里 40 多处写着 `from dualswitch.ui.window import MainWindow`
这类路径，垫片让它们继续指向"当前平台那棵树"，不用全改。它只做一件事：把
`dualswitch.ui.<name>` 换成 `dualswitch.ui.{mac,win}.<name>` 的**同一个模块对象**。

---

## 3. 契约（`chrome/*.py` 必须提供的字段）

契约的代码版在 `chrome/base.py` 的 docstring 里，表格版在这里。**新增字段必须两端同时加**，
否则另一端会 `AttributeError`；`tests/test_chrome_contract.py` 会直接拦住。

| 名字 | 类型 | Windows | macOS | 用途 |
|---|---|---|---|---|
| `NAME` | str | `"windows"` | `"macos"` | 日志 / 调试 |
| `PLATFORM_LABEL` | str | `"Windows"` | `"macOS"` | 诊断弹窗里的平台名 |
| `PLATFORM_LOGO` | str | `"windows"` | `"apple"` | 本机图标名（剪贴板页文本记录的缩略图） |
| `PALETTE` | `Palette` | 品牌蓝 `#2F6BFF` | 系统蓝 `#0A7AFF` | 主色、按钮渐变、字体、字号、键帽符号表 |
| `NATIVE_CHROME` | bool | `False` | `True` | 窗口外框/标题栏是否由系统绘制 |
| `ROOT_QSS` | str | 白底 + 1px 描边 + 14px 圆角 | 无描边无圆角 | `#root` 的 QSS |
| `STATUSBAR_QSS` | str | 底部两个圆角 | 空 | 状态栏圆角 |
| `QSS_EXTRA` | str | `""` | `""` | 其它平台专属 QSS（目前都没用上） |
| `BODY_TOP` | int | 16 | 14 | 内容区上边距（mac 少了自绘标题栏） |
| `INLINE_SETTINGS_ROW` | bool | `False` | `True` | 主窗口内容区是否放「设置…」行 |
| `SETTINGS_IN_WINDOW` | bool | `True` | `True` | 设置是否为主窗口内的一页（「‹ 返回」回主页面，不另开窗口） |
| `SETTINGS_ROW_HINT` | str | `""` | `"⌘ ,"` | 设置行右侧提示 |
| `CLICK_HINT` | str | `"双击图标打开主界面"` | `"单击图标打开菜单"` | 常驻图标提示、菜单里的说明（**必须与本端实际行为一致**） |
| `STATUS_ACTIVATE` | str | `"show_main"` | `"switch"` | 点常驻图标干什么。Windows 双击只摊开主界面（切屏不可逆，不绑双击）；mac 的菜单栏左右键都弹菜单，所以 mac 这个值目前不会被真正的点击触发 |
| `REOPEN_ON_ACTIVATE` | bool | `False` | `True` | 点 Dock / Cmd+Tab 回来时自动展开窗口 |
| `DOCK_FOLLOWS_WINDOW` | bool | `False` | `True` | 窗口收起后程序坞图标是否消失（mac 用 `NSApplicationActivationPolicy` 切换，菜单栏图标不受影响） |
| `STRICT_BACKEND` | str | `"hook"` | `"auto"` | 勾选「只认右 Alt/Option」时写入的 `hotkey_backend` |
| `BACKEND_OPTIONS` | list[str] | auto/dxva2/command/mock | auto/m1ddc/command/mock | 高级页下拉 |
| `MATCH_DESC` | str | 「按设备名或 device_id 的片段…」 | 「按 m1ddc 列出的显示器名…」 | 显示器页说明 |
| `HOTKEY_COPY` | dict | 4+1 条文案 | 4+1 条文案 | 热键页四行的标题/说明 |
| `APP_ICON_NAMES` | tuple[str] | `("screen-switch.ico",)` | `("screen-switch.icns", "screen-switch.ico")` | 图标候选 |

函数：

| 函数 | 返回 | 说明 |
|---|---|---|
| `titlebar(title, show_settings=True)` | `QWidget \| None` | Windows 返回自绘标题栏；mac 返回 `None`（用系统标题栏） |
| `build_status_item(icon, on_click)` | 状态项对象 | Windows → `QtStatusItem`（双击摊开主界面）；mac → `MacStatusItem`（左右键都弹菜单） |
| `build_app_menu(runner)` | `QMenuBar \| None` | mac 返回全局菜单栏；Windows 返回 `None` |
| `activate(window)` | None | 把窗口抢到前台（mac 必须显式 activate） |
| `set_dock_visible(visible)` | None | 显示/隐藏程序坞图标（mac 切激活策略；Windows 空实现） |
| `notify(status, title, message, icon)` | None | 系统通知；mac 暂只写日志（见第 6 节） |
| `open_path(path)` | None | 用系统默认程序打开文件/目录 |
| `accessibility_granted(prompt=False)` | bool | 是否已有「辅助功能」权限；Windows 恒 True |
| `request_accessibility()` | bool | 弹系统授权框 + 打开「辅助功能」面板 |
| `open_accessibility_settings()` | None | 只打开面板，不弹框 |
| `permission_target()` | str | 该为谁打勾（脚本运行 = 终端名；打包后 = 「屏幕切换」；Windows 空串） |

状态项对象需要实现：`set_tooltip(str)` / `set_menu(list[MenuEntry])` / `show()` / `hide()` /
`is_visible()` / `notify(title, msg, icon, msecs)`。

**`PALETTE.key_glyphs` 的坑**：它是「修饰键文案 → 符号」的查表，key 必须与
`Hotkey.describe()` / `Hotkey.mac_describe()` 用 `+` 切出来的**单个片段完全一致**。
`Right Option`、`Right Alt` 本身就是**带空格的一个片段**，所以 `theme.pretty_hotkey()` 只按 `+` 切分、
每段 `strip()` 后再查表（早期版本先 `replace(" ", "")`，把 `Right Option` 粘成 `RightOption` 查不到，
界面上就露成 `RightOption Space`）。加新符号时照抄 `mac_describe()` 里的名字。

---

## 4. 迁移对照（如果有人还在用旧位置）

| 旧位置（2026-10-17 之前） | 新位置 |
|---|---|
| `ui/widgets.py::TitleBar` | `ui/chrome/win.py::titlebar()` 返回的控件 |
| `ui/widgets.py::CaptionButton` | `ui/chrome/win.py::titlebar()` 内部类 |
| `ui/widgets.py::CaptionButton._paint_gear` | `ui/widgets.py::paint_gear(p, c, color, scale)`（共享，Glyph 也用它） |
| `ui/statusitem.py::create_status_item` | `chrome/win.py::build_status_item` / `chrome/mac.py::build_status_item` |
| `ui/statusitem.py::QtStatusItem` | `ui/chrome/win.py::QtStatusItem` |
| `ui/statusitem.py::MacStatusItem` | `ui/chrome/mac.py::MacStatusItem` |
| `theme.IS_MAC` / `theme.NATIVE_CHROME` | 已删除；用 `chrome.NATIVE_CHROME` 等字段 |
| `theme.ACCENT` 等 token | 还在，但值来自 `chrome.PALETTE`；改配色请改 chrome |
| `runner._open_path()` | `chrome.open_path()` |

`ui/statusitem.py` 现在只剩 `MenuEntry` + `build_menu_entries()`（纯数据，两端共用）。

---

## 5. 硬性约定（违反会被测试拦住）

1. **共享文件里不许出现平台分支**：`theme.py` / `widgets.py` / `window.py` / `settings.py` /
   `runner.py` / `statusitem.py` 中不得出现 `sys.platform`、`platform.system()`、`IS_MAC`。
   `tests/test_chrome_contract.py::test_shared_ui_has_no_platform_branch` 会扫源码。
   （`chrome/__init__.py` 是唯一允许做平台选择的地方。）
2. **新增契约字段两端同时加**：只加一端 → 另一端启动即 `AttributeError`；
   `test_chrome_contract.py` 的表里同步补一行。
3. **chrome 模块顶层不要 import `..theme` / `..widgets`**：`theme` 需要 import chrome 取配色，
   顶层互相 import 会成环。需要控件时在函数内 import（现有代码都是这么写的，照抄即可）。
4. **共享控件保持两端一致**：想给 mac 单独调卡片圆角/间距，优先在 `chrome.QSS_EXTRA` 里覆盖，
   而不是在 `widgets.py` 里加判断。
5. 改完必须跑：`pytest -q`（含 2 个 chrome 契约用例 + 1 个共享文件无平台分支用例）。

---

## 6. 已知边界（哪些差异暂时还留在共享层）

| 项 | 现状 | 要不要继续拆 |
|---|---|---|
| 系统通知 | Windows 用托盘气泡；mac 只写日志（要 UNUserNotificationCenter + 授权，成本高） | 不做，窗口内横幅已覆盖 |
| 窗口尺寸 | 两端都是 440×600（`theme.WIN_H`），mac 少了自绘标题栏所以内容区更高 | 不动，视觉上一致 |
| 设置页四页签 | 两端同一套（页签在内容区，不是 mac 的 sidebar 式偏好设置） | 若要改成 mac 偏好设置面板，动 `settings.py` + `chrome` 新增 `SETTINGS_STYLE` |
| 长文本省略 / 列表行 | 共享实现 | 不动 |
| 热键页的「只认右 Alt / 右 Option」 | 同一个开关、同一个 `ralt` 语义，只是文案不同 | 已由 `HOTKEY_COPY` 覆盖 |

---

## 7. 两端并行开发时的合并流程

1. **Windows 侧**（当前主干）：拉取本次改动后，先 `pytest -q`，重点看 `test_chrome_contract.py`。
2. Windows 侧继续改界面 → 只提交 `chrome/win.py`（+ 必要时 `widgets.py`）。这样 mac 分支
   rebase 时不会碰到它。
3. mac 侧同理，只改 `chrome/mac.py`。
4. 若两边都动了 `widgets.py` / `settings.py` / `runner.py`：说明在改「两端共同结构」，
   这类改动本来就该串行做，先合一方再 rebase 另一方。
5. 契约字段变更（第 3 节表格）**必须由两端一起确认**，因为它同时影响两个 chrome 文件。

---

## 8. 变更日志

- 2026-10-17 **热键文案按平台说人话 + 默认改左 Option + 补「辅助功能」授权链路**。
  · `Hotkey.describe()` 在 macOS 上直接返回 `mac_describe()` —— 之前返回 `Right Alt+Space`，
    界面上出现 Mac 键盘根本没有的 `Alt`（键帽印的是 ⌥）；
  · mac 默认热键 `ralt+space` → `alt+space`（= ⌥ Space，左 Option）；
    右 Option 仍可用「只认右 Option」开关切到 `ralt`；
  · `PALETTE.key_glyphs` 增补 `Alt` / `Right Alt` / `Ctrl` / `Win` 兜底映射，任何漏网的英文键名都显示成符号；
  · 契约新增 4 个权限函数（见第 3 节）：mac 上 CGEventTap 必须先 `prompt=True` 让 TCC 把进程
    写进列表，否则用户在「辅助功能」里永远找不到本应用；
  · `window.show_banner(kind, msg, action_text, action)` 支持带按钮（「去授权」），
    授权后切回应用会自动 `retry_hotkey()`，不必重启。
- 2026-10-17 **键帽符号查表修正（`theme.py`，共享层）**：`pretty_hotkey()` 不再先吞空格，
  改为 `+` 切分 + 逐段 `strip()`，`Right Option` 这类双词修饰键能正常命中 `key_glyphs`。
  mac 侧显示为 `⌥ Space`（此前是 `RightOption Space`）。契约本身没变，只是补了第 3 节那条「坑」。
- 2026-10-17 **平台分层落地**：新增 `ui/chrome/`（base / win / mac），把标题栏、托盘/菜单栏、
  配色字体、平台文案、图标候选、打开路径全部下沉。共享文件删除全部平台分支；
  新增 `tests/test_chrome_contract.py`（契约字段 + 无平台分支双保险）。
  同步产物：`docs/UI-DESIGN-MAC.md`（mac 端界面规格）。
