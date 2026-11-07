# V3 实施任务清单（P0–P3）

> 2026-11-07 · 每条都有**可观察的验收标准**，没有就不算做完（遵守 `COORDINATION.md` 第 2 节）。
> 设计依据见 [`PROFILE-AND-ROLES-DESIGN.md`](PROFILE-AND-ROLES-DESIGN.md) 与 [`V3-PLAN.md`](V3-PLAN.md)。P4（键盘）暂缓。

---

## P0 · 清理 v1 —— ✅ 已完成（2026-11-07）

**为什么要先做**：V3 要改的 `ui/__init__.py`、`config.py`、两个 `switch_page.py`，和 v1 的清理面重叠。越晚删越痛。

### 现状（实测）

| 项 | 数量 |
|---|---|
| `ui/mac/` + `ui/win/` | **8,059 行**（各 9 个文件，完全对称） |
| 8 个兼容垫片 `ui/*.py` | 168 行（`platform_ui()` 动态转发到上面两棵树） |
| `cli.py` 的 `ui` 子命令 + `cmd_ui` | 约 80 行 |
| `tests/test_ui.py`（v1 冒烟，mac 上已整文件 skip） | 595 行 |
| 受影响测试 | 另 5 个文件、约 1,900 行需要改引用：`test_chrome_contract` / `test_focus_style` / `test_clipboard_page` / `test_remote_switch` / `test_source_state` |
| **合计** | **约 8,900 行删除 + 约 1,900 行测试迁移** |

### 建议：**删，不留副本**

理由：
1. **git 历史就是归档**，随时能 `git checkout <commit> -- src/dualswitch/ui/mac` 取回；
2. 留着会继续腐烂 —— 现在已经有 5 个文档引用（`docs/UI-DESIGN*.md`）指向 `archive/`，v1 代码里的注释全是失效路径；
3. **最严重的是**：`platform_ui()` 现在还指向 v1，所以 `test_remote_switch` / `test_source_state` / `test_clipboard_page` 在 mac 上**测的其实是废弃的 v1 代码**，v2 并没有被这些用例保护；
4. 打包时 8 个垫片要 `--hidden-import`，删掉能简化打包。

### 任务

| # | 做什么 | 验收 |
|---|---|---|
| **T0.1** | 删 `tests/test_ui.py`（纯 v1 冒烟） | `pytest tests/` 少 1 个文件，其余不变 |
| **T0.2** | 删 `ui/mac/`、`ui/win/` | `python -c "import dualswitch.ui.mac"` 报 ModuleNotFound |
| **T0.3** | 删 8 个垫片（`ui/{clipboard_page,focus_style,runner,settings,statusitem,theme,widgets,window}.py`）与 `platform_ui()` | `grep -r "platform_ui" src/` 无结果 |
| **T0.4** | 删 `cli.py` 的 `ui` 子命令与 `cmd_ui` | `dualswitch ui` 报「无效子命令」 |
| **T0.5** | 迁移 5 个测试的引用到 `ui.mac2` / `ui.win2` | `pytest` 全绿 |
| **T0.6** | `ui/__init__.py` 重写：只留 `available()`、`NAMES` 改成指向 v2 | 文档字符串里不再提 `ui/mac` |
| **T0.7** | 打包脚本去掉 8 个垫片的 hidden-import | `build.sh` 能出包，启动正常 |

验收命令：`pytest -q`（基线见下）+ `python -m dualswitch ui2` 能开窗。

**实际结果（2026-11-07）**：删掉 `ui/mac` + `ui/win`（8,059 行）、8 个垫片、`cmd_ui`、
`tests/test_ui.py` / `test_clipboard_page.py` / `test_focus_style.py`，并迁移 5 个测试与
`chrome/{mac,win}.py` 的 4 处垫片导入（改走 `mac2` / `win2`）。顺带发现 **chrome 运行时依赖
v1 theme**，一并修掉。测试从 490 passed / 25 skipped → **500 passed / 16 skipped**
（净 +10：chrome 契约测试从整文件跳过变成真跑）。
⚠️ **Windows 端 pull 后要改一处**：`packaging/windows/install_autostart.ps1` 的
`$mode = "ui"` 已指向被删除的 `ui` 子命令，要改成 `ui2`（Windows 树由对方维护，本次未动）。

