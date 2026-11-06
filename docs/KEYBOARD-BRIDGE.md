# 键盘跟随切屏（软件键盘桥）方案

> 一句话：**画面切到哪台，键盘就从哪台输出**。不切物理通道，只转发按键。
> 键盘永远留在 Windows，Mac 只负责"把收到的按键注入进去"。

- 方案日期：2026-11-06
- 状态：**Mac 注入端已实现（2026-11-06）；Windows 捕获端未实现。** 落地位置见 §17。
  本文件是设计真源，改行为前先改这里。
- 设备前提：Windows 台式机（网线）/ MacBook Air（Wi-Fi）/ 洛斐 Lofree FLOW2（OE927）
- 相关文档：[SCREEN-OWNERSHIP.md](SCREEN-OWNERSHIP.md)（画面归属）、[REMOTE-SWITCH.md](REMOTE-SWITCH.md)（对端代发）、
  [CLIPBOARD-SYNC-CONTRACT.md](CLIPBOARD-SYNC-CONTRACT.md)（同一条 TCP 的帧格式）、
  [FEASIBILITY-LOGI-EASYSWITCH.md](FEASIBILITY-LOGI-EASYSWITCH.md)（鼠标为什么能跟着走）

---

## 0. 结论先行

1. **能做，而且不需要 Deskflow。** 焦点不是"猜"出来的——我们自己的切屏命令就是唯一权威，
   这是 Deskflow 靠光标撞边缘判断做不到的。
2. **方向定为 Windows 捕获 → Mac 注入（单向）**，见 §2。这个方向让三条硬伤里的两条**直接消失**。
3. **延迟不是问题**：链路新增约 3–10ms（有线→AP→Wi-Fi），打字毫无感觉。真正的敌人是 Wi-Fi 尾部抖动。
4. **真硬伤有三条**（§10）：Mac 锁屏、Windows 锁屏/安全桌面、Windows 钩子被静默摘除。
   前两条只能规避+告知，第三条能工程解决（双通道互证）。
5. **不做的事**：不做双向、不做鼠标跟随（已有罗技通道）、不做光标跨屏、不传字符只传键位。

---

## 1. 需求定义

"屏幕在哪端，键盘就在哪端输出" 的精确表述，四条：

| # | 规则 |
|---|---|
| R1 | 一次切屏动作**成功**后，键盘输出去向 = 该次切屏的目标端 |
| R2 | 切屏**失败**（本地+对端都没成）→ 键盘不动，保持原样 |
| R3 | 键盘物理位置不变：永远连在 Windows（BT 或 2.4G 都由用户自己决定） |
| R4 | 用户随时可按 `Fn+Q/W/E/R` 把键盘物理挪走，软件必须优雅降级，不抢、不拦、不报错 |

R1 里的"切屏动作"包括全部四个入口：界面按钮、全局热键、CLI `--to`、对端代发。

---

## 2. 为什么方向是 Windows → Mac

| 维度 | **Windows 捕获 → Mac 注入** | Mac 捕获 → Windows 注入 |
|---|---|---|
| Windows UIPI（任务管理器 / UAC 窗口打不进） | **不存在**（Windows 只捕获，不注入） | 会撞，且无解 |
| macOS 安全输入（密码框 / 终端安全键盘） | **不存在**（Mac 只注入，不捕获） | 会撞，且无解 |
| Mac 无显示器时休眠 | **不存在**（Mac 永远是有画面的那台） | 会撞，要跟系统对抗 |
| 桥挂了怎么办 | MacBook 自带键盘立刻顶上 | 无显示器的 Windows 一点输入都没有 |
| 对主力机的影响 | Windows 照常打字，桥只是旁路 | 主力机最先受影响 |
| 代价 | Windows 侧捕获用低级钩子，比 CGEventTap 脆（§10-3、§8.6） | 捕获稳，但注入侧的坑堵不上 |

**结论：选 Windows → Mac。** 这不是偏好问题——上表前两行是"无论怎么写代码都堵不上"的系统边界，
而这个方向让它们根本不发生。

补充：键盘本来就在 Windows 桌上、主要给 Windows 用。主用场景零改动，只在"看 Mac"那段时间才走转发。

---

## 3. 前置事实（本次调研确认）

### 3.1 键盘

| 项 | 事实 | 影响 |
|---|---|---|
| 型号 | 洛斐 FLOW2 / OE927，矮轴 68 键三模 | — |
| 通道 | BT 3 个 + 2.4G 一个；**`Fn+Q` / `Fn+W` / `Fn+E` 切蓝牙 1/2/3，`Fn+R` 切 2.4G** | 逃生通道，固件层，软件看不见也拦不住（好事） |
| 系统键位 | `Fn+N` = Windows/Android，`Fn+M` = Mac/iOS | **保持 `Fn+N`**，键位差异交给软件映射（§7） |
| QMK/VIA | 支持，但 **VIA 仅限有线模式**，且原厂固件没有"切主机"指令 | 排除"软件切通道"这条路 |
| HID++ | 非罗技，无 `CHANGE_HOST 0x1814` | 鼠标那条现成通道复用不了 |

