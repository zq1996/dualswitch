# 画面归属 与 对端代发切屏 · 现状与整改方案

> 2026-10-24 立。**本文取代以下文档里的「结论」部分**，有冲突以本文为准：
> `INVESTIGATION-INPUT-BOUNCE.md`、`TEST-PEER-DISPATCH.md`、`REMOTE-SWITCH.md` §2。
> 本文的每条结论都来自**真机实测**或**代码定位**，不以旧文档为依据。

---

## 0. 一句话

**归属判断和切换动作被绑死在一起，而判断用的那个信号（能不能枚举到显示器）在两端都是假的。**
Windows 上唯一可信的信号是 `VCP 0x60` 的**读数**（17/18 跟随物理输入），而 Mac 读不到（`-118`）。
所以：归属改成「读输入源」，切换动作与归属**解耦**，成功判定改成「读数变成目标值」。

---

## 1. 实测事实（2026-10-24 09:04–09:18，两端同时采样）

### 1.1 Mac 侧：屏切走切回，一个可观测字段都不变

采样工具 `outputs/detect_probe.py`（只读；CG 全套标志 + NSScreen + m1ddc + ioreg），
787 个样本 / 09:10:19–09:17:56 / 每 0.5s 一条。

用户 09:16:16 用 OSD 切到 Mac、09:16:36 切回 Windows（时刻由 §1.2 的 Windows 读数确认）。
该窗口（09:16:15–09:16:40，44 个样本）内：

| 观测面 | 值（两种状态完全相同） |
|---|---|
| `CGGetOnlineDisplayList` | `[1, 2]` |
| `CGGetActiveDisplayList` | `[1, 2]` |
| 外接屏 `CGDisplayIsActive/Online/Asleep` | `1 / 1 / 0` |
| 外接屏 `CGDisplayIsInMirrorSet` / `IsMain` / `IsBuiltin` | `0 / 0 / 0` |
| 尺寸 / 刷新率 / 可用模式数 | `2560×1440 / 75Hz / 122` |
| `NSScreen` 列表 | `Built-in Retina 1470×956@2x`、`Q2790R3 2560×1440@1x` |
| `m1ddc display list` | `[1] (null)`、`[2] Q2790R3` |
| ioreg `AppleCLCD2` | `NormalModeActive=Yes`、`DispPerfState=0`、`displayOnContinuousTimestamp` 冻结 |

⇒ **`active_monitors()`（NSScreen + `CGDisplayIsActive`）在这台机器上没有任何区分力**，
`f413cf0` 那次「补 CGDisplayIsActive 过滤」是死代码。

### 1.2 Windows 侧：同样是幻影，但**能读出真实输入源**

用户在 PowerShell 里每 2s 跑一次 `python -m dualswitch probe --json`：

```
09:16:05  monitors=1  active=  input=17  err=
09:16:17  monitors=1  active=  input=18  err=      ← 画面已切到 Mac（USB-C）
09:16:35  monitors=1  active=  input=18  err=
09:16:37  monitors=1  active=  input=17  err=      ← 切回
09:16:40  monitors=1  active=  input=     err=读取 VCP 0x60 失败：GetVCPFeature 返回 0 (WinError -1071241847)
```

两条结论：

1. **Windows 也枚举得到「不在显示」的屏**：画面在 Mac 时 `monitors` 恒为 1。
   两端同时说「我看得到」⇒ 现有 `T_STATE` 的 `sees` 布尔值**零信息量**，
   10-23 那套联合判定从设计上就是空转。
2. **`VCP 0x60` 读数是真的**：17↔18 严格跟随用户物理切换，逐次采样对得上。
   同刻 Mac 读同一项是 `-118`（IOKit 错误，USB-C/DP Alt Mode 下读通道不可用）。

### 1.3 两个必须处理的读数特性

| 特性 | 实测 | 应对 |
|---|---|---|
| 偶发读取失败 | 80 次里 2 次（`GetVCPFeature 返回 0`，WinError -1071241847） | 「最近一次成功值 + 最大年龄」，超龄才判 unknown |
| 写入后可能回显 | 旧文档称「写 18 就读到 18，几秒后回到真值」；本次**无写入**，读数全程跟随真实切换 | 刚发过 Set 的 N 秒内（暂定 5s）不把读数当归属证据 |

### 1.4 本机 DDC 写通道始终有效，不受「画面在哪」影响（2026-10-24 真机推翻旧假设）

