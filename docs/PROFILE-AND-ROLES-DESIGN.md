# 显示器配置文件 · 角色解耦 · 改动清单（设计稿）

> 2026-11-07 立 · 承接 [`MONITOR-PROFILE.md`](MONITOR-PROFILE.md)（那边是实测事实，这边是设计）。
> **状态：纯设计，未改动任何代码。** 页面部分见 §6。
> 一句话：把「平台（Mac/Windows）」和「角色（本机/对端）」拆开，把「端口码」从配置里挪进「显示器 profile」。

---

## 1. 先说结论

| 你的问题 | 答案 |
|---|---|
| 需要做显示器配置文件吗 | **需要**，而且是这个产品能不能给别人用的分水岭 |
| 新显示器要人工调试出接口码吗 | **是，必须**。码表读不出来（见 MONITOR-PROFILE §3），只能标定 |
| 还要提供「各接口接的是哪台系统」吗 | **是**，这叫 `wiring`（接线表），和端口码是两件事 |
| 两个 Windows 相连有影响吗 | **能跑，但语义全错**。功能上凑合可用，所有文案/图标/设置项都会把对方显示成「Mac」 |
| 页面要改吗 | **要**。端口行从「固定 2 行」变成「按屏的端口数动态生成」，设置页删掉两个数字框 |

---

## 2. 双 Windows 场景：不是跑不了，是「全都叫错了名字」

现在的模型是 `host ∈ {windows, mac}`，**这一个变量同时承担了三个含义**：

1. **平台**（决定读哪套 DDC 后端、图标画苹果还是微软）
2. **角色**（本机 / 对端）
3. **端口**（`sources[host]` 取的就是 VCP 值）

代码里随处可见这个耦合：

```python
# config.py:245
def effective_host(self):
    if self.this_host in ("windows", "mac"): return self.this_host
    return "windows" if sys.platform == "win32" else "mac"

# config.py:251
def other_host(self):
    return "mac" if self.effective_host() == "windows" else "windows"

# switch_page.py:245
for host in ("windows", "mac"):          # 固定两行
    ActionRow(host, int(cfg.sources[host]), is_local=(host == here))

# tray.py:137
other_label = "Mac (USB-C)" if other == "mac" else "Windows (HDMI)"
```

### 逐条判断双 Windows 的实际后果

| 环节 | 双 Windows 下的实际行为 | 判定 |
|---|---|---|
| 切屏本身 | `sources = {windows:17, mac:100}` 只是「两个口的值」，`target_value(target)` 照样取到 | ✅ **能工作** |
| 本机/对端判定 | Win 上 `sys.platform=="win32"` → 本机恒为 `windows` 行，对方恒为 `mac` 行。**恰好等价于角色** | ✅ 行为对，语义错 |
| 归属判定 | `host_of_value()` 反查，返回值是 `windows`/`mac` 两个「角色代号」 | ✅ 逻辑对 |
| 界面文案 | 对方那台 Windows 显示成 **「Mac」**，按钮写「请对端发送」旁边标注 Mac | ❌ **错** |
| 图标 | `_kind = "windows" if host=="windows" else "apple"` → 对方画**苹果 logo** | ❌ **错** |
| 托盘菜单 | `Mac (USB-C)` / `Windows (HDMI)` 硬编码 | ❌ **错** |
| 设置页 | 两个数字框叫「Windows 输入源 / Mac 输入源」 | ❌ **错**，双 Win 用户无法理解 |
| 配对页 | 用 `peer.plat`（hello 里上报的真实平台）→ 两个 Windows logo | ✅ **对**（这里反而没踩坑） |
| `display_power._other` | `"windows" if local_host=="mac" else "mac"` → 本机 Win 时 other 恒为 `mac` | ⚠️ 语义错，行为等价 |

**结论：双 Windows 不需要「新功能」才能跑，但需要「改名 + 拆概念」才能用得不别扭。** 而这个拆分恰好就是 profile 方案本来就需要的结构，两件事一起做最省。

> 补充：`sync/engine.py:292` 上报的 `platform` 用的是 `host_label`，双 Windows 时两台都正确上报 `windows`——**这里本来就该用平台，碰巧没错**。说明「平台」这个概念在协议层是存在的，只是配置层把它和角色混了。

---

## 3. 目标模型：三个正交的维度

```
平台 platform ∈ {macos, windows}     ← 决定 DDC 后端、图标
角色 role     ∈ {local, peer}        ← 本机 / 对端
端口 port     ∈ {hdmi1, hdmi2, dp, usb_c, …}   ← 跟显示器走
```

三者关系：

