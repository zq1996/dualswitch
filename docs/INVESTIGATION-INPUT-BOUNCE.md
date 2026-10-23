# 输入源回弹调查（交接文档）

> 2026-10-23 晚，Windows 端排查记录。**给接手的人看：先读第 1、2 节，别急着改代码。**
> 这次排查反复推翻了三次结论，第 2 节列了所有走过的弯路，务必先看。

---

## 0. 一句话

**核心问题：Mac 端点「切换到 Mac」/「切换到 Windows」都不工作，而对端代发从来没被触发过。**

Windows 日志里**零条**「替对端切屏」—— Mac 端根本没有发出过 `T_SWITCH` 请求。
所以问题不在链路、不在代发协议，而在 **Mac 端压根没打算请人代发**。

> ⚠️ **两个现象必须分开，别混为一谈**（本文件初版混了，导致结论跑偏）：
>
> | 现象 | 触发方式 | 实际行为 |
> |---|---|---|
> | **A. 我调试时** | 直接调 `backend.set_input(18)` | 屏切到 Mac，2–3 秒后自己回到 Windows |
> | **B. 用户点软件按钮** | `_execute()` → `set_input(18)` | 回读报成功，但屏**根本没动**（`detect` 一直判 windows） |
>
> A 和 B 调的是**同一个方法**，行为却不同 —— 差异很可能在并发：
> `_execute` 期间 `_readback` / `_detect_once` 也在访问 DDC 总线（串行总线，会互相打断）。
> 但**这不是当前的主要矛盾**，先解决 Mac 端不发请求的问题。

---

## 1. 确定的事实（有证据，不要重新怀疑）

| # | 事实 | 证据 |
|---|---|---|
| 1 | **调试时**直接调 `set_input(18)`：能切到 Mac，Mac 上出画面 | 用户目视（调试场景，非按钮） |
| 2 | 调试时切过去后 **2–3 秒自动回到 Windows** | 用户目视（调试场景，非按钮） |
| 2b | **点软件按钮时**：回读报 `VCP 0x60 = 18`，但屏没动，`detect` 一直判 windows | 日志 `21:50:09.446 → 21:50:16.645` |
| 3 | 显示器完成一次切换要 **2–3 秒** | 用户确认 |
| 4 | 显示器**没有**「自动识别输入源」功能 | 用户确认 |
| 5 | Windows 日志里**零条**「替对端切屏」⇒ Mac 从没发过 `T_SWITCH` | `%APPDATA%\dualswitch\logs\dualswitch.log` 全量 grep |
| 6 | Mac 端点「切换到 Mac」「切换到 Windows」两个方向都不好用 | 用户报告 |
| 7 | 两端已配对（`windows-b2053759` / `mac-68cdd6d1`），链路会间歇连通 | 日志 20:38:03 配对成功 |
| 8 | Mac 端应用没开时 47823/47824 拒绝连接（`WinError 10061`），开了立刻通 | 端口实测 |
| 9 | `VCP 0x60` 的 `max = 14`（异常低，正常应是输入源掩码 0x12+） | `probe_input()` |
| 10 | Mac 端 IP `192.168.3.18`；`config.json` 里 `clipboard.peer` 是空串，靠 UDP 47824 发现 | 配置文件 |

---

## 2. 已被证伪的假设（**不要再走这些路**）

| # | 错误假设 | 为什么错 |
|---|---|---|
| 1 | 剪贴板开关关掉导致代发不可用 | 用户剪贴板一直开着，与场景无关。解耦改动本身不算错，但不是病根 |
| 2 | 显示器 Auto Source 抢输入源 | 用户明确确认没有这个功能 |
| 3 | **Windows 的 DDC 写入无效** | 用户目视确认能切过去。我误把「Bash 能看到输出」当成「屏没切」 |
| 4 | 时序参数太短导致切不过去 | 参数确实偏短（已修），但它只影响「回读误判」，不影响「能不能切」 |
| 5 | 用 `EnumDisplayMonitors` / hmon 观测切换 | Windows 对切走的显示器**保留幻影条目**，hmon 恒为 1，不可信 |

---

## 3. 核心待查问题

**Mac 端点切换时，为什么没有发出 `T_SWITCH`？**

`_try_remote` 只有两个触发条件（`app.py` 的 `_execute`）：

1. 本次运行从没枚举到过外接屏 → 先试代发
2. 本地 `set_input` 抛 `DDCError` → 试代发

所以「不发请求」只有两种可能：

- **M1. Mac 本地 `set_input` 没抛异常**：`m1ddc` 返回 0（假成功）
  ⇒ 不进 `except DDCError` ⇒ 不触发代发
- **M2. Mac 判「画面在本机」，压根没打算切到 Mac**：
  点「切换到 Windows」发的是 17（屏已在 17，无变化），
  永远不会请求代发 18 ⇒ 用户看到的就是「点了没用」

**M2 的根因**：Mac 用 `m1ddc display list` 判断归属。屏切到 HDMI 后，
USB-C 链路的 DCP 代理还在，**照样列得出这块屏** ⇒ `present=True`
⇒ 永远判「画面在本机」。而 Windows 的 `EnumDisplayMonitors` 只枚举有活动信号的屏，
两端语义根本不对称。

