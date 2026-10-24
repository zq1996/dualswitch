# 归档：历史记录，不再更新

> 2026-10-19 建，2026-10-24 扩充。这里的文档记录「当时为什么这么做」，**不是现状**。
> 要改行为/查现状，看 [`docs/README.md`](../README.md) 里的现行文档；不要按这里的方案动手。

| 文档 | 记录了什么 | 现状以此为准 |
|---|---|---|
| [RESEARCH.md](RESEARCH.md) | 2026-10-17 选型调研：为什么没直接用 DDCommando / m1ddc，而自研应用层 | `PLATFORM-SPLIT.md`、`ddc/` 代码 |
| [VERIFY-M1DDC.md](VERIFY-M1DDC.md) | 开发前的一次性验证：AOC Q2790PC 在 m1ddc 下的输入源切换可行性、读通道 -118 的由来 | `ddc/macos.py` 顶部注释、`TROUBLESHOOTING.md` §2 |
| [CLIPBOARD-SYNC-PLAN.md](CLIPBOARD-SYNC-PLAN.md) | 跨端剪贴板的方案与工作量评估（实现前） | `CLIPBOARD-SYNC-CONTRACT.md`（契约） |
| [CLIPBOARD-SYNC-HANDOFF.md](CLIPBOARD-SYNC-HANDOFF.md) | 给 Windows 端 AI 的联调交接单（两端口径对齐） | `COORDINATION.md` + `ISSUES.md` |
| [WIN-TRAY-INVESTIGATION.md](WIN-TRAY-INVESTIGATION.md) | Windows 托盘图标不显示的完整排查（完整性级别 → Shell_NotifyIcon 被 UIPI 丢弃） | `TROUBLESHOOTING.md` §4.1 |
| [INVESTIGATION-INPUT-BOUNCE.md](INVESTIGATION-INPUT-BOUNCE.md) | 2026-10-23 输入源回弹调查。**结论部分已作废**：`CGDisplayIsActive` 恒为 1，`active_monitors()` 那条修法不成立。只有第 1 节（现象）和第 2 节（走过的弯路）还有背景价值 | `SCREEN-OWNERSHIP.md` |
| [TEST-PEER-DISPATCH.md](TEST-PEER-DISPATCH.md) | 2026-10-23 对端代发真机验收流程。**判据失效**：第 0 步要求的「活动画面: 无」永远不会出现 | `SCREEN-OWNERSHIP.md` §7 |
| [UI-DESIGN.md](UI-DESIGN.md) | 2026-10-17 Windows 界面设计 v1（440 宽卡片堆叠） | `UI-WINDOWS2-DESIGN.md` + `design/redesign/` |
| [UI-DESIGN-MAC.md](UI-DESIGN-MAC.md) | 2026-10-17 macOS 界面设计 v1 | `UI-MAC2-IMPLEMENTATION.md` + `design/redesign/` |
| [UI-DESIGN-CLIPBOARD.md](UI-DESIGN-CLIPBOARD.md) | 剪贴板页 v1 设计。**四态语义、敏感内容不记录等规则仍有效** | `design/redesign/INTERACTIONS-v2.md` §10/§11 |
| [CLIPBOARD-SYNC-LOG.md](CLIPBOARD-SYNC-LOG.md) | 剪贴板同步的验证记录（三道闸），功能已完成并稳定 | `tests/test_clipboard_*.py` |