> **⚠️ 别按网上手册把通道键改回数字键。** FLOW2 的 100 键版本手册写的是 `Fn+1/2/3/4`，
> 但本机这只 68 键实物是 `Fn+Q/W/E/R`（2026-11-06 用户确认）。两者键帽印字就不一样，
> 文档、界面文案、验收步骤一律以实物为准。
>
> 顺带一个要一起验证的点：按 `Fn+Q` 切通道时，OS 那边会不会**同时收到一个裸 `q`**。
> 正常固件不会透传，但万一会，转发期间按通道键就会往对面打进一个字母——归到阶段 0 一起测。

### 3.2 网络

Windows **网线**、Mac **Wi-Fi**。链路是"有线 → 路由器/AP → 无线"，
只有最后一跳是无线——比两端都 Wi-Fi 好，但**接收侧（Mac）的省电唤醒仍是唯一的延迟尖峰来源**（§9.3）。

### 3.3 现有代码里可以直接用的

| 位置 | 用途 |
|---|---|
| `app._after_screen_switch(target_host)` | **唯一的切屏成功回调**，已经挂了鼠标联动；键盘焦点挂同一个点（4 个调用点：`_execute` / `_try_remote` / `_do_local` / `_do_remote`） |
| `sync/protocol.py` `T_*` + `DSYN` 帧 | 加 `0x40/0x41/0x42` 三个类型即可，旧版本按"忽略未知帧"处理，有序升级 |
| `sync/transport.py` `Link.send()` | 直接发帧；**需要在 `_make_link` 里补 `TCP_NODELAY`**（现在没设，见 §6.4） |
| `hotkey/windows.py::LowLevelHookBackend` | **已经装了 `WH_KEYBOARD_LL`**——键盘桥不能再装第二个钩子（§8.6） |

---

## 4. 焦点模型（本方案的核心，也是最容易做错的地方）

### 4.1 状态

```
focus = { target: "windows" | "mac", epoch: int, armed: bool }
```

- `target`：键盘输出去向。
- `epoch`：单调递增序号，每次切屏成功 +1。用来丢弃"过期的按键帧"和两端对账。
- `armed`：**吞键闸门**。只有当 `target != 本机` **且** 链路健康 **且** 用户没暂停时才为真。
  `armed=False` 时一律放行——这是安全底线（§8.5）。

### 4.2 唯一推进点

```python
def _after_screen_switch(self, target_host: str) -> None:
    # ... 现有鼠标联动 ...
    self._kbd.on_focus(target_host)   # 新增，内部 try/except 兜底，永不抛
```

四个调用点已经覆盖了"本地成功 / 对端代发成功 / 对端替我们执行"全部路径，
**两端最终都会走到这里**，所以不需要新增一轮握手协议。

> 注意：`_after_screen_switch` 只在成功分支被调用。失败时什么都不会发生，
> 天然满足 R2。不要在这里做"再判断一次画面在哪"——那会引入第二套真源。

### 4.3 状态机

```
        切屏命令（界面 / 热键 / CLI / 对端代发）
                        │
              ┌─────────▼──────────┐
              │ _execute 成功了吗？ │──否──> focus 不变，键盘不动（R2）
              └─────────┬──────────┘
                        │是
              ┌─────────▼────────────────────┐
              │ _after_screen_switch(target) │  ← 与鼠标联动同一个挂载点
              │ epoch += 1; focus = target   │
              └─────────┬────────────────────┘
                        │
        ┌───────────────┴────────────────┐
        │                                │
 focus == windows                 focus == mac
 本机直通：不吞键、不转发          吞键 + 转发（armed=True）
 Mac 侧：收到 keyup 就全量释放
```

### 4.4 过渡期（那 2–3 秒）怎么办

切屏命令发出后，显示器要 1–3 秒才真正换画面。这段时间有三种策略：

| 策略 | 行为 | 评价 |
|---|---|---|
| A 立即切换（**推荐**） | 命令成功即刻改 focus | 用户按完键会自然停顿等画面；配合 toast「键盘已跟随」足够 |
| B 延迟切换 | 等 `settle_ms` 再改 | 会出现"键盘哑住"的空窗，比误投更难受 |
| C 缓冲后补发 | 过渡期按键先存，确认后补 | 过度设计，且补发的字会在画面上突然蹦出来 |

默认 A，保留 `blur_guard_ms`（默认 0）作为可选的"切换后 N 毫秒内丢弃按键"，
但**默认关闭**——丢弃比误投更难解释。

### 4.5 unknown 态怎么处理

`app._display_source` 允许 `unknown`（画面归属判不出）。**键盘不跟着 unknown 走**：

- 归属 unknown 时，`_after_screen_switch` 根本不会带 unknown（它只在命令成功时收到明确的 target）。
- 若界面因归属回弹把状态改回 unknown，**focus 不变**——宁可按错的继续，也不要在两条路之间反复横跳。
- 界面在 unknown 时给键盘跟随行加一句灰字："画面归属未确认，键盘跟随可能不准"。

这是延续 SCREEN-OWNERSHIP 的原则：**不猜**。

---

## 5. 架构

