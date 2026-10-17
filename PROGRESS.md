# 项目进度（可中断续跑）

> 最后更新：2026-10-17 16:30
> 一句话状态：**双向切屏已真机闭环。`sources = {windows: 17, mac: 18}`，Windows 侧热键、DDC、回读、托盘全部实测通过；macOS 侧还需装 dualswitch 本体并授权辅助功能。**
>
> 👉 **下一步：macOS 侧安装运行（T3）。** 双端切屏链路本身已经验证完毕。

---

## 0. 中断后如何继续（先看这里）

新会话接手时按顺序做三件事，不要重新读需求文档再重新规划：

```bash
cd D:/work/workbuddy/dualswitch
# 1. 确认代码健康（应输出 60 passed）
"C:/Users/Administrator/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m pytest -q

# 2. 确认硬件仍然可达（只读，不会切屏）
PYTHONPATH=src "C:/Users/Administrator/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m dualswitch doctor

# 3. 从第 5 节「未完成」继续
```

环境：Python 3.13.14（`C:\Users\Administrator\.workbuddy-ai\binaries\python\envs\default`），
已装 `pystray 0.19.5` / `pillow 13.0.0` / `pytest 9.1.1`，并且已 `pip install -e .`，
所以 `dualswitch` 命令可直接用（不需要 PYTHONPATH）。

---

## 1. 阶段进度

| # | 阶段 | 状态 | 证据 |
|---|---|---|---|
| 1 | 调研现成方案并选型 | ✅ 完成 | `docs/RESEARCH.md`，结论：无完全匹配，自研应用层 |
| 2 | 项目骨架 + 配置/日志 | ✅ 完成 | `config.py` / `logging_setup.py`，14 个配置用例通过 |
| 3 | DDC 后端（Win/macOS/command/mock） | ✅ 完成（Win 真机） | `probe` 输出 `MONITOR\AOC2790`、`VCP 0x60 = 17` |
| 4 | 全局热键（Win register + hook，macOS CGEventTap） | ✅ 完成（Win 真机） | `tools/e2e_hotkey.py` 两种模式均 OK |
| 5 | 编排 / 异步回读 / 托盘 / CLI | ✅ 完成 | 38 个 app+ddc 用例；托盘冒烟通过 |
| 6 | 打包与开机自启 | ✅ 脚本就绪 | `packaging/` 下 6 个脚本，未实际执行打包 |
| 7 | 文档 | ✅ 完成 | README / RESEARCH / VERIFY / TROUBLESHOOTING / 本文件 |
| 8 | 真机切屏验收 | ✅ **双向闭环** | `Set 18`→Mac（回读确认 64ms）；`Set 17`→Windows（回读确认 64ms）；热键真实触发也已验证 |
| 9 | macOS 实机验证 | ✅ 切屏已验证 | `m1ddc set input 17` 成功；USB-C 真实值 = 18（需求文档的 27 是错的） |
| 10 | macOS 端安装 dualswitch 本体 | ⏳ 待做 | 需 `pip install -e ".[tray,macos]"` + 辅助功能授权 |
| 11 | Windows 界面设计稿 + 应用图标 | ✅ 完成 | `design/windows/index.html`、`design/icon/`、`docs/UI-DESIGN.md` |
| 12 | Windows 图形界面实现（PySide6） | ✅ 完成并跑通 | `src/dualswitch/ui/`，`dualswitch ui`；64 个用例全绿 |

---

## 2. 本机环境事实（已实测，2026-10-17）

| 项 | 值 |
|---|---|
| OS | Windows 10，AMD Radeon RX 6750 XT |
| 显示器 | `MONITOR\AOC2790\{4d36e96e-...}\0003`，`\\.\DISPLAY1`，描述为 `Generic PnP Monitor` |
| VCP 0x60 读回 | `cur=17`，`max=14` ← **max 是乱码**，印证需求文档 P-2「HDMI 通道响应解析异常」 |
| VCP 0x60 合法值 | **15 = DP，17 = HDMI，18 = USB-C**；19–28 全部被拒绝（含 27） |
| VCP 0x10 亮度 | `cur=99, max=100`（正常） |
| 热键 Alt+Space | `RegisterHotKey` 可注册（`doctor` → `可注册`） |
| 枚举耗时 | `list_monitors` 中位 2.0ms |
| DDC 往返耗时 | `get_input` 中位 63.0ms（含取句柄） |