```
显示器 profile:  port → VCP 数值      （随屏变，标定得来）
接线 wiring:     role → port          （随人变，手工选）
```

于是：

```python
vcp_value  = profile.ports[wiring[target_role]]     # 要发的数
target_host = peer_device if target_role=="peer" else local_device
```

**平台不再参与切屏决策**，只用于选后端和画图标。双 Windows、Mac+Mac、Mac+Windows 三种组合走同一套逻辑。

### 主键选 `mfgID:productCode`，两端天然一致

mac 侧拿到的 `LegacyManufacturerID=1507`、`ProductID=9987` 就是 **EDID 里的 manufacturer ID 和 product code**。Windows 侧从注册表 `...\Device Parameters\EDID` 解出来的也是这两个字段。

⇒ 主键 `AOC:9987` 在两端**不需要任何映射表**就能对上。这是选它而不是选型号名的核心理由。
（型号名 `U27N3G6R4B` 只作为展示用，不作主键——同型号存在命名变体。）

---

## 4. 新增功能

| # | 功能 | 说明 | 优先级 |
|---|---|---|---|
| N1 | **显示器身份识别** | 启动 + 屏插拔时读 `mfg:pid` + 型号 + 序列号 + 所属 slot。macOS 已跑通原型（`AppleCLCD2.DisplayAttributes` + `dispN/dispextN` 关联） | P1 |
| N2 | **Profile 库** | `内置表`（自己用过的屏）+ `用户表`（标定得来）。未命中走 default | P1 |
| N3 | **端口语义命名** | 用 `hdmi1 / hdmi2 / dp / usb_c` 替代数字含义，UI 才能自解释 | P1 |
| N4 | **接线表 wiring** | role → port 的映射，设置页可选「这台接哪个口 / 对方接哪个口」 | P1 |
| N5 | **标定向导** | 引导逐口切 OSD，后台抓读，自动填表存 profile（本次人工流程的产品化） | P2 |
| N6 | **未标定降级** | 未命中 profile 时用保守默认（17/18）+ 界面提示「这块屏没标定过」，**绝不阻断切屏** | P2 |
| N7 | **角色/平台解耦** | 双 Windows、双 Mac 支持（§2、§3） | P3 |
| N8 | **协议传端口名** | `T_SWITCH` 载荷从数值改 `port` 名，两端各自解析，避免码表不一致 | P3 |

---

## 5. 改动清单（文件级）

### 5.1 新增文件

| 文件 | 内容 |
|---|---|
| `src/dualswitch/ddc/identity.py` | `read_displays()`：返回 `[(slot, mfg, product_code, name, serial)]`；`handle_for_slot()`。纯 IOKit，启动时一次，不进轮询 |
| `src/dualswitch/ddc/profiles.py` | profile 读写、内置表、匹配、迁移逻辑 |
| `tests/test_ddc_identity_macos.py` | 平台门禁 + mock IORegistry 数据 |
| `tests/test_profiles.py` | 匹配/迁移/降级用例（共享用例，不整文件跳过） |

### 5.2 修改文件

| 文件 | 改什么 | 风险 |
|---|---|---|
| `config.py` | 加 `MonitorProfile`；`sources` → `profile.ports` + `wiring`；`effective_host()/other_host()` 降级为**仅用于 UI 文案**，切屏路径不再用；旧配置自动迁移（`sources{windows,mac}` + `this_host` → `wiring{local,peer}`） | **高**：配置结构变更，必须按 2026-11-06 的字段保留规则处理，不能「不认识就退回默认」 |
| `ddc/avservice.py` | `_external_locked()` 从「VCP 校验和试错」改为 `dispextN` + `ProductName` 判定；新增按 slot 取句柄 | 中，但有回归测试兜底 |
| `ddc/macos.py` | `list_monitors()` 带 identity；`get_input()` 增加「连续两读取稳定值」策略（新屏读成功率 50%） | 中 |
| `app.py` | `host_of_value()` → `port_of_value()`；`_execute()` 用 `wiring → profile.ports` 取码；归属判定返回 port 而非 host | 高（核心路径） |
| `display_power.py` | `_other` 不再依赖平台推断（§2 最后一条） | 中 |
| `tray.py` / `ui/*/statusmenu.py` | 文案从 `Mac (USB-C)` 改为「对端机器名 · 端口名」，图标按 **platform** | 低 |
| `ui/mac2/switch_page.py`、`ui/win2/switch_page.py` | 端口行动态生成（§6）；显示器卡片加型号与「标定」入口 | 中，两侧必须对称 |
| `ui/mac2/settings_page.py`、`ui/win2/settings_page.py` | 删「Windows/Mac 输入源」两个数字框，换 profile 管理区（§6.3） | 中 |
| `ui/*/widgets.py` | `_kind` 按 platform 而非 host 名 | 低 |
| `sync/engine.py` + `sync/protocol.py` | （P3）`T_SWITCH` 载荷改 port 名，需同步 `CLIPBOARD-SYNC-CONTRACT.md` §11 | **契约变更，需双端确认** |

