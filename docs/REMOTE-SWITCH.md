# 远端代发切屏（ISSUE-002）· 协议与分工

> 2026-10-19 立 · 适用 `src/dualswitch/sync/{protocol,transport,engine}.py` + `app.py`
> **这份文档是给"另一端的 AI / 另一个人"看的**：Windows 端读完第 5 节就知道要做什么
> （结论：**本次 0 行代码**，pull + 验证）。

---

## 1. 要解决的问题

显示器在 Windows 输入上时，macOS 枚举不到这块屏（`m1ddc` 报
`Could not find a suitable external display`），本机发不出 DDC Set。
于是「坐在 Mac 前把画面叫回来」这件事做不到 —— 之前只能去按显示器 OSD，或者跑到 Windows 那台按热键。

反向不需要解决：屏在 Mac 上时，Mac 自己就能切走，而 Windows 端本来就看不到屏。

## 2. 设计决定：本地优先，失败才请对端

**没有**采用「Mac 一律请 Windows 代发」。原因是一条未验证的物理事实：有些显示器在非当前输入上
仍然保持 DDC 通道，那种情况下 Mac 本地就能切，绕一圈反而慢（还要走加密 TCP）。

所以固定顺序是：

```
按热键 / 点按钮
  → 本地 backend.set_input(value)          ← 65ms 左右
      · 成功 → 结束（与以前完全一样，不碰网络）
      · 抛 DDCError（m1ddc 找不到屏）→ 走远端
  → engine.request_switch(value)            ← 3s 超时
      · 对端回执 ok  → 成功，界面写「已由 Windows 端代发 · 回读跳过」
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
- 不做「循环代发」：执行方只发 DDC、不回切屏请求；不存在两端互踢。
- 不做「自动重试风暴」：一次触发最多一次远端请求，超时就报错，等用户再按。
- 执行方的 DDC 调用放在独立小线程里（`remote-switch`），不堵 socket 读循环 —— 否则一次 65ms
  的 Set 会拖慢剪贴板收帧。
- 演练模式（`dry_run`）下执行方直接拒绝（"本机处于演练模式"），不会假装成功。

## 5. 两端分工

| 端 | 要做什么 |
|---|---|
| **macOS** | 已实现：`protocol.py` 两个类型、`transport.py` 收发与分发、`engine.py` 请求/回执/幂等、`app.py` 本地失败回落、`ui` 未知态下按钮可点（文案「从 Windows 切过来」） |
| **Windows** | **`git pull` 即可，0 行代码。** `sync/*` 与 `app.py` 都是共享层，只归 macOS 端写 |

Windows 端要做的只有三件**验证**（不是开发）：

```powershell
git pull --rebase
python -m pytest -q                              # 应全绿（新增 17 例在 tests/test_remote_switch.py）
dualswitch doctor                                # 确认 clipboard.enabled=yes、已配对
```

然后两端都开着程序，在 Mac 上按热键 / 点「从 Windows 切过来」：

- Windows 端日志应出现 `替对端切屏 VCP 0x60=18 → ok`
- Mac 端日志应出现 `远端代发切屏 → ok`，界面页脚「已由 Windows 端代发 · 回读跳过」

如果 Windows 日志里是 `忽略未知帧 <SWITCH …>` —— 说明它跑的是旧版本，升级即可。

前置条件：**两端都要开着程序**（远端代发挂在剪贴板同步链路上，`clipboard.enabled=false` 时不可用）。
两端在同一个网段、已配对（`dualswitch clip pair --pin <码>`）。

## 6. 验收

```bash
# 单测（macOS / Windows 都跑同一份）
pytest -q tests/test_remote_switch.py
```

覆盖：帧往返（加密 + 明文）、未连接快速失败、超时、成功回执、对端拒绝、幂等去重、
非法 VCP 拒绝、无执行器、本地失败回落、从未见过屏时走对端、对端未就绪时不发请求、
未知态按钮随对端在线状态开关。

人工验收（需要真机 + 显示器）：

1. 显示器切到 HDMI（Windows），Mac 端界面应为「未检测到外接显示器」且按钮**可点**；
2. 点「从 Windows 切过来」→ 3 秒内画面切到 Mac；
3. 在 Windows 端拔掉网线再点一次 → 按钮立刻变灰（250ms 轮询），点了报「未检测到外接显示器」。

## 7. 已知未验证项

「屏在 HDMI 时 Mac 是否仍能枚举到它」还没在有显示器的情况下实测过（2026-10-19 当时没接屏）。
不影响本设计：能枚举到就走本地（第 2 节的优先级），枚举不到才走远端。
拿到显示器后跑一次 `m1ddc display list` 即可把这个事实记进 `TROUBLESHOOTING.md`。
