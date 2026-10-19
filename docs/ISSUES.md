# 任务台账

> 格式见 `docs/COORDINATION.md` 第 2 节。**每条必须有「验收」命令**，没有就不算登记。
> 状态：`待做` → `进行中` → `已合并 <sha>`。改状态只改自己 owner 的行。

---

## ISSUE-001 没接外接屏时误报「当前画面 Windows」

- **owner**: mac
- **影响**: 两端（`app.py` 是共享代码，Windows 端 pull 即可，**不要另写一份**）
- **现象**: 拔掉外接屏启动，约 4 秒后界面变成「当前画面 Windows / 已同步外部切换 · 当前 Windows」，
  日志出现 `检测到画面已在 windows（显示器离开本机）`。根因：`detect_source()` 把「枚举不到」
  一律当成「画面在另一台」，缺 `unknown` 这一态。
- **期望**:
  - 本次会话**从未**枚举到外接屏 → 卡片显示「未检测到外接显示器」，两张卡都不点亮，
    页脚不再出现「已同步外部切换」；
  - 顺手把「切换到 …」按钮置灰或改成「重新检测」，避免盲发后报
    `Could not find a suitable external display`；
  - **保持不变**：见过屏、之后消失 → 仍判为「在另一台」（这是正常切走的信号）。
- **验收**:
  1. `QT_QPA_PLATFORM=offscreen pytest -q tests/test_source_state.py --basetemp=.pytest-tmp`
     （新增文件：三态 ①②③ —— 从未见过 / 见过后消失 / 当前可见）
  2. 真机：拔掉外接屏启动，等 10 秒，界面不出现 Windows 状态；插回屏后 5 秒内恢复正确状态。
  3. `pytest -q --basetemp=.pytest-tmp` 全绿。
- **状态**: 已合并 `d8667f1`（mac 端完成，Windows 端 pull 即生效，**无需改代码**）
  · 15 例新增测试；真机无外接屏启动 7 秒，日志不再出现「检测到画面已在 windows」
  · 全量 198 passed；`tools/check_mac_ui.py` 全部通过

---

## ISSUE-002 屏在 Windows 输入时，Mac 端无法切回

- **owner**: mac（`sync/protocol.py` + `sync/engine.py` 都在共享层；Windows 端预计 0 行代码）
- **影响**: 两端；但**先做物理验证再动代码**（见下）
- **现象**: 屏走 HDMI（Windows）时，macOS 侧枚举不到该屏，`m1ddc set input` 报
  `Could not find a suitable external display`，点「切换到 Mac」必然失败。
- **前置验证（30 秒，不做完不许设计协议）**:
  屏显示 Windows 时，在 Mac 上跑 `m1ddc display list`：
  - 若仍列出 `Q2790R3` → **本 issue 直接作废**，本地直发即可，只改按钮的失败提示；
  - 若只有 `[1] (null)` → 走远端代发。
- **期望（走远端代发时）**: Mac 点「切换到 Mac」→ 经现有 TCP 通道请求对端代发 VCP 0x60=18
  → 对端执行后回 ACK → Mac 端页脚显示「已由 Windows 端切换」。
- **契约（需双端确认，`[contract]` 前缀）**:
  新增 `T_SWITCH = 0x30`，header 至少 `{"vcp": 96, "value": 18, "req": "<幂等 id>"}`；
  对端回 `T_ACK` 带同一 `req`。旧端收到未知类型必须**忽略而不拆连接**（兼容口子）。
- **验收**:
  1. `pytest -q tests/test_sync_protocol.py -k switch --basetemp=.pytest-tmp`（黄金样本：固定 header → 固定字节）
  2. 真机：Mac 点按钮，Windows 端日志出现 `remote switch vcp=0x60 value=18`，屏 3 秒内切过来。
- **状态**: 待做（等 ISSUE-002 前置验证结论）

---

## 待登记

新问题先在对话里描述清楚「什么现象 + 期望什么」，再按上面格式补一条，
**不要**先写设计文档。
