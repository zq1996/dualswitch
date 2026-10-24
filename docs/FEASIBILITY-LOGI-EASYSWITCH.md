# 罗技 POP Mouse Easy-Switch 软件切换：可行性研究

> 结论先行：**可行，且已在两端实测到设备层**。但存在一条物理约束，决定了它不是"单边加个功能"，
> 而是两端各加一段。

- 研究日期：2026-10-24
- 设备：Logitech POP Mouse（蓝牙直连，PID `0xB030`）
- 环境：Windows 10.0.19045 + macOS（MacBook Air），两端均安装 Logi Options+

---

## 1. 结论

| 问题 | 答案 |
|---|---|
| 能不能用软件切 Easy-Switch | **能**，走 HID++ 2.0 的 `CHANGE_HOST` (0x1814) |
| Windows 端能不能发 | **能，已实测**（`switch 2` 把鼠标从槽 1 切到槽 2 成功） |
| macOS 端能不能发 | **能**（可打开 HID++ 集合、读到 `CHANGE_HOST` index 0x0A、当前槽位 2） |
| 要不要卸载/退出 Options+ | **不用**。两端实测 Options+ 运行时 HID++ 通道照常可读写 |
| 能不能做成"点切屏按钮就跟着切" | 能，但**切回方向必须由对端发起**（见 §4） |

---

## 2. 实测数据

### 2.1 设备暴露的 HID 集合

```
Col01  usage_page=0x0001 usage=0x0002   ← 标准鼠标
Col02  usage_page=0xFF43 usage=0x0202   ← HID++ 通道（注意不是常见的 0xFF00）
```

report descriptor（Col02）：

```
06 43 FF   Usage Page 0xFF43
0A 02 02   Usage 0x0202
A1 01      Collection (Application)
85 11        Report ID 0x11
09 02         Usage 0x02
15 00 26 FF 00   Logical 0..255
75 08 95 13      8 bit × 19
81 00            Input  (19 bytes)
09 02 91 00      Output (19 bytes)
C0
```

长报文总长 20 字节（含 report ID）。蓝牙直连时 device index = `0xFF`。

### 2.2 特性表

| 特性 | ID | 运行时 index |
|---|---|---|
| IRoot | 0x0000 | 0x00 |
| CHANGE_HOST | 0x1814 | **0x0A** |
| HOSTS_INFO | 0x1815 | 0x0B |
| DEVICE_NAME | 0x0005 | 0x03 |

`getHostInfo` 返回 **hostCount=3、currentHost=0**（Windows 侧），Mac 侧读到 `currentHost=1`。
三个槽位 `hostType` 均为 `1`（蓝牙），设备不存主机名。

### 2.3 报文格式（关键，踩过坑）

官方规范（Logitech `cpg-docs/hidpp20/README.rst`）长报文：

```
byte0  Report ID        0x11
byte1  Device Index     0xFF（直连）
byte2  Feature Index    0x0A（动态解析得到，不能硬编码）
byte3  Function(高4位) | Software ID(低4位)
byte4+ Parameters
```

**坑**：byte3 是「高半字节 Function、低半字节 Software ID」。写成反过来（sw 在高位）时，
设备不会报错，而是把 `func=0` 的 GetFeature 当成 `func=1` 的 GetProtocolVersion，
一律返回 `04 05`（协议 4.5）。表现是"所有请求都回同一个值"，极易误判为设备不支持。

正确报文：

```
查询主机槽位   11 FF 0A 01                      → 回 numHosts, currentHost
切换主机       11 FF 0A 11 <槽位-1>             → 成功即断开，无回复
```

### 2.4 切换语义

- **成功 = 设备立刻断开重连，没有回复**。超时/无回复是正常现象，不代表失败。
- 切换后目标主机需要 1–3 秒蓝牙重连。
- 目标槽位必须已配对。**切到空槽会失联**，只能按物理 Easy-Switch 键恢复。
- 不能自动重试：超时后结果不确定，重试可能把设备推到错误的槽。

---

## 3. 与 Logi Options+ 的关系

不是"替代"，是"在旁边插一条命令"：

- Options+ 是 GUI 程序，**没有 CLI / API / AppleScript 字典**，程序无法调用它切通道。
  它内部确实会切（Flow 就是它在切），但能力不对外开放。
