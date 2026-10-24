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

```
owner(v) = {17: "windows", 18: "mac"} 的映射（来自 cfg.sources，不写死）
```

证据优先级（`SwitchController.classify()` 重写）：

| 顺序 | 证据 | 说明 |
|---|---|---|
| 1 | **本机 `get_input()` 读数** | Windows 有（§1.2），Mac 为 `-118` ⇒ 自然落空 |
| 2 | **对端上报的读数**（`T_STATE.input`） | Mac 靠这条拿到真相 |
| 3 | 都没有 | `SOURCE_UNKNOWN`，界面老实说不确定 |

- 两层读数都带**年龄**：本机 10s、对端 15s 内有效；超龄视为 `None`。
- 两端读数理论上不可能冲突（只有 Windows 读得到）。真冲突时**信本机**并记一条 warning。
- **删除**「枚举到 ⇒ 画面在本机」这条捷径。`_ever_seen_monitor` 只用来回答「这块屏存在过吗」，
  不再参与归属。

### 3.2 切换动作不依赖归属

```
用户点了「切到 X」→ v = target_value(X)
   1) 本机 backend.set_input(v)                     # 记 ok/error，不当成功依据
   2) 对端在线 → engine.request_switch(v)           # 同一个值，幂等
   3) 任一端「已下达」即继续；两端都失败才报错（错误里带两端原因）
   4) 进入验证（§3.3）
```

- 不再有 `wants_here` / `picture_here` 这两个门；`_try_remote` 也不再被它们挡。
- 两端发同一个值不会打架：握屏那端生效，另一端是 no-op。代价最多多 65ms + 一次往返。
- 对端离线就跳过第 2 步（`remote_ready`），不白等 3 秒。
- 副作用正向：Windows 端「本机 Set 假失败但显示器其实归 Mac 输入」这类场景，由第 2 步兜住。

### 3.3 成功判定 = 读数变成目标值

`_readback` 改成「读 owner」而不是「读 m1ddc 的返回值」：

- 每 500ms 查一次：本机能读就用本机，否则用对端上报；
- 读到 = 目标值 ⇒ `confirmed`；超时（8s）⇒ `unconfirmed`，但仍保留「指令已下达」；
- **`m1ddc` / `SetVCPFeature` 的返回码只说明「命令下了」，不说明「生效了」**。

### 3.4 三态与界面

| 态 | 含义 | 界面 |
|---|---|---|
| `windows` / `mac` | 读数明确指向某端 | 顶栏显示「当前画面：X」 |
| `unknown` | 两端都读不到（对端离线 / 屏没接） | 顶栏显示「不确定」，**不猜** |

界面改成**两个固定按钮**：「切到 Mac」「切到 Windows」各一个，直接对应显式 target，
不再由 `source_state` 反推方向。设备卡（`deviceClicked`）已经是显式目标，保留。

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
| `src/dualswitch/app.py` | `classify()` 重写（3.1）；`_execute()` 去 `wants_here`/`picture_here`（3.2）；`_readback` 改读数（3.3）；`_picture_here` 删除 |
| `src/dualswitch/ddc/macos.py` | `active_monitors()` 保留但**不再被调用**（或标 deprecated，注释指向 §1.1） |
| `src/dualswitch/ui/mac2/switch_page.py` | 两个固定按钮 + 顶栏三态 |
| `src/dualswitch/ui/mac2/runner.py` | `do_switch(target)` 直连两个按钮；去掉 `_next_target()` 的方向推断 |
| `config.json`（本机） | `readback.delay_ms=3000`、`timeout_ms=8000` |

### 5.1 兼容与边界

- **旧版本共存**：老对端不发 `input` ⇒ 新端得 `unknown`，行为退化为「不确定」而不是错误答案。
- **Windows 树只读**：`ui/win2` 的双按钮由 Windows 端决定；共享层修好后即使它还是单按钮，
  方向也会算对（`_next_target()` 读 `source_state`，后者已正确）。
- **打包**：`/Applications/屏幕切换.app` 是 10-22 15:15 的旧二进制，**任何真机验证前必须
  `./packaging/macos/build.sh && ./packaging/macos/install-app.sh`**，或用源码 `dualswitch ui2`。

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

7. `test_switch_requests_peer_even_when_local_set_ok` —— 本机 Set 成功也要发 `T_SWITCH`。
8. `test_switch_succeeds_when_only_peer_succeeds` / `test_switch_fails_only_when_both_fail`。
9. `test_readback_confirms_from_input_reading` —— 对端上报变成目标值 ⇒ `confirmed`。

`tests/test_sync_engine.py`

10. `test_peer_state_carries_input` —— `T_STATE/ACK` 往返带 `input`；缺字段时得 `None`。

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
| 屏在 Windows，Mac 端 `probe --json` | `input: null`、归属 `unknown`/`windows`（由对端上报给出） |
| 屏在 Windows，Mac 界面 | 顶栏「Windows」，两个按钮均可点 |
| 点「切到 Mac」 | 2–3s 内画面切到 Mac；Mac 日志有「对端代发 → ok」或本地 Set；`readback: confirmed` |
| 点「切到 Windows」 | 对称；**Mac 日志必须出现 `T_SWITCH` 记录**（这是历史上从没出现过的一行） |
| 对端退出后再点 | 不白等，立刻报「对端离线」，本机 Set 结果照常显示 |

---

## 8. 未决

1. **切到 Mac 后 2–3 秒回弹**（旧现象，本次未查）。若仍复现，先做「切过去后拔掉 Windows HDMI」
   实验判断是谁在抢源；与新架构无关，但会让「切换成功」看起来像失败。
2. **Windows 写入后读数回显窗口**有多长未测，暂取 5s。
3. Windows 端 `probe` 的 `active` 恒为 null（后端未实现），日志里不要据此判断归属。
