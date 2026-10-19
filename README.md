# dualswitch · 双系统单显示器切换助手

> 应用显示名：**屏幕切换**（包名、CLI、配置文件目录仍为 `dualswitch`）
> Windows 界面设计稿见 [design/windows/index.html](design/windows/index.html) 与 [docs/UI-DESIGN.md](docs/UI-DESIGN.md)；
> 应用图标见 [design/icon/](design/icon/)（透明底 1024 母版 + 多尺寸 ICO）。

一台显示器同时接 Windows（HDMI）和 Mac（全功能 USB-C / DP Alt Mode），
在**当前看得见的那台机器**上按一个热键，显示器立刻切到另一台。

- 热键：Windows `Alt + Space`，macOS `Right Option + Space`
- 切换方式：DDC/CI 盲发 `SetVCPFeature(0x60, 17|18)`，不读当前源、不探测能力集
- 常驻：托盘 / 菜单栏，开机自启
- 剪贴板：不自己做，交给 [CrossPaste](https://crosspaste.com) 独立运行（与本工具完全解耦）

> 适配目标机型：AOC Q2790PC。实测输入源值：**17 = HDMI，18 = USB-C，15 = DP**（网上常见的 27 会被这块屏直接拒绝）。
> HDMI 通道的 VCP 读回 `max=14` 是乱码，所以切换只发不收。

---

## 1. 为什么不用现成工具

GitHub 上没有同时满足「Windows + macOS 双端 + 全局热键 + 托盘常驻 + 对 Q2790PC 盲发」的项目。
最接近的几个各有缺口（详见 [docs/archive/RESEARCH.md](docs/archive/RESEARCH.md)）：

| 项目 | 覆盖 | 缺口 |
|---|---|---|
| [DDCommando](https://github.com/AustinSchoen/DDCommando) | Win + macOS，DDC 切输入源 | 只有 CLI，无热键/托盘；macOS 仅 Apple Silicon |
| [m1ddc](https://github.com/waydabber/m1ddc) | macOS DDC CLI | 只有 CLI，无热键/托盘 |
| [ControlMyMonitor](https://www.nirsoft.net/utils/control_my_monitor.html) + WinHotKey | Windows 零代码方案 | 仅 Windows，需装两个软件并手工配置 |
| [Crisp](https://crisp.monster/) | macOS 菜单栏切输入源 | 仅 macOS |
| [monitorcontrol](https://github.com/newAM/monitorcontrol) | Python 跨平台 DDC 库 | 库而非应用，macOS 侧依赖 ddcctl |

所以本项目自己写应用层，DDC 通信复用成熟实现（Windows 直调 `dxva2.dll`，macOS 调 `m1ddc`）。
如果你只想要「零代码」，README 末尾给了等价替代方案。

---

## 2. 快速开始

### Windows

```powershell
git clone <repo> dualswitch && cd dualswitch

py -3 -m venv .venv
.\.venv\Scripts\pip install -e ".[tray,gui]"   # gui = PySide6 图形界面

.\.venv\Scripts\dualswitch probe     # 只读探测，不会切屏
.\.venv\Scripts\dualswitch doctor    # 环境自检
.\.venv\Scripts\dualswitch ui        # 图形界面（推荐）
.\.venv\Scripts\dualswitch run       # 无窗口：托盘 + 热键

# 或者直接双击 start-ui.bat
```

> **别直接敲 `python`**：本机 PATH 里第一个 `python` 是 `C:\Python27\python.exe`（Python 2.7），
> 会报 `No module named dualswitch`。项目装在 venv 里，两条路走：
> 用 `\.venv\Scripts\dualswitch`（没有 `.venv` 时见下），或者用仓库根的两个包装脚本 ——
> `start-ui.bat`（开界面）、`clip.bat status|probe|get|send|pair|selftest`（剪贴板命令），
> 它们会自动找到装好依赖的那个解释器。

### macOS

```bash
brew install m1ddc                    # DDC/CI 后端
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tray,macos]"        # macos extra = pyobjc

m1ddc set input 17                    # 【开发前强制验证】应切到 Windows 画面
m1ddc set input 18                    # 【开发前强制验证】应切回 Mac 画面（USB-C = 18）

dualswitch probe                      # 只读探测
dualswitch run                        # 常驻：菜单栏 + 右 Option + Space
```

首次运行 macOS 会提示授权：**系统设置 → 隐私与安全性 → 辅助功能**，勾选
运行本程序的终端 / Python / `dualswitch.app`。没有这个权限，`CGEventTap` 会返回空。

---

## 3. 工作原理

```
按下热键（热键线程）
   │  只做去抖 + 入队，微秒级返回
   ▼
工作线程 ──► 重新枚举物理显示器（~2ms）──► SetVCPFeature(0x60, 17|18)（~60ms）
   │
   ├─► 记录「触发 → 发出」延迟，>200ms 打警告
   └─► fork 回读线程：+300ms 后读一次 VCP 0x60，1s 内最多试 N 次
          ├─ 等于目标值 → INFO「回读确认」
          └─ 失败/不符 → INFO「回读未确认」（Q2790PC HDMI 通道已知乱码，只记录不打扰）
```

几个刻意的设计：

- **每次切换都重新取句柄**。切走之后显示器会从系统消失，缓存句柄必然失效；重新枚举只要 2ms。
- **不做能力集探测**。Q2790PC 的 HDMI 通道读回是乱码（本机实测 `cur=17, max=14`），
  任何基于读值的判断都会误判。
- **回读不阻塞**。切到 Mac 的瞬间 Windows 已经看不到显示器，回读必然失败，所以默认只写日志。
- **失败不崩**。`set_input` 的异常全部被工作线程兜住，进程继续常驻，托盘仍可手动切换。

---

## 4. 配置

配置文件位置：

| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\dualswitch\config.json` |
| macOS | `~/Library/Application Support/dualswitch/config.json` |

首次 `run` 自动生成。也可用 `--config <path>` 或环境变量 `DUALSWITCH_CONFIG` 指定。
完整字段见 [config.example.json](config.example.json)。

最常改的几项：

```json
{
  "hotkey": "alt+space",
  "sources": { "windows": 17, "mac": 18 },
  "monitor_match": "AOC",
  "readback": { "enabled": true, "notify_on_unconfirmed": false }
}
```

| 字段 | 说明 |
|---|---|
| `hotkey` | 语法 `修饰键+主键`；`alt`/`ralt`/`ctrl`/`shift`/`win`，主键 `space`、字母、`f1`-`f12` 等 |
| `hotkey_backend` | `auto` / `register` / `hook`；Windows 上 `ralt+space` 会自动选 `hook`（严格右 Alt） |
| `sources` | VCP 0x60 的目标值。AOC Q2790PC 实测：HDMI=17，USB-C=18，DP=15（27 会被拒绝） |
| `this_host` | `auto` 按平台判断；也可强制 `windows`/`mac` |
| `switch_mode` | `other`（默认，切到另一台）/ `toggle`（在两侧翻转） |
| `monitor_match` | 物理显示器匹配子串，命中 `device_id`（如 `MONITOR\AOC2790\...`） |
| `ddc_backend` | `auto` / `dxva2` / `m1ddc` / `command` / `mock` |
| `command_template` | `ddc_backend=command` 时的外部命令，如 `["ddcutil","setvcp","60","{value}"]` |
| `dry_run` | `true` 时只记录日志，不发 Set（演练用） |

热键冲突（被输入法/截图工具占用）时，`run` 会打印可操作提示并继续运行，托盘菜单仍可用。

---

## 5. 命令

| 命令 | 作用 |
|---|---|
| `dualswitch ui` | **图形界面**：主窗口 + 托盘 + 热键（需 `.[gui]`） |
| `dualswitch run` | 无窗口常驻（托盘 + 热键） |
| `dualswitch run --no-tray --dry-run` | 无界面演练，只写日志 |
| `dualswitch switch --to mac` | 立刻切一次（`--wait-readback` 等回读结果） |
| `dualswitch probe [--json]` | **只读**探测：显示器列表 + 当前 VCP 值 |
| `dualswitch doctor [--json]` | 环境自检：热键可注册性、DDC、权限、日志目录 |
| `dualswitch config init/show/path` | 配置管理 |
| `dualswitch clip pair --pin 123456` | **跨端剪贴板配对**：两端输入同一个 6 位码（写入配置，终身有效） |
| `dualswitch clip status` | 看配对/连接状态：`已配对`、`认证通过`、`对端设备` |
| `dualswitch clip probe` | 本机剪贴板格式（排查「另一端收不到」时先看这个） |
| `dualswitch clip get` / `send "文字"` | 手工读一次 / 推一条（联调时定位「哪一环断了」） |
| `dualswitch clip selftest` | 本机格式矩阵自测 |

---

## 6. 开机自启与打包

**Windows**

```powershell
# 打包（默认 --onedir：产物是文件夹 dist\dualswitch\，整个文件夹一起拷走）
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

# 需要单文件 exe 时才加 -OneFile。本机实测单文件双击约三成概率报
# "Could not create temporary directory!"（安全软件间歇拦截 %TEMP% 建目录），所以默认用 onedir
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -OneFile

# 开机自启（启动文件夹快捷方式）
powershell -ExecutionPolicy Bypass -File packaging\windows\install_autostart.ps1 -Exe .\dist\dualswitch\dualswitch.exe
```

**macOS**

```bash
./packaging/macos/build.sh              # 打包装箱，生成 dist/dualswitch.app
./packaging/macos/install-app.sh        # 装进 /Applications/屏幕切换.app
./packaging/macos/install-app.sh --login-item   # 顺便加进登录项（开机自启）
```

> `.app` 就是标准的 macOS 应用，拷到「应用程序」文件夹即可，和 App Store 下的应用
> 在系统眼里没有区别（区别只在签名：本机用自签名证书 `dualswitch Self Signed`，
> 没有 Apple 公证，**换一台 Mac 打开会被告警拦住**，需要右键 → 打开）。
> 换路径不影响辅助功能授权（授权认的是「签名身份 + bundle id」，不是路径）。
>
> 从终端跑源码时用 launchd 那条路：`./packaging/macos/install.sh "$(which python3)"`。
> launchd 拉起的是非交互进程，菜单栏图标可能不显示，日常用还是装 .app。
> macOS 用 launchd 拉起时是非交互进程，菜单栏图标可能不显示；想要稳定图标和
> 稳定的辅助功能授权身份，用 `.app` + 登录项。

---

## 7. 验收对照（需求文档 §6）

| 验收项 | 状态 | 证据 |
|---|---|---|
| 1. 双端热键切屏，2 秒内出画面 | ✅ 两端均已真机验证 | Windows→Mac：热键触发后 VCP 变 18；Mac→Windows：`⌥ Space` → `m1ddc set input 17`（用户实测确认） | `tools/e2e_hotkey.py` + 真机 |
| 2. 触发到 Set 发出 ≤ 200ms | ✅ 实测 56–64ms（Set 调用本身），枚举 2ms | 真机日志 + `tools/bench_readonly.py` |
| 3. 回读静默通过 / 不确认时只记日志、不崩溃 | 已覆盖 | `tests/test_app.py` |
| 4. 快捷键可配置并生效 | 已满足 | `tests/test_config.py`、`tests/test_hotkey.py` |
| 5. 与 CrossPaste 互不干扰 | 结构上解耦（无任何集成代码） | 无相关依赖 |

详细进度与「中断后如何继续」见 [PROGRESS.md](PROGRESS.md)。

---

## 8. 目录

```
dualswitch/
├── src/dualswitch/
│   ├── config.py          配置加载/校验/落盘
│   ├── app.py             切换编排（热键→Set→异步回读）
│   ├── tray.py            托盘/菜单栏（pystray，无窗口模式）
│   ├── ui/                图形界面（PySide6）：window/settings/widgets/theme/runner
│   ├── cli.py             命令行
│   ├── ddc/               windows.py(dxva2) / macos.py(m1ddc) / command.py / mock.py
│   └── hotkey/            windows.py(RegisterHotKey+低级钩子) / macos.py(CGEventTap)
├── tests/                 96 个用例，无需硬件
├── tools/                 bench_readonly.py / e2e_hotkey.py / diag_macos_hotkey.py / verify_m1ddc.sh
├── design/                windows/index.html（界面设计稿）+ icon/（透明母版 / PNG / ICO）
├── start-ui.bat           双击启动图形界面（无控制台）
├── packaging/             windows + macos 打包与自启（macos/install-app.sh 装进「应用程序」）
└── docs/                  现状文档（CONTRACT / PLATFORM-SPLIT / UI-DESIGN* / TROUBLESHOOTING）+ archive/（历史记录）
```

---

## 9. 已知限制

- Windows `RegisterHotKey` 不区分左右 Alt：默认 `alt+space` 会响应任意 Alt+Space。
  要严格只认右 Alt，把 `hotkey` 改成 `ralt+space`（自动切到低级钩子后端，已实测可区分左右）。
- 部分全屏游戏 / 反作弊环境会屏蔽 `RegisterHotKey` 与键盘钩子。
- Alt+Space 在少数应用里被窗口系统菜单占用，可能不触发；换 `ctrl+alt+space` 即可。
- 只支持单显示器；多显示器不在 V1 范围。
- macOS 包是自签名（`dualswitch Self Signed`）、未公证：**在别的 Mac 上首次打开要右键 → 打开**。
  辅助功能授权认签名身份，重打包不会失效，但换成 ad-hoc 签名就会失效。
- macOS 关窗口后程序坞图标消失、只留菜单栏图标（`NSApplicationActivationPolicy` 切换），
  要找回来点菜单栏图标 →「打开主界面」。
- CrossPaste 的 mDNS 发现依赖 UDP 5353，被路由器/防火墙拦截时改用手动 IP 连接（与本工具无关）。

---

## 10. 如果你只想要零代码方案

- **Windows**：[ControlMyMonitor](https://www.nirsoft.net/utils/control_my_monitor.html)
  `ControlMyMonitor.exe /SetValue "\\.\DISPLAY1\Monitor0" 60 18`，再用 AutoHotkey / WinHotKey 绑键。
- **macOS**：`m1ddc set input 17`，用 Raycast / Karabiner / 快捷指令绑一个热键。
- **macOS 图形界面**：Crisp（菜单栏点一下切输入源）。

代价是要维护两套互不相关的东西，且没有统一的托盘状态与日志。本项目就是为了省掉这部分。