- 实测两端 Options+ 运行中，HID++ 集合都能正常打开并通信，**不需要退出或卸载**。
- 社区工具（OpenLogi / host-slot-switch）文档里说"两者争用 HID++"，在本机 Windows 与 macOS 上
  均未复现（至少读取与 GetFeature 写入不冲突；`setCurrentHost` 实测也成功）。

---

## 4. 架构约束（最重要的一条）

> **命令只能由鼠标当前连接的那台电脑发出。切走后，原主机再也够不着这只鼠标。**

由此推出方向性差异：

| 场景 | 谁发命令 | 可行 |
|---|---|---|
| 画面 Windows → Mac | **Windows** 发（鼠标还在 Windows） | ✅ |
| 画面 Mac → Windows | **Mac** 发（鼠标还在 Mac） | ✅ |
| 画面 Mac → Windows，由 Windows 发命令拉鼠标 | Windows 够不着鼠标 | ❌ |

所以：**切屏动作的发起方，未必是切鼠标的执行方**。

正确模型是「**谁身上还挂着鼠标、而画面已经走了，就由谁把鼠标推出去**」：

- 画面切到 Mac ⇒ Windows 上的鼠标该走了 ⇒ Windows 推
- 画面切回 Windows ⇒ Mac 上的鼠标该走了 ⇒ Mac 推

这与物理约束天然一致，也是唯一可行的分工。

---

## 5. 集成方案

### 方案 A（推荐）：切换路径挂钩子 + 对端通知

1. 本机执行切屏（`_do_local` / `_do_remote` 成功返回后），若**鼠标仍连在本机**，
   发 HID++ 把鼠标切到「画面去向」对应的槽位。
2. 同时通过现有 TCP 链路通知对端：「画面已回到你那边，请把鼠标推回来」。
3. 对端收到后执行同一条 HID++ 命令。

配置项（`config.json`）：

```json
{
  "mouse_switch": {
    "enabled": true,
    "local_slot": 1,
    "peer_slot": 2,
    "settle_ms": 800
  }
}
```

### 方案 B（兜底）：对端轮询归属

复用现有 `T_STATE` / `T_STATE_ACK`（5 秒互报）：对端发现「画面已不在本机、但鼠标仍在本机」时
主动推鼠标。延迟较大（最多 5 秒），只作为方案 A 的兜底。

### 不推荐：靠画面归属自动切

用户明确否决——归属判定本身有时不准，会让鼠标来回抖。

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 切到未配对槽位导致失联 | 切换前校验目标槽 < hostCount，且要求用户显式配置槽位号；失败提示"按物理键恢复" |
| 蓝牙重连 1–3 秒，期间无输入 | 切屏本身 2–3 秒，可并行；界面上不做阻塞等待 |
| 连续快速切换导致乱序 | 与切屏共用同一把串行锁，`settle_ms` 内不重复发 |
| Options+ 未来版本收紧 HID++ 访问 | 探测失败时降级为「不切鼠标」，不影响切屏主流程 |
| macOS 休眠唤醒后 HID 句柄失效 | 每次切换前重新枚举/打开，不复用长连接 |

---

## 7. 现有工具

| 文件 | 用途 |
|---|---|
| `tools/logi_host_switch.py` | `list` / `probe` / `switch N`，跨平台（hidapi） |
| `logi-mouse.bat` | Windows 入口（**必须 CRLF**） |
| `logi-mouse.sh` | macOS/Linux 入口（**必须 LF + 可执行位**） |

用法：

```bash
logi-mouse.bat probe          # Windows
sh logi-mouse.sh probe        # macOS（首次 pip3 install --user hidapi）
logi-mouse.bat switch 2       # 切到 2 号槽
```

---

## 8. 参考

- Logitech HID++ 2.0 规范：`github.com/Logitech/cpg-docs/blob/master/hidpp20/README.rst`
- `dolphin1404/host-slot-switch`（MIT）：Windows 走 HIDAPI 控制输出、Linux 走 Solaar CLI；
  同样记录了"成功即断开、禁止自动重试"
- `AprilNEA/OpenLogi`（Apache-2.0）：跨平台 Rust HID++ 实现，含 Host Switch 键盘→鼠标联动，
  采用"先切鼠标、后切键盘"的顺序（因为切走后通道失效）