结论：`SetVCPFeature` 与 `GetVCPFeature` 同一量级，**「触发 → Set 发出」实测路径约 65ms，满足 ≤200ms**。

---

## 3. 已完成的实现细节

### 3.1 切换链路
- `app.SwitchController`：热键线程只做去抖 + 入队（微秒级），工作线程串行发送，回读独立线程。
- 每次 `set_input` 重新枚举物理显示器并 `DestroyPhysicalMonitor` 释放 —— 因为切走后显示器会消失，缓存句柄必失效。
- `SetVCPFeature` 失败重试 1 次（间隔 80ms），仍失败抛 `DDCError`，工作线程兜住，进程不崩。
- `dry_run` 只写日志不发 Set，用于演练与自动化测试。

### 3.2 回读
- 默认 `delay=300ms`、`timeout=1000ms`、`interval=200ms`，与需求 §3.4 一致。
- 三种结局：`confirmed`（INFO）/ `unconfirmed`（INFO，默认不弹通知）/ `skipped`。
- 切到 Mac 后 Windows 侧回读必然失败，属预期，日志措辞已注明。

### 3.3 热键
- Windows `RegisterHotkeyBackend`：`RegisterHotKey(None, id, MOD_ALT|MOD_NOREPEAT, VK_SPACE)` + 独立线程消息循环；错误码 1409 给出「被占用」的中文可操作提示。
- Windows `LowLevelHookBackend`：`WH_KEYBOARD_LL`，用 `GetAsyncKeyState(VK_RMENU)` 严格判右 Alt，可选吞键避免弹出窗口系统菜单。
  - 踩坑：`GetModuleHandleW` 未声明 `restype` 时 64 位下句柄被截断 → `SetWindowsHookEx` 报 `WinError 126`。已修复（见 `hotkey/windows.py` 注释）。
- macOS `QuartzEventTapBackend`：`CGEventTapCreate` + `CFRunLoop`，keyCode 61（右 Option）+ 49（Space），先检查 `AXIsProcessTrustedWithOptions` 并给出授权路径。

### 3.4 配置
- JSON，单文件，未知字段直接报错（防止拼写错误静默失效）。
- 默认值即生产值：`sources = {windows:17, mac:18}`、`monitor_match="AOC"`。
  （18 是实测出来的，不是 MCCS 标准的 27 —— 见 `docs/VERIFY-M1DDC.md`）
- 热键字符串在配置校验阶段就解析，非法值启动即失败，不会带病运行。

### 3.5 CLI
`run` / `switch` / `probe`（只读）/ `doctor` / `config`。`probe` 与 `doctor` 支持 `--json`。

---

## 4. 验证记录

| 时间 | 动作 | 结果 |
|---|---|---|
| 15:06 | `pytest -q` | 52 passed（新增 CLI 用例后共 60） |
| 15:08 | `probe` | 枚举到 `MONITOR\AOC2790`，`VCP 0x60 = 17` |
| 15:08 | `doctor` | 11 项全 OK（配置文件 WARN 属首次运行正常） |
| 15:08 | `bench_readonly.py` | 枚举 2ms / DDC 往返 63ms |
| 15:08 | `e2e_hotkey.py`（register） | 合成 Alt+Space → 日志出现 `[dry-run] 本应发送 Set VCP 0x60 = 27`（当时默认值还是 27，现已改 18） |
| 15:09 | `e2e_hotkey.py --strict`（hook） | 左 Alt 不触发、右 Alt 触发 → 严格模式生效 |
| 15:11 | `run --dry-run`（托盘） | 托盘图标出现，无异常退出 |
| 15:12 | 热键被占用场景 | 另起进程占住 Alt+Space → `doctor` 报 `[FAIL] 热键可用性` 并给出中文提示；`run` 打印 `[WARN]` 后继续以托盘模式运行 |
| 16:05 | 亮度写入测试（HDMI） | 99 → 40 → 99，写入并回读成功 → DDC 写通道正常 |
| 16:05 | VCP 扫描 26/28/19–25 | 全部被拒绝（回读仍 17） |
| 16:06 | VCP 扫描 15/18 | 都被接受；15 切过去黑屏（DP 空口），18 出现 Mac 画面 |
| 16:25 | 双向真实切换 | `Set 18` 回读确认 18 / 63.9ms；`Set 17` 回读确认 17 / 64.4ms |
| 16:28 | 热键真实触发 | 常驻进程 + 合成 Alt+Space → VCP 变 18，画面切到 Mac |
| 17:35 | `dualswitch ui --dry-run` 点击切换 | 卡片两态翻转、`✓ [演练] 未真正发送指令`，界面状态与 controller 同步 |
| 17:41 | `pytest -q` | 64 passed（含 4 个离屏 UI 用例） |