```
┌─ Windows（键盘所在端 = 捕获端） ───────────────────────────────┐
│                                                                │
│  Lofree FLOW2 ──BT/2.4G──> Windows HID                          │
│                                 │                              │
│         ┌───────────────────────┴───────────────────────┐      │
│         │ keyboard/hook_windows.py（单一 LL 钩子）       │      │
│         │  ├─ ① 热键匹配 → 回调 app（白名单，永不转发）  │      │
│         │  ├─ ② 键盘路由 → focus==mac 时编码成 T_KEY    │      │
│         │  └─ ③ 吞键闸门 armed：只有全绿才 return 1     │      │
│         └───────────────────────┬───────────────────────┘      │
│                                 │ T_KEY（复用 DSYN 加密帧）     │
└─────────────────────────────────┼──────────────────────────────┘
                                  │  有线 → AP → Wi-Fi
┌─ macOS（注入端） ────────────────┼──────────────────────────────┐
│                                 ▼                              │
│  sync/transport ──> keyboard/inject_macos.py ──> CGEventPost   │
│                                 │                              │
│                                 └─> 前台应用 / 输入法（原生合成）│
│                                                                │
│  app._after_screen_switch(target) ──> keyboard/router.py       │
└────────────────────────────────────────────────────────────────┘
```

### 5.1 模块清单

| 文件 | 端 | 职责 |
|---|---|---|
| `keyboard/keymap.py` | 共享 | HID usage ↔ Windows VK / macOS virtual keycode，修饰键策略 |
| `keyboard/router.py` | 共享 | focus 状态机、epoch、键位位图对账、全量释放 |
| `keyboard/capture_windows.py` | Windows | 捕获 + 吞键闸门 + Raw Input 互证 + 保活 |
| `keyboard/inject_macos.py` | macOS | CGEventPost 注入 + 修饰键 flags + CapsLock 对齐 |
| `keyboard/hook_windows.py` | Windows | **重构**：把 `hotkey/windows.py` 的钩子提出来做成唯一钩子，热键与桥共用 |
| `sync/protocol.py` | 共享 | 加 `T_KEY / T_KEYSTATE / T_FOCUS` |
| `tools/key_feeder.py` | 本机 | 从 stdin/文件喂按键，**不接 Windows 也能验收 Mac 端** |
| `tools/inject_probe.py` | macOS | 阶段 0 的五个待验证问题，10 分钟跑完 |

---

## 6. 协议

### 6.1 新增帧类型

沿用 `DSYN` 帧（magic + ver + type + hdrlen + JSON header + payload），
旧版本对未知 type 走"忽略"分支，不断连——和 `T_SWITCH/T_STATE` 当初一样。

| type | 名称 | 频率 | header |
|---|---|---|---|
| `0x40` | `T_KEY` | 按键发生时 | `{"e":12,"s":345,"u":4,"d":1,"m":8,"t":45231}` |
| `0x41` | `T_KEYSTATE` | 10Hz（100ms） | `{"e":12,"s":345,"down":[4,225],"caps":0,"rtt":3}` |
| `0x42` | `T_FOCUS` | 切屏时 + 5s 保底 | `{"e":12,"target":"mac"}` |

字段：`e`=epoch，`s`=seq，`u`=HID usage（键盘页 0x04–0xE7），`d`=1 按下/0 抬起，
`m`=当时的修饰键位图，`t`=源端毫秒时间戳（只用于测延迟，不参与逻辑）。

单帧约 60 字节；连续打字 10 键/秒 ≈ 600 B/s。可忽略。

### 6.2 为什么不用 payload 传二进制

仓库现有协议 header 就是 JSON，一致性 > 省 30 字节。真要压体积，V2 再换。

### 6.3 序号与过期帧

接收端比较 `e`：**`e` 小于本地 epoch 的帧直接丢弃**。
这条能挡住"切屏前一刻发出的按键在切屏后才到"这类乱序（TCP 有序，但跨 epoch 的语义过期仍会发生）。

### 6.4 TCP_NODELAY（必须补）

`transport.py::_make_link` 现在没有 `TCP_NODELAY`。键盘帧是小包，
Nagle + 延迟 ACK 会造成 40ms 级的周期性停顿——这正是老 Synergy/Barrier 被骂"打字发黏"的经典原因。

```python
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
```

这一行是"自研能比 Deskflow 顺"的第一块砖。

---

## 7. 键位映射

### 7.1 原则

**按物理位置映射，不传字符。**

理由：传字符要做布局换算、要处理输入法、要处理死键，而且输入法候选窗会跑到源端去。
传 HID usage 就等于"把一块键盘插到对面的 USB 口上"——对端输入法在**对端**原生合成，
候选窗出现在你正在看的那块屏上，和真 KVM 一模一样。

### 7.2 修饰键（用户已定：`Alt → ⌘`、`Win → ⌥`）

| Windows 物理键 | VK | macOS | 效果 |
|---|---|---|---|
| 左 Ctrl / 右 Ctrl | `VK_LCONTROL` / `VK_RCONTROL` | `kVK_Control` / `kVK_RightControl` | 控制键，位置不变 |
| 左 Win | `VK_LWIN` | `kVK_Option` | 远左那颗 = ⌥ |
| 左 Alt | `VK_LMENU` | `kVK_Command` | **空格左边那颗 = ⌘，所以 Alt+C 就是复制** |
| 右 Alt | `VK_RMENU` | `kVK_RightCommand` | 对称 |
| 右 Win | `VK_RWIN` | `kVK_RightOption` | 对称 |
| Menu 键 | `VK_APPS` | 不映射 | 丢弃 |

这套映射是"位置优先"，和用户肌肉记忆对齐。设置页留一个 `mod_map: position | semantic` 开关，
`semantic` = 按名字（Win↔⌘、Alt↔⌥）。

