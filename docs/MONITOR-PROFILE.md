# 显示器身份识别 与 按型号配置接口码表

> 2026-11-07 立 · 换屏实测后写（AOC Q2790R3 → AOC U27N3G6R4B）。
> **结论先行：读型号可行且已经跑通原型；读「接口码表」不可行，只能标定。**
> 本文只记录调研结论与设计建议，**未改动任何生产代码**。

---

## 0. 一句话结论

- **显示器型号能读**，而且能读到比型号名更硬的东西：厂商 + 产品 ID + 序列号 + 出厂周。★已实测
- **型号和 DDC 句柄能精确绑定**（靠 IORegistry 里 `dispN` / `dispextN` 前缀），顺带解决「挑错屏」这个老坑。★已实测
- **接口码表（哪个口 = 哪个数）读不出来**，因为 `VCP 0x60` 的 `max` 字段在这块屏上是坏的、读成功率约 50%、切走后还会返回陈旧值。★已实测
- ⇒ 正确做法是 **「识别型号 → 查本地 profile → 没用过就引导标定一次 → 存下来」**。你说的思路成立，只差「码表靠标定而非靠读」这一条。

---

## 1. 型号能读吗？能，而且有四种途径

同一时刻（2026-11-07）对 `AOC U27N3G6R4B` 实测：

| 途径 | 拿到什么 | 评价 |
|---|---|---|
| `ioreg` → `AppleCLCD2` 的 `DisplayAttributes.ProductAttributes` | **AOC / U27N3G6R4B / PID 9987 / SN UTHN8HA014094 / 2022 第 32 周 / 3840px 原生宽** | ★**最优**，纯 IOKit 可读，字段最全 |
| `m1ddc display list` | `U27N3G6R4B` + CGDisplay UUID | 可用，但依赖外部二进制 |
| `QScreen.name()`（PySide6） | `U27N3G6R4B` | 可用；但 `manufacturer()` / `model()` / `serialNumber()` 在 macOS 上**全是空串** |
| `system_profiler SPDisplaysDataType` | `U27N3G6R4B` | 能用但慢（秒级），且要解析文本 |

`DisplayAttributes` 原文（外接屏）：

```
"ProductAttributes"={
  "ManufacturerID"="AOC", "LegacyManufacturerID"=1507,   # 1507 = ASCII "AOC" 的 PNP 编码
  "ProductName"="U27N3G6R4B",
  "ProductID"=9987, "SerialNumber"=14094,
  "AlphanumericSerialNumber"="UTHN8HA014094",
  "YearOfManufacture"=2022, "WeekOfManufacture"=32
},
"NativeFormatHorizontalPixels"=3840,        # 确认是 4K 面板
"MaxHorizontalImageSize"=60, "MaxVerticalImageSize"=34    # 60×34cm ≈ 27"
```

内建屏那条**没有 `ProductName` 字段**，只有 `ManufacturerID="00-10-fa"` 和一个超大 `ProductID`。
⇒ 「有没有 ProductName」天然就能把内建屏过滤掉，不用再靠 VCP 校验和去猜。

### 建议的 profile 主键

用 **`厂商:ProductID`**，即 `AOC:9987`。

理由：型号名可能带后缀变体（`U27N3G6R4B` vs `U27N3RB`），PID 是 EDID 里的数字，跨端解析稳定；序列号留给「同型号多台」的场景做细分，不作主键。

---

## 2. 型号 ↔ DDC 句柄：`dispN` / `dispextN` 是关联键

这是本次最实用的发现。IORegistry 里两条链是分开的：

| 屏 | 显示控制器 | EDID 属性 | DDC 节点 |
|---|---|---|---|
| 内建 | `disp0@7C000000` → `AppleCLCD2` | 无 ProductName | `disp0:dcpav-service-epic:0` → `DCPAVServiceProxy` (Location=Embedded) |
| 外接 | `dispext0@8000000` → `AppleCLCD2` | **AOC / U27N3G6R4B** | `dispext0:dcpav-service-epic:0` → `DCPAVServiceProxy` (Location=External) |

两边名字里都带同一个前缀 `dispN` / `dispextN`，**这就是把「型号」和「IOAVService 句柄」绑在一起的键**。

原型已验证可行（纯 ctypes + 一次 `ioreg`）：

```
DDC proxy:  slot=disp0      rid=0x100096103  → 内建屏
            slot=dispext0   rid=0x100098d7d  → 外接屏
显示器型号:  {name: None,            mfg: '00-10-fa', pid: 55199728219201}
            {name: 'U27N3G6R4B',   mfg: 'AOC',      pid: 9987, serial: 'UTHN8HA014094'}
⇒ 外接屏 = 有 ProductName 的那条，其 slot 对应的 proxy 就是正确句柄
```

### 这条顺带解决的历史坑

