# macOS 客户端界面设计

> 2026-10-17 · 应用显示名 **屏幕切换**（包名 / CLI 仍是 `dualswitch`）
> 参考 `docs/UI-DESIGN.md`（Windows 端）与 `docs/PLATFORM-SPLIT.md`（代码分层）
> 实现：`src/dualswitch/ui/chrome/mac.py`（外观真源）+ 共享的 `window/settings/widgets`

---

## 1. 定位：菜单栏是主界面，窗口是"详情页"

Windows 端窗口和托盘各占一半；mac 端不一样——**mac 用户对常驻工具的预期是「菜单栏一个图标」**，
窗口只在「确认状态 / 改配置」时打开。所以：

| | Windows | macOS |
|---|---|---|
| 主入口 | 托盘图标 | 菜单栏图标 |
| 触发切换 | 双击托盘图标 | **单击**菜单栏图标 |
| 右键 | 菜单 | 菜单 |
| 窗口 | 启动即显示 | 启动即显示（首次引导），关掉后靠菜单栏活着 |
| 设置入口 | 标题栏齿轮 | 应用菜单 `屏幕切换 → 设置…`（`⌘,`）+ 主窗口「设置…」行 |
| 退出 | 托盘菜单「退出」 | `⌘Q` / 菜单栏「退出」 |

> **为什么 mac 单击、Windows 双击？** Windows 托盘单击太容易误触（紧挨着时钟/网络图标），
> 所以定成双击；mac 菜单栏图标本身就是「点一下出东西」的语义，双击反而不自然。
> 这条差异只体现在 `chrome/*.CLICK_HINT` 与 `build_status_item` 里。

---

## 2. 菜单栏

- 图标：18×18 **模板图**（`ui/assets/menubar.png`，36px @2x），系统按菜单栏明暗自动反色
- 提示（tooltip）：`屏幕切换 · ⌥ Space（单击图标切换）`
- 左键：立刻切换（等同主按钮）
- 右键 / `⌃`+左键：弹菜单

菜单结构：

```
当前画面：Windows          ← 灰色只读行
打开主界面
────────────
切换到 Mac（USB-C）
强制切到 Windows（HDMI）
────────────
探测显示器 / 读取输入源
打开配置目录
查看日志
设置…                    ⌘,
────────────
热键：⌥ Space（单击图标切换）   ← 灰色只读行
退出 屏幕切换             ⌘Q
```

「当前画面」与「切换目标」会跟着状态翻转（`runner._poll_state` 里 250ms 轮询到变化就重建菜单）。

---

## 3. 主窗口 440×600

- **系统标题栏**（红绿灯 + 居中「屏幕切换」），窗口圆角、投影、边框全部由系统绘制
- 内容区与 Windows 完全同构：状态卡 → 设备卡 ×2 → 操作列表 → 状态栏
- 多一行「设置…」（Windows 在标题栏齿轮里）：列表末尾，右侧提示 `⌘ ,`
- 内容区上边距 14（Windows 16，因为那边有 44px 自绘标题栏）

```
┌─ ● ● ● ──── 屏幕切换 ────────┐   ← 系统标题栏
│  当前画面            [运行中] │
│  Windows                     │
│  Q2790R3 · HDMI · 输入源 17   │
│  [⇄ 切换到 Mac      ⌥ Space] │
│  ✓ 上次切换 63.9 ms · 回读已确认│
│  ┌────────┐ ┌────────┐        │
│  │Windows ●│ │  Mac ○ │        │
│  └────────┘ └────────┘        │
│  🔍 探测显示器          只读 › │
│  📄 查看日志  dualswitch.log › │
│  〜 运行诊断          doctor › │
│  ⚙ 设置…               ⌘ , ›  │
│ ● 热键已启用 · ⌥ Space            v1.0.0 │
└──────────────────────────────┘
```

状态卡里的显示器名：mac 上取 `m1ddc display list` 里那块外接屏的真实名字（如 `Q2790R3`），
不用 `monitor_match`（默认留空＝第一台外接屏）。

---

## 4. 设置窗口 440×484

四个页签与 Windows 一致，**结构与控件完全相同**，只有文案/选项按平台不同：

| 页 | 与 Windows 的差异 |
|---|---|
| 常规 | 无差异（切换模式 / 通知 / 回读提示 / 去抖） |
| 热键 | 「严格区分左右 Alt」→ **「只认右 Option」**；「开机后常驻托盘」→ **「关闭窗口后仍在菜单栏运行」**；「吞掉热键」说明改为「不再把这次按键传给其它程序」 |
| 显示器 | 说明改为「按 m1ddc 列出的显示器名匹配；留空 = 只接一台外接屏时直接用它」 |
| 高级 | DDC 后端下拉只有 `auto / m1ddc / command / mock`（没有 dxva2）；`auto` 在 mac 上即 m1ddc |

「只认右 Option」的落地方式与 Windows 一致：把热键 spec 里的 `alt` ↔ `ralt` 互转。
mac 的 `CGEventTap` 天生能区分左右，所以不需要 Windows 那套低级钩子后端，
配置里 `hotkey_backend` 写 `auto` 即可（从 Windows 拷过来的 `hook` 会被忽略）。

**默认热键 = `alt+space` = ⌥ Space（左 Option）**。mac 键盘上没有 Alt 键，键帽印的是 ⌥，
所以 `Hotkey.describe()` 在 mac 上返回 `Option+Space`，界面再经 `theme.pretty_hotkey()`
缩成 `⌥ Space`；任何路径漏过来的 `Alt` 也会被 `PALETTE.key_glyphs` 映射成 ⌥。
想强制只认右 Option（`ralt+space`）就打开「只认右 Option」开关。