复现命令：

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m dualswitch probe
python tools/bench_readonly.py
python tools/e2e_hotkey.py
python tools/e2e_hotkey.py --strict
```

---

## 5. 未完成 / 待办

### T1 ✅ 真机切屏验收 —— 已通过（2026-10-17 16:2x）

```
Windows → Mac : Set 0x60=18 → 回读确认 18，调用耗时 63.9ms
Mac → Windows : Set 0x60=17 → 回读确认 17，调用耗时 64.4ms
热键真实触发   : 常驻进程 + 合成 Alt+Space → VCP 变 18（画面切到 Mac），5 秒后 CLI 切回 17
```

两项都满足需求 §6：2 秒内出画面、触发到 Set 发出 < 200ms。

### T2 ✅ macOS 侧切屏验证 —— 已通过

| 项 | 结论 |
|---|---|
| Mac → Windows | ✅ `m1ddc set input 17` 成功（前提：显示器先处于 USB-C 输入） |
| HDMI 上 DDC 写 | ✅ 亮度 99→40→99 写入并回读成功 |
| HDMI 上输入源切换 | ✅ 有效：15 / 18 被接受 |
| **18 = USB-C** | ✅ 用户目视确认（15 = DP 黑屏，18 = Mac 画面） |
| 27 = USB-C？ | ❌ 需求文档的假设是错的，27 被显示器直接拒绝 |
| 合法输入值 | **15 = DP，17 = HDMI，18 = USB-C** |

### T3（待做）macOS 端安装 dualswitch 本体
切屏链路已通，但 Mac 上还没装这个应用本身：
1. 解压 `dualswitch-1.0.0-mac.zip`
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -e ".[tray,macos]"`（macos extra = pyobjc，CGEventTap 需要）
4. `dualswitch doctor` —— 应显示 `pyobjc/Quartz OK`、`辅助功能权限 OK`（首次要在
   系统设置 → 隐私与安全性 → 辅助功能 里勾选运行它的终端 / Python）
5. `dualswitch run` —— 菜单栏出现图标，右 Option + Space 应能切回 Windows
6. 验证：Mac 按右 Option + Space → 切到 Windows；Windows 按 Alt + Space → 切回 Mac

### T4（可选）打包实测
`packaging/windows/build.ps1` 与 `packaging/macos/build.sh` 只写了脚本，没有实际跑过 PyInstaller。
首次打包可能需要在 `--hidden-import` 里补模块（pystray 后端、PIL 插件）。

### T5（可选）24h 稳定性
需求 §5 要求「7×24 无内存泄漏，以实际观察为准」。未做，建议挂一晚后看任务管理器。

### T7（待确认）剪贴板跨设备同步 —— 当前**未实现**
本仓库内**没有任何剪贴板相关代码**（无模块、无依赖、无配置字段），
唯一出现的位置是 README 第 1 节与验收表第 5 条：明确写了「不自己做，交给 CrossPaste 独立运行」。
如果要做，需要新立项：监听 `AddClipboardFormatListener` / 变更计数轮询 + 局域网传输（mDNS + TLS），
与切屏主链路完全独立。设计稿里也没有为它留位置。

### T6（可选）热键被占用场景的端到端验证
已实现提示文案与降级逻辑，**已于 15:12 实测通过**（另起进程占住 Alt+Space 后启动本程序：
`doctor` 报 FAIL 并给出处理建议，`run` 打印 WARN 后继续托盘常驻）。

---

## 6. 文件地图