`ddc/avservice.py` 的 `_external_locked()` 现在靠「VCP 回复能不能过校验和 + RC + 回显」来挑句柄，注释里记了两个坑（内建屏 rc=0 也能读、EDID 头区分不了内外接）。
用 `dispextN` 前缀 + `ProductName` 判断，**这一步可以直接变成确定性的**，不再需要试错。

---

## 3. 接口码表：读不出来，这是硬约束

`VCP 0x60`（Input Source）的读回在 `AOC U27N3G6R4B` 上有三个问题，都在 2026-11-07 实测：

| 现象 | 证据 |
|---|---|
| **`max` 字段是坏的** | 恒定报 `max=14`，但实际读到过 `17`、`100`；对照的 `0x10` 亮度 `max=100`、`0x12` 对比度 `max=100` 都正常 |
| **读成功率约 50%** | 30 次连读 15 次成功；同一通道连续两次 I2C 事务必然吞掉一次 |
| **切走后返回陈旧值** | 画面不在本机时，旧句柄一直回最后一次的快照（曾卡在 `15` 不动），必须重建句柄才拿到新值 |

> 2026-11-07 Windows 端更正（`WINDOWS-VERIFY.md` §0.4 / §0.6）：`max=14` **不是这块新屏的特征** ——
> 旧屏 `AOC Q2790PC` 早在 2026-10-17 就报 `cur=17, max=14`（`PROGRESS.md:225`），
> Windows 端今天在旧屏上 `probe_input()` 同样是 `(17, 14)`。这是 AOC/dxva2 的通病。
> 「读成功率 ~50%」也是 mac/IOAVService 特有：Windows dxva2 同进程连读 **10/10 成功**。
> `readback=unreliable` 的结论不变，但**理由和平台差异要分开写**。

MCCS 规范也没有「列出全部受支持取值」的命令，所以**没有任何办法靠读来枚举码表**。

⇒ 码表只能 **标定**：让用户在 OSD 里逐个口切一遍，后台抓读，把「口 → 值」存进 profile。
2026-11-07 就是这么把这块屏的表测出来的（见 §5），全程零风险、约 30 秒。

---

## 4. 建议架构

### 4.1 配置结构：把「端口码」和「主机接线」拆开

现在的 `sources = {windows: 17, mac: 18}` 把两件事混在一起了：**端口码**（跟屏走）和**哪台主机接哪个口**（跟接线走）。换屏时前者变、后者不变，但现在的结构逼着一起改。

建议拆成三层：

```jsonc
{
  "monitors": {
    "AOC:9987": {                        // ← 主键：厂商:ProductID
      "name": "U27N3G6R4B",
      "vcp": 96,                          // 0x60
      "ports": {                          // ← 跟屏走，标定得来
        "hdmi1": 17, "hdmi2": 17, "dp": 15, "usb_c": 100
      },
      "readback": "unreliable",           // 读回是否可信：full / unreliable / none
      "notes": "max 恒报 14，不可用于校验；读成功率约 50%"
    },
    "AOC:Q2790R3": { "name": "Q2790R3", "vcp": 96,
                     "ports": { "hdmi": 17, "usb_c": 18 }, "readback": "full" }
  },
  "wiring": { "windows": "hdmi1", "mac": "usb_c" },   // ← 跟接线走，人配
  "active_monitor": "auto"                             // auto = 按当前识别的屏
}
```

好处：
- 换屏 → 只换 profile，`wiring` 不动；
- 换接线（比如 Mac 改接 HDMI2）→ 只改 `wiring`，码表不动；
- 别人的屏、临时屏 → 未命中 profile 时走保守默认（沿用 `17/18`）并提示标定。

### 4.2 匹配与降级流程

```
启动 / 屏插拔 / 切到切换页
  → 读身份（mfg:pid）        ← §1，一次 ioreg，~ms 级
  → 命中 profile？──是──→ 用它的 ports
        │
        否
        ↓
    用 default profile（17/18）+ 界面提示「这块屏没标定过」
        ↓
    用户点「标定」→ 逐口切 OSD 抓读 → 写回 profile
```

降级要**绝不阻断切屏**：宁可发一个可能错的码，也不能让用户卡在「不认识这块屏」上动不了。

### 4.3 标定向导（把本次人工流程产品化）

本次实测的流程就是雏形，可直接做成向导：

1. 提示「请在显示器菜单里依次切到 HDMI1 → HDMI2 → DP → USB-C，每口停 5 秒」；
2. 后台每 ~0.5s 重建句柄读 `0x60`（必须重建，否则会读到 §3 的陈旧值）；
3. 值变化即记一条 `(时间, 值)`，切换顺序由用户按提示走，值序列即码表；
4. 结束时展示「HDMI1=17, DP=15, USB-C=100」，让用户确认后写 profile。

注意：**连续两次相同的值要合并**（本次 HDMI1/HDMI2 都是 17，合法），不能当成「漏了一个口」。

### 4.4 跨端：协议里应该传「端口名」而不是数值