---

## 5. 状态与异常

| 状态 | 呈现 | 是否打断 |
|---|---|---|
| 切换成功 | 状态卡更新 + 绿勾 + 耗时 | 否 |
| 切换中 | 按钮禁用 + `●` 转圈 + 文案换目标机名 | 否 |
| 切换失败 | 红色横幅（错误 + 人话解释）+ 脚注「切换失败，详见日志」 | 是（窗口内） |
| 热键未授权 | 启动即黄色横幅：`热键未启用：缺少「辅助功能」权限…`，右侧带 **「去授权」** 按钮 | 是（窗口内） |
| 回读未确认 | 只写日志 + 脚注「回读未确认（已知现象）」 | 否 |
| 外部切换 | 脚注「已同步外部切换 · 当前 Mac」 | 否 |

**系统通知**：mac 上暂不弹通知（需要 `UNUserNotificationCenter` 授权 + 打包签名，成本高于收益）。
失败与未确认走窗口内横幅 + 日志；`chrome/mac.py::notify()` 目前只写日志。

---

## 6. 设计 token（mac 差异）

| 类别 | Windows | macOS | 原因 |
|---|---|---|---|
| 主色 | `#2F6BFF` | `#0A7AFF` | 贴近系统强调色，避免"Windows 蓝"出现在 mac 上 |
| 主按钮渐变 | `#3F7BFF → #2456E0` | `#3D93FF → #0A6BE0` | 同上 |
| 字体 | Segoe UI Variable → Segoe UI → 微软雅黑 | **SF Pro Text → SF Pro Display → PingFang SC** | 系统字体 |
| 正文 | 12.5px | **13px** | mac 系统正文就是 13pt |
| 窗口圆角/投影 | 自绘 14px + 柔和投影 | 系统绘制 | 自绘会显得不像 mac 应用 |
| 快捷键写法 | `Alt + Space` | `⌥ Space`（符号表在 `PALETTE.key_glyphs`） | 键帽符号更接近系统文案 |
| 状态栏 | 底部两个圆角 | 无圆角（系统窗口不带圆角内容区） | — |

其余（卡片 11–13px 圆角、`#E5E9F1` 描边、绿色指示灯、31px 主机名、间距 14/18）两端一致，
见 `docs/UI-DESIGN.md` 第 4 节。

---

## 7. 打包与自启

```bash
./packaging/macos/build.sh          # PyInstaller → dist/dualswitch.app（Finder 里显示「屏幕切换」）
open dist/dualswitch.app            # 双击等价
```

实测（2026-10-17）：约 95MB，arm64，ad-hoc 签名通过；`dist/dualswitch.app/Contents/MacOS/dualswitch doctor`
可以只读自检。受限环境里 PyInstaller 的缓存目录可能需要挪到仓库内：

```bash
PYINSTALLER_CONFIG_DIR="$PWD/build/pyi-cache" ./packaging/macos/build.sh
```

- `Info.plist`：`CFBundleDisplayName=屏幕切换`、`CFBundleIdentifier=com.dualswitch.app`、
  `CFBundleShortVersionString=1.0.0`、`LSUIElement=false`（保留 Dock 图标，因为主窗口是真实窗口）、
  `LSMinimumSystemVersion=11.0`
- 打包后会跑一次 ad-hoc `codesign --sign -`：TCC 按「签名 + 路径」记授权，不签名的话每次重打包
  都可能被系统当成新应用，用户得反复去勾「辅助功能」
- 图标：`design/icon/screen-switch.icns`（由 `python design/icon/build_icns.py` 从 PNG 母版
  手写 ICNS 容器生成；`design/icon/build_icns.sh` 只是它的薄壳，方便沿用旧命令）
- 菜单栏模板图：`python design/icon/build_menubar_icon.py` → `src/dualswitch/ui/assets/menubar.png`
  （36×36 纯黑透明图，系统按深浅色自动反色）
- 开机自启：把 `.app` 加入 **系统设置 → 通用 → 登录项**（推荐）；launchd 方式见 `packaging/macos/install.sh`
- **辅助功能授权**（mac 上唯一必须手动做的一步）：热键走 `CGEventTap`，没有权限就建不起来。
  · 授权对象 = **启动它的那个程序**：用终端跑 `dualswitch ui` 时为「终端」/「iTerm」；
    双击 `屏幕切换.app` 时为「屏幕切换」。窗口横幅会把这句写出来（`chrome.permission_target()`）。
  · TCC 只会把**主动请求过**的二进制写进列表，所以程序启动时会调
    `AXIsProcessTrustedWithOptions(prompt=True)` 弹一次系统对话框；点横幅上的 **「去授权」**
    会再弹一次并把「辅助功能」面板打开。用户勾选后切回本窗口，程序会自动重试注册（不必重启）。
  · 列表里还是找不到时：面板底部有 `+`，直接到 `dist/屏幕切换.app` 或
    `/Applications/Utilities/Terminal.app` 手动添加。

---

## 8. 已知限制

| 限制 | 影响 | 状态 |
|---|---|---|
| `m1ddc get input` 在 USB-C(DP Alt Mode) 下返回 `-118`（IOKit 错误） | 读值不可用 → `probe` 显示读取失败、回读永远「未确认」 | 已知；写通道正常，切换不受影响 |
| 显示器序号从 `m1ddc display list` 自动解析 | 内置屏 `(null)` 被过滤，避免把指令发给内置屏 | 已实现 |
| 不支持系统通知 | 失败只弹窗口横幅 | 已知取舍 |
| 菜单栏图标数量 | 若同时跑 `dualswitch run`（pystray）与 `dualswitch ui`，会出现两个图标 | 文档已说明不要同时跑 |