```
dualswitch/
├── PROGRESS.md                  ← 本文件
├── README.md                    使用说明 + 验收对照
├── config.example.json          全字段示例
├── pyproject.toml
├── src/dualswitch/
│   ├── cli.py                   命令分发；run 的启动流程在这里
│   ├── app.py                   SwitchController（切换编排、回读、去抖）
│   ├── config.py                Config / from_dict / validate / load / save
│   ├── tray.py                  TrayApp（pystray 菜单、通知）
│   ├── logging_setup.py         RotatingFileHandler
│   ├── ddc/base.py              DisplayBackend 协议、MonitorInfo、DDCError
│   ├── ddc/windows.py           dxva2 直调（盲发 Set）
│   ├── ddc/macos.py             m1ddc 子进程封装
│   ├── ddc/command.py           外部命令模板后端
│   ├── ddc/mock.py              测试/演练
│   └── hotkey/
│       ├── base.py              热键解析、VK/mac keycode 映射
│       ├── windows.py           RegisterHotKey + 低级钩子
│       ├── macos.py             Quartz CGEventTap
│       └── mock.py              ManualHotkeyBackend
├── tests/                       60 用例，无需硬件
├── tools/
│   ├── bench_readonly.py        只读耗时测量
│   ├── e2e_hotkey.py            端到端热键（dry-run，不切屏）
│   ├── scan_input_windows.py    VCP 0x60 候选值扫描（Windows，带自动切回）
│   ├── diag_macos.sh            macOS DDC 诊断（架构/外接屏/VCP 读值/亮度写入）
│   └── verify_m1ddc.sh          macOS 强制验证脚本
├── packaging/                   Windows/macOS 打包 + 自启
└── docs/                        RESEARCH / VERIFY-M1DDC / TROUBLESHOOTING
```

---

## 7. 关键决策（改动前请先读）

| 决策 | 原因 | 如果要改 |
|---|---|---|
| DDC 只发不收 | Q2790PC HDMI 通道 `max=14` 乱码，读值不可信 | 换显示器后可开 `readback` 严格校验 |
| 每次切换重新取句柄 | 切走后显示器从系统消失 | 缓存方案会在切回时失效，别改 |
| 回读失败只记 INFO | 切走必然读不到，WARNING 会刷屏 | `readback.notify_on_unconfirmed` 可开通知 |
| Python 而非 Tauri/Go | 本机无 Rust/Go 工具链；核心零依赖；改配置不用重打包 | 应用层接口已抽象，可平移 |
| `monitor_match` 默认 `"AOC"` | 物理描述是 `Generic PnP Monitor`，只能靠 device_id 匹配 | 换显示器改这里 |
| 配置未知字段直接报错 | 防拼写错误静默失效 | 不要放宽 |

---

## 8. 变更日志

- 2026-10-17 19:20 **托盘切换改为左键双击**。
  单击误触率太高：`_on_tray_activated` 只认 `QSystemTrayIcon.DoubleClick`，单击不做任何事。
  托盘提示补「（双击图标切换）」，避免单击没反应被当成坏了；右键菜单不变。
  新增用例 `test_tray_double_click_switches_single_click_does_not`，73 全绿。
  `docs/UI-DESIGN.md` 的交互表与待确认项同步更新（原第 7 节问题 2 标记为已定为双击）。
- 2026-10-17 19:15 **主页面 / 托盘的热键文案统一刷新**。
  原来热键文案只在 `on_config_saved` 一处手写刷新，托盘提示与菜单项「热键：xxx」更是启动时生成后永不更新；
  任何一条路径漏掉，界面就会停在旧值。
  收敛成 `_refresh_hotkey_ui()`：以「实际注册的 hotkey 对象」为准，同时刷新主页面键帽、状态栏、托盘 tooltip 和菜单项，
  并在 250ms 轮询里兜底调用（字符串比较，无变化直接返回）。
  `_rebuild_hotkey` 成功/失败后也调用一次，注册失败时状态栏显示「热键未启用」而不是「热键未启用 · 未启用」。
  实测（真实保存流程）：保存后 60ms 内键帽 / 状态栏 / 托盘三处全部同步为新热键。72 用例全绿。