> ⚠️ 删 v1 之前先跑一次全绿基线并记下通过数，删完对比，**不许用「少了一些测试所以全绿」糊过去**。

---

## P1 · 显示器识别 + profile + 动态端口（mac 3–4 天 / Windows 1–1.5 天）

**前置阻塞：✅ 已解除（2026-11-07 Windows 端实跑）** —— W1 通过：Windows 能拿到 `厂商:PID`，
跨端主键 `AOC:9987` 一致；但 WMI 的 `ProductCodeID` 是 **十六进制 ASCII 数组**，必须 `int(s,16)` 才是 EDID 数值，
且注册表 EDID 有历史堆积，只认 `Active` 实例。细节与「型号↔DDC 句柄」的 PnP 绑定键见
[`WINDOWS-VERIFY.md`](WINDOWS-VERIFY.md) §0.2 / §0.3。

| # | 任务 | 改哪些文件 | 验收 |
|---|---|---|---|
| **T1.1** | 读显示器身份 | 新增 `ddc/identity.py`（~260 行） | `python -m dualswitch monitor identity` 输出 `AOC:9987 slot=dispext0 name=U27N3G6R4B`；内建屏被过滤掉 |
| **T1.2** | Profile 结构与匹配 | 新增 `ddc/profiles.py`（~380 行） | `pytest tests/test_profiles.py`：命中/未命中/序列号细分/内置表 4 类用例 |
| **T1.3** | 配置结构迁移 | `config.py`（~220 行改） | ① 旧 `sources{windows,mac}` 能自动迁移成 `wiring{local,peer}` + default profile；② **保存后旧字段不丢**（沿用 2026-11-06 的 `_extra` 规则）；③ 未知字段保留 |
| **T1.4** | 精确挑 DDC 句柄 | `ddc/avservice.py`（~90 行改） | 拔插显示器后 `probe` 仍选对屏；内建屏永不被选中（`test_ddc_avservice` 补用例） |
| **T1.5** | 切屏走 wiring + ports | `app.py`（~180 行改） | 日志从「→ mac (18)」变成「→ usb_c (100)」；未标定屏走 default 且**不报错** |
| **T1.6** | 动态端口行 | `ui/mac2/switch_page.py`、`ui/win2/switch_page.py`（各 ~180 行） | 4 口屏显示 4 行、2 口屏显示 2 行；行高 58、主按钮 150×38 不变（截图比对） |
| **T1.7** | 测试 | 新增 ~450 行 | `pytest -q` 全绿 |

**P1 结束时你该能看到的**：换一块屏，**不用改 config.json**，切换页自动列出这块屏的端口和码，切屏正确。

---

## P2 · 标定引导 + 显示器配置页（mac 2–3 天 / Windows 1.5–2 天）—— ✅ 已完成（2026-11-07，v3 分支）

**实际结果**：新增 `ddc/calibration.py`（纯状态机）+ mac2/win2 各一套
`calibrate.py` / `monitor_page.py`；设置页「显示器」组第一行加「显示器配置 ›」；
切换页端口行与窗口高度会随 profile 变化重算。标定流程先让用户勾选端口
（因为显示器不通过 EDID/DDC 上报接口列表），再逐口切换；抓到值后自动切回起始口，
同码端口可点「已切到（读数不变）」手动确认，空口可跳过/重试。
测试：`tests/test_calibration.py` 8 条 + 两端 UI 用例；V3 全量 **538 passed / 16 skipped**。

| # | 任务 | 改哪些文件 | 验收 |
|---|---|---|---|
| **T2.1** | 标定向导（含**自动切回**） | 新增 `ui/mac2/calibrate.py`（~480 行） | ① 切到目标口、读到值后 **3 秒内自动把画面切回起始口**；② 起始口自动跳过；③ 空口（无信号）提示「跳过/重试」；④ 中断可恢复 |
| **T2.2** | 显示器配置子页 | 新增 `ui/mac2/monitor_page.py`（~380 行） | **570 高内不出现滚动条**（用 `tools/shots_v2.py` 出图核对）；已保存列表可展开/收起 |
| **T2.3** | 设置页加入口 | `ui/mac2/settings_page.py`（~120 行改） | 「显示器」分组最前多一行「显示器配置 ›」，**原有 12 行一条不动**；点进去是同窗口内切页 |
| **T2.4** | 未标定降级 | `app.py` / `profiles.py`（~120 行） | 未命中 profile 时用 17/18、界面提示「未标定」、**切屏仍可用** |
| **T2.5** | Windows 端对称实现 | `ui/win2/*`（~1,100 行） | 与 mac 端同款交互；Windows 也要能标定 |
| **T2.6** | 测试 | ~400 行 | 标定状态机的纯逻辑用例（不碰真显示器） |