`T_SWITCH=0x30` 现在传的是 VCP 数值（17/18）。一旦两端 profile 不一致（比如一端缓存了旧屏的表），就会发错码且互相都察觉不到。

建议协议改传**端口语义**（`hdmi1` / `dp` / `usb_c`），由各端用自己的 profile 解析成数值；解析不出来就回 ACK 失败、走本地兜底。这样「换屏」这件事两端各自消化，不必同步码表。

> ⚠️ 这是契约变更，要动 `docs/CLIPBOARD-SYNC-CONTRACT.md` §11 和 Windows 端，需双方确认后再做。

---

## 5. 本次实测数据（2026-11-07，供 profile 预置）

`AOC U27N3G6R4B`（PID 9987，SN UTHN8HA014094）：

| 端口 | `0x60` 值 | 置信度 |
|---|---|---|
| HDMI 1 | **17** | 实测（切换时抓到） |
| HDMI 2 | **17** | 推断（切两个 HDMI 口只出现一次跳变，说明同码） |
| DP | **15** | 实测（15 = MCCS 标准 DisplayPort） |
| USB-C | **18** | 实测（2026-11-08 复测：连读 20+ 次全为 18；原文记的 100 作废） |

对照 `AOC Q2790R3`（旧屏，2026-11-06 前）：`HDMI=17`、`USB-C=18`，且 `readback` 一次即可确认。

> **2026-11-08 更正：`18` 在这块屏上是有效的，就是 USB-C。**
> 原文「注意 `18` 在这块新屏上无效」是**错的** —— 那天读到的 `100` 是坏帧/瞬时值
> （`max` 字段还恒报 14），照它填内置表会让标定向导直接卡死：起始读数 `18`
> 在码表里找不到对应口 → 起始口丢失 → 不能自动切回，而且每个读到 `18` 的口
> 都被判成 `same-as-start`，表现为「读到 18，一直不稳定」。
>
> 同一天扫了一遍可写入的输入码：**只有 15 / 17 / 18 被接受**，`19 / 20 / 21 / 27`
> 写进去再读回还是原值（被拒）。所以 HDMI1 与 HDMI2 只能是同码 `17`
> —— 这两个口靠读数**无法区分**，标定时必须靠用户点「已切到」确认。
>
> 另一个坑：连续切几次输入之后，`0x60` 会短暂报 **50**（约数秒），画面仍在 Mac 上，
> 发一条 `set input 18` 就回到 `18`。这是瞬时状态，不是第三个口。

---

## 6. 未验证 / 风险

| 项 | 状态 | 影响 |
|---|---|---|
| Windows 端怎么读型号 | ✅ **已验证**（2026-11-07，`WINDOWS-VERIFY.md` §0.2） | `WmiMonitorID` 给 `AOC` + PID 的**十六进制 ASCII 串**（`int(s,16)` 才等于 EDID 数值）；注册表 `EDID` 值可直解十进制。**同一块屏两端主键一致（AOC:9987）**。注意 WMI 只列 Active 屏、注册表有历史堆积 |
| 同型号不同批次码表是否相同 | 未知 | 若不同，主键要加固件/出厂周细分，或标定结果优先于预置表 |
| 一块屏同时接受多个码 | 未知 | profile 的 `ports` 建议支持「一个口多个码」的 fallback 列表 |
| `dispextN` 在多外接屏时是否稳定 | 未验证 | 接了第二块外接屏时需复测 slot 是否仍唯一对应 |
| 100 这类非标准码的含义 | 未知 | 只能靠标定确认，不能反推 |

---

## 7. 对现有代码的影响点（本次不改，仅记录）

- `ddc/avservice.py`：`_external_locked()` 可换成 `dispextN` + `ProductName` 判定（§2）。
- `config.py`：`sources` 结构演进为 profile + wiring（§4.1），要做**向后兼容读取**（旧配置的 `sources` 自动映射成一个 default profile）。参照 2026-11-06 的字段保留规则，不能因为不认识就退回默认值。
- `ui/mac2/switch_page.py`、`ui/win2/switch_page.py`：`PORT = {"windows": "HDMI", "mac": "USB-C"}` 和「本机发送 17 / 18」的文案是写死的，改成按当前 profile 生成。
- `sync`：`T_SWITCH` 载荷若要改传端口名，属契约变更（§4.4）。

---

## 附：原型脚本

型号 + 句柄关联的可行原型（纯 `ctypes` IOKit + 一次 `ioreg`，约 90 行）已验证输出：

```
slot=disp0     rid=0x100096103  → 内建屏（无 ProductName）
slot=dispext0  rid=0x100098d7d  → AOC U27N3G6R4B / 9987 / UTHN8HA014094
```

落地时建议：把「读身份」做成**启动时一次 + 屏插拔事件触发**，不要放进轮询循环（macOS 上 DDC 承受不了持续 I2C 流量，见 `SCREEN-OWNERSHIP.md`）。
