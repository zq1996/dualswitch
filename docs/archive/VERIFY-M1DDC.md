# 开发前强制验证：m1ddc 对 AOC Q2790PC 的输入源切换

> 对应需求文档 §3.2 / §9。这是整份需求里**唯一尚未实测闭环**的环节，
> 也是核心功能成立的最终判据。**没通过就不要往下写功能代码。**
>
> ✅ **结论（2026-10-17 已闭环）**：DDC/CI 路径成立。
> 但这块屏的输入源值是 **17 = HDMI，18 = USB-C，15 = DP**，
> 需求文档假设的 **27 是错的**（27 被显示器直接拒绝）。

## 为什么必须先验证

需求假设 `27 = USB-C`、`17 = HDMI-1`。这两个值来自 MCCS 常见取值 + m1ddc 文档，
但 AOC 的 USB-C 输入源编号并不保证符合标准。

**实测结果证明这个怀疑是对的**：27 被显示器拒绝，真实值是 18。如果直接照文档写死 27，
代码能跑、日志显示"已发送"，但画面永远不动 —— 这正是必须先在真机上验证的原因。

## 步骤

在 **Mac** 上（显示器用全功能 USB-C 连接），终端执行：

```bash
brew install m1ddc

# 先看能不能看到显示器
m1ddc display list

# 方向一：Mac → Windows（USB-C → HDMI）
m1ddc set input 17
# 期望：显示器 2 秒内切到 Windows 画面

# 方向二：Windows → Mac（HDMI → USB-C）
m1ddc set input 18          # 注意：不是 27
# 期望：显示器切回 Mac 画面
```

也可以直接跑本仓库的脚本（带倒计时、逐项确认、最后打印回传报告）：

```bash
chmod +x tools/verify_m1ddc.sh

./tools/verify_m1ddc.sh --status   # 只读：显示器列表 + 当前输入源，不切屏
./tools/verify_m1ddc.sh            # 验证 17 / 18 两个方向（脚本里 VCP_MAC 默认 18）
./tools/verify_m1ddc.sh --scan     # 值不对时，自动逐个候选值找 USB-C 通道
```

脚本最后会打印一段 `===== 把下面这段贴回给助手 =====`，把那段贴回来即可，
不用手工整理结论。

> 脚本必须在 **Mac 本机终端**里跑：需要肉眼确认「画面切过去了吗」。
> 通过 SSH 执行只能看到返回码，看不到画面。

## 通过标准

- 两个方向都成功切换；
- 显示器无异常（不黑屏卡死、不闪回、OSD 不报「无信号」）；
- 切回后画面正常，Mac 的系统设置里能看到显示器。

## 实测记录

| 日期 | 环境 | 结果 | 备注 |
|---|---|---|---|
| 2026-10-17 15:5x | Mac + USB-C | ✅ `m1ddc set input 17` 成功切到 Windows | 前提：显示器必须先处于 USB-C 输入，否则 DDC 链路不存在 |
| 2026-10-17 16:0x | Windows + HDMI | ⚠️ `Set 0x60=27` 返回成功但显示器不动 | 需求文档的 27 是错的 |
| 2026-10-17 16:1x | Windows + HDMI | ✅ 扫描确定 `18 = USB-C` | 用户目视确认：15 = DP（黑屏）、18 = Mac 画面 |
| 2026-10-17 16:2x | 双端 | ✅ **双向闭环**：`Set 18` → Mac（回读确认，64ms）；`Set 17` → Windows（回读确认，64ms） | 热键 Alt+Space 真实触发也已验证 |

### VCP 0x60 实测值（AOC Q2790PC，2026-10-17）

在 Windows/HDMI 侧逐值扫描，判据是「Set 后回读 0x60 是否变化」：

| 值 | 结果 | 推断 |
|---|---|---|
| 15 | ✅ 接受 | **DP**（用户目视：切过去黑屏，说明该口没接设备） |
| 17 | ✅ 接受 | **HDMI**（Windows 所在，已确认） |
| 18 | ✅ 接受 | **USB-C**（用户目视：切过去出现 Mac 画面）✅ |
| 19–26 | ❌ 拒绝（回读仍 17） | 非法值 |
| 27 | ❌ 拒绝 | **不是这块屏的 USB-C**（与需求文档假设不符） |
| 28 | ❌ 拒绝 | 非法值 |

**结论**：这块屏只认 `15 / 17 / 18` 三个输入源值，说明它确实只有三个视频口。
27 被直接拒绝，不是「切过去但没信号」，而是**值本身非法**。

同时验证了 HDMI 通道上的 DDC 写是通的：亮度 `99 → 40 → 99` 写入并成功回读。
所以问题不在 Windows 侧代码，纯粹是 **USB-C 的 VCP 值不是 27**。

### 最终配置

```json
{ "sources": { "windows": 17, "mac": 18 } }
```

### 经验：读回不可靠时，用「目视 + 自动切回」定位

