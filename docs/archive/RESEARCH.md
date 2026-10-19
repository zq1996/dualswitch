# 现成方案调研（2026-10-17）

调研目标：是否存在「Windows + macOS 双端、全局热键、托盘常驻、能把 AOC Q2790PC 的输入源
在 HDMI / USB-C 之间切换」的开源项目，可直接拿来用或少量改造。

结论：**没有完全匹配的**。最接近的是 DDCommando（跨平台 DDC CLI），但它只提供命令行，
热键、托盘、常驻、失败兜底都要自己补，补完的工作量与自研应用层基本相同；
而它的 DDC 底层实现可以间接复用（macOS 用 m1ddc，Windows 用 dxav2，都是同一套 API）。

---

## 1. 候选对比

| 项目 | 平台 | 切输入源 | 全局热键 | 托盘/菜单栏 | 对 Q2790PC 盲发 | 结论 |
|---|---|---|---|---|---|---|
| [DDCommando](https://github.com/AustinSchoen/DDCommando) | Win + macOS(Apple Silicon) | ✅ `--set-input 17` | ❌ | ❌ | 需自己加 | 只作参考/后备命令后端 |
| [m1ddc](https://github.com/waydabber/m1ddc) | macOS | ✅ `set input 18` | ❌ | ❌ | ✅ | **作为 macOS DDC 后端复用** |
| [Crisp](https://crisp.monster/) | macOS | ✅ 菜单栏点击 | ❌（点菜单） | ✅ | 未知 | 仅 macOS，闭源 |
| [ControlMyMonitor](https://www.nirsoft.net/utils/control_my_monitor.html) | Windows | ✅ `/SetValue 60 17` | ❌ | ❌ | ✅ | 零代码方案的一半 |
| [WinHotKey](https://www.howtogeek.com/) / AutoHotkey | Windows | 靠外部命令 | ✅ | 部分 | — | 零代码方案的另一半 |
| [monitorcontrol](https://github.com/newAM/monitorcontrol) | Win + macOS + Linux | ✅ Python 库 | ❌ | ❌ | 需自己处理 | 库，非应用；macOS 依赖 ddcctl |
| [ddcutil](https://github.com/rockowitz/ddcutil) | Linux | ✅ | ❌ | ❌ | — | 平台不符 |
| [Lunar](https://lunar.fyi/) / BetterDisplay | macOS | 部分支持 | ❌ | ✅ | — | 闭源/收费，输入源切换非重点 |
| [nativedisplaybrightnessplus](https://github.com/huaikitty/nativedisplaybrightnessplus) | macOS | ✅ | ❌ | ✅ | 未知 | 仅 macOS |

### DDCommando 细节（最接近的一个）

- Go + CGO，Windows 走 DXVA2，macOS 走 IOAVService（仅 Apple Silicon）。
- 提供 `--list / --get-input / --set-input <code> / --alias`，可脚本化。
- **没有**全局热键、没有托盘常驻、没有回读兜底、没有配置热键的 UI。
- 它的 `--set-input` 正是我们要做的事，说明技术路径成立；但把它包成常驻热键工具，
  等于自己写一个应用，只是把 DDC 调用换成子进程 —— 反而多一层进程开销和故障面。

### m1ddc 细节（被选为 macOS 后端）

- `m1ddc set input 17` / `m1ddc get input` / `m1ddc display list`。
- 直接走 IOKit 私有接口，是 macOS 上事实标准。
- 复用它可以避免在 Python 里重写 IOKit 绑定（pyobjc 对 IOAVService 支持很差）。

---

## 2. 决策

自研应用层（Python，零核心依赖），DDC 通信按平台复用成熟实现：

| 平台 | DDC 实现 | 理由 |
|---|---|---|
| Windows | `ctypes` 直调 `dxva2.dll` 的 `SetVCPFeature` | 无需第三方包；完全控制盲发行为；失败可重试 |
| macOS | 子进程调 `m1ddc` | 不重复造 IOKit 轮子；用户可自行 `brew install` |
| 任意 | `command` 后端（模板化外部命令） | 兜底：ddcutil / DDCommando / 厂商工具都能接 |

应用层自己负责：热键注册（Win32 `RegisterHotKey` / 可选低级钩子 / macOS `CGEventTap`）、
托盘常驻、去抖、异步回读、日志、配置、开机自启、打包。

### 为什么不选 Tauri / Go + systray

需求文档 §8 建议 Tauri 或 Go + systray，产物小、常驻开销低。实际取舍：

- 本机无 Rust / Go 工具链，引入需要额外安装与构建时间；Python 已就绪。
- 自用工具，常驻内存 Python 约 25–40MB，托盘常驻场景可接受（需求 §5 只要求「以实际观察为准」）。
- Python 版本**核心零第三方依赖**，Windows 侧只用 `ctypes`，没有编译步骤，
  改热键/改 VCP 值不需要重新打包。
- 需要「产物小、双击即用」时，`packaging/` 里的 PyInstaller 脚本可以产出单文件 exe / .app。

如果后续要长期分发或追求极致占用，应用层逻辑（`config.py` / `app.py` / `ddc/`）可以
平移到 Rust/Go，接口已经按后端抽象切开了。

---

## 3. 零代码替代方案（如果不想维护代码）

| 平台 | 组合 | 命令 |
|---|---|---|
| Windows | ControlMyMonitor + WinHotKey / AutoHotkey | `ControlMyMonitor.exe /SetValue "\\.\DISPLAY1\Monitor0" 60 18` |
| macOS | m1ddc + Raycast 脚本 / Karabiner | `m1ddc set input 17` |

缺点：两套东西互不知情，没有统一日志，热键冲突要自己排查，切走失败时没有兜底。
本项目存在的意义就是把这部分收敛成一个常驻进程。

---

## 4. 参考

- MCCS VCP 0x60 = Input Source Select；17 = HDMI-1，18 = HDMI-2，15 = DP-1，27 = USB-C（厂商相关）。
  **实测提醒**：AOC Q2790PC 上 18 才是 USB-C，27 被拒绝 —— 别照抄标准值，必须实测。
- DDC/CI 规范：VESA Monitor Control Command Set。
- `dxva2.dll`：`GetPhysicalMonitorsFromHMONITOR` / `SetVCPFeature` / `GetVCPFeatureAndVCPFeatureReply`。
