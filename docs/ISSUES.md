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

- **owner**: mac（`sync/protocol.py` + `sync/engine.py` 都在共享层；Windows 端 0 行代码）
- **影响**: 两端（Windows 端只需 pull + 验证）
- **现象**: 屏走 HDMI（Windows）时，macOS 侧枚举不到该屏，`m1ddc set input` 报
  `Could not find a suitable external display`，点「切换到 Mac」必然失败。
- **设计**: 见 [`REMOTE-SWITCH.md`](REMOTE-SWITCH.md)。**本地优先、失败才请对端代发**，
  所以「屏在别的输入时 macOS 还能不能枚举到」这个未验证项不再阻塞实现：
  能枚举到走本地，枚举不到才走链路。
- **契约（`[contract]`）**: 新增 `T_SWITCH = 0x30` / `T_SWITCH_ACK = 0x31`，
  header `{"vcp":96,"value":18,"req":"<幂等 id>"}`；旧端落到「忽略未知帧」，不断连。
- **验收**:
  1. `pytest -q tests/test_remote_switch.py --basetemp=.pytest-tmp`（17 例：帧往返、超时、
     回执、拒绝、幂等、非法 VCP、本地失败回落、对端未就绪不发请求、未知态按钮）
  2. 真机：屏在 Windows，Mac 点「从 Windows 切过来」→ 3 秒内切过来；
     Windows 端日志出现 `替对端切屏 VCP 0x60=18 → ok`
  3. 拔掉对端网线：按钮 250ms 内变灰
- **状态**: 已合并 `012a4f3`（mac 侧全链路 + 17 例测试）；Windows 端 pull 即可。
  真机验收（需要接显示器）待做 —— 当前机器没外接屏。

---

## 待登记

新问题先在对话里描述清楚「什么现象 + 期望什么」，再按上面格式补一条，
**不要**先写设计文档。
