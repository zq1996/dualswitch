# 远端代发切屏（ISSUE-002）· 协议与分工

> 2026-10-19 立 · 适用 `src/dualswitch/sync/{protocol,transport,engine}.py` + `app.py`
> **这份文档是给"另一端的 AI / 另一个人"看的**：Windows 端读完第 5 节就知道要做什么
> （结论：**本次 0 行代码**，pull + 验证）。

---

## 1. 要解决的问题（对称：两边都可能需要）

显示器只会出现在它当前选中的输入上。所以**谁不持有画面，谁就切不回来**：

| 场景 | 屏在 | 看不到屏的是 | 谁能发 DDC | 谁需要求助 |
|---|---|---|---|---|
| 坐在 Mac 前，屏还留在 Windows | HDMI | **macOS** | Windows | Mac 请 Windows 代发 |
| 坐在 Windows 前，屏还留在 Mac | USB-C | **Windows** | macOS | Windows 请 Mac 代发 |

以前这两种情况都只能去按显示器 OSD，或者跑到对面那台机器上按热键。
**远端代发是双向能力，不是 mac 端专属**：`sync/*`、`app.py`、`cli.py`、`ui/` 全是共享层，
两端跑的是同一份代码，谁是发起方、谁是执行方由运行时决定（见第 4 节的「不循环」）。

## 2. 设计决定：本地优先，失败才请对端

**没有**采用「一律请对端代发」。原因是一条未验证的物理事实：有些显示器在非当前输入上
仍然保持 DDC 通道，那种情况下本机就能切，绕一圈反而慢（还要走加密 TCP）。

固定顺序对两端完全一致：

```
按热键 / 点按钮（在哪台机器上按，就以哪台为发起方）
  → 本地 backend.set_input(value)          ← 65ms 左右
      · 成功 → 结束（与以前完全一样，不碰网络）
      · 抛 DDCError（找不到屏）→ 走远端
  → engine.request_switch(value)            ← 3s 超时
      · 对端回执 ok  → 成功，界面写「已由 X 端代发 · 回读跳过」
      · 超时 / 拒绝  → 失败，界面报**本地那条**错误（信息量更大）
```

对端没连上时**根本不发请求**（避免每次都白等 3 秒）。

## 3. 报文（新增两个类型，旧版本会忽略，不会断连）

沿用第 4 节的帧格式（`DSYN` + ver + type + hdrlen + JSON header + payload），加密与认证不变。

| 类型 | 值 | 方向 | header 字段 |
|---|---|---|---|
| `T_SWITCH` | `0x30` | 发起方 → 执行方 | `{"vcp": 96, "value": 18, "req": "<16 位 hex>"}` |
| `T_SWITCH_ACK` | `0x31` | 执行方 → 发起方 | `{"req": "<同上>", "ok": true/false, "detail": ""}` |

- `vcp` 固定 `0x60`（Input Source Select）。**执行方必须校验它与自己配置的 `vcp_code` 一致**，
  不一致直接拒绝 —— 防止一端改了 VCP 之后另一端盲发到别的控制项。
- `value` 必须是执行方 `sources` 里出现过的值（17 / 18），否则拒绝。
- `req` 是发起方生成的随机 id；执行方按它在 10 秒内做幂等（重复到达就重发上次的回执，不重复执行）。
- payload 恒为空，`plen=0`。
- 兼容性：旧版本在 `Transport._handle_frame` 里落到「忽略未知帧」，连接不受影响；
  发起方会在 3 秒后超时报「对端未响应（可能版本过旧）」。

## 4. 安全与边界

- 只走**已认证**的链路（`Link.authenticated`），未配对直接 `LinkError("未配对")`。
- **不做循环代发**：执行方只发 DDC、绝不因为执行失败再向对方发一个 SWITCH。
  链路的两端也只有一个「对端」，不存在 A→B→A 的转发环。
- 同一个 `req` 只执行一次（10 秒窗口），所以网络重传不会把屏切两次。
- 不做「自动重试风暴」：一次触发最多一次远端请求，超时就报错，等用户再按。
- 执行方的 DDC 调用放在独立小线程里（`remote-switch`），不堵 socket 读循环 —— 否则一次 65ms
  的 Set 会拖慢剪贴板收帧（两个方向都一样）。