**旧假设**（本文旧版 / `ddc/macos.py` 旧注释 / `INVESTIGATION-INPUT-BOUNCE.md` B2）：
「屏在 HDMI 时，USB-C 上的 `m1ddc set input` 返回 0 是**假成功**——打在一个没在监听的
输入口上」。

**实测推翻**（2026-10-24 11:23–11:25，日志 `dualswitch.log`）：画面在 Windows(HDMI/17) 时，
点「本机发送 18」，**屏照样切回 Mac**；画面在 Mac 时「本机发送 17」同样一步到位。
两条命令都只走了本机 `backend.set_input()`（`_do_local`，不碰对端），日志里
「已发送切换指令 → …（VCP 0x60=…）」即本机通道，103–131ms 完成。

**机制**：DDC/CI 是**随线走的旁路通道**（USB-C = DP AUX、HDMI = I2C DDC 脚）。
显示器的 MCU 在**每个输入口**上都监听 DDC 请求，与「当前正在显示哪一路」无关——
输入源切换（VCP 0x60）本来就得从任意一路都能下达，否则从「当前画面那台」根本
没法把画面切走。而 USB-C 链路在屏切走后保持在线（§1.1 幻影显示器就是它的副作用），
所以 Mac 的 DDC 事务一直打得到这块屏。

**结论修正**：

| 旧结论 | 新结论 |
|---|---|
| `set input` 返回 0 = 假成功 | 返回 0 = **真生效**；读通道（-118）不可用才导致「回读未确认」 |
| 「请对端发送」是画面在对面时的**必经路** | 「本机发送」始终可达，是**主路** |
| 本机发不动时才点「请对端发送」 | 「请对端发送」降级为**兜底**：本机 DDC 栈坏掉（驱动 / 权限 / m1ddc 缺失）或换一台只认活跃输入口的显示器时才用 |

---

## 2. 现有代码为什么必然失败（`src/dualswitch/app.py`）

### 2.1 归属：枚举这条票压过一切（`app.py:252`）

```python
if present:                          # present 来自 probe_active_monitors()
    return self.cfg.effective_host()  # Mac 上恒真 ⇒ 恒返回 mac
peer = self._peer_sees()             # T_STATE 证据永远走不到
```

`present` 在 Mac 上恒 `True`（§1.1），在 Windows 上也恒 `True`（§1.2）⇒ 两端都永远判「画面在
我自己这儿」。这就是用户看到的「初始显示屏幕在 Mac、实际在 Windows」。

### 2.2 目标算反（`app.py:393`）

`_resolve_target()` 按 `_display_source` 推：判成 `mac` ⇒ target 恒 `windows` ⇒
`wants_here = (target == cfg.effective_host())`（`app.py:447`）恒 `False`。

### 2.3 对端代发的四条触发路径，Mac 上全部不可达

| # | 条件（`_execute`） | 为什么到不了 |
|---|---|---|
| 1 | `picture_here is False and wants_here` | `picture_here` 恒 True |
| 2 | `picture_here is None and wants_here` | 不为 None |
| 3 | `self._source_checked and not self._ever_seen_monitor` | 首次枚举后就恒 False |
| 4 | `except DDCError:` → `_try_remote` | `m1ddc display 2 set input N` **返回 0**，从不抛异常 |

⇒ **Mac 端一次 `T_SWITCH` 都没发出过**。链路层是好的（`protocol`/`transport`/`engine`/CLI 注入
都在，`tools/e2e_remote_switch.py` 8/8 通过）——「代发从来没好用过」不是网络问题，是这四条路
在这台机器上全是死路。

### 2.4 即使触发了，也可能是 no-op 却回 `ok`

代发的 `value` 由错误的 target 算出。当某端误以为「画面在我这儿」时 target 是对端，
于是它请求对端「切到对端自己的输入」——对端执行完是 no-op，`_serve_switch` 仍回 `ok=true`。
**这是「代发成功、屏一动不动」的机制。**

### 2.5 回读窗口比切换还短

本机 `~/Library/Application Support/dualswitch/config.json`：
`readback.delay_ms=300 / timeout_ms=1000`，而显示器一次切换要 **2–3 秒**（用户确认）。
窗口等不到结果，永远报「未确认」。（Windows 侧已改成 3000/8000，Mac 没跟。）

---

## 3. 新架构

### 3.1 归属 = 输入源读数（唯一可信证据）

