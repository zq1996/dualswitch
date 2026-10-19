# 归档：历史记录，不再更新

> 2026-10-19 建。这里的文档记录「当时为什么这么做」，**不是现状**。
> 要改行为/查现状，看 `docs/` 根目录对应文档；不要按这里的方案动手。

| 文档 | 记录了什么 | 现状以此为准 |
|---|---|---|
| [RESEARCH.md](RESEARCH.md) | 2026-10-17 选型调研：为什么没直接用 DDCommando / m1ddc，而自研应用层 | `PLATFORM-SPLIT.md`、`ddc/` 代码 |
| [VERIFY-M1DDC.md](VERIFY-M1DDC.md) | 开发前的一次性验证：AOC Q2790PC 在 m1ddc 下的输入源切换可行性、读通道 -118 的由来 | `ddc/macos.py` 顶部注释、`TROUBLESHOOTING.md` §2 |
| [CLIPBOARD-SYNC-PLAN.md](CLIPBOARD-SYNC-PLAN.md) | 跨端剪贴板的方案与工作量评估（实现前） | `CLIPBOARD-SYNC-CONTRACT.md`（契约）+ `CLIPBOARD-SYNC-LOG.md`（验证） |
| [CLIPBOARD-SYNC-HANDOFF.md](CLIPBOARD-SYNC-HANDOFF.md) | 给 Windows 端 AI 的联调交接单（两端口径对齐） | `COORDINATION.md` + `ISSUES.md` |
| [WIN-TRAY-INVESTIGATION.md](WIN-TRAY-INVESTIGATION.md) | Windows 托盘图标不显示的完整排查（完整性级别 → Shell_NotifyIcon 被 UIPI 丢弃） | `TROUBLESHOOTING.md` §4.1 |