`m1ddc get input` 在 USB-C 通道上返回过 `-118`（`0xFF8A`，无效回包），
读值这条路走不通。可靠的办法是：**逐个值切过去停几秒再自动切回**，人眼看画面。

```bash
# Windows 侧：15/18 各停 8 秒，自动切回 17
python tools/scan_input_windows.py --sweep --values 15,18 --dwell 8
```

判据：切到真实输入口 → 出现对应机器的画面；切到空口 → 黑屏；值非法 → 显示器不动。

**另一个坑**：Mac 睡眠 / 合盖时 USB-C 不输出画面，切过去也是黑屏，
看起来和「值不对」一模一样。复测前先确认 Mac 是醒着的。

## 失败怎么定位（按顺序，不要跳）

先跑诊断脚本，一次拿到所有事实：

```bash
chmod +x tools/diag_macos.sh
./tools/diag_macos.sh              # 只读
./tools/diag_macos.sh --write-test # 额外测亮度写入（判断 DDC 写通道）
```

它会依次检查：架构（Intel / Apple Silicon）、macOS 是否识别到外接屏、`m1ddc display list`、
四项 VCP 读值、多显示器索引，最后打印回传报告。

### A. 执行命令时，显示器显示的是哪台机器的画面？（最常见）

**如果当时显示器显示的是 Windows 画面，那命令必然没反应** —— 显示器切到 HDMI 输入时，
USB-C 那条链路已经断开，macOS 看不到这块屏，`m1ddc` 没有任何对象可以控制。

必须先让显示器处于 **USB-C 输入**（Mac 有画面）再测：

1. 用显示器摇杆/按键 → `Source / 输入源` → 选 `USB-C`；
2. 确认 Mac 的「系统设置 → 显示器」里能看到这块 AOC；
3. 再执行 `m1ddc set input 17`。

诊断脚本第 2 节的 `system_profiler` 输出能直接看出 macOS 有没有识别到外接屏。

### B. DDC 通道根本没建立（亮度也调不动）

用亮度做判据，这是最快的一刀：

```bash
m1ddc get luminance     # 读不到 → 通道不通
m1ddc set luminance 30  # 亮度没变 → 通道通但写被忽略
```

读不到亮度时，按这个顺序排查：

1. **OSD 里 DDC/CI 是否开启**：AOC 一般在 `设置 / Extra → DDC/CI → 开启`。关着的话亮度也调不了。
2. **USB-C 线是否支持视频**：能出画面才说明 DP Alt Mode 成立。Q2790PC 的 Type-C 是 15W 供电，
   只保证 DP Alt Mode + USB 数据，不保证 DDC 通道。
3. **换 DP 线交叉验证**：用 DP 线把 Mac 接上（如果 Mac 有 DP/转接），再跑一次亮度测试。
   - DP 上能调亮度、USB-C 上不能 → 这块屏的 USB-C 通道没实现 DDC/CI → 方案要改。
4. **关掉 HDR / 可变刷新率**：部分显示器在 HDR 下 DDC 通道异常。
5. **Intel Mac**：`m1ddc` 只支持 Apple Silicon。Intel 机器改用 `ddcctl` 或 BetterDisplay。

### C. DDC 通（亮度能调）但输入源不响应

说明通道没问题，是「VCP 0x60 这个指令」没被接受。两种可能：

1. **VCP 值不对**：用 `./tools/verify_m1ddc.sh --scan` 逐个试 15/16/17/18/27/28。
   找到能切的值就改配置 `"sources": { "windows": 17, "mac": <找到的值> }`。
2. **该通道不接受输入源切换**：少数显示器的 USB-C 口只实现亮度/对比度，不实现 0x60。
   此时可试 `m1ddc set input 17 --no-retry` 之类变体，或用 DDCommando 交叉验证：
   `ddcommando --get-input` / `--set-input 17`（它走 IOAVService，和 m1ddc 不同实现）。

### D. 两个方向都不行，且亮度也调不动

DDC/CI 路径在 USB-C 上不成立，需求文档 §7 的「阻塞」风险坐实。退路：

1. **换连接方式**：Mac 用 USB-C → DP 转接器接显示器 DP 口（DP 通常 DDC 支持最好），
   再验证一次。这是最可能救回方案的一步。
2. **厂商私有通道**：AOC 的 i-Menu 走私有协议，逆向成本高，不建议。
3. **物理 KVM 切换器**：几十到两百块，一键切换，比软件方案更稳。
4. **显示器 OSD 手按**：最差退路。

**不要**在没定位清楚前改 Windows 侧代码 —— 那部分已经验证可用，问题在 Mac 侧链路。

## Windows 侧的对应验证（可选但建议）

Windows 端是盲发，没有「读值正确性」问题，但可以确认句柄能拿到、Set 能发出去：

```powershell
dualswitch probe                  # 只读：确认能枚举到 MONITOR\AOC2790
dualswitch switch --to mac --wait-readback   # 真发：会立刻切走画面
```

⚠️ `switch` 会真的切屏。确认手边有 Mac 可以切回来再执行。