先澄清一个容易读错的措辞：**两端跑的是同一份代码，各自有自己的「本机」**。
所以这一节必须逐台写，不能笼统说「本机」：

| 在哪台机器上跑的程序 | 它自己读 `VCP 0x60` | 它怎么定归属 |
|---|---|---|
| **Windows 那台** | 读得到（17 / 18，实测见 §1.2） | 读到的就是真相，直接映射；不看 Mac 报什么 |
| **Mac 那台**（就是当前开发机） | 读不到（`-118`，IOKit 错误） | 只能采用 Windows 通过 `T_STATE` 上报的读数 |
| 两端都拿不到（Windows 没开 / 读数超龄） | — | `SOURCE_UNKNOWN`：界面显示「不确定」，**不猜** |

一句话：**能读到的那一端说了算**。这台 Mac 天生是「看不见的那一端」，所以必须依赖对端上报——
这是硬件事实（§1.1、§1.2），不是把 Mac 当二等公民。

```
owner(v) = cfg.sources 反查：17 → "windows"，18 → "mac"（不写死，按配置来）
```

证据优先级（`SwitchController.classify()` 重写）：

| 顺序 | 证据 | 说明 |
|---|---|---|
| 1 | 本机 `get_input()` 读数（带年龄） | Windows 上有效；Mac 上必然是 `None` |
| 2 | 对端上报的读数 `T_STATE.input`（带年龄） | Mac 上靠这条拿真相 |
| 3 | 都没有 | `SOURCE_UNKNOWN` |

- 两层读数都带**年龄**：本机 10s、对端 15s 内有效；超龄视为 `None`。
- 只有两端都能读（将来换机型 / 换接线）时才可能冲突；真冲突时**信本机**并记一条 warning。
- **删除**「枚举到 ⇒ 画面在本机」这条捷径。`_ever_seen_monitor` 只用来回答「这块屏存在过吗」，
  不再参与归属。

### 3.2 切换 = 四个显式动作（不再有「自动判断该谁发」）

| 界面动作 | 实际做什么 |
|---|---|
| 本机发送 **17** | `backend.set_input(17)` |
| 本机发送 **18** | `backend.set_input(18)` |
| 请对端发送 **17** | `engine.request_switch(17)` |
| 请对端发送 **18** | `engine.request_switch(18)` |

- **去掉「本机优先、失败才请对端」的隐式兜底**：谁发由用户点哪个按钮决定，日志和界面永远能对上
  是哪条路。原来那套兜底的两个门（`wants_here` / `picture_here`）在这台硬件上恒真（§2.3），
  实际从来没生效过，却让「点了为什么没反应」变得不可解释。
- **也去掉「两端都发同一个值」的自动双发**（2026-10-24 决定）：`m1ddc` 的 Set 会返回 0 但没生效，
  自动双发会让「到底哪条起了作用」不可见。显式两条路 = 一次点击就能复现或排除。
- 「请对端发送」在对端离线时**按钮置灰**（`remote_ready`），点了也不会白等 3 秒。
- 热键（⌥Space）不再猜方向：它等价于「请对端把我这边的值送回去」？——**不**。热键保留旧语义
  `switch_mode`（默认送到另一台），因为它是肌肉记忆入口；按钮才是显式通道。

### 3.3 成功判定 = 读数变成目标值

`_readback` 改成「读 owner」而不是「读 m1ddc 的返回值」：

- 每 500ms 查一次：本机能读就用本机，否则用对端上报；
- 读到 = 目标值 ⇒ `confirmed`；超时（8s）⇒ `unconfirmed`，但仍保留「指令已下达」；
- **`m1ddc` / `SetVCPFeature` 的返回码只说明「命令下了」，不说明「生效了」**。

### 3.4 页面规格（`design/redesign/switch-actions.html`，2026-10-24）

```
显示器                                    [对端在线]
[屏] Q2790R3
     2560×1440 · 75 Hz · HDMI 与 USB-C 各一根线
     画面在 Windows · 对端读数 17 · 6 秒前      ← 拿不到就写「画面位置未知」

[17] HDMI / Windows 侧     [本机发送 17]  [请对端发送 17]
[18] USB-C / Mac 侧·本机   [本机发送 18]  [请对端发送 18]

「本机发送」直接把 DDC 命令发给显示器，画面在哪边都能切，一步到位；「请对端发送」是兜底——本机 DDC 不可用时由对端代发。
✓ 上次 09:36 · 请对端切到 18 · 往返 142 ms · 已确认
```