> 键盘保持 `Fn+N`（Windows 模式）不变。**不要**为了 Mac 去切 `Fn+M`——
> 那样在 Windows 下 Win/Ctrl 会错位，得不偿失。

### 7.3 CapsLock 对齐（容易漏的真坑）

两端 CapsLock 是独立状态。若 Windows 开着、Mac 关着，转发过去打出来的大小写是错的。

处理：
1. 进 focus 时读一次对端 CapsLock 状态（macOS `IOHIDGetModifierLockState`）。
2. 不一致就对**对端**注入一次 CapsLock，把它对齐到源端。
3. 之后 CapsLock 照常当普通键转发。

### 7.4 媒体键与侧边触控条

Lofree 的 `Fn+F1..F12`、侧边触控条（音量/亮度滑动）走的是 Consumer page 或厂商页，
不一定以标准键盘 usage 上报。**V1 不转发**，只转发键盘页 0x04–0xE7；配置项 `forward_media_keys` 留口子。

用户在 Mac 上调音量，本来就有 Mac 自己的键和音量菜单，损失可接受。

### 7.5 输入法

- 中文：由 Mac 原生输入法合成，候选窗在 Mac 屏上，正常。
- 状态不连续：Windows 是英文、Mac 是拼音时，切过去第一下会出现意料之外的候选窗。
  这不是 bug，是两台独立机器的固有现象（真 KVM 也一样）。
- 如果 Mac 侧是"美式键盘"而 Windows 是"中文拼音"，要在文档里提一句，别让用户以为是转发坏了。

---

## 8. 按键状态机（防连键、防卡键）

Deskflow 的"连键"和"修饰键卡死"不是网络问题，是状态机没做对。下面七条是硬性要求。

### 8.1 单一 owner

一个 `keydown` 由谁先收到，它的 `keyup` 就归谁处理，**不因中途切屏改变归属**。
切换时若还有键按着，走 §8.3 全量释放，而不是"让 keyup 跟去新家"。

### 8.2 重复键

两个候选，必须先测（§14 阶段 0-①）：

| 实测结果 | 策略 |
|---|---|
| macOS 对注入的 keydown **会自动重复** | 源端只发第一次 keydown，**丢掉后续重复**（判据：同 usage 已 down 且无中间 up） |
| macOS **不会**自动重复 | 源端把 Windows 产生的重复事件全部转发（重复速率自然等于 Windows 的设置） |

两条分支代码量都很小，但**选错了就是"按一下出五个字"**。这是必须实测的第一件事。

### 8.3 切屏时全量释放

`focus` 变化时，把当前记录的所有"按下中"的 usage 逐一对**被离开的那台**注入 keyup。
这一步专治"按住 Shift 切屏 → Mac 上 Shift 永远卡住"。

### 8.4 100ms 位图对账

`T_KEYSTATE` 每 100ms 带一份"我现在认为哪些键按着"。
接收端比对本地实际状态：多出来的补 keyup，少了的不补（宁少不多，补多了会误触发）。
连续 3 次不一致 → 记 warning（这是排查连键的唯一线索）。

它同时兼三职：链路存活判据、RTT 采样、按键状态对账。

### 8.5 吞键闸门（安全底线）

**只有同时满足以下四条才允许 `return 1` 吞掉按键：**

1. `focus.target != 本机`
2. 链路已 ready 且最近 300ms 内收到过对端心跳
3. 该键不在白名单（§8.7）
4. 事件不是自己注入的（`LLKHF_INJECTED` 位）

任何一条不满足 → `CallNextHookEx` 放行。

> 为什么这么严：吞了但没送出去，按键会落进那个你**看不见**的 Windows 窗口里乱敲，
> 可能触发 Win+L 直接锁屏、可能删掉东西。宁可让用户在本机打出一串乱码。

### 8.6 钩子只能有一个

`hotkey/windows.py::LowLevelHookBackend` **已经装了 `WH_KEYBOARD_LL`**。
再装第二个钩子会形成链式调用，顺序按安装时间倒序，吞键行为变得不可预测。

方案：把钩子提到 `keyboard/hook_windows.py`，做成**唯一钩子 + 分发器**：

```
钩子回调 → ① 热键匹配（命中就回调 app，并按配置吞掉）
         → ② 键盘桥路由（编码、入队、决定是否吞）
         → ③ CallNextHookEx
```

`hotkey/windows.py` 的 `hook` 后端改成"向分发器注册一个匹配器"，对外行为不变，
现有测试（`tests/` 里钩子相关用例）必须继续绿。

### 8.7 白名单：热键必须能回来

转发期间如果连切屏热键一起吞掉，用户就**再也切不回去了**（热键在 Windows 上，被自己吞了）。所以：

| 永远不转发、且不吞 | 说明 |
|---|---|
| 切屏热键（默认 `Alt+Space`） | 由本机 DualSwitch 处理 |
| 紧急停止组合（默认 `Ctrl+Alt+Shift+K`） | 一键解除吞键，把键盘还给 Windows |
| 键盘固件层（`Fn+Q/W/E/R`、背光） | 根本不会到达 OS，天然放行 |

**已知冲突**：`Alt+Space` 在 Mac 上是 Spotlight（`⌘Space`，因为 Alt→⌘）。
白名单会让它在转发期间只切屏、不落到 Mac。需要 Spotlight 时用 MacBook 自带键盘，或在设置里改呼出键。
这条必须写进用户文档，否则会被当成 bug。