- 演练模式（`dry_run`）下执行方直接拒绝（"本机处于演练模式"），不会假装成功。
- 执行方会校验 `vcp` 与 value 是否属于自己的配置 —— 不信任对端发来的任意值。

## 5. 两端分工

**能力是双向的，代码只有一份**：`sync/*`、`app.py`、`cli.py`、`ui/` 都是共享层，
两端跑同一份实现，只是运行时角色不同（谁按的谁发起，另一台执行）。

| 端 | 要做什么 |
|---|---|
| **macOS**（本仓库） | 已实现：`protocol.py` 两个类型、`transport.py` 收发与分发、`engine.py` 请求/回执/幂等、`app.py` 本地失败回落、`ui` 未知态下按钮可点 |
| **Windows** | **`git pull` 即可，0 行代码。** 共享层只归 macOS 端写；Windows 端的 DDC 后端（`ddc/windows.py`）本来就有，执行方直接复用它 |

两端都要做的只有**验证**（不是开发）：

```powershell
git pull --rebase
python -m pytest -q                              # 应全绿（新增 18 例在 tests/test_remote_switch.py）
dualswitch doctor                                # 确认 clipboard.enabled=yes、已配对
```

**方向 A（Mac 请 Windows 代发）**：显示器停在 HDMI，Mac 上点「从 Windows 切过来」

- Windows 端日志：`替对端切屏 VCP 0x60=18 → ok`
- Mac 端日志：`远端代发切屏 → ok`，页脚「已由 Windows 端代发 · 回读跳过」

**方向 B（Windows 请 Mac 代发）**：显示器停在 USB-C，Windows 上点「从 Mac 切过来」

- Mac 端日志：`替对端切屏 VCP 0x60=17 → ok`
- Windows 端日志：`远端代发切屏 → ok`

如果执行方日志里是 `忽略未知帧 <SWITCH …>` —— 说明它跑的是旧版本，升级即可。

前置条件：**两端都要开着程序**（远端代发挂在剪贴板同步链路上，`clipboard.enabled=false` 时不可用）。
两端在同一个网段、已配对（`dualswitch clip pair --pin <码>`）。

## 6. 验收

```bash
# 单测（macOS / Windows 都跑同一份）
pytest -q tests/test_remote_switch.py

# 本机端到端（只有一台机器 / Mac 不在场时跑这个）
python tools/e2e_remote_switch.py        # 期望 8/8
```

`tools/e2e_remote_switch.py` 起两个**真实** `SyncEngine`（A 扮 Windows、B 扮 Mac），
各占一个空闲端口，走 127.0.0.1 的真实 socket + 真实加密握手，覆盖：链路建立、
**方向 A / 方向 B 两个方向**、重复 req 幂等、非法 VCP 拒绝、对端离线 0ms 快速失败、
对端重启自动重连、真实 CLI 执行器已挂上（演练模式下明确拒绝）。
执行方是**记录型 executor**，不写真实 DDC —— 所以它不会把显示器切走，也不需要 Mac。

覆盖：帧往返（加密 + 明文）、未连接快速失败、超时、成功回执、对端拒绝、幂等去重、
非法 VCP 拒绝、无执行器、本地失败回落、**两个方向都回落**、对端未就绪时不发请求、
未知态按钮随对端在线状态开关（两端文案各测一次）。

人工验收（需要真机 + 显示器），两个方向各做一遍：

1. 显示器切到 HDMI（Windows）→ Mac 端界面「未检测到外接显示器」且按钮**可点**（「从 Windows 切过来」）；
   点它 → 3 秒内画面切到 Mac；
2. 显示器切到 USB-C（Mac）→ **Windows 端**同样显示未知态，按钮「从 Mac 切过来」，点它 → 画面切回 Windows；
3. 任一端拔掉网线再点一次 → 按钮立刻变灰（250ms 轮询），点了报「未检测到外接显示器」。

## 7. 已知未验证项

「屏在别的输入时，本机还能不能枚举到它」还没在有显示器的情况下实测过（2026-10-19 当时没接屏）。
两个方向都不受这个影响：能枚举到就走本地（第 2 节的优先级），枚举不到才走远端。
拿到显示器后各跑一次 `m1ddc display list`（Mac）与 `dualswitch probe`（Windows），
把结论记进 `TROUBLESHOOTING.md`。