- 2026-10-17 19:10 **修复：改了热键保存后不生效，旧热键仍然有效**。
  根因：`SettingsWindow._save()` 直接**就地修改** `self.cfg`（`cfg.hotkey = ...`），
  而 `self.cfg` 就是 runner 里那份运行中的对象，于是 `on_config_saved` 里
  `old_hotkey = self.cfg.hotkey` 读到的已经是新值，`cfg.hotkey != old_hotkey` 永远为 False，
  `_rebuild_hotkey` 从来没被调用过。顺带还有个隐患：校验失败时运行中的配置已经被改脏了。
  修复：`_save` 改为「取 to_dict → update → from_dict 校验 → 保存 → emit」，全程不碰运行中的对象。
  runner 侧再加一道保险：新增 `_hotkey_spec` 记录**实际注册的那个**热键，比较用它而不是 `self.cfg.hotkey`。
  真机 E2E（真实按键注入，dry-run）：点录制 → 按 Ctrl+Alt+F12 → 点保存 →
  `runner.hotkey = Ctrl+Alt+F12`、配置文件同步、旧热键触发 0 次、新热键触发 1 次。
  新增用例 `test_save_keeps_live_config_untouched`，72 全绿。
- 2026-10-17 19:00 **修复：改热键时按到「当前热键」会直接切屏且录不上**。
  现象：当前热键 Alt+Space，先改成别的，再改回 Alt+Space 时，画面直接切走且没录上。
  根因：录制期间全局热键仍在注册，按到当前热键会被系统当成 WM_HOTKEY 吞掉去触发切换，
  录制窗口根本收不到这个按键。
  修复：`HotkeyRecorder.recordingChanged(bool)` → `UiRunner._suspend_hotkey()/_resume_hotkey()`，
  录制时 `stop()` 掉热键后端，结束后按当前配置重建。
  · **恢复要延迟 350ms**：录制是在 keyPress 里结束的，立刻 RegisterHotKey 会让这一次按下的键
    被新注册的热键再吞一次（真机 A/B 实测：延迟前录完立刻又切走一次）
  · 切页签 / 保存 / 恢复默认 / 窗口隐藏 / 失焦都会取消录制，避免热键一直停着
  · 组合键不合法时给提示（「至少需要一个修饰键」/「这个组合不支持」），不再静默忽略
  真机 E2E（注入真实按键，dry-run）：热键生效时注入 → 触发 1 次；录制中注入 → 被录制器捕获且不触发；
  录制结束后注入 → 热键恢复生效。71 用例全绿（新增 2 个录制器用例）。
- 2026-10-17 18:50 **修复：外部切换后界面状态不跟随**。
  现象：应用切到 Mac 后，在 Mac 上执行 `m1ddc set input 17`（或 dualswitch CLI）切回 Windows，
  界面仍显示「当前画面 Mac」。根因：`SwitchController._current_source` 只在**本进程自己切换**时更新，
  外部切换它完全不知道；顺带 `switch_mode="toggle"` 也会算错目标。
  修复：新增 `SwitchController.detect_source()` + 后台 `source-detector` 线程（默认 2s 一轮）。
  判据是**显示器还在不在本机**——显示器只会在它当前选中的输入端被枚举到，能枚举到 = 画面在本机。
  · 连续两轮结果一致才采信（两个方向都是，防切换滞后 / 拔线 / 驱动抖动误判），修正延迟 ≤ 4s
  · 自己刚切完 8s 内静默（`detect_settle_s`），避免显示器还没真正切走就翻回旧状态
  · `dry_run` 下不检测；枚举异常只跳过这一轮（并清空确认计数）
  界面：`runner._poll_state` 发现状态变化且没有新切换记录时，脚注显示「已同步外部切换 · 当前 Windows」。
  真机只读验证：`detect_source()` → `windows`；把 `monitor_match` 改成不存在的值 → `mac`。
  新增 3 个用例，69 全绿。
- 2026-10-17 18:40 **显示器页说明与「检测」按钮**。
  用户反馈「匹配关键字 / AOC 输入框」看不懂用途。补页面顶部说明（这页决定操作哪台显示器 + 各机器的输入源值），
  行标题改为「显示器匹配关键字」，描述改为「按设备名或 device_id 的片段定位这台屏，匹配不到就用第一台」；
  新增「检测」按钮（`SettingsWindow.probeRequested` → runner `do_probe`），点一下直接列出系统枚举到的显示器，
  照着填关键字即可。热键页描述补「保存即生效」。新增用例 `test_display_page_probe_button`，66 用例全绿。