### 8.8 注入回环防护

- Windows 侧：`KBDLLHOOKSTRUCT.flags & LLKHF_INJECTED` → 直接放行，不路由。
- macOS 侧：给注入的 CGEvent 打 `kCGEventSourceUserData` 标记（卫生措施，Mac 侧不装 tap，暂无回路）。

---

## 9. 延迟

### 9.1 预算（估算，待真机实测替换）

| 环节 | 有线→AP→Wi-Fi |
|---|---|
| Windows 钩子回调 + 编码 | 0.1–0.5 ms |
| 内核发送 + 网线 + AP 转发 | 0.3–1.0 ms |
| Wi-Fi 空口（下行） | 1–5 ms |
| Mac 唤醒 + 解码 + CGEventPost | 0.2–1.0 ms |
| **新增合计** | **约 3–8 ms（p50），p99 视 Wi-Fi 环境 15–40 ms** |

对比：Deskflow 常见 30–100 ms。差额不在网线，在它的队列、TLS 记录层、以及没关 Nagle。

### 9.2 怎么量（不靠感觉）

1. **RTT**：现有 `T_PING/T_PONG` 直接读，10Hz 采样，记 p50/p95/p99。
2. **单向**：`T_KEY` 里的 `t` 与接收端时钟差（同一台机器做校准，或用 RTT/2 近似）。
3. **端到端感知**：`tools/key_feeder.py` 发 1000 个键，Mac 端用 `CGEventTap` 只监听不打日志，量注入到应用的时间。
4. 验收线：**p50 < 8ms、p99 < 20ms**（Wi-Fi）。

### 9.3 Wi-Fi 尾部抖动是唯一真问题

平均 5ms 对打字毫无影响（人感知阈值约 30ms），但省电唤醒会让"空闲后第一下"多 10–100ms。
对策（按性价比）：

1. **100ms 心跳**（`T_KEYSTATE` 本身就是心跳）让接收侧无线保持活跃——**这是唯一纯软件有效的手段**。
2. Windows 侧网卡关节能（设备管理器 → 电源管理；`powercfg /setacvalueindex` 无线适配器 = 最高性能）。
3. 两台连同一个 5GHz AP，别跨 AP、别一 5G 一 2.4G。
4. 验收看 **p99**，不看均值。均值会骗人。

---

## 10. 硬伤清单

### 10.1 不可解（只能规避 + 告知用户）

| # | 硬伤 | 现象 | 规避 |
|---|---|---|---|
| 1 | **Mac 锁屏** | 注入到登录窗/锁屏界面会被系统拒绝，密码打不进去 | 文档写清：解锁请用 MacBook 自带键盘。界面在检测到锁屏时提示一句 |
| 2 | **Windows 锁屏 / UAC 安全桌面** | 输入走的是另一个桌面，钩子收不到；`Ctrl+Alt+Del` 更是 SAS，用户态拦不住 | 同上：这段时间键盘对 Mac 是哑的，用户需先解锁 Windows |
| 3 | **Mac 侧特殊应用拒收注入** | 个别全屏游戏、部分受保护窗口 | 无解，日志记一条即可 |
| 4 | **Wi-Fi 尾部抖动** | 空闲后第一下可能慢 | §9.3 |

### 10.2 有代价（可解，但要设计进去）

| # | 坑 | 对策 |
|---|---|---|
| 5 | **Windows 钩子被静默摘除**（回调卡住超过 `LowLevelHooksTimeout`，默认 300ms，系统不报错直接卸） | 双通道互证：再开一路 `Raw Input (RIDEV_INPUTSINK)` 当"确实有按键"的证人；钩子沉默但 Raw Input 有动静 ⇒ 重装钩子。同时把注册表 `LowLevelHooksTimeout` 调到 1000ms |
| 6 | **Windows 会自己睡**（画面在 Mac 上，敲 20 分钟，Windows idle 到点就睡，键盘当场断） | 转发期间持续 `SetThreadExecutionState(ES_SYSTEM_REQUIRED)` |
| 7 | **macOS 辅助功能/输入监控授权** | 需要 `CGEventPost` 权限；自签名 bundle 重打包后 TCC 可能失效（老问题）→ 固定签名 + 安装说明 + 启动自检提示 |
| 8 | **键盘跟随与鼠标跟随时延不一致** | 鼠标走蓝牙重连要 1–3 秒，键盘是毫秒级 ⇒ 切到 Mac 后键盘立刻能用、鼠标还要等一会儿。这是硬件通道的固有差异，界面提示即可 |
| 9 | **两端热键冲突**（`Alt+Space` 在 Mac 是 Spotlight） | §8.7 白名单 + 文档 |

### 10.3 直接可解

键位映射、CapsLock 对齐、全量释放、位图对账、注入回环、断链即放行、加密传输。

---

## 11. 权限、电源、安全