M1 和 M2 会叠加：判错了目标，就算真发了也发不出去。

---

## 4. 建议的下一步（按信息量排序）

### 实验 1（最高优先级，1 分钟，直接在 Mac 上跑）

屏**保持在 Windows**（就是现在这个状态），在 Mac 终端执行：

```bash
m1ddc display list
```

- **列出了 Q2790 / AOC** ⇒ **M2 成立**，归属判据必须换
  （改用 CoreGraphics `CGGetActiveDisplayList` 数「活动外接屏」，
  它只返回真正在输出画面的屏）
- **没列出** ⇒ 判据没问题，转去查 M1

顺带跑这两条，确认「假成功」是否存在：

```bash
m1ddc get input      # 读当前输入源（USB-C 下可能返回负数，已知）
m1ddc set input 17   # 屏在 Windows 时：报错=正常，静默返回 0=假成功
```

### 实验 2（验证「回弹」是谁造成的，次要）

切到 Mac 后**立刻物理拔掉 Windows 的 HDMI 线**（或者拔显示器端）：

- Mac 画面**保持** → Windows 侧 HDMI 信号是罪魁
- Mac 画面**仍消失** → 显示器 / Mac 侧问题

### 实验 3

切到 Mac 时用**手机录像**（别用这台电脑录，屏切走就没了），看：
- Mac 画面持续多久
- 消失前有无「信号搜索 / 分辨率切换 / 无信号」提示

### 实验 4

显示器 OSD 里找：`USB-C 优先` / `信号源锁定` / `DisplayPort 版本` / `DP Alt Mode` 等设置。

---

## 5. 观测工具的坑（这次踩得最惨，务必记住）

| 工具 | 为什么不能用 |
|---|---|
| **Bash 命令输出** | 命令结束后**一次性返回**。「我能看到输出」不能证明屏没切——切走那几秒我这边是黑屏，只是看不到 |
| `probe_input()` / `get_input()` | 读的是**写入回显**。写 18 就读到 18，几秒后自己回到真实值，不能反映硬件输入源 |
| `EnumDisplayMonitors` / hmon | 对切走的显示器**保留幻影条目**，恒为 1 |
| `list_monitors()` | 同上（底层也是 EnumDisplayMonitors + DXVA2） |

**只有这三种观测算数**：用户眼睛、手机录像、独立于 DDC 的第三方观测（如显示器 OSD 自己显示的输入源）。

---

## 6. 当前代码状态（都在 `origin/main`）

今天推了 6 个提交，**都没解决核心问题**，但其中几项是合理改进，不要盲目回滚：

| 提交 | 内容 | 是否该保留 |
|---|---|---|
| `6543888` | 归属判定补「对端在线」证据 + 拆 `_display_source` | 保留（修了冷启动永远 unknown） |
| `0cd7c12` | 剪贴板与链路解耦 + T_STATE 联合判定（0x32/0x33） | 保留（协议层，**未真机验证**） |
| `fcd7f78` | 代发失败不再静默，错误带对端原因 | 保留（诊断能力） |
| `843d68f` | 回读读到目标值后复读，识别回弹 | 保留 |
| `5ac8c3b` | 时间参数按「切换 2–3 秒」对齐 | 保留 |

关键参数现状：
```
detect_settle_s   = 6.0     # 必须 > 显示器切换耗时（2–3 秒）
readback.delay_ms = 3000    # 必须等切换完成再读
readback.timeout_ms = 8000  # 必须 > delay_ms
```

> ⚠️ 用户机器上的 `%APPDATA%\dualswitch\config.json` 已被改成上述值，
> 原文件备份为 `config.backup-2144.json`。

---

## 7. 已知但未处理的 Mac 端缺陷

这两条**独立于回弹问题**，但会让「对端代发」永远不触发：

1. **归属判据错**：Mac 用 `m1ddc display list` 判断「屏在不在本机」。
   屏切到 HDMI 后，USB-C 链路的 DCP 代理还在，**照样列得出这块屏**
   ⇒ `present=True` ⇒ 永远判「画面在本机」。
   Windows 的 `EnumDisplayMonitors` 只枚举有活动信号的屏，两端语义不对称。
   建议改用 CoreGraphics `CGGetActiveDisplayList` 数「活动外接屏」。

2. **本地 Set 假成功**：Mac 通过 USB-C 发 Set，屏在 HDMI 时显示器会忽略，
   但 `m1ddc` 返回 0（成功）⇒ 不进 `except DDCError` ⇒ 永远不触发 `_try_remote`。

---

## 8. 日志与配置位置

```
Windows: %APPDATA%\dualswitch\config.json
         %APPDATA%\dualswitch\logs\dualswitch.log
Mac:     ~/Library/Application Support/dualswitch/   （待确认）
         ~/.dualswitch/
```

常用检索：
```bash
grep -aE "远端代发|替对端切屏|切换失败|检测到画面" dualswitch.log
grep -a "剪贴板连接状态" dualswitch.log | tail -20
```