**P2 结束时**：一块全新显示器，**跟着向导切 4 次输入源，30 秒自动完成标定**。

---

## P3 · 角色解耦 + 协议（mac 1.5–2 天 / Windows 1–1.5 天）

| # | 任务 | 改哪些文件 | 验收 |
|---|---|---|---|
| **T3.1** | `platform` / `role` 拆分 | `config.py`、`app.py`（~380 行改） | 双 Windows 下界面**不出现「Mac」字样**；两台 Windows 各自显示自己的机器名 |
| **T3.2** | 协议传端口名 | `sync/protocol.py`、`sync/engine.py`（~180 行） | `T_SWITCH` 载荷是 `{"port":"hdmi1"}`；对端解析不出时回 ACK 失败并本地兜底；**要改 `CLIPBOARD-SYNC-CONTRACT.md` §11** |
| **T3.3** | 文案与图标按 role | `tray.py`、`statusmenu.py`、`widgets.py` ×2（~200 行） | 托盘不再硬编码 `Mac (USB-C)`；图标按 `platform` 取 |
| **T3.4** | 剪贴板角标 | `ui/*/clipboard_page.py`、icons（~80 行） | 本机系统原色、对方紫底；**图标沿用 `icons.py`，不自绘** |
| **T3.5** | 测试 | ~350 行 | 双 Windows / Mac+Win / 双 Mac 三种组合的文案断言 |

---

## 全局验收（每个阶段结束都要跑）

```bash
.venv/bin/python -m pytest -q                  # 全绿，且通过数不低于基线
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/shots_v2.py    # 出图核对界面没走样
.venv/bin/python -m dualswitch probe           # DDC 仍可用
```

基线（2026-11-07）：`.venv` 下 **490 passed / 25 skipped**。

---

## 文档现状（P1–P3 够不够）

| 需要什么 | 有没有 | 在哪 |
|---|---|---|
| 为什么要做、实测依据 | ✅ | `MONITOR-PROFILE.md` |
| 数据结构与解耦模型 | ✅ | `PROFILE-AND-ROLES-DESIGN.md` |
| V2/V3 并存与配置隔离 | ✅ | `V3-PLAN.md` §1–§5 |
| 标定自动切回流程 | ✅ | `V3-PLAN.md` §6.6 |
| 页面设计与尺寸约束 | ✅ | `V3-PLAN.md` §6 + `design/v3/index-v3.html` |
| 工作量与工期 | ✅ | `V3-SCOPE.md` |
| **可执行任务 + 验收** | ✅ | **本文** |
| profile 的 JSON schema 逐字段定义 | ⚠️ 待补 | T1.2 开工时先定，写进 `profiles.py` 的 docstring |
| 旧配置迁移的逐字段规则表 | ⚠️ 待补 | T1.3 开工时先写测试再写实现 |
| 端口名枚举全集（除 hdmi/dp/usb_c 还有 vga/dvi？） | ⚠️ 待定 | T1.2 前定 |
| P3 协议帧的具体字段 | ⚠️ 待补 | T3.2 前先改契约 §11 |

> 结论：**P1 可以开工**（Windows 的 W1 已于 2026-11-07 回收并通过）。上面 4 个 ⚠️ 都是"开工第一天的第一件事"，不需要提前补文档。
> 另：W3 实测发现双 Windows 下「平台被当角色用」会撞车（`WINDOWS-VERIFY.md` §0.5），**P3 不只是改名**，
> 建议 T1.3 定 `wiring`/profile 结构时就把 `platform / role / port` 三者分开，别等 P3 返工。
