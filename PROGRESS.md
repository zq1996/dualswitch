# 项目进度（可中断续跑）

> 最后更新：2026-10-19 09:20
>
> 👉 **2026-10-19（ISSUE-001 已关闭）**：画面归属加了**第三态 `unknown`**。以前
> `detect_source()` 只有「能枚举到→本机 / 枚举不到→另一台」两分支，拔掉外接屏启动
> 4 秒后界面会谎报「已同步外部切换 · 当前 Windows」。现在「本次运行从没见过这块屏」
> 单列为一态：状态卡显示「未检测到外接显示器」、按钮与设备卡置灰、菜单不给会失败的入口，
> 也**不会盲发 Set**（直接给中文原因）。启动时那次只读探测经 `note_scan()` 喂给控制器，
> 首帧就是对的。`tests/test_source_state.py` 15 例；全量 198 passed。
> **已重打包并重装**（`/Applications/屏幕切换.app`，2026-10-19 09:43 启动验证：
> 打包版=True、热键仍启用、8 秒内日志无「检测到画面已在 windows」）。
> `install-app.sh` 顺手修了两个坑：往已存在的 bundle 里 `ditto` 是合并语义（旧文件会留下
> 导致签名对不上），以及 `codesign --deep` 在受限环境会自己写 `.BC.*` 临时文件把包判成坏包。
> **协作流程见 [`docs/COORDINATION.md`](docs/COORDINATION.md) + [`docs/ISSUES.md`](docs/ISSUES.md)**
> （任务=编号+可观察行为+验收命令；共享层只有 macOS 端一个作者）。
> 下一个：ISSUE-002（屏在 Windows 时 Mac 切回来），**先做 30 秒物理验证**再设计协议。
>
> **并行开发中：跨端剪贴板同步（P0 文字 + P1 图片）**
> 分工与契约见 [`docs/CLIPBOARD-SYNC-CONTRACT.md`](docs/CLIPBOARD-SYNC-CONTRACT.md)。
> 界面设计与可交互稿见 [`docs/UI-DESIGN-CLIPBOARD.md`](docs/UI-DESIGN-CLIPBOARD.md) 与
> `design/clipboard/index.html`（浏览器直接打开）。
> **Windows 端 AI 先读该文档第 0 节**：只写 `src/dualswitch/clipboard/windows.py` 与
> `tests/test_clipboard_windows.py`，其余文件一律只读。共用契约 `clipboard/base.py`
> （`ClipItem` + 后端 `poll/write/probe/close` 语义）已冻结，契约测试
> `tests/test_clipboard_contract.py` 已就绪（11 passed / 2 skipped，真实后端用
> `DUALSWITCH_CLIP_TEST=real` 跑）。
> **剪贴板同步：macOS 端 P0（文字）+ P1（图片）已全部实现并自测通过**，Windows 端后端仍未落地
> （`clipboard/windows.py` 归 Windows 侧）。两端联调的 10 项人工验证待做，见
> [`docs/CLIPBOARD-SYNC-LOG.md`](docs/CLIPBOARD-SYNC-LOG.md)。
>
> 一句话状态：**两端都已落地可用。Windows 侧切屏 / 热键 / DDC / 回读 / 托盘 / 图形界面 / onedir 打包全部实测通过；
> macOS 侧界面（菜单栏 + 原生窗口 + 四页设置）跑在真机上，`⌥ Space` 全局热键已修复并通过用户实测
> （根因是 CGEvent 字段取错、异常被静默吞掉），已打包并装进 `/Applications/屏幕切换.app`，
> 关窗口后程序坞不留图标、只保留菜单栏图标。`sources = {windows: 17, mac: 18}`。**
>
> 👉 **2026-10-18 16:15（Windows 侧）**：打包版「关窗口后托盘没图标」的真凶已定位 ——
> 不是代码，是 `D:\work` 目录带了 `Low Mandatory Level` 完整性标签，打出来的 exe 继承了它，
> 进程以低完整性级别运行，`Shell_NotifyIcon` 被 UIPI 静默丢弃（详见 §8 与 `docs/TROUBLESHOOTING.md` §4.1）。
> `build.ps1` 现在会在打包后把产物标签抬到 High，重新打包的 `dist\dualswitch\` 已实测：图标出现、
> 关窗口后图标仍在、进程常驻。旧产物留在 `dist\dualswitch.old-20261018\`（可删）。
>
> 👉 **2026-10-18 17:2x 补记（Windows 剪贴板后端已完成）**：`clipboard/windows.py` 按契约 M1 落地，
> 只用了 `ctypes`（不依赖 Qt / Pillow 也能读，写 DIB 时借用它们的 C 解码器）。
> 三个未知数已实测：截图工具给 **`CF_DIB` 32bpp**、浏览器额外给注册格式 **`PNG`**（优先取后者，
> 字节级原样往返）；资源管理器复制文件时 `probe()` 能看到 **`CF_HDROP`**（P2 备料，`poll()` 返回 None）；
> 序列号确实**先于数据**变化 → 读空时 `sleep(10ms)` 重读一次。
> 4K 图实测：PNG→DIB 373ms、DIB→PNG 241ms（都在后台线程，不挡切屏）。
> 测试：`tests/test_clipboard_windows.py` 58 例；契约测试带 `DUALSWITCH_CLIP_TEST=real` 时
> **14 passed / 3 skipped**（含真实后端参数），全量 **175 passed / 6 skipped**。
>
> 👉 **下一步（Windows 侧）**：M1 与剪贴板页都已闭环，剩下的**只有 macOS 端能做的** ——
> ① `sync/*`（M2：protocol / transport / engine）与 `app.py` 的 `on_before_switch` 钩子（M3）。
> 这一层是"双端能不能取到值"的唯一通道，目前仓库里**一行都没有**，所以现在无法联调；
> ② `tools/clip_selftest.py`（M5）尚不存在，验收表里那条 `--role windows` 暂时跑不了；
> ③ `cli.py` 的 `dualswitch clip ...` 子命令（契约第 8 节）也还没写。
>
> 👉 **2026-10-18 17:35 补记（剪贴板页已落地）**：`ui/clipboard_page.py` + `ui/clipboard_feed.py`
> 已实现并接进主窗口（Windows 走标题栏图标、mac 走列表行，都不新开窗口），
> 版式按 `design/clipboard/index.html` 还原（440×600、页头 40、行 52、预览吃剩余、操作栏 48）。
> ⚠️ 这两个文件按契约归 macOS 端，这次是**越界先做**（用户要求 start-ui 里能看到页面）；
> `sync/engine.py` 交付后，把 runner 里的 `LocalClipFeed` 换成 engine 即可，页面不用改。
> 联调代码（`sync/*`）按用户要求**没有写**，已撤回。
>
> 👉 **2026-10-18 18:0x（合并后）**：上面两条「下一步」都作废 —— macOS 端已交付 M1→M5
> （`clipboard/macos.py`、`sync/*`、CLI、页面、自测工具），Windows 端已交付后端与标题栏入口，
> 两边在 `main` 上合并。**现在双端第一次具备联调条件**：
> `dualswitch clip pair` 配对 → 两端复制/切屏 → `dualswitch clip status` 看 `authenticated`。
> 合并口径：页面以 macOS 端那版为准（引擎驱动），Windows 后端 + 标题栏入口 + 本机记录源
> 全部保留；引擎起不来时页面退回 `LocalClipFeed`，不再是死入口。
>
> 👉 **下一步（mac 侧）**：等 Windows 后端落地后做 `docs/CLIPBOARD-SYNC-LOG.md` 里那 10 项人工联调；
> 同时继续日常观察热键长时间可用性（tap 被系统禁用会自动重开，日志里有
> `事件 tap 回调超时…已重新启用` 与 `激活策略 → Accessory` 可查）。
> Windows 端双击 `dist\dualswitch\dualswitch.exe` 即进主界面（整个文件夹一起用），右键托盘可退出。
> 界面/分层改动请看 `docs/UI-DESIGN-MAC.md` 与 `docs/PLATFORM-SPLIT.md`（后者是给另一端 AI 读的契约）。

---

## 0. 中断后如何继续（先看这里）

新会话接手时按顺序做三件事，不要重新读需求文档再重新规划：

```bash
# ── Windows ──────────────────────────────────────────────
cd D:/work/workbuddy/dualswitch
"C:/Users/Administrator/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m pytest -q
PYTHONPATH=src "C:/Users/Administrator/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m dualswitch doctor
python tools/fix_integrity.py --self    # 托盘图标能不能注册：看本进程 / 资源管理器的完整性级别

# ── macOS ────────────────────────────────────────────────
cd ~/work/workbuddy/dualswitch
QT_QPA_PLATFORM=offscreen ~/.workbuddy/binaries/python/envs/default/bin/python \
    -m pytest tests -q -s -p no:cacheprovider --basetemp=.pytest-tmp   # 96 passed
QT_QPA_PLATFORM=offscreen ~/.workbuddy/binaries/python/envs/default/bin/python \
    tools/check_mac_ui.py                               # 34 项核对
~/.workbuddy/binaries/python/envs/default/bin/python -m dualswitch doctor
~/.workbuddy/binaries/python/envs/default/bin/python tools/diag_macos_hotkey.py   # 热键现场诊断
```

> mac 上必须带 `-s` **和 `--basetemp=.pytest-tmp`**：受限环境里 pytest 的输出捕获与 `tmp_path`
> 都要写临时文件，`/tmp` 不可写时整套用例直接 ERROR（`tests/conftest.py` 已兜住 `tmp_path`，
> 但捕获发生在 conftest 之前）。这是环境问题，不是代码问题。

环境：Python 3.13.14（Windows：`C:\Users\Administrator\.workbuddy-ai\binaries\python\envs\default`；
macOS：`~/.workbuddy/binaries/python/envs/default`），
已装 `pyside6-essentials 6.12` / `pyobjc 12.2` / `pystray 0.19.5` / `pytest 9.1.1`，并且已 `pip install -e .`。

**注意 `dualswitch` 命令的可见性**：`pip install -e .` 把入口脚本装在 **venv 的 bin 里**
（`~/.workbuddy/binaries/python/envs/default/bin/dualswitch`），而这个目录**不在系统 PATH 上**，
所以裸敲 `dualswitch ui` 会 `command not found`。三种写法任选：

```bash
dualswitch ui                                    # 已在 ~/.local/bin 做了软链（2026-10-17 加）
~/.workbuddy/binaries/python/envs/default/bin/dualswitch ui    # 绝对路径，最稳
source ~/.workbuddy/binaries/python/envs/default/bin/activate && dualswitch ui   # 激活 venv
```

> 软链：`ln -s ~/.workbuddy/binaries/python/envs/default/bin/dualswitch ~/.local/bin/dualswitch`
> （`~/.local/bin` 已在 PATH 里；删掉即恢复原状）
> 装依赖时若走代理只有 ~10KB/s，PySide6 会装 20 分钟；用
> `env -u HTTP_PROXY -u HTTPS_PROXY pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[gui,tray,macos]"`。

---

## 1. 阶段进度

| # | 阶段 | 状态 | 证据 |
|---|---|---|---|
| 1 | 调研现成方案并选型 | ✅ 完成 | `docs/RESEARCH.md`，结论：无完全匹配，自研应用层 |
| 2 | 项目骨架 + 配置/日志 | ✅ 完成 | `config.py` / `logging_setup.py`，14 个配置用例通过 |
| 3 | DDC 后端（Win/macOS/command/mock） | ✅ 完成（Win 真机） | `probe` 输出 `MONITOR\AOC2790`、`VCP 0x60 = 17` |
| 4 | 全局热键（Win register + hook，macOS CGEventTap） | ✅ 完成（Win 真机） | `tools/e2e_hotkey.py` 两种模式均 OK |
| 5 | 编排 / 异步回读 / 托盘 / CLI | ✅ 完成 | 38 个 app+ddc 用例；托盘冒烟通过 |
| 6 | 打包与开机自启 | ✅ Windows 已实测 | `packaging/` 下脚本齐全；`dist\dualswitch.exe`（56MB 单文件）已构建并验证 |
| 7 | 文档 | ✅ 完成 | README / RESEARCH / VERIFY / TROUBLESHOOTING / 本文件 |
| 8 | 真机切屏验收 | ✅ **双向闭环** | `Set 18`→Mac（回读确认 64ms）；`Set 17`→Windows（回读确认 64ms）；热键真实触发也已验证 |
| 9 | macOS 实机验证 | ✅ 切屏已验证 | `m1ddc set input 17` 成功；USB-C 真实值 = 18（需求文档的 27 是错的） |
| 10 | macOS 端安装 dualswitch 本体 | ✅ 完成 | `pip install -e ".[tray,macos]"` + 辅助功能授权 + 打包成 `/Applications/屏幕切换.app` |
| 11 | Windows 界面设计稿 + 应用图标 | ✅ 完成 | `design/windows/index.html`、`design/icon/`、`docs/UI-DESIGN.md` |
| 12 | Windows 图形界面实现（PySide6） | ✅ 完成并跑通 | `src/dualswitch/ui/`，`dualswitch ui`；64 个用例全绿 |
| 13 | macOS 界面设计 + 实现（菜单栏 + 原生窗口） | ✅ 完成 | `docs/UI-DESIGN-MAC.md`、`ui/chrome/mac.py`；`⌘,` / `⌥ Space` / 菜单栏右键菜单 |
| 14 | 平台分层（Windows / mac 不撞同一文件） | ✅ 完成 | `ui/chrome/{base,win,mac}.py` + `docs/PLATFORM-SPLIT.md` + `tests/test_chrome_contract.py` |
| 15 | macOS DDC 后端修缺陷（多屏序号 / -118 读失败） | ✅ 完成 | `ddc/macos.py`：自动按名字选屏、内置屏过滤、读失败给人话提示 |
| 16 | macOS 打包（.app / .icns / 菜单栏模板图） | ✅ 脚本与资源就绪 | `packaging/macos/build.sh`、`design/icon/build_icns.py`、`build_menubar_icon.py`；未实跑 PyInstaller |
| 17 | macOS 界面离屏验收（契约 / 窗口 / 设置 / DDC 解析） | ✅ 34 项全通过 | `tools/check_mac_ui.py`；`pytest -q -s` 83 passed |
| 18 | macOS 热键可用性（⌥ 文案 / 左 Option 默认 / 辅助功能授权 / .app） | ✅ 代码与打包完成 | `hotkey/macos.py` 权限链路、`window.show_banner(..., "去授权")`、`dist/屏幕切换.app` |
| 19 | **macOS 热键真机可用（根因修复）** | ✅ **用户实测确认「好用了」** | 根因：`CGEventGetIntegerValueField(event, kCGKeyboardEventFlags)` 字段不存在 → 每次回调抛 AttributeError 被静默吞掉。改 `CGEventGetFlags` + tap 被禁用自动重开；`tests/test_hotkey_mac_tap.py`（10 例）、`tools/diag_macos_hotkey.py` |
| 20 | macOS 装机（/Applications + 登录项） | ✅ 已装并运行 | `packaging/macos/install-app.sh`（ditto + 清 quarantine + 验签 + `--login-item`）；`/Applications/屏幕切换.app`，换路径后辅助功能授权未失效 |
| 21 | 关窗口只留菜单栏（程序坞不留图标） | ✅ 实现完成（待日常使用确认观感） | `chrome.DOCK_FOLLOWS_WINDOW` + `chrome.set_dock_visible()`（`NSApplicationActivationPolicy` 0↔1 实测）、`MainWindow.hiddenToBackground` 信号 |
| 22 | 全量测试 | ✅ 217 passed / 5 skipped | `QT_QPA_PLATFORM=offscreen pytest tests -q --basetemp=.pytest-tmp`（本机 /tmp 不可写） |
| 23 | 跨端剪贴板 · Windows 后端（M1） | ✅ 完成 | `clipboard/windows.py` + `tests/test_clipboard_windows.py`（58 例）；契约 real 14 passed / 3 skipped |
| 24 | 跨端剪贴板 · 剪贴板页 | ✅ 完成（两端入口） | 页面以 macOS 端那版为准（引擎驱动，`tests/test_clipboard_page.py`）；Windows 保留标题栏图标、托盘菜单项、`chrome.EXTRA_H=0`；引擎起不来时退回 `ui/clipboard_feed.py` |
| 25 | 剪贴板 M1：macOS 后端 | ✅ 完成 | `clipboard/macos.py`：NSPasteboard 直连、TIFF→PNG、双写 PNG+TIFF、敏感标记；契约测试真实后端 18 例全绿 |
| 26 | 剪贴板 M2：网络层 | ✅ 完成 | `sync/{protocol,transport,discovery,engine}.py`：帧/加密/对称 TCP/选举/退避重连/环抑制三层 |
| 27 | 剪贴板 M3：接线 | ✅ 完成 | `config.ClipboardConfig`、`SwitchController.on_before_switch`、CLI `dualswitch clip ...` |
| 28 | 剪贴板 M4：界面 | ✅ 完成 | `ui/clipboard_page.py`（列表/预览/操作栏/空态）挂进主窗口；mac 列表 5 行、高度 620 |
| 29 | 剪贴板 M5：自测工具 | ✅ 完成 | `tools/clip_sync_selftest.py` 7/7、`tools/clip_selftest.py` 7/7；结果见 `docs/CLIPBOARD-SYNC-LOG.md` |
| 30 | 跨端剪贴板 · 双端联调 | 🟡 前置全绿，等真机 | Windows 侧四道自测全过：契约 real 14/3、`clip_selftest` 6 PASS/1 MANUAL、`clip_sync_selftest` **7/7**、全量 216 passed；只剩「两端各 `clip pair` 一次 + 10 项人工验证」（需两台机器 + 用户在场），清单见 `docs/CLIPBOARD-SYNC-LOG.md` 第 4 节 |

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

### 2.1 macOS 侧环境事实（2026-10-17 实测）

| 项 | 值 |
|---|---|
| DDC 工具 | `m1ddc`（brew 装），`m1ddc display list` 输出 `[1] (null)`（内置屏）+ `[2] AOC Q2790R3` |
| 序号基准 | **1 基**，且内置屏也在列表里 → 必须按名字挑屏，不能写死序号 |
| 内置屏名字 | `(null)` —— 直接过滤，避免把指令发给笔记本屏 |
| 读通道 | USB-C 输入下 `m1ddc get input` 返回 **-118**（IOKit 错误码）→ 读值不可用，写通道正常 |
| 热键 | Quartz `CGEventTap`，右 Option `keyCode 61` + Space `49`，需「辅助功能」授权 |
| 菜单栏 | 必须有 `NSStatusItem`（PySide6 `QSystemTrayIcon` 在 mac 上不显示菜单栏图标） |
| 权限 | **CGEventTap 必须有「辅助功能」授权**，否则 `CGEventTapCreate` 直接返回空 |
| 授权对象 | TCC 按「负责进程」归属：终端里跑 = 给**终端**授权；双击 .app = 给**屏幕切换**授权 |
| 列表里为何找不到本应用 | TCC 只收录**主动请求过**的二进制 → 必须先 `AXIsProcessTrustedWithOptions(prompt=True)` 弹一次框 |

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
| 21:40 | macOS `pytest -q -s` | 82 passed（mac 侧新增契约测试 + 平台默认值 + 键帽用例） |
| 21:55 | `tools/check_mac_ui.py` | 27/29 → 修 `pretty_hotkey` 后 **29 项全通过** |
| 22:00 | `pretty_hotkey("Right Option+Space")` | `⌥ Space`（原为 `RightOption Space`：先 `replace(" ","")` 把双词修饰键粘成一个词，符号表查不到） |

复现命令：

```bash
# Windows
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m dualswitch probe
python tools/bench_readonly.py
python tools/e2e_hotkey.py
python tools/e2e_hotkey.py --strict

# macOS（系统 python 没装 PySide6，必须用 venv 里的解释器）
PY=~/.workbuddy/binaries/python/envs/default/bin/python
QT_QPA_PLATFORM=offscreen $PY -m pytest tests -q -s -p no:cacheprovider
QT_QPA_PLATFORM=offscreen $PY tools/check_mac_ui.py
$PY -m dualswitch probe
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

### T3 ✅ macOS 端安装 dualswitch 本体 —— 已完成（2026-10-18）

源码方式（开发调试用）：
1. 解压 `dualswitch-1.0.0-mac.zip`
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -e ".[tray,macos]"`（macos extra = pyobjc，CGEventTap 需要）
4. `dualswitch doctor` —— 应显示 `pyobjc/Quartz OK`、`辅助功能权限 OK`（首次要在
   系统设置 → 隐私与安全性 → 辅助功能 里勾选运行它的终端 / Python）
5. `dualswitch run` —— 菜单栏出现图标，⌥ Space 应能切回 Windows
6. 验证：Mac 按 ⌥ Space → 切到 Windows；Windows 按 Alt + Space → 切回 Mac

日常使用（已落地）：
1. `./packaging/macos/build.sh` 打包
2. `./packaging/macos/install-app.sh`（或加 `--login-item` 开机自启）→ `/Applications/屏幕切换.app`
3. 辅助功能授权跟着签名身份走，换路径不用重新勾选（已在真机确认）

### T5（观察项）mac 热键长时间可用性

修好字段取值后用户已实测可用。剩下的风险是系统在回调超时 / 安全输入时关掉 tap：
现在收到 `kCGEventTapDisabledByTimeout|ByUserInput` 会自动 `CGEventTapEnable(tap, True)` 重开，
日志里会留 `事件 tap 回调超时（第 N 次），已重新启用`。若哪天又不灵：
先 `tools/diag_macos_hotkey.py` 看「收到事件数 / 命中数 / 被禁用次数」，再判断是权限、事件没进来、还是判定不匹配。

### T4 ✅ Windows 打包实测 —— 已通过（2026-10-17 20:30，采用 onedir）

```
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# 默认 --onedir；确目标机器没有 Temp 拦截问题时可用 -OneFile 换回单文件
```

| 项 | 结果 |
|---|---|
| 产物 | `dist\dualswitch\` 文件夹（exe 5.2MB + `_internal`，共 138MB），`--windowed` 无控制台 |
| 版本信息 | 产品名「屏幕切换」，文件详情页可识别 |
| `probe` | 正常枚举 AOC2790、VCP 0x60 = 17 |
| `ui --dry-run` | 窗口/托盘正常，进程名 `dualswitch`、窗口标题「屏幕切换」 |
| 可靠性 | onedir 连续启动 8/8 成功；**onefile 双击约 3 成失败**（Temp 被拦截），已弃用 |
| 双击运行 | 不带参数默认进 `ui`（见 `packaging/entrypoint.py`） |

打包踩坑：`--specpath build` 会把 `--add-data` / `--version-file` / `--icon` 的相对路径
基准改到 `build\`，必须用 `$root\` 绝对路径；`screen-switch.ico` 需在
`src/dualswitch/ui/assets/` 里放一份，否则打包后 `theme.asset()` 找不到。
macOS 侧 `packaging/macos/build.sh` 已可用（含 `--specpath` 路径修复 + ad-hoc `codesign`），待真机出包验收。

### T8（待你操作）macOS 界面真机验收清单

离屏能查的都查过了（`tools/check_mac_ui.py` 34 项），剩下这些必须在真机点一遍：

0. **先解决「辅助功能」授权**（这是热键能不能用的唯一开关）：
   · 终端里跑 → 打开「系统设置 → 隐私与安全性 → 辅助功能」，把**「终端」**打开（不是找「屏幕切换」）
   · 双击 .app 跑 → 列表里会出现**「屏幕切换」**
   · 找不到入口就点主窗口横幅上的 **「去授权」**：它会弹系统框 + 直接打开该面板；
     勾选后切回本窗口，程序会自动重试注册（不用重启）
1. `cd ~/work/workbuddy/dualswitch && dualswitch ui`
   （等价写法：`~/.workbuddy/binaries/python/envs/default/bin/python -m dualswitch ui`；
   裸 `python` 是系统解释器，没装 PySide6）
   → 菜单栏出现双屏图标（系统深浅色下都应清晰，模板图自动反色）
2. **单击**图标 → 画面应切到 Windows（单击即切，不是双击；这是 mac 端与 Windows 端唯一的交互差异）
3. **右键**图标 → 出现菜单：`当前画面：Mac` / `切换到 Windows（HDMI）` / `强制切到 Mac` / `设置… ⌘,` / `退出 ⌘Q`
4. 主窗口关闭（红点）→ 窗口消失但不退出进程，图标仍在；再单击图标 → 窗口重新展开
5. `⌘ ,` 打开设置 → 四个页签（热键 / 显示器 / 行为 / 高级）都应正常渲染，高级页后端下拉**不应出现 dxva2**
6. 按 `⌥ Space`（**左** Option，默认值）→ 应切屏。
   设置页热键那一行应显示 `⌥ Space`（不是 `Alt + Space`）；想看右 Option 才打开「只认右 Option」
7. 设置 → 热键 → 录制，按一个新组合 → 按钮文案应变成 `⌃⌥ X` 这类符号写法

---

### T9（待排查，用户同意先放着）辅助功能授权后热键仍不生效

状态：2026-10-17 23:15 授权成功过一次（日志「热键已重建：Option+Space」），
但随后用户反馈**快捷键仍不工作**，界面点按切换正常。

已知线索：
- `AXIsProcessTrusted()` 已经翻过 OK，说明 TCC 这一关通了
- 热键后端是 `cgEventTap`，`CGEventTapCreate` 成功（否则会抛 HotkeyError）
- 所以怀疑点转到「事件是否真的进到回调」：tap 建在独立线程的 run loop 上
  （`hotkey-quartz` 线程），Qt 主线程事件循环不会驱动它 —— 这点在 PyInstaller
  打包环境下可能表现不同（源码方式此前是好的）
- 下一次排查建议：在 `_callback` 里对任意 KeyDown 打一行 DEBUG 日志，
  先确认 tap 有没有收到事件；再区分「收不到」还是「收到了但 `_flags_ok` 判错」

---

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
│   ├── cli.py                   命令分发；run / ui 的启动流程在这里
│   ├── app.py                   SwitchController（切换编排、回读、去抖）
│   ├── config.py                Config / from_dict / validate / load / save / platform_defaults
│   ├── tray.py                  TrayApp（pystray 菜单、通知）
│   ├── logging_setup.py         RotatingFileHandler
│   ├── ddc/base.py              DisplayBackend 协议、MonitorInfo、DDCError
│   ├── ddc/windows.py           dxva2 直调（盲发 Set）
│   ├── ddc/macos.py             m1ddc 子进程封装（按名字选屏、内置屏过滤）
│   ├── ddc/command.py           外部命令模板后端
│   ├── ddc/mock.py              测试/演练
│   ├── hotkey/
│   │   ├── base.py              热键解析、VK/mac keycode 映射
│   │   ├── windows.py           RegisterHotKey + 低级钩子
│   │   ├── macos.py             Quartz CGEventTap
│   │   └── mock.py              ManualHotkeyBackend
│   └── ui/
│       ├── chrome/              ★ 平台差异唯一落点（改外观只碰这里）
│       │   ├── __init__.py      惰性按平台选 win / mac
│       │   ├── base.py          Palette 数据类 + 契约说明
│       │   ├── win.py           蓝主色 / 自绘标题栏 / 双击切 / .ico / deg
│       │   └── mac.py           系统蓝 / 系统标题栏 / 单击即切 / .icns / ⌘⌥⌃⇧
│       ├── theme.py             组装 token 与 QSS（无平台分支）
│       ├── widgets.py           指示灯 / 设备卡 / 状态卡 / 自绘图标
│       ├── statusitem.py        菜单条目纯函数（两端共用）
│       ├── window.py            主窗口
│       ├── settings.py          四页签设置（含热键录制）
│       ├── runner.py            窗口 + 状态项 + 热键 + 控制器接线
│       └── assets/              menubar.png（模板图）、OS 图标
├── tests/                       82 用例，无需硬件（含 chrome 契约测试）
├── tools/
│   ├── bench_readonly.py        只读耗时测量
│   ├── e2e_hotkey.py            端到端热键（dry-run，不切屏）
│   ├── scan_input_windows.py    VCP 0x60 候选值扫描（Windows，带自动切回）
│   ├── diag_macos.sh            macOS DDC 诊断（架构/外接屏/VCP 读值/亮度写入）
│   ├── check_mac_ui.py          mac 界面离屏核对（34 项，无显示器也能跑）
│   └── verify_m1ddc.sh          macOS 强制验证脚本
├── design/
│   ├── icon/                    图标母版 + 10 档 PNG + .ico + .icns
│   │   ├── build_icns.py        手写 ICNS 容器（不依赖 sips/iconutil）
│   │   └── build_menubar_icon.py QPainter 画 36×36 纯黑模板图
│   └── windows/index.html       Windows 界面设计稿
├── packaging/                   Windows/macOS 打包 + 自启（Windows 已实测出 exe）
└── docs/
    ├── UI-DESIGN.md             Windows 界面规格
    ├── UI-DESIGN-MAC.md         macOS 界面规格（菜单栏为主）
    ├── PLATFORM-SPLIT.md        ★ 平台分层契约（另一端 AI 先读这个）
    └── RESEARCH / VERIFY-M1DDC / TROUBLESHOOTING
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
| 平台外观差异只放 `ui/chrome/` | 两端并行改 UI 会撞同一批文件 | 新差异往 `chrome` 加字段，**共享文件禁写 `sys.platform`**；`tests/test_chrome_contract.py` 会拦 |
| mac 端单击图标出菜单、Windows 端双击切换 | 菜单栏图标点一下出菜单是 mac 系统惯例；Windows 托盘单击容易误触 | 由 `chrome.CLICK_HINT` + 各自 `build_status_item` 决定 |
| mac 的设置是窗口内一页 | 点「设置」再弹一个窗口像旧式对话框，用户明确要求翻页 + 返回 | `chrome.SETTINGS_IN_WINDOW`；Windows 仍是独立窗口（自绘标题栏不能嵌） |

---

## 8. 变更日志

- 2026-10-18 18:2x **[merge] 同步 `056c5cc`（macOS 端清理合并残留）+ Windows 侧收尾**。
  mac 端发现 `ui/window.py` / `ui/runner.py` 里各留了两份 `attach_clipboard` / `on_clipboard_page` /
  `show_clipboard` / `close_clipboard` —— Python 靠「后定义覆盖前定义」照样跑、测试也全绿，
  但前一份是死代码，**后一份还丢过功能**（`show_clipboard` 少了 `refresh()` + `setFocus()`，
  表现是翻页后列表空白、键盘不响应）。这是我上一次合并只清了冲突块、漏掉自动合并区留下的。
  · 该提交是快进合并，无冲突。清理后 `show_clipboard_page` 改用 `_set_title()`，
    Windows 自绘标题栏文字能跟着页面变（macOS 版本原来漏了这步）。
  · 顺手做完 mac 端在提交信息里留的活：`chrome/win.py` 的 `clipboardClicked` 信号
    **没有任何接收者**（`window.py` 直接连 `btn_clipboard.clicked`），删掉信号与那行 connect。
  · **新增守卫 `tests/test_no_duplicate_defs.py`**：AST 扫描 `src/dualswitch/**/*.py`，
    同一个类（或模块）里出现同名方法就直接失败并指出「哪个文件、哪两行」。
    这类残留靠跑测试永远发现不了，只能静态查。已用临时文件验证守卫真的会报警。
  · 验证：全量 **217 passed / 5 skipped**；两种模式离屏冒烟（无引擎 → `LocalClipFeed` +
    500ms 轮询 / 有引擎 → `SyncEngine`）翻页、返回、标题、退出全部正常。

- 2026-10-18 18:2x **[merge] 再同步 origin/main：macOS 端补了联调交接说明**（`9385a42`，纯文档）。
  · 新增 `docs/CLIPBOARD-SYNC-HANDOFF.md`：四步自测、联调顺序（先 CLI 定位「没读到/没发出/没收到/没写入」
    四环，最后才做 10 项人工验证）、6 条已踩过的坑。契约第 0 节加了指向它的链接。
  · 该文档写作时还不知道 Windows 侧已完成 M1，所以第 1–4 步是「待办」口吻。**已在其 §0 补上现状对账表**
    （第 1–4 步全部 ✅，只剩真机联调），并把它列的三个未知数实测结论写了进去。
  · 本次把交接文档里那两道闸**真跑了一遍**（此前只有 macOS 端的结论）：
    - `tools/clip_sync_selftest.py` → **7/7**：文本 / 图片 / 自写回弹 / 环抑制 / 重复投递 /
      20MB 大图（6847ms，sha256 一致）/ 断线重连。**证明协议与引擎在 Windows 上同样跑通。**
    - `tools/clip_selftest.py --role windows` → **6 PASS / 0 FAIL / 1 MANUAL**（敏感标记需手工构造）。
    - `python -m dualswitch clip probe` → `formats: PNG, CF_DIB, CF_BITMAP, CF_DIBV5`，
      `png_decoder: qt（可用）`。
  · 冲突只有契约文档一处（两边在同一位置加状态块），合并为「两端都已落地 + 指向交接文档」。
  · 结论：**跨端联调的前置条件已经全部满足，剩下的是需要两台机器和用户在场的部分。**

- 2026-10-18 18:10 **[merge] 合并 `origin/main`（macOS 端剪贴板 M1–M5）到 Windows 端分支**。
  两边并行做了同一个功能，冲突集中在「剪贴板页」这一个文件上。合并口径：
  · **页面以 macOS 端那版为准**（`ClipboardPage(engine)`，由 `SyncEngine` 驱动，有
    `tests/test_clipboard_page.py` 14 例）；本地那版 795 行按 `LocalClipFeed` 取数，删除。
  · **本地独有的一层全部保留**：`clipboard/windows.py`（macOS 端没有 Windows 后端）、
    标题栏剪贴板按钮（`CaptionButton("clip")`）、托盘菜单「剪贴板…」、`theme.py` 的剪贴板 QSS、
    `widgets.Glyph` 的 `clip` 图标。
  · `ui/clipboard_feed.py` 改成**与 `SyncEngine` 同接口**（`ClipRecord` 直接用引擎的 dataclass，
    方法名对齐 `records/status/copy_to_local/repush/drop_record/clear_records`，`status()` 补
    `authenticated`/`peer_device`），定位从「引擎落地前的临时源」变成「引擎起不来时的兜底」。
    runner 里 `clip_engine or LocalClipFeed(cfg)`，本机模式额外挂一个 500ms 采集定时器
    （引擎模式不建，它自己有后台线程）。
  · `window.py` 两边各写了一份 `win_h`（本地是实例属性、远程是方法），统一成方法；
    `attach_clipboard/show_clipboard_page` 用远程版（与设置页同构）。
  · `chrome/{win,mac}.py` 自动合并后留下了重复的 `INLINE_CLIPBOARD_ROW` / `EXTRA_H`，去重；
    标题栏 `titlebar(..., show_clipboard=)` 参数保留（Windows 有按钮，macOS 忽略并返回 None）。
  · 测试：本地那 4 个针对旧页面内部结构（`page.bar` / `page.pv_text` / `selected_id()`）的用例
    删除 —— 同样的行为远程 `test_clipboard_page.py` 已用真引擎覆盖；保留并重写了
    「两端入口」「键盘导航」「本机记录源环形缓冲 + 接口对齐」三条。全量 **216 passed / 5 skipped**。
  · 顺手修一条**偶发失败**：`test_probe_reports_png_format_after_image_write` 在
    `DUALSWITCH_CLIP_TEST=real` 下，若剪贴板此刻被别的程序占着，`probe()` 只报 `state=busy`
    （没有 `has_image` 键）→ 直接 KeyError。改成 state 不是 ok 就 skip，不再误报。
  · 冒烟：离屏真实启动 `UiRunner` 两种模式各跑一遍（无引擎 → `LocalClipFeed` + 500ms 轮询；
    有引擎 → `SyncEngine` + 不建轮询），翻页/返回/退出都正常。

- 2026-10-18 17:35 **[clip-win] 剪贴板页落地（主窗口内一页，不新开窗口）**。
  `start-ui` 里终于能点进去了：Windows 标题栏齿轮左边的剪贴板图标、mac 列表里的「剪贴板」行。
  · 新增 `ui/clipboard_page.py`（按 `design/clipboard/index.html` 还原：页头 40 / 行 52 /
    列表最多 252 / 预览吃剩余高度 / 操作栏 48 / 状态栏 34，440×600 正好不触发窗口加高）；
    图片缩略图 36×36 按 cover 裁切、>8MB 不画；预览区图片等比缩放、文本可选中；
    ↑↓ 切换、Enter/双击复制、Delete 删除、Esc 返回；刷新靠「记录签名」比对，避免每 300ms 重建控件。
  · 新增 `ui/clipboard_feed.py`：**同步引擎落地前的本机记录源**（接口与 `SyncEngine` 对齐：
    `records/status/copy_to_local/resend/remove/clear`），环形缓冲 10 条，退出即清。
    runner 里换成 engine 即可，页面一行不用改。
  · `chrome/win.py`：标题栏加剪贴板按钮（`CaptionButton("clip")` 自绘板身+夹子）+
    `INLINE_CLIPBOARD_ROW=False` / `EXTRA_H=0`；`chrome/mac.py` 补 `True` / `20`（窗口加高 20）。
  · `window.py`：`attach_clipboard/show_clipboard_page/on_clipboard_page`（与设置页同构）、
    标题栏文字跟着页面变、窗口基准高度改用 `theme.WIN_H + chrome.EXTRA_H`。
  · `theme.py` 补剪贴板页 QSS；`widgets.Glyph` 加 `clip` 图标；托盘菜单加「剪贴板…」。
  · 测试：`tests/test_ui.py` 新增 7 例（两端入口、列表/选中/空态、复制与清空、键盘、挂载、
    环形缓冲）。全量 **175 passed / 6 skipped**。
  · ⚠️ 这些文件按契约归 macOS 端，属于越界先做；`sync/*` 未写（用户要求撤回）。

- 2026-10-18 17:20 **[clip-win] Windows 剪贴板后端（M1）落地：纯 ctypes，DIB↔PNG 自己转**。
  按 `docs/CLIPBOARD-SYNC-CONTRACT.md` 只写了 `clipboard/windows.py` 与它的测试。
  · **读**：`GetClipboardSequenceNumber` 做变更检测 + 去重；图片优先取注册格式 `PNG`
    （零转换、字节级原样，跨端 digest 才对得上），没有才 `CF_DIB`/`CF_DIBV5` → PNG。
    DIB 解码支持 1/4/8（调色板）/24/32bpp、top-down（负高度）、行填充、`BI_PNG` 直通；
    32/24bpp 全程走切片（`data[2::3]` 这类，C 速度），4K 图 241ms。
  · **写**：`CF_UNICODETEXT` + `HTML Format`（10 位定宽字节偏移，中文片段按字符算会错位）
    + `PNG` + `CF_DIB`（32bpp `BI_RGB`）。PNG→DIB 需要解码器，优先 PySide6、其次 Pillow，
    都没有就只写 PNG 并记 info —— 纯 Python 解 4K PNG 要十几秒，宁可少一个格式也不卡同步线程。
    另加一条独立验证用例：给 DIB 补 14 字节 BMP 文件头，**Pillow 能解出同样的像素**，
    证明写进剪贴板的 `CF_DIB` 对 Word / Chrome 合法（不是自己验自己）。
  · **环抑制第一道闸**：`write()` 后记序列号，`poll()` 只吞**一次**（吞完不更新 `_last_seq`）。
    这两条同时满足契约测试的两条互相拉扯的用例：`test_write_then_poll_swallows_echo`
    （写后第一次必须 None）与 `test_text_roundtrip_unicode`（第二次必须读得到）。
  · **敏感内容**：`ExcludeClipboardContentFromMonitorProcessing` 出现即跳过；
    `CanIncludeInClipboardHistory` / `CanUploadToCloudClipboard` 只在值为 `0` 时跳过
    （写 `1` 是应用明确表示"可以传"，不该误杀）。
  · **竞态**：序列号变了但数据没落地 → `sleep(10ms)` 重读一次；仍为空才记序号。
    `OpenClipboard` 被占（Office 存盘、复制大文件）时重试 0/5/10/20/40ms，合计 ~75ms。
  · **`_BUSY` 与 `None` 必须分开**：打不开剪贴板时 poll 返回 None 但**不记序号** ——
    混为一谈会把那一次复制永久丢掉（有回归用例 `test_busy_poll_returns_none_without_losing_content`）。
  · **修了契约测试里一个只在 Windows 后端存在后才会触发的崩溃**：
    `_snapshot()` 里 `ctypes.c_wchar_p(GetClipboardData(13))` 没声明 `restype`，
    64 位下句柄被截断成 int32 再当指针解引用 → **访问违例**（实测把 pytest 直接打崩）；
    `_restore()` 则是把 Python 缓冲区指针直接丢给 `SetClipboardData`（不是 HGLOBAL，必然失败，
    剪贴板反被清空）。改为调用 `windows.py` 新增的 `snapshot_text()` / `restore_text()`，
    **没有动任何断言**（该文件仍是两端共用，只修了助手）。
  · 新增 `tests/test_clipboard_windows.py`（58 例）：纯函数层 30 例（DIB 各位深/行序/填充、
    PNG 编解码、`HTML Format` 字节偏移、敏感标记判定）+ 真实剪贴板层 28 例
    （文本/图片/mixed 往返、自写吞掉、digest 去重、`CF_HDROP` 只列不传、被占用时抛错）。
    全量 **175 passed / 6 skipped**（带 `DUALSWITCH_CLIP_TEST=real`）。

- 2026-10-18 17:20 **剪贴板同步（macOS 端）文字 + 图片全部落地**。M1→M5 一次做完：
  · `clipboard/macos.py`：绕开 Qt 直接用 NSPasteboard。Qt 在 mac 上只写 `public.tiff`、
    只读 `application/x-qt-image`，浏览器/Electron 两边都粘不出来。读图片统一重编码成 PNG，
    写图片同时写 PNG + TIFF；命中 `org.nspasteboard.ConcealedType` 的内容整条跳过。
  · `sync/`：帧格式 `DSYN` + header JSON + payload；自研 seal/unseal（SHA256 keystream +
    HMAC 截断）+ counter 单调防重放；对称 TCP（两端都监听也都拨号）。
    **踩坑**：两端同时拨号会互相把对方的连接踢掉（各自"先 ready 的那条"不一致），
    改成"拨号方 device_id 小者胜出"的一致选举规则后才稳定。
  · `engine`：剪贴板只在 worker 线程读写；`push_now()` 只 set 一个事件（<1ms），
    切屏链路挂 `on_before_switch`，不会拖慢 200ms 目标。
  · 自环抑制三层：后端吞自写、引擎按 digest 丢、按 ts 单调拒绝旧内容。
  · UI：`ui/clipboard_page.py` 挂进主窗口页面栈（第 3 页），mac 列表加「剪贴板」行、
    窗口 600 → 620（`chrome.EXTRA_H`）。设置页多一个「剪贴板」页签（现在 5 个）。
  · CLI：`dualswitch clip status|probe|get|pair|send|selftest`；配对是两端输入同一个 6 位码。
  · 测试：153 passed / 12 skipped（新增协议 19 例、引擎 10 例、页面 14 例、mac 后端 10 例）；
    `tools/clip_sync_selftest.py` 7/7、`tools/clip_selftest.py` 7/7。

- 2026-10-18 16:10 **Windows 打包版「关窗口后托盘没图标」根因定位并修掉：exe 带了 Low 完整性标签**。
  真凶不在代码里：`D:\work` 这个目录带 `Low Mandatory Level` 标签且是 `(OI)(CI)` 继承的，
  `dist` 里打出来的 `dualswitch.exe` 全部继承了它 → Windows 以**低完整性级别**启动进程 →
  `Shell_NotifyIcon` 被 UIPI 静默丢弃（explorer 在本机是提权的 High）。
  实测对照（同一份 exe，只改标签）：
  · `Low` → 图标永不出现（Qt 报 `可见=True`、`位置=未知`；pystray 抛 `[WinError 5] 拒绝访问`）
  · `Medium` → 仍不出现（低于 explorer 的 High）
  · `High` → 第 2 次重注册后 `位置=2256,1400 24x40`，图标出现
  这解释了此前所有「换了实现还是没图标」的现象：Qt 与 pystray 走的是同一个
  `Shell_NotifyIcon`，级别不够时两条通道一起死，而 `dualswitch run`（pythonw.exe 从
  `C:\Users\...` 启动，无标签 → 继承终端/资源管理器的 High）从来都是好的。
  · 新增 `src/dualswitch/winenv.py`：读「本进程 / 资源管理器」的完整性级别，
    `blocked()` 纯函数判「会不会被 UIPI 挡」，`fix_hint()` 给出可复制的 `icacls` 命令。
  · `dualswitch ui` 启动即记 `完整性级别：本进程=… 资源管理器=…`；
    runner 在级别不够时**不做无用重试、不切 pystray 兜底**，直接把「关窗口 = 隐藏」
    改成「最小化到任务栏」并在界面挂横幅（不再留下无入口进程）。
  · pystray 兜底不再说谎：线程里的异常会被记下来（`failed` / `error`），
    起不来就保留 Qt 图标并记 ERROR，而不是「已换成兜底实现，图标应立即出现」。
  · `packaging/windows/build.ps1` 打包后自动 `icacls … /setintegritylevel (OI)(CI)High /T`，
    产物不会再继承低标签（High 不会提升进程级别，进程级别 = min(用户, 文件)）。
  · 新增 `tools/fix_integrity.py`：`--self` 看两个级别、`--path X` 查标签、`--set High` 修一次。
  · 排障手册 §4 重写：第一步就看完整性级别；补上 Win10（`TrayNotify\IconStreams`）与
    Win11（`NotifyIconSettings`）的差异，删掉「首次位置未知 = 注册失败」的误判口径
    —— 实测首次注册确实可能没有位置，重注册一次就出现。
  · 测试 +13（`tests/test_integrity.py`：级别判定 / 被挡时跳过重试与兜底 / pystray 失败上报），
    共 105 例通过（`test_hotkey_mac_tap` 改成模块级 skip，Windows 上不再卡在收集阶段）。

- 2026-10-18 13:10 **装上「应用程序」+ 关窗口只留菜单栏（程序坞不留图标）**。
  · 新增 `packaging/macos/install-app.sh`：`ditto` 复制到 `/Applications/屏幕切换.app`
    （保留框架符号链接与签名）、清 `com.apple.quarantine`、校验签名、`--login-item` 加登录项。
    坑：**别在 /Applications 里跑 `codesign --deep`** —— 受限环境下它会留下 `.BC.D_*` /
    `.cstemp` 临时项，把封印搞坏（`unsealed contents present in the root directory`）。
    签名一律在仓库里做好再复制。
  · 新增外观契约 `chrome.DOCK_FOLLOWS_WINDOW` + `chrome.set_dock_visible()`：
    mac 走 `NSApplicationActivationPolicy` —— 窗口收起切 `Accessory`（程序坞图标、⌘Tab、
    应用菜单一起消失，菜单栏 NSStatusItem 不受影响），窗口打开切回 `Regular`。
    Windows 是空实现。
  · `MainWindow.hiddenToBackground` 信号（关闭窗口时发出）；runner 新增
    `_enter_background / _leave_background / hide_window`，`⌘H` 改走 `hide_window`。
  · 不用 `LSUIElement`：那是启动期一次性的，设 true 后窗口打开也不会有程序坞图标。
  · 测试 +2（关窗口发信号 / DOCK_FOLLOWS_WINDOW 只在 mac 为真），全量 96 例通过。

- 2026-10-18 12:40 **mac 热键彻底不响的根因：读了不存在的 CGEvent 字段，异常被静默吞掉**。
  现象：按 `⌥ Space`「偶尔灵一次，之后怎么按都没反应」。
  根因：`hotkey/macos.py` 取修饰键位图写成了
  `CGEventGetIntegerValueField(event, kCGKeyboardEventFlags)` —— CGEvent 的字段枚举里
  **没有**「键盘事件 flags」，pyobjc 的 `Quartz` 里连这个常量都不存在，于是**每个**按键
  回调都抛 `AttributeError`；整段逻辑埋在闭包里又有 `except Exception: pass` 兜底，
  结果就是热键 100% 失效而日志里一句错都没有（那唯一一次「好用」是用户点了菜单栏图标）。
  修法：改用 `CGEventGetFlags(event)`（新增 `event_flags()` 收口）；
  顺带补三处会让热键「用着用着就死」的坑：
  · `kCGEventTapDisabledByTimeout` / `ByUserInput` 必须 `CGEventTapEnable(tap, True)`
    重开，否则系统关掉一次就再也不回来；
  · 回调异常改成「记前 3 次 + 计数」而不是静默 pass；
  · 保留 `CFMachPortCreateRunLoopSource` 引用（被回收 tap 会哑），
    右 Option 判定加 `NX_DEVICERALTKEYMASK(0x40)` 兜底，吞掉 keyDown 同时吞 keyUp。
  验证：新增 `tests/test_hotkey_mac_tap.py`（10 例，假 Quartz 缺常量时抛 AttributeError，
  复现旧 bug）+ `tools/diag_macos_hotkey.py`（实时看事件 / `--simulate` 合成热键）；
  全量 93 例通过；用固定证书重签重打包（`dist/dualswitch.app`），旧包留
  `dist/dualswitch.app.old-20261018`。

- 2026-10-17 23:20 **mac 端交互对齐系统习惯：菜单栏单击出菜单、设置改为窗口内一页**。
  · 菜单栏：`MacStatusItem.on_status_clicked` 不再区分左右键，**单击/右键都弹菜单**，
    切换动作走菜单里的「切换到 …」；`CLICK_HINT` 改为「单击图标打开菜单」
    （原来单击即切，与其它菜单栏工具的心智不一致）。
  · 设置：新增 `chrome.SETTINGS_IN_WINDOW`（mac `True` / win `False`）。
    mac 上点「设置…」不再弹新窗口，而是同一个窗口翻到设置页（600→484 高），
    左上角「‹ 返回」回主页面，保存后也自动回主页面。
    `SettingsWindow` 增加 `embedded` 参数与 `backRequested` 信号；Windows 保持独立窗口
    （自绘标题栏嵌进来会变成两层标题）。
  · 核对脚本补 6 项（翻页/标题/高度/返回按钮/返回后状态）；`check_mac_ui.py` 全通过。

- 2026-10-17 22:44 **「已授权却一直弹窗要授权」的根因：ad-hoc 签名让 TCC 记录按 cdhash 认人**。
  现象：辅助功能里 dualswitch 开关明明是打开的，程序仍报未授权，且系统授权框反复弹。
  两个独立原因叠在一起：
  · **记录失效**：`codesign -d -r-` 显示 ad-hoc 签名的 designated requirement 是
    `cdhash H"..."` —— 纯二进制哈希。TCC 授权时把这条要求存进库，**重新打包换 cdhash
    后旧记录就永远匹配不上**，开关还留在 ON，看起来像"已授权"。修法：把失效条目点「−」
    移除再重新添加（或 `tccutil reset Accessibility com.dualswitch.app`）。
    长期解：用固定身份的证书签名（DR 变成 identifier + 证书），TCC 才能跨重打包存活。
  · **弹窗放大器**：`QuartzEventTapBackend.start()` 每次都 `prompt=True`，而
    `_retry_on_activate` 让每次切回应用都重试一次 → 对话框连环弹。改为
    `accessibility_granted()` 一个进程只放行一次 prompt，用户主动点「去授权」才再弹
    （`request_accessibility()` 绕过抑制）。
  另外提示文案补了两点：TCC 列表里显示的是 `dualswitch`（不是中文名「屏幕切换」），
  以及「开关是开的却仍报未授权 = 旧构建的失效记录，移除后重新添加」。
  新增用例 `test_accessibility_prompt_only_once_per_process`。

- 2026-10-17 22:38 **修复打包版双击闪退：chrome 平台模块没被打进包**。
  现象：`dist/dualswitch.app` 双击后窗口一闪就消失，日志末尾
  `ModuleNotFoundError: No module named 'dualswitch.ui.chrome.mac'`。
  根因：`chrome/__init__.py` 用 `importlib.import_module(f".{name}")` 选平台模块，
  模块名是字符串拼出来的，PyInstaller 静态分析看不见，`chrome/mac.py` 从来没进 PYZ——
  源码方式一切正常，只有冻结版才炸（所以 CI/测试也照不出来）。
  修复：改成静态条件导入（`if sys.platform == "darwin": from . import mac`），
  两个平台模块顶层只 import `base`，不会与 `theme` 成环；
  `build.sh` 补 `--hidden-import dualswitch.ui.chrome.{mac,win}` 兜底。
  加两道防线：① `tests/test_chrome_contract.py::test_chrome_selector_imports_statically`
  用 AST 查动态导入（文档里提到 importlib 不算数）；
  ② `packaging/entrypoint.py --selfcheck` 让产物自己 import 一遍界面栈，
  `build.sh` 构建末尾调用，缺模块直接让构建失败（别用 `strings` 查模块名，
  PYZ 是压缩的，正常产物也查不到）。
  实测：`--selfcheck` 9 个模块全过；`open dist/dualswitch.app` 进程存活。
  ⚠️ 重打包换了 ad-hoc 签名，TCC 会当成新应用：需到「隐私与安全性 → 辅助功能」
  重新勾选一次「屏幕切换」。

- 2026-10-18 15:10 **托盘图标：Qt 通道失灵时自动换 pystray**。
  实测数据（打包版）：`托盘可用=True 图标可见=True 图标为空=False 尺寸齐全`，但
  `位置=未知`，且系统「选择哪些图标显示在任务栏上」里**没有 dualswitch.exe 的条目**
  —— 说明 `Shell_NotifyIcon` 没被系统接受，而 Qt 对此不抛异常。
  · 依据：Qt 上游提交 `ca851b3` 把 Windows 上「图标藏在 `^` 里」的 `geometry()` 统一成
    `QRect()`，所以 `位置=空` 只能说明「通知区域里没有这一格」，`isVisible()` 更不能信。
  · 对策：`_confirm_status_item` 重试两次仍无位置 → `_switch_to_fallback_status()` 换实现。
    Windows 侧新增 `PystrayStatusItem`（pystray + Pillow，和 `dualswitch run` 同一条老路，
    本机已实测能出图标），菜单仍由共享的 `MenuEntry` 翻译（`pystray_menu()`，可单测），
    双击仍等于「切换到对方」。macOS 侧 `build_fallback_status_item()` 返回 None。
  · 同时：`qInstallMessageHandler` 让 Qt 的告警进日志（前缀 `[Qt]`），托盘图标改为
    「主窗口 show() 之后再创建」，新增 `QtStatusItem.recreate()`（NIM_ADD 从未成功时
    只有重建有用）。
  · `MenuEntry` 增加 `default` 字段；`pytest` 101 passed。
- 2026-10-18 13:35 **Windows 打包版「关窗口后托盘没图标、进程还在」——先让它自己说出来**。
  现象：`dualswitch.exe`（打包版）点标题栏关闭后，托盘里没有图标，任务管理器里进程仍存活；
  同一份代码用 `start-ui.bat`（`pythonw.exe`）跑就正常。代码路径完全一致（`QSystemTrayIcon`
  启动时创建、`closeEvent` 只 `hide()`），所以问题不在「关闭逻辑」，而在**图标有没有进通知区域**。
  · 诊断：`QtStatusItem` 现在把注册结果写日志（托盘可用 / 图标可见 / 图标是否为空 / 尺寸 / 位置），
    启动 2.5 秒后再复查一次，不可见就自动重注册（Explorer 重启后图标会丢）。
  · 空图标兜底：`QIcon(路径)` 读不出格式时**不报错、只返回空图标**（打包漏带
    `plugins/imageformats/qico` 时就是这样，任务栏因为用 exe 资源图标看不出来）。
    新增 `runner.load_app_icon()`：空图标 → 改用 `tray.make_icon_image()` 画的箭头图并记 ERROR。
  · 不再制造「无入口进程」：状态项新增 `is_available()`，托盘不可用时把
    `MainWindow.withdraw_to_tray` 置 False，关窗口改为**最小化**（原来一律隐藏）。
  · 关闭路径收敛：自绘标题栏的关闭按钮从 `hide()` 改为 `close()`，统一走 `closeEvent`。
  · 最可能的那条：Win11 对没出现过的新 exe 默认把托盘图标收进「隐藏的图标」溢出面板，
    按可执行文件路径记偏好 —— 所以 `pythonw.exe` 正常、新打出来的 `dualswitch.exe` 第一次就「没有」。
  排障表见 `docs/TROUBLESHOOTING.md` §4（含 `taskkill` 应急退出）。`pytest` 99 passed。
- 2026-10-17 22:10 **打包版诊断能力增强 + 清理调试产物**。
  `packaging/entrypoint.py`：崩溃时同时把完整堆栈打到 stderr、写文件按「默认日志 →
  exe 同目录 → C:\」三级候选依次尝试，弹窗内容带堆栈；
  新增 `DUALSWITCH_FAULTDUMP=<文件>` 环境变量，N 秒未退出即 dump 所有线程堆栈。
  `dualswitch ui` 新增 `--console`（把日志同时打到控制台，打包版排障用）。
  清理：删除 `dist-dbg*/`、`build-dbg*/`、`build/`、根目录临时配置/日志共约 1300 个调试文件。
  说明：`dist/`（打包成品 138MB）保留；本机环境会拦截部分可执行文件写入用户目录，
  源码方式（`start-ui.bat`）不受影响，继续以其为日常入口。
- 2026-10-17 22:05 **修复键帽符号：`⌥ Space` 不再显示成 `RightOption Space`**。
  `theme.pretty_hotkey()` 原来先 `describe.replace(" ", "")` 再按 `+` 切分，把 `Right Option` / `Right Alt`
  这类**本身就带空格的双词修饰键**粘成一个词，`PALETTE.key_glyphs` 查不到就原样输出。
  改为只按 `+` 切分、每段 `strip()` 后再查符号表（并保留去空格二次查表的兜底）。
  `tools/check_mac_ui.py` 由 27/29 → **29 项全通过**；`pytest`（mac）82 passed。
- 2026-10-17 22:00 **测试可在受限环境跑通**。
  `tests/conftest.py` 增加 `_ensure_tmpdir()`：系统临时目录不可写时退到仓库内 `build/tmp`，
  避免只读 `/tmp` / 沙箱禁止删探针的环境里 `tmp_path` 用例整套 ERROR。
  `test_load_missing_returns_defaults` 改为断言 `platform_defaults()`（mac 默认是 `ralt+space`，不是 `alt+space`）。
  mac 上跑测试需带 `-s`（pytest 捕获要写临时文件，先于 conftest 执行）。
- 2026-10-17 21:45 **macOS 界面离屏验收脚本**。新增 `tools/check_mac_ui.py`（29 项）：
  chrome 契约 / 平台默认配置 / 主窗口与设置窗口构建 / 设置真实保存 / m1ddc 输出解析，
  不接显示器也能跑，用于在没有真机交互的情况下守住 mac 端行为。
- 2026-10-17 21:40 **热键三个真实问题一次修掉（真机反馈）**。
  用户实测：① 设置页显示 `Alt+Space`，Mac 键盘上根本没有 Alt 键；② 按 ⌥ Space 没反应；
  ③ 去系统设置授权时列表里找不到本应用。三条其实是两件事：
  · **文案**：`Hotkey.describe()` 在 mac 上返回 `Right Alt+Space`，而键帽印的是 ⌥。
    改为 darwin 直接走 `mac_describe()`（Option/Command），`pretty_hotkey()` 再缩成 `⌥ Space`；
    设置页录制按钮、状态栏、状态卡键帽、菜单栏 tooltip 全部统一（原来设置页还有一处
    自己写的 `" + ".join(raw.replace(" ", "").split("+"))`，会显示成 `RightAlt + Space`，已删）。
  · **按不到**：默认 spec 是 `ralt+space` = **右** Option，而正常人按的是**左** Option。
    mac 默认改 `alt+space`（左），右 Option 留给「只认右 Option」开关；用户已有配置已就地迁移。
  · **授权**：TCC 只会把**主动请求过**的二进制写进「辅助功能」列表，老代码虽然调了
    `AXIsProcessTrustedWithOptions(prompt=True)`，但提示文案让人以为要找「屏幕切换」。
    现在 `permission_target()` 会算出真实授权对象（终端里跑 = 终端，双击 .app = 屏幕切换），
    横幅右侧加了 **「去授权」** 按钮：弹系统框 + 打开面板 + 切回本应用自动重试注册。
  · 打包：`packaging/macos/build.sh` 修掉 `--specpath` 导致的 `--add-data` 相对路径解析错误，
    并补 ad-hoc `codesign`（否则每次重打包都会变成"新应用"，授权要重新勾）。
  验收：`tools/check_mac_ui.py` 新增 7 项（左 Option 文案 / 不出现 Alt / 默认 alt+space /
  授权对象 / 横幅按钮 / 录制按钮符号）全通过；`pytest` 83 passed。
- 2026-10-17 21:20 **平台分层落地（回应「两端会不会撞同一文件」）**。
  新增 `src/dualswitch/ui/chrome/{__init__,base,win,mac}.py`：配色、字体、字号、自绘标题栏有无、
  单击/双击切换、后端下拉选项、应用图标名、菜单栏/托盘实现、通知与打开路径，全部收进各自模块。
  `theme.py` / `statusitem.py` / `runner.py` 改为**零平台分支**（`grep sys.platform` 在 5 个共享文件里为 0）；
  `widgets.CaptionButton` / `TitleBar` 下沉到 `chrome/win.py`，`widgets` 只留共享的 `paint_gear`。
  新增 `tests/test_chrome_contract.py`（17 个字段 + 6 个函数 + 禁平台分支扫描）。
  契约文档 `docs/PLATFORM-SPLIT.md`：「改 X 动哪个文件」对照表 + 并行开发合并流程，供另一端 AI 直接读。
- 2026-10-17 20:50 **macOS 界面实现（菜单栏为主 + 原生标题栏窗口）**。
  `chrome/mac.py`：`NSStatusBar.statusItemWithLength_` 建状态项、模板图 `setTemplate_(True)`、
  左键单击切换 / 右键 `popUpMenuPositioningItem_atLocation_inView_` 弹菜单；
  `build_app_menu()` 用 `QMenuBar` 提供 `⌘,` / `⌘H` / `⌘Q`；`activateIgnoringOtherApps_` 让窗口真的到前台。
  主窗口改系统标题栏（`NATIVE_CHROME=True`，无自绘标题栏），设置行下沉到内容区「设置… ⌘ ,」。
  无 pyobjc 时自动退化到 `QtStatusItem`（Dock 里能看到图标，功能不完整但不崩）。
  规格见 `docs/UI-DESIGN-MAC.md`。
- 2026-10-17 20:30 **Windows 打包改用 onedir（onefile 在该机型不可用）**。
  onefile 启动时要往 `%TEMP%` 解压 `_MEIxxxxxx`，本机实测**双击约有 3 成概率报
  「Could not create temporary directory!」**（连续点 start 5 次失败 2 次；从脚本启动反而不复现，
  判定为系统镜像 / 安全软件对交互启动的进程间歇性拦截 Temp 建目录）。
  改为默认 `--onedir`：产物是 `dist\dualswitch\` 整个文件夹（exe 5.2MB + `_internal`，共 138MB），
  启动不解压 → 从根上规避，启动也更快；8/8 连续启动全部成功。
  `build.ps1` 增加 `-OneFile` 开关（默认 onedir），产物提示同步更新；
  附带 `dist\使用说明.txt`（双击用法、配置/日志路径、任务管理器进程名、安装方式）。
  原 onefile 产物 `dist\dualswitch.exe` 已删除，避免和 `dist\dualswitch\dualswitch.exe` 混淆。
- 2026-10-17 20:30 **macOS DDC 后端三处缺陷**。
  · `m1ddc display list` 序号是 **1 基**且列表里含内置屏 `(null)` → 改为解析后过滤 `(null)`、
    按显示器名匹配、`_display_args()` 自动补 `display N`；
  · `get input` 在 USB-C 下返回 `-118` 被当成「解析失败」→ 改为抛出「读通道不可用（IOKit -118）」的可读错误；
  · mac 默认 `monitor_match="AOC"` 匹配不到外接屏 → `platform_defaults()` 在 mac 上留空（空 = 取第一台）。
  新增 `config.platform_defaults()`，`load()` / `ensure_config_file()` 都走它。
  `hotkey.resolve_backend_name()` 在 mac 上恒返回 `quartz`，兼容从 Windows 拷来的 `register`/`hook` 配置。
- 2026-10-17 20:10 **macOS 打包资源**。`design/icon/build_icns.py` 手写 ICNS 容器
  （`icns` magic + 类型/长度/PNG 块，10 档），产出 `design/icon/screen-switch.icns`；
  `build_menubar_icon.py` 用 QPainter 画 36×36 纯黑模板图（`CompositionMode_Clear` 抠前后屏间隙）；
  `packaging/macos/build.sh` 补 `--icon` / `--add-data` / `--hidden-import AppKit,Foundation`
  与 `PlistBuddy` 的 `CFBundleDisplayName=屏幕切换` / `LSUIElement=false` / `LSMinimumSystemVersion=11.0`。
  `build_icns.sh` 降级为调用 Python 的薄壳（`sips`/`iconutil` 在受限环境会触发安全拦截）。
- 2026-10-17 19:40 **Windows 打包实测通过，产出单文件 exe（T4 完成）**。
  `packaging/entrypoint.py` 改为：**不带参数 = 直接进图形界面**（双击即用），
  带参数按 CLI 语义执行；windowed 模式下没有控制台，因此启动异常会写日志 +
  `MessageBoxW` 弹窗，不再「双击没反应」。
  新增 `packaging/windows/version_info.txt`（产品名「屏幕切换」，任务管理器/文件详情页可直接认出来）。
  `screen-switch.ico` 补进 `src/dualswitch/ui/assets/`（原来只在 design/icon，打包后 theme.asset 找不到）。
  `build.ps1` 的所有路径参数改成 `$root\\` 绝对形式——**`--specpath build` 会把 `--add-data` /
  `--version-file` 的相对路径基准改到 build 目录**（第一次、第二次构建都栽在这）。
  实测：`dist/dualswitch.exe` 56MB 单文件，`probe` 正常读出 AOC2790 / VCP 0x60 = 17；
  `ui --dry-run` 起窗口，任务管理器里进程名为 `dualswitch`、窗口标题「屏幕切换」，与源码方式
  （`pythonw.exe -m dualswitch ui`）区分明显。
  ⚠️ 该 onefile 版本在「双击启动」场景下不稳定（见上一条），已被 onedir 版取代。
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