| 元素 | 规则 |
|---|---|
| 大标题 | 是**显示器名**（确定的硬件），不是「当前画面」（猜的） |
| 「画面在 X」 | 一行小字，**必须带出处与年龄**；拿不到就写「未知」，绝不猜 |
| 两个 `本机发送` | 实心主色（直接动作） |
| 两个 `请对端发送` | 白底描边 + 纸飞机图标；对端离线时置灰 |
| 已删除 | 「强制切」按钮、两张可点击的设备卡（都只是「本机发送 X」的重复入口）、「切换到 X」单按钮 |
| 底部注脚 | 上一次动作 + 耗时 + 验证结果（`已确认` / `回读不可用`） |

### 3.5 三态只剩展示用途

| 态 | 含义 | 界面 |
|---|---|---|
| `windows` / `mac` | 读数明确指向某端 | 小字「画面在 X」+ 绿点 |
| `unknown` | 两端都读不到（对端离线 / 屏没接） | 小字「画面位置未知」+ 灰点，**不猜** |

归属**不再参与任何按钮的可用性或计算**——四个按钮永远可点（对端的仅在离线时灰）。

---

## 4. 协议变更

`T_STATE = 0x32` / `T_STATE_ACK = 0x33` 的 header 增加字段：

```json
{"req": "<hex>", "input": 18}      // input: int | null；null = 本机读不到
```

- `sees` 字段**保留但不再参与归属**（供日志 / 诊断），老版本仍能互发。
- 收到没有 `input` 的 ack（老版本对端）⇒ 视为 `None`，回落 `unknown`，不会误判。
- 发送间隔维持 5s（`PEER_STATE_INTERVAL_S`）。

---

## 5. 改动清单（不碰 Windows 树）

> `ddc/windows.py` **不需要改**：`get_input()` 两端本来就有，共享层直接复用。
> Windows 端 `git pull` 后只需回归，不需要写代码。

| 文件 | 改动 |
|---|---|
| `src/dualswitch/cli.py` | `_sees_monitor()` → `_owner_value() -> int \| None`（`backend.get_input()`，异常返回 None）；接 `engine.set_owner_probe` |
| `src/dualswitch/sync/engine.py` | `_monitor_probe` 换成 owner 探针；`T_STATE` 收发 `input`；`peer_input(max_age)` |
| `src/dualswitch/app.py` | `classify()` 重写（3.1）；新增 `set_input_local(v)` / `switch_remote(v)` 两个显式入口（3.2）；`_readback` 改读数（3.3）；`_picture_here` / `_try_remote` 的自动兜底逻辑删除 |
| `src/dualswitch/ddc/macos.py` | `active_monitors()` 保留但**不再被调用**（标 deprecated，注释指向 §1.1） |
| `src/dualswitch/ui/mac2/switch_page.py` | 按 §3.4 重写：四动作按钮、删 `force_btn`、删 `DeviceCard` 两块、大标题改显示器名、「画面在 X」降级为带出处的小字 |
| `src/dualswitch/ui/mac2/runner.py` | 四个 `Signal` 直连 `do_local(value)` / `do_remote(value)`；删 `do_force` / `_next_target()` 的方向推断 |
| `design/redesign/switch-actions.html` | 新页面原型（本次已产出，两态：正常 / 对端离线） |
| `config.json`（本机） | `readback.delay_ms=3000`、`timeout_ms=8000` |

**分两步落地**（避免一次改太多不好定位）：

1. **动作层**：`app.py` 两个显式入口 + UI 四个按钮 + 删强制切。此时「画面在 X」还显示未知，
   但切换已经可用、可复现。
2. **读数层**：`T_STATE.input` + `classify()` 重写 + 「画面在 X」小字 + 验证改读数。

### 5.1 兼容与边界

- **旧版本共存**：老对端不发 `input` ⇒ 新端得 `unknown`，行为退化为「不确定」而不是错误答案。
- **Windows 树只读**：`ui/win2` 的页面由 Windows 端决定。共享层只提供
  `set_input_local(v)` / `switch_remote(v)` 两个显式入口和 `owner()` 读数；win2 若暂时沿用旧的
  单按钮界面，功能仍可用（`_next_target()` 读 `source_state`，后者已正确），只是看不到四条通道。