| 项 | 做法 |
|---|---|
| macOS 权限 | 需要"辅助功能"（注入）。启动自检：`AXIsProcessTrusted()` 为假就提示去开，不静默失败 |
| Windows 权限 | 捕获不需要管理员；也不需要管理员注入（Windows 只捕获）。**这是本方向的又一个便宜** |
| Windows 防休眠 | 转发期间 `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`，`armed=False` 时撤销 |
| 传输安全 | 复用现有 psk + AEAD，键盘帧同链路同密钥。**键盘帧不单独降级** |
| 用户知情 | 转发期间界面/托盘必须明确显示"键盘正在转发到 Mac"，并给出"停止"入口 |
| 日志 | 默认不打按键内容，只打 usage + 计数 + 延迟分布；排查时才开 `keyboard_debug` |

> 原则：**能证明"我们没在偷键盘"的能力，比键盘功能本身更重要。**

---

## 12. 配置项

```json
{
  "keyboard_follow": {
    "enabled": false,
    "capture_host": "windows",
    "mod_map": "position",
    "release_chord": "ctrl+alt+shift+k",
    "blur_guard_ms": 0,
    "forward_media_keys": false,
    "key_state_hz": 10,
    "repeat_mode": "auto"
  }
}
```

- `enabled` 默认 **false**：与鼠标联动同样的理由——配错影响面大，先让用户主动开。
- `capture_host`：哪台机器挂着键盘。写死 `windows` 也行，留成字段是为了将来换布局。
- `repeat_mode`：`auto` 表示阶段 0 实测后自动选（§8.2），也允许手动钉死。

---

## 13. 界面落点（v2，**已实现**）

真源：`design/redesign/INTERACTIONS-v2.md` §14（版式 + 状态表 + 验收清单）。
Windows 端照做版：`docs/UI-WINDOWS2-DESIGN.md` §5.4。

切屏页在「显示器卡片」与「快速操作」之间新增一节 **「跟随画面」**，两行：

| 行 | 右侧 | 状态行 |
|---|---|---|
| 键盘跟随 | 开关 | `转发中 → Mac · 往返 6ms` / `已暂停 · 按键留在 Windows` / `链路断开 · 已自动回到 Windows 本机` |
| 鼠标跟随 | 开关（槽位没配时**隐藏**） | `跟随中 · 本机槽 2 ⇄ 对端槽 1` / `已关闭` / `槽位未配置 · 在 config.json 填 mouse_switch` |

三条设计约束：

1. **一个状态，两个入口。** Mac 单方面关掉接收而 Windows 照吞 = 按键黑洞，
   所以状态由链路同步，两端只是同一状态的显示/操作入口（§4.1）。
2. **槽位没配就不给开关**（不是置灰）——打了也没效果，置灰会被当成"待会儿能开"。
3. **窗口 780×580 → 780×720。** 加这张卡后切换页最小高度 642，580 的窗口装不下，
   `setFixedSize` 不滚动，会直接压出重叠。守备用例 `test_switch_page_still_fits_the_window`。

## 14. 分阶段实施

每个阶段都能**独立验收**，且阶段 1 完全不需要 Windows 在场。

### 阶段 0：验证五个待定事实（Mac 上就能做，约 10 分钟）

**状态：工具已就绪（`tools/key_feeder.py --probe`），结论未回填。**

`tools/key_feeder.py --probe`：

| # | 问题 | 影响 |
|---|---|---|
| ① | macOS 对注入的 keydown 是否自动重复 | 决定 §8.2 走哪条分支 |
| ② | 安全输入开启时，CGEventPost 是否仍能注入（终端 / 密码框） | 决定 §10.1-1 的范围 |
| ③ | 锁屏界面注入是否失败 | 确认 §10.1-1 |
| ④ | 注入事件能否被普通 app 正常接收（TextEdit 打一段中英混排） | 基本功 |
| ⑤ | 注入时设置 flags 是否影响输入法合成 | 决定 §7.2 实现方式 |

**产物**：一份 5 条结论，写回本文件 §16。

### 阶段 1：Mac 注入端 + 协议 + 喂键工具（不需要 Windows）

**状态：已实现（2026-11-06）。** 落地位置见 §17。

- `keyboard/keymap.py`：HID usage ↔ kVK 表（手写常量，不生成 —— 表本身是标准，生成器反而多一层）
- `keyboard/state.py`：按键状态机
- `keyboard/inject_macos.py`：Quartz 注入（懒加载，可注入假实现）
- `keyboard/receiver.py`：收帧 → 状态机 → 注入
- `sync/protocol.py`：`T_KEY(0x40) / T_KEYSTATE(0x41) / T_FOCUS(0x42)`
- `sync/transport.py`：三个回调 + 三个发送方法 + **`TCP_NODELAY`**
- `sync/engine.py`：只搬运，不认识按键含义
- `tools/key_feeder.py`：本地直喂 / 走真实链路 / 阶段 0 探测
- **验收**：`--text "Hello, world!"` → TextEdit 里正确出现；`--keys "0xE2:1 0x06:1 0x06:0 0xE2:0"`
  → 复制（Alt+C = ⌘C）；连发 1000 键无丢帧。

### 阶段 2：Windows 捕获端（设备可用时）

**状态：已实现（2026-11-06）。** 落地位置见 §18。所有"吞键安全"逻辑都在这里。

- `keyboard/hook_windows.py`：抽出唯一钩子 + 分发器，`hotkey/windows.py` 改为注册匹配器
- `keyboard/capture_windows.py`：路由、闸门、心跳、全量释放、往返采样
- **验收**：`tools/key_tap_probe.py` 打印每个键、是否吞掉、闸门四条件的状态；
  拔网线 → 立刻停止吞键（这条必须看着日志验）；`hotkey` 相关测试全绿。