- 2026-10-17 18:30 **设置页控件重排（页签 / 底部操作条 / 内边距 / 下拉框）**。
  页签：容器 36 → 42 高、圆角 10，四个页签 `addWidget(btn, 1)` 等宽四等分，选中白底不再只包住文字；
  `Segmented`（两段选择器）同样改为定宽等分（每段 76px）。
  底部：新增 `#footer` 操作条（#F7F9FC + 上边框，`WA_StyledBackground`），「恢复默认」「保存」收进条内垂直居中，
  不再浮在白底上；状态栏同步为 #F7F9FC，两条同色用发丝线分隔。
  内边距：`#ghost` 补 `padding: 0 14px; min-width: 52px`，`#solid` 补 `padding: 0 22px`——
  修掉「切换热键」「打开配置文件」「恢复默认」文字贴边的问题（同类问题一次覆盖）。
  下拉框：QSS 的 `::down-arrow` 无法用 border 画三角形（会渲染成一条横杠），改为
  `widgets.Combo` 自绘（圆角边框 + 内缩文字 + 1.4px 圆头 chevron，hover/focus 变蓝边），QSS 只保留弹层样式。
  热键录制按钮显示改为 `Alt + Space`（`spec()` 仍返回 `Alt+Space`）。`SETTINGS_H` 470 → 484。65 用例全绿。
- 2026-10-17 18:10 **主页面加设置入口 + 标题栏重做**。
  标题栏新增设置齿轮（`CaptionButton("gear")` → `TitleBar.settingsClicked` → `MainWindow.openSettingsRequested`，
  runner 里已有 `show_settings` 接线）；设置窗口自身用 `show_settings=False` 隐藏齿轮。
  最小化 / 关闭改为 QPainter 自绘线性图标（1.3px 圆头描边，26px 高，悬停圆角底，关闭悬停红底白叉），
  替换原先大小与基线都不一致的 `–` / `✕` 字符。
  标题栏底色改为 `#F5F7FB` + 1px 分隔线 + 顶部圆角——**关键坑**：QWidget 子类不设
  `WA_StyledBackground` 时 QSS 背景完全不生效（之前 `transparent` 看不出问题）。
  「运行中」药丸去掉描边，改浅绿底绿字；主按钮键帽去掉半透明白描边。
  新增回归用例 `test_main_window_has_settings_entry`，65 用例全绿。
- 2026-10-17 17:55 **界面细节优化（按钮 / 框线 / 布局）**。
  主页面：主按钮右侧的 Alt、Space 两格键帽合并为单个「Alt + Space」芯片（不再像表格单元格），
  新增 `HeroCard.set_hotkey()`，保存设置后键帽与状态栏同步刷新；脚注成功态真正用上绿色 token
  （修掉 `heroFootOk` 定义了却没用上的冗余代码）；状态栏禁用热键时不再显示「热键已启用 · 未启用」矛盾文案。
  设置页：窗口高度 600 → 470（`theme.SETTINGS_H`），消除中部大片空白；
  QSlider / QComboBox 补 QSS（细轨道 + 白圆柄、统一下拉框，替换原生糙样式）；
  ghost 按钮边框由 #E5E9F1 加深为 #D5DCE8（「恢复默认」不再看起来像无边框）；
  高级页配置文件路径改为单行中间省略，不再在路径中间断行；
  页签加 hover 态。64 用例全绿，真机截图逐页核验。
- 2026-10-17 17:35 **图形界面落地（`dualswitch ui`）**。
  新增 `src/dualswitch/ui/`：`theme.py`（token + QSS）、`widgets.py`（无边框标题栏 / 指示灯 / 设备卡 / 状态卡 / 自绘小图标）、
  `window.py`（主窗口）、`settings.py`（四页签设置，含热键录制）、`runner.py`（窗口 + QSystemTrayIcon + 热键 + 控制器接线）。
  依赖 `.[gui]` = PySide6-Essentials；未安装时 `ui` 命令给出安装提示并退出，`run` 路径不受影响。
  行为：关闭窗口 = 收进托盘；托盘左键单击直接切换；保存设置后按需重建热键；切换结果由 250ms 轮询同步到界面。
  `tests/test_ui.py` 新增 4 个离屏用例（资源解析、两态切换、设置保存、非法输入拦截），总数 60 → 64。
  图标资源：`ui/assets/*.png`（官方 OS 图标正常/置灰两版），打包脚本补 `--icon` 与 `--add-data`。