### 5.3 不需要改的

- `pairing_page.py`：已经用 `peer.plat` 画图标，双 Windows 天然正确。
- 剪贴板全链路：与显示器无关。
- 鼠标联动（HID++）：与显示器无关。

---

## 6. 页面设计

### 6.1 切换页：从「固定两行」变成「按屏的端口数」

现状是死板两行：`17 · HDMI` / `18 · USB-C`，各带「本机发送」「请对端发送」。
问题：新屏有 4 个口（HDMI1/HDMI2/DP/USB-C），两行装不下，且 `18` 已经不对了。

改后结构（自上而下）：

1. **显示器卡片**（沿用现有 hero 区）
   - 主行：`AOC U27N3G6R4B` · 4K · 27"
   - 副行：`USB-C ← 这台` · `HDMI 1 ← DESKTOP-V6DVLD8`
   - 右侧：`● 已标定` 或 `⚠ 未标定 · 去标定`（可点）
2. **端口行**（数量 = profile 的端口数，最多 4）
   - 每行：`端口名` + `码值`（小字灰） + `[本机发送]` + `[请对端发送]`
   - **带「这台」「对方」角标**，标出哪一端当前接在这个口
   - 当前画面所在的口整行高亮（沿用 `is_local` 的视觉）
3. **底部状态行**：沿用现有「上次动作」+ note

关键交互变化：
- 行数不再固定 → 布局必须能容纳 2/3/4 行，且**不同行高一致**（沿用 `setFixedSize` 的教训）
- 「这台/对方」角标取代原来的 `windows/mac` 语义 → 双 Windows 也不再显示 "Mac"
- 未标定时：端口行**仍然可点**（发默认值），但顶部挂一条黄色提示条

### 6.2 标定向导（新页面/弹窗）

三步，每步一屏：

1. **准备**：显示当前识别的屏（型号+序列号），提示「请准备好在显示器菜单里切换输入源」
2. **逐个标定**：进度条 `1/4`，提示「现在切到 **HDMI 1**，停 5 秒」，右侧实时显示抓到的值（`17` / `—`），抓到后自动进下一步
3. **确认**：表格列出 `HDMI1=17, HDMI2=17, DP=15, USB-C=100`，可手工改，`保存` 写入 profile

### 6.3 设置页：profile 管理区替代两个数字框

现状：`Windows 输入源 [17]` / `Mac 输入源 [18]`（双 Win 用户看不懂）。

改后：

- **当前显示器**：`AOC U27N3G6R4B`（只读）+ `重新识别`
- **接线**：`这台接 [USB-C ▾]` / `对方接 [HDMI 1 ▾]` ← 下拉来自 profile 的端口
- **端口码表**：`HDMI 1 = 17` / `HDMI 2 = 17` / `DP = 15` / `USB-C = 100`，带 `标定` 按钮
- **其它已保存的显示器**：列表（AOC Q2790R3 …），可删除

---

## 7. 分期

| 期 | 范围 | 可独立验证 |
|---|---|---|
| **P1** | identity + profile 结构 + 接线表 + 切换页动态端口（保持 `sources` 兼容读取） | 换屏后不用改配置就能切对 |
| **P2** | 标定向导 + 未标定降级 + 设置页改版 | 一块全新屏 30 秒内可标定 |
| **P3** | role/platform 解耦（双 Windows）+ `T_SWITCH` 传端口名 | 两台 Windows 相连，界面不再出现 "Mac" |

P1 不动协议、不动 Windows 端共享层，**mac 端可独立交付**；P3 才需要双端一起改。

---

## 8. 风险

| 风险 | 处理 |
|---|---|
| 配置结构变更导致用户配置丢失 | 严格迁移 + `.bak` + `_degraded` 不覆盖原件（2026-11-06 教训） |
| 同型号不同批次码表不同 | 标定结果**优先于**内置表；主键可加 `#week` 细分 |
| Windows 端读型号方式未验证 | P1 前必须先确认 Windows 能拿到 `mfg:pid`，否则主键不成立（这是**唯一的前置阻塞项**） |
| 端口行数变化引发布局回归 | 沿用 v2 的教训：`setFixedSize`、`wordWrap`、实测字体宽度 |
| 两块外接屏时 `dispextN` 是否稳定 | 未验证，P1 前需补测（接第二块屏跑一次原型） |