> **两处与设计稿的偏差（实现期决定，都有理由）**：
> 1. **Raw Input 互证没做**。它的作用是"钩子漏掉的键由 Raw Input 补看"，用来判断
>    闸门是否该开；但闸门真正的判据是"链路活着 + 画面在对面"，Raw Input 补不出这两条
>    信息，反而多一条常驻的注册链路。V1 不做，等真出现"钩子漏键"的现场再加。
> 2. **`SetThreadExecutionState` 保活没做**。捕获端不阻止休眠 —— 画面在 Mac 时
>    Windows 本来就该睡（这正是本方案比"反向桥"省事的地方：Mac 永远有画面）。
>    真需要防休眠时应该由用户在电源设置里配，不该由键盘桥偷偷改全局状态。

### 阶段 3：联调

- 两端 focus 接 `_after_screen_switch`
- **验收**：连续切 20 次不出错端、不卡键；打字 10 分钟无卡顿；拔网线再插回能自愈；
  按 `Fn+W` 把键盘挪走后不报错、不抢。

### 阶段 4：打磨

CapsLock 对齐、媒体键（可选）、界面状态、异常文案、日志分级。

**工作量粗估**：阶段 0–1 约 400 行，阶段 2 约 350 行，阶段 3 主要是调试。

---

## 15. 退出策略（必须有）

| 层 | 手段 |
|---|---|
| 硬件层 | `Fn+Q/W/E/R` —— 固件行为，软件看不见也拦不住，**永远有效** |
| 软件层 | 设置开关；紧急组合 `Ctrl+Alt+Shift+K` 一键解除吞键 |
| 崩溃层 | 进程退出 → 钩子随之消失 → 按键自动直通 Windows |
| 断链层 | 一个心跳周期（≤300ms）内停止吞键 |

这四层任意一层生效，用户都不会被"卡在没键盘的状态"。

---

## 16. 开放问题（阶段 0 要回答的）

1. macOS 对注入 keydown 的重复行为？→ 决定 `repeat_mode` 默认值
2. 安全输入（终端/密码框）是否拦注入？→ 决定要不要在 Mac 上提示"此窗口不可转发"
3. 锁屏界面注入是否静默失败？→ 决定 §10.1-1 的文案
4. Lofree 侧边触控条是否以 HID 上报？→ 决定 §7.4 是否要做
5. Raw Input 互证在 `LLKHF_INJECTED` 事件上是否也会触发？→ 影响 §8.6 的判据
6. Windows 的 `hotkey_backend=auto` 下用的是 `RegisterHotKey`，那现有钩子可能根本没装
   —— 是否统一改成"总是装钩子"？会改变现有热键行为，需要在阶段 2 单独评估

---

## 17. 实现状态（Mac 端，2026-11-06）

### 17.1 文件清单

| 文件 | 说明 |
|---|---|
| `src/dualswitch/keyboard/keymap.py` | HID usage ↔ kVK 全表、修饰键重映射、CapsLock/功能键、字符表（喂键工具用） |
| `src/dualswitch/keyboard/state.py` | `KeyState`：按下集合、重复键判据、全量释放 |
| `src/dualswitch/keyboard/inject_macos.py` | `MacInjector`：`CGEventPost(kCGHIDEventTap)`、CapsLock 读写、权限查询 |
| `src/dualswitch/keyboard/receiver.py` | `KeyboardReceiver`：epoch/seq 过滤、注入、对账、焦点释放 |
| `src/dualswitch/sync/protocol.py` | `T_KEY/T_KEYSTATE/T_FOCUS` + `key_header/key_state_header/focus_header` |
| `src/dualswitch/sync/transport.py` | 三个回调 + `send_key/send_key_state/send_focus` + `TCP_NODELAY` |
| `src/dualswitch/sync/engine.py` | 只搬运，不解释按键含义；`keyboard_stats` |
| `src/dualswitch/config.py` | `KeyboardFollowConfig` + 合并/校验 |
| `src/dualswitch/app.py` | `set_keyboard()`；`_after_screen_switch()` 里通知释放 |
| `src/dualswitch/cli.py` | 按 `keyboard_follow.enabled` 建接收器并挂到引擎 |
| `tools/key_feeder.py` | 本地直喂 / 链路喂 / 阶段 0 探测 / 申请授权 |
| `tests/test_keyboard.py` | 31 例：映射、状态机、注入端、协议字段 |

### 17.2 实现期发现的两个真问题（都已在代码里改掉）

1. **pyobjc 的 `Quartz` 里没有 `AXIsProcessTrusted`**（实测这台机器 `hasattr` 为 False）。
   查它会 `AttributeError`，被 `except` 兜底后表现成"一切正常、就是打不出字"。
   正确的是 `CGPreflightPostEventAccess()`（macOS 10.15+，不弹窗、不阻塞）。
   同理 `IsSecureEventInputEnabled` 也不在 `Quartz` 里，得用 ctypes 走 Carbon。
   —— 和 `kCGKeyboardEventFlags` 那次踩坑是同一类：**平台相关的能力探测不能靠猜**。
2. **receiver 的 epoch 只能跟对端走**。本端若在本地切屏时也自增，两端会数出两套号，
   之后所有帧都被判过期 —— 现场表现是"连得上、状态正常、就是不打字"。
   现在本地切屏只做 `release_all()`，不动 epoch。