- 2026-10-17 17:05 **界面设计 v3：亮灯改绿色**。设备卡的「正在使用」由品牌蓝/青改为绿色指示灯 + 极浅绿底，
  与显示器电源灯同一语言；未使用态不变（灰底 + 去色 + 内凹灯）。
- 2026-10-17 17:00 **界面设计 v2：换用用户提供的图标 + 设备卡亮灯/灭灯**。
  主窗口两台设备改用 Windows / Mac 官方图标（`#0078D4` 四格 / Apple 剪影），
  使用中=亮灯（品牌色底 + 原色图标 + 发光指示灯），未使用=灭灯（灰底 + grayscale 38% + 内凹指示灯）。
  应用图标改为用户提供的双屏插画：白底 JPG 按「亮度+饱和度」双判据抠成透明，1024 母版 → 10 档 PNG + 9 档 ICO；
  上一版自绘图标归档到 `design/icon/_old/`。UI 代码仍未开始，等确认后动工。
- 2026-10-17 16:35 **Windows 界面设计稿 v1 + 应用图标定稿**。
  应用显示名定为 **屏幕切换**（包名 / CLI 仍为 `dualswitch`）。
  新增 `design/windows/index.html`（主窗口 / 设置 / 三种异常态 / 托盘菜单 / 图标规范，浅色 Fluent 440×600）；
  `design/icon/`（512 栅格矢量母版 + 10 档 PNG + 9 尺寸 ICO，三档字形：≤20 只留箭头、24–32 屏幕+箭头、≥40 加支架）；
  `docs/UI-DESIGN.md`（token、页面规格、实现路径）。UI 代码尚未编写。
- 2026-10-17 16:30 **双向闭环，VCP 值定案 `{windows:17, mac:18}`**。
  代码默认值、示例配置、测试、README、验证文档、扫描脚本已统一改为 18。
  真机实测：`Set 18` 回读确认 63.9ms，`Set 17` 回读确认 64.4ms，热键真实触发也通过。
- 2026-10-17 16:10 **定位到真实原因：VCP 值错，不是代码问题**。
  HDMI 上 DDC 写通道实测正常（亮度 99→40→99）；输入源切换也有效（15/18 被接受）。
  但 `Set 0x60=27` 被显示器直接拒绝（回读仍 17），26/28/19–25 同样拒绝。
  这块屏只认 **15 / 17 / 18** 三个输入值。新增 `tools/scan_input_windows.py`（逐值/批量扫描，带自动切回）。
  下一步改为在 Mac 上 `m1ddc get input` 直接读出 USB-C 的真实值。
- 2026-10-17 16:00 **macOS 首次实测未通过**：`m1ddc set input 17/27` 无反应，R-1 风险触发。
  新增 `tools/diag_macos.sh`（架构/外接屏识别/VCP 四项读值/亮度写入测试），
  重写 `docs/VERIFY-M1DDC.md` 的失败定位决策树（A 画面归属 → B DDC 通道 → C 输入源指令 → D 退路）。
- 2026-10-17 15:42 生成 `dualswitch-1.0.0-mac.zip`（70KB，41 文件，排除 .venv/缓存），供拷到 Mac 验证。
- 2026-10-17 15:35 `tools/verify_m1ddc.sh` 增加 `--status` / `--scan` 两种模式与「回传报告」输出；
  标记 T2 为当前待办。
- 2026-10-17 16:35 修复 `dualswitch config show` 的 AttributeError（子命令未传 `--config` 时属性缺失），
  新增 `tests/test_cli.py`（8 个用例），测试总数 52 → 60。
- 2026-10-17 15:15 初版完成：Windows 侧全链路 + 52 用例 + 打包脚本 + 文档。
- 2026-10-17 15:09 修复 `GetModuleHandleW` restype 缺失导致低级钩子 `WinError 126`。
- 2026-10-17 15:08 真机只读探测通过（AOC2790 / VCP 17 / max 乱码）。
