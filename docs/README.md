# 文档索引

> 这里只放**现状**。已经作废或被取代的移到 [`archive/`](archive/README.md)，那边记录"当时为什么这么做"，不是现状。

## 架构与协作

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| [PLATFORM-SPLIT.md](PLATFORM-SPLIT.md) | 平台差异如何分层：哪些进 `ui/chrome/{win,mac}.py`，哪些必须共享 | 改动涉及平台分支时 |
| [COORDINATION.md](COORDINATION.md) | 双端协作规则：同一处代码只有一个作者，只协调接口 | 两端并行开发时 |

## 核心机制

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| [SCREEN-OWNERSHIP.md](SCREEN-OWNERSHIP.md) | **画面归属判定**：为什么只认 VCP 0x60 直读、幻影枚举为什么不可信 | 改归属判断、看到「画面回弹」时 |
| [REMOTE-SWITCH.md](REMOTE-SWITCH.md) | 对端代发：本机 DDC 打不到时请对端发 Set | 改远端链路时 |
| [CLIPBOARD-SYNC-CONTRACT.md](CLIPBOARD-SYNC-CONTRACT.md) | 跨端剪贴板的接口契约（三件事共用一条 TCP：代发 / 配对 / 剪贴板） | 动 `sync/` 或 `clipboard/` 时 |

## 功能

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| [FEASIBILITY-LOGI-EASYSWITCH.md](FEASIBILITY-LOGI-EASYSWITCH.md) | **罗技鼠标联动**：HID++ `CHANGE_HOST` 实测数据、报文格式、物理约束、异常矩阵 | 用/改鼠标联动、排查不生效时 |

## 界面

| 文档 | 内容 |
|---|---|
| [UI-WINDOWS2-DESIGN.md](UI-WINDOWS2-DESIGN.md) | Windows v2 界面规格（尺寸、颜色、交互、实现落点） |
| [UI-MAC2-IMPLEMENTATION.md](UI-MAC2-IMPLEMENTATION.md) | macOS v2 实现说明：视觉/图标/交互落点，哪里对应原型 |

界面规格的**真源**是 `design/redesign/INTERACTIONS-v2.md` 与原型 `design/redesign/index-v2.html`，
上面两份是它们的实现侧解读。

## 排障与台账

| 文档 | 内容 |
|---|---|
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 按症状查：托盘图标不显示、DDC 读不到、热键不响应、鼠标联动不生效… |
| [ISSUES.md](ISSUES.md) | 任务台账（未关闭的问题） |

## 历史

[docs/archive/](archive/README.md) —— 已作废/已完成的记录，**不要按里面的方案动手**。
包括：v1 界面设计、输入源回弹调查、对端代发旧验收流程、剪贴板验证记录等。