- **打包 / 启动方式**：`/Applications/屏幕切换.app` 是 **10-22 15:15** 的旧二进制。
  10-24 08:53 那次启动日志写的是
  `可执行文件：/Applications/屏幕切换.app/Contents/MacOS/dualswitch（2026-10-22 15:15:33）`，
  说明那一轮跑的**是打包版、不是源码**，所以 10-23 晚的三个修复当天根本没生效。
  验证前先确认日志里这一行是 `.../envs/default/bin/python3`（源码）还是 `.app/...`（打包）；
  用打包版必须先 `./packaging/macos/build.sh && ./packaging/macos/install-app.sh`。

---

## 6. 测试计划（先写红用例）

`tests/test_source_state.py`

1. `test_owner_from_input_maps_both_values` —— 17→windows、18→mac、99→None。
2. `test_classify_uses_input_reading_over_enumeration` —— 枚举到屏但读数是 17 ⇒ **windows**
   （旧代码会返回 mac，必须先在旧代码上验红）。
3. `test_classify_falls_back_to_peer_input` —— 本机读不到、对端 input=18 ⇒ mac。
4. `test_classify_unknown_when_no_readings` —— 两端都读不到、枚举得到 ⇒ **unknown**（不许猜）。
5. `test_classify_ignores_stale_reading` —— 11s 前的读数 ⇒ 视为 None。
6. `test_classify_ignores_reading_right_after_set` —— Set 后 5s 内的读数不作为归属证据。

`tests/test_remote_switch.py`

7. `test_local_action_only_sets_locally` —— 「本机发送」**不发** `T_SWITCH`（显式通道，不自动兜底）。
8. `test_peer_action_only_requests_peer` —— 「请对端发送」**不碰**本机 backend。
9. `test_peer_action_blocked_when_offline` —— 对端离线时直接失败、不白等 3 秒。
10. `test_readback_confirms_from_input_reading` —— 读数变成目标值 ⇒ `confirmed`。

`tests/test_ui_mac2.py`

11. `test_switch_page_has_four_actions` —— 四个按钮都在、`force_btn` 不存在、设备卡不再可点。
12. `test_switch_page_shows_owner_with_source` —— 有读数时小字带「对端读数 · N 秒前」；无读数时写「未知」。

`tests/test_sync_engine.py`

13. `test_peer_state_carries_input` —— `T_STATE/ACK` 往返带 `input`；缺字段时得 `None`。

AST 守卫（`tests/test_source_state.py`）：`classify` 内不得再出现「枚举结果直接 return 主机」的形态。

---

## 7. 验收

```bash
# 单测（macOS / Windows 同一份）
/usr/bin/python3 -m pytest tests/test_source_state.py tests/test_remote_switch.py \
    tests/test_sync_engine.py --basetemp=.pytest-tmp -q
# 协议端到端（不需要真显示器）
PYTHONPATH=src python3 tools/e2e_remote_switch.py
```

真机（两端都跑新代码）：

| 步骤 | 期望 |
|---|---|
| 屏在 Windows，Mac 端 `probe --json` | `input: null`（Mac 读不到）、对端上报应为 `17` |
| 屏在 Windows，Mac 界面 | 小字「画面在 Windows · 对端读数 17 · N 秒前」，四个按钮都可点 |
| 点「本机发送 18」 | 画面应切到 Mac（2–3s）；日志只有本机 Set，**没有** `T_SWITCH` |
| 点「请对端发送 18」 | 画面应切到 Mac；**Mac 日志出现「远端代发切屏 → ok」**（历史上从没出现过的一行），Windows 日志出现「替对端切屏」 |
| 点「本机发送 17」 | 画面回 Windows；日志只有本机 Set |
| 对端退出后 | 两个「请对端发送」立即置灰；点本机按钮照常工作，不白等 |
| 全流程 | 界面小字始终与真实画面一致；**一次都不许出现「说 Mac 实际 Windows」** |

---

## 8. 未决

1. **切到 Mac 后 2–3 秒回弹**（旧现象，本次未查）。若仍复现，先做「切过去后拔掉 Windows HDMI」
   实验判断是谁在抢源；与新架构无关，但会让「切换成功」看起来像失败。
2. **Windows 写入后读数回显窗口**有多长未测，暂取 5s。
3. Windows 端 `probe` 的 `active` 恒为 null（后端未实现），日志里不要据此判断归属。