### 17.3 怎么验（不需要 Windows）

```bash
# ① 环境 + 重复键行为（会注入一次长按 'a'，先点进 TextEdit）
python3 tools/key_feeder.py --probe

# ② 未授权时触发系统弹窗
python3 tools/key_feeder.py --request-access

# ③ 打一段字（光标先进 TextEdit）
python3 tools/key_feeder.py --text "Hello, world!"

# ④ 快捷键：Alt→⌘ 已生效的话，这条就是 ⌘C
python3 tools/key_feeder.py --keys "0xE2:1 0x06:1 0x06:0 0xE2:0"
```

### 17.4 还没做

| 项 | 归属 |
|---|---|
| ~~Windows 捕获端~~ | **已做（2026-11-06）**，见 §18 |
| ~~`T_KEYSTATE` 的发送侧~~ | **已做**（捕获端 worker 空闲时发，兼 RTT 采样） |
| ~~界面状态行 / 开关~~ | **已做**（两端切换页「跟随画面」卡片） |
| 媒体键、侧边触控条 | 阶段 4（§7.4） |
| 阶段 0 的五条结论回填 §16 | 跑一次 `--probe` 即可 |
| **真机联调**（连续切 20 次 / 打字 10 分钟 / 拔网线自愈） | 阶段 3，待做 |

## 18. 实现状态（Windows 捕获端，2026-11-06）

### 18.1 文件清单

| 文件 | 说明 |
|---|---|
| `src/dualswitch/keyboard/hook_windows.py` | `KeyboardHook`：**进程唯一**的 `WH_KEYBOARD_LL` + 分发器，引用计数式 `acquire/release` |
| `src/dualswitch/keyboard/capture_windows.py` | `KeyboardCapture`：闸门、epoch、发送队列、心跳、全量释放、RTT 采样 |
| `src/dualswitch/keyboard/keymap.py` | 新增 `USAGE_TO_WIN_VK` / `VK_TO_USAGE` / `usage_for_vk()`（协议里只有 usage，捕获端必须先翻回 usage） |
| `src/dualswitch/hotkey/windows.py` | `LowLevelHookBackend` 改为向共享钩子注册匹配器（不再自己装钩子） |
| `src/dualswitch/keyboard/__init__.py` | `create_endpoint(cfg, …)`：按平台返回捕获端 / 注入端，`cli.py` 不写平台分支 |
| `src/dualswitch/cli.py` | 端点总是创建；`attach(engine)` 接上发送通道与 RTT 探测；`_on_keyboard_release` 落盘 |
| `src/dualswitch/app.py` | `_commit_read_host()` 里补一条：**外部切换**（对端发起 / 手按 OSD）也通知键盘端点 |
| `src/dualswitch/ui/win2/runner.py` | `_refresh_follow()` + 两个开关 handler（关 = 立刻停吞 + 抬键） |
| `tools/key_tap_probe.py` | 阶段 2 验收：逐键打印闸门四条件与吞/放行 |
| `tests/test_keyboard_windows.py` | 17 例：闸门、白名单、epoch、抬键、心跳、钩子共用 |

### 18.2 实现期发现/决定的三件事

1. **钩子回调里不能做 I/O。** 第一版直接在回调里 `engine.send_key()`，被自己否掉了：
   `WH_KEYBOARD_LL` 的回调在系统输入链上同步执行，一次阻塞的 `sendall` 就能让整台机器
   打字卡顿。改成"回调只入队（`queue.Queue`，上限 256）+ worker 线程发送"，
   队列满就不吞（宁可漏几个键，也不能把输入链堵住）。
2. **闸门的"链路活着"用"最近 300ms 有成功发送"代替"收到对端心跳"。** 设计稿写的是
   后者，但注入端不发心跳帧（它只收），捕获端根本收不到。改成自证：worker 每 100ms
   发一次 `T_KEYSTATE`，成功就刷新时间戳 —— 发送成功本身就说明链路还在。
   失败则闸门在 300ms 内自动关闭，不需要对端通知。
3. **`hotkey_backend=hook` 与键盘桥共用钩子这件事，必须用"引用计数"而不是"谁先装谁赢"。**
   热键重建（`_rebuild_hotkey`）会 `stop()` 再 `start()`，如果桥的钩子跟着被摘掉，
   用户改个热键就把键盘转发弄哑了 —— 现在是最后一个 `release()` 才真正摘钩子。
   另有一个坑：`self._on_event` 每次取值都是新的 bound method 对象，
   `release()` 里用 `is` 比较永远不相等（钩子静默泄漏），必须用 `==`。

### 18.3 怎么验（Windows）

```bash
# ① 只看不吞：逐键打印 usage / 是否注入 / 闸门四条件
python tools/key_tap_probe.py

# ② 真吞键（画面必须在 Mac，否则闸门不开）：Ctrl+Alt+Shift+K 解除
python tools/key_tap_probe.py --swallow

# ③ 单元用例（不需要真键盘，闸门逻辑用假钩子 + 假引擎）
pytest -q tests/test_keyboard_windows.py
```

**真机联调（阶段 3）仍未做**：连续切 20 次、打字 10 分钟、拔网线再插回、
按 `Fn+W` 把键盘挪走后不报错 —— 这四条要在两台机器都在场时验。
