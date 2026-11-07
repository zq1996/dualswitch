# Windows 端验证任务（换屏 / profile 方案前置）—— ✅ 已回收

> 2026-11-07 · 给 Windows 端。**这一轮不改代码，只要数据。**
> 背景：mac 端换了显示器（AOC Q2790R3 → AOC U27N3G6R4B），发现 `VCP 0x60` 的接口码跟屏走、而且读不出来只能标定。
> 想做成「按屏配置」之前，**必须先确认 Windows 端能不能拿到同一套显示器身份**。拿不到，方案就得换。
>
> **状态：2026-11-07 16:10 Windows 端实跑完毕，W1 / W2 / W3 全部有结论。**
> **W1 阻塞解除 ⇒ P1 可以开工**，但原任务里对 `ProductCodeID` 的假设是错的，且 W3 的结论比预期严重（见 §0.6）。
> 本轮未动任何生产 Python 代码；只按 P0 留下的 Windows 端 TODO 修了两个 `.ps1`（`ui` → `ui2`，见 §0.7）。

---

## 0. 回报（Windows 端，2026-11-07 16:10 实跑）

```
W1: Manufacturer=AOC  ProductCode=10128 (=0x2790；脚本 A 原样是 ASCII "2790")
    Name=Q2790R3  Serial=5363  Active=True  2019 W43        （脚本 A，当前活动屏）
    脚本 B 是否一致：换算后一致（B=10128 十进制；A="2790" 是十六进制字符串，int(s,16)=10128）。
                     但两份脚本的「原值」不相等，原任务 §W1「应当给出相同 ProductCode」不成立，见 §0.2。
    另：注册表里存在 mac 端那块新屏 AOC2703 = AOC / PID 9987 / SN 14094，
        与 mac 报的 ProductID=9987、SerialNumber=14094、SN=UTHN8HA014094 三项吻合
        ⇒ 两端都能派生出主键 AOC:9987 ✅ W1 意图成立
W2: probe 结果=成功，「当前输入源: VCP 0x60 = 17（读数仅供参考）」（冷启动首次读偶发失败，见 §0.4）
    读到 0x60=17，当前接的口=HDMI（本机 Windows 路，sources.windows=17）
    10 次成功率=10/10（同进程连读，全为 17，57–69ms）；独立进程冷启动 13/14 成功
W3: 目标行文案=「本机输入口」/「Mac 的输入口」，PORT windows=HDMI、mac=USB-C
    图标=苹果 logo（chrome/win.py 把 PEER_LOGO 写死成 "apple"）
    托盘=「Mac (USB-C)」/「Windows (HDMI)」  设置页=「Windows [17]  Mac [18]」
    配对页=两台 Windows 都显示 Windows logo + "Windows" 标签（取 hello.plat，这里是对的）
    ⚠️ 但「功能可用、只是文案/图标错」不成立：平台被当成角色用，双 Windows 会撞车，见 §0.5 / §0.6
```

### 0.1 环境（都是本机只读实测，没有切换画面、没有写 DDC）

| 项 | 值 |
|---|---|
| 系统 / Python | Windows 10 / 3.13.14（`doctor` 报），dualswitch 1.0.0 |
| DDC 后端 | `dxva2`，`supports_ddc_detect = True` |
| 当前活动屏 | `DISPLAY\AOC2790\7&3ae6a500&1&UID524_0`（`WmiMonitorID.Active=True`） |
| 枚举到的设备 | `MONITOR\AOC2790\{4d36e96e-e325-11ce-bfc1-08002be10318}\0003` / `\\.\DISPLAY1` |
| 配置 | `sources={windows:17, mac:18}`、`this_host=auto`、`switch_mode=other` |
| 物理显示器数 | 1（注册表历史里还有 5 条，见 §0.2） |

### 0.2 W1 明细：身份能读，但字段口径要改

**脚本 A（`Get-CimInstance WmiMonitorID`）原始输出**（`uint16[]` 都要去掉 0 再 `[char]` 拼）：

```
InstanceName      : DISPLAY\AOC2790\7&3ae6a500&1&UID524_0
ManufacturerName  : 65,79,67,0,...        → "AOC"
ProductCodeID     : 50,55,57,48,0,...     → "2790"     ← 十六进制字符串，不是十进制数
UserFriendlyName  : 81,50,55,57,48,82,51 → "Q2790R3"
SerialNumberID    : 53,51,54,51,0,...     → "5363"
Active/Year/Week  : True / 2019 / 43
```

`Get-CimClass` 核对：`ManufacturerName` / `ProductCodeID` / `SerialNumberID` / `UserFriendlyName`
**四个字段的 CimType 全是 `UInt16Array`**；[MS Learn · WmiMonitorID](https://learn.microsoft.com/en-us/windows/win32/wmicoreprov/wmimonitorid)
的 MOF 定义同样是 `uint16 ...[]`。所以 `ProductCode=9987` 这种十进制数**不会**从 WMI 直接出来，
Windows 侧必须 `int("".join(chr(c) for c in arr if c), 16)` 才得到 EDID 数值：
`"2790" → 0x2790 → 10128`。

**脚本 B（注册表 `...\Enum\DISPLAY\*\Device Parameters\EDID` 解 128 字节）**：

| 注册表实例 | 厂商 | ProductCode | hex | Serial | EDID 键最后写入(UTC) | 备注 |
|---|---|---|---|---|---|---|
| `AOC2790\7&3ae6a500&1&UID524` | AOC | 10128 | `0x2790` | 5363 | 2025-07-15 | **当前活动屏**，与脚本 A 同一块（换算后一致） |
| `AOC2790\1&8713bca&0&UID0` | AOC | 10128 | `0x2790` | 5363 | 2025-05-18 | 同一块屏的旧实例 |
| `AOC2790\5&3984e4d2&0&UID260` | AOC | 10128 | `0x2790` | 5363 | 2025-05-18 | 同上 |
| `AOC2703\7&3ae6a500&0&UID524` | AOC | **9987** | `0x2703` | **14094** | 2025-06-05 | **mac 端新屏 AOC U27N3G6R4B**（三项全对上） |
| `DELD0E6\1&28a6823a&8&UID256` | DEL | 53478 | `0xD0E6` | 810179157 | 2026-10-16 | 历史屏 |
| `DELD0E6\1&28a6823a&9&UID256` | DEL | 53478 | `0xD0E6` | 810179157 | 2026-11-03 | 历史屏 |

EDID 头字节实测 `05 E3 90 27`（`AOC2790`）/ `05 E3 03 27`（`AOC2703`）：
`0x05E3` 的 5-bit 编码 = `A`、`O`、`C`；`0x10/0x11` 小端即 product code。
**同一块屏在两端给出的 `厂商:PID` 完全相同（`AOC:9987`）**，主键方案成立。

四条落地注意（都是这次实测才发现的）：

1. **WMI 的 PID 是十六进制 ASCII 串**，直接 `int(s)` 会得到 `2790`（错）而不是 `10128`；
   跨端主键必须统一成十进制，否则必然对不上。
2. **WMI 只列 `Active=True` 的屏（本机 1 条），注册表是历史堆积（本机 6 条，含 3 条同一屏的重复实例 + 2 台已拔掉的屏）**。
   `P1` 的「当前屏」判定必须以活动实例为准；注册表只作回退/补充，且要按 PnP 实例 ID 精确取，**不能全量遍历当当前屏**。
3. 备用脚本 B 的 `Serial` 用 `(edid[15]<<24)|...` 在 PowerShell 5.1 是 **有符号 Int32**，
   遇到高位序列号的屏会出负数；生产代码里请用无符号解析。
4. 备用脚本 B 没有解 EDID 的 display descriptor（`0x36` 起）拿型号名，而 A 有 `UserFriendlyName`；
   建议 `ddc/identity.py` 统一输出 `(mfg, pid_dec, serial, name, year, week, pnp_instance)`。

### 0.3 附赠：Windows 也有 `dispN` 那种「型号 ↔ DDC 句柄」绑定键

`MONITOR-PROFILE.md` §2 说 mac 靠 `dispextN` 把 IORegistry 身份和 IOAVService 句柄绑起来。
Windows 侧的对应键是 `EnumDisplayDevicesW(..., EDD_GET_DEVICE_INTERFACE_NAME=0x1)` 给出的 PnP 实例路径，
本机实测（同一个 `\\.\DISPLAY1`）：

```
flags=0  → MONITOR\AOC2790\{4d36e96e-e325-11ce-bfc1-08002be10318}\0003   ← 现有 ddc/windows.py 用的
flags=1  → \\?\DISPLAY#AOC2790#7&3ae6a500&1&UID524#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}
```

中间的 `7&3ae6a500&1&UID524` 与 WMI `InstanceName`、注册表 EDID 键名**逐字相同**。
⇒ 可以确定性走 `HMONITOR → 设备名 → PnP 实例 → EDID 身份 → GetPhysicalMonitorsFromHMONITOR 句柄`，
`ddc/windows.py::_open()` 已经按 HMONITOR 枚举，P1 的 T1.4「精确挑 DDC 句柄」在 Windows 上不用试错。

### 0.4 W2 明细：`0x60` 能读，而且比 mac 稳得多

| 观测 | 结果 |
|---|---|
| `python -m dualswitch probe` | 成功：`当前输入源: VCP 0x60 = 17（读数仅供参考）` |
| `python -m dualswitch doctor` | `VCP 读数 0x60 = 17` |
| 同进程连读 10 次 `get_input()` | **10/10 成功**，10 次全是 `17`，每次 57–69 ms |
| 独立进程冷启动 `probe`（共 14 次） | 13 成功 / 1 失败：`GetVCPFeature 返回 0 (WinError -1071241851 = 0xC0262585)` |
| `probe_input()` | `(17, 14)` —— `max=14` 是坏字段，详见 §0.6 第 4 条 |

mac 侧报的「~50% 成功率、连续两次必吞一次」在本机 **没有复现**：Windows/dxva2 同进程 10/10。
差异只在**冷启动第一次**：`ddc/windows.py::get_input()` 没有重试（`set_input()` 有 2 次），
进程刚起、物理句柄刚开时偶发失败。P1 若要把读数用于标定，给 `get_input()` 补一次重试即可。

另外：原任务 W2 里的 `python -m dualswitch list` **当前 CLI 不存在**（子命令只有
`run / ui2 / switch / probe / doctor / config / display / clip`），`list` 会报 `invalid choice`。
`display` 在 Windows 上是「停用外接屏」的 macOS 专属项，会直接 SKIP；列显示器请用 `probe`。

### 0.5 W3 明细：文案/图标确实错，但功能语义也会撞车

本机没有第二台 Windows 可组双 Windows 实机，以下是**代码逐处核对**（文件行号以 4af40bf 为准），
真机双 Windows 的 UI 观感仍建议用户在场时再看一眼：

| 检查点 | 代码位置 | 结论 |
|---|---|---|
| 切换页两行 | `ui/win2/switch_page.py:58` `TITLE={"windows":"Windows","mac":"Mac"}`、`:239` `for host in ("windows","mac")`、`:127` `f"{_title(host)} 的输入口"` | ✅ 现状就是「本机输入口」+「**Mac** 的输入口」 |
| 端口名 | `switch_page.py:57` `PORT={"windows":"HDMI","mac":"USB-C"}` | ✅ 写死 |
| 图标 | `ui/chrome/win.py:19` `PEER_LOGO = "apple"`（注释直说「对端就是另一台」） | ✅ 苹果 logo |
| 托盘菜单 | `ui/win2/tray.py:36,61`；`tray.py:137`（老路径） | ✅ `Mac (USB-C)` / `Windows (HDMI)` |
| 设置页 | `ui/win2/settings_page.py:324–338` `_sources_control()` | ✅ 「Windows [17]  Mac [18]」 |
| 配对页 | `ui/win2/pairing_page.py:246` `_platform_name()`、`:1088` `plate_pixmap()` | ✅ 取 `hello.plat`，两台 Windows 都画 Windows logo |

**但「只是文案/图标错」这个前提不成立**，因此 P3 不能只做改名：
`config.py:244–251` 的 `effective_host()` / `other_host()` 只有 `windows|mac` 两个值，
且 `this_host=auto` 时**按 `sys.platform` 取值** —— 两台 Windows 都自认 `windows`。
于是第二台上 `app.py:240` `host_of_value()` 把 17 判成「本机」、18 判成「对端」，
`_commit_read_host()` / `detect_source()` / `_resolve_target()` 全跟着这个角色走：
它可能把「画面就在自己这儿」判成「在对面」，然后自动把画面切走。
`sources` 的键和 `switch_mode` 用的也是这两个角色字面量，不是真平台；
而 `T_STATE/T_STATE_ACK` 只有 `req` / `sees` 布尔 / `kb`，**不带角色也不带读数**，
两端之间没有第三方信息可以给这个错判纠偏。
临时绕法是在其中一台手工设 `this_host="mac"`（校验允许），功能能通但整套文案全反。
⇒ **P3 要把「平台（hello.plat）」「角色（local/peer）」「端口名」三者拆开**，与
`PROFILE-AND-ROLES-DESIGN.md` 的三维解耦方向一致；至少要让归属判断不再依赖 `sys.platform` 推角色。

### 0.6 给 mac 端的结论 / 需要改的设计假设

1. **W1 阻塞解除，P1 可以开工**：Windows 能拿到 `厂商:PID`，跨端主键 `AOC:9987` 一致（§0.2）。
   `V3-TASKS.md` 的「P1 前置阻塞」可以划掉。
2. `ddc/identity.py` 的 Windows 分支按 §0.2 四条口径写；不要按原任务期望的「WMI 直接给 9987」实现。
3. 型号 ↔ 句柄绑定用 §0.3 的 PnP 实例路径，T1.4 不必照搬 mac 的试错法。
4. **`MONITOR-PROFILE.md` §3 把 `max=14` 当成「这块新屏坏」要更正**：旧屏 `AOC Q2790PC`
   早在 2026-10-17 就是 `cur=17, max=14`（`PROGRESS.md:225`、
   `docs/archive/INVESTIGATION-INPUT-BOUNCE.md:73`），本机今天在这块旧屏上也是 `(17, 14)`。
   这是 AOC/dxva2 的通病、不是新屏特征。`readback=unreliable` 的结论仍成立，但理由要改。
5. W2 的 `list` 子命令不存在；Windows 冷启动首次读偶发失败，建议给 `get_input()` 补一次重试（§0.4）。
6. **W3 不是「改名」级改动**，见 §0.5；双 Windows 的角色解耦要和 P1 的 wiring 设计一起考虑，别排到最后再返工。

### 0.7 本轮代码改动与自检

- `packaging/windows/install_autostart.ps1`：`$mode` 的 `ui` → `ui2`（P0 删 v1 后 `ui` 子命令已不存在，
  自启快捷方式会直接报「无效子命令」）；同步改注释、Description、模式文案，**保留 UTF-8 BOM**，语法解析 0 错。
- `packaging/windows/build.ps1`：构建末尾用法提示 `$target ui` → `$target ui2`（同一处 v1 残留），BOM 保留。
- 未动生产 Python 代码；`packaging/entrypoint.py` 里「v1 仍可用 `dualswitch ui` 显式启动」这句注释也已失效，
  属共享文件，留给 mac 端顺手删。
- 全量测试：`PYTHONPATH=src QT_QPA_PLATFORM=offscreen pytest tests` → **567 tests / 557 passed / 0 failed / 10 skipped**（本机基线）。
- 合并远端后按约定扫了改动文件的同名定义，无重复遮蔽。

---

## 以下为原始任务（存档，保持原文；结果见 §0）

---

## W1（阻塞项）· 能否读到显示器的 厂商 + 产品号

这是唯一的前置阻塞项。mac 端拿到的身份是：

```
ManufacturerID = "AOC"   ProductID = 9987   SerialNumber = UTHN8HA014094
```

`9987` 就是 EDID 里的 **product code**，`AOC` 是 EDID 里的 **manufacturer ID**（mac 侧同时给了数字 `1507`，那是同一字段的 5-bit 编码值）。

**请在 Windows 上跑这段（PowerShell，只读，不改任何东西）：**

```powershell
Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID | ForEach-Object {
  [PSCustomObject]@{
    Instance      = $_.InstanceName
    Manufacturer  = -join ($_.ManufacturerName  | Where-Object {$_ -ne 0} | ForEach-Object {[char]$_})
    ProductCode   = $_.ProductCodeID
    Name          = -join ($_.UserFriendlyName  | Where-Object {$_ -ne 0} | ForEach-Object {[char]$_})
    Serial        = -join ($_.SerialNumberID    | Where-Object {$_ -ne 0} | ForEach-Object {[char]$_})
  }
} | Format-List
```

**判定标准**

| 结果 | 结论 |
|---|---|
| 能拿到 `Manufacturer=AOC` + `ProductCode=9987` | ✅ 主键 `AOC:9987` 成立，两端天然一致，**P1 可以开工** |
| 只能拿到 `Name`（如 `U27N3G6R4B`），没有 mfg/pid | ⚠️ 只能退化成「型号名」作主键，需要处理同名多型号的风险 |
| `WmiMonitorID` 为空 / 报错 | ⚠️ 退路：读注册表 EDID 原始字节，见下方备用脚本 |

**备用脚本（直接解 EDID 二进制，字段位置是 EDID 标准，两端一致）：**

```powershell
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\DISPLAY' | ForEach-Object {
  $mon = $_
  Get-ChildItem $mon.PSPath | ForEach-Object {
    $edid = (Get-ItemProperty "$($_.PSPath)\Device Parameters" -Name EDID -ErrorAction SilentlyContinue).EDID
    if ($edid -and $edid.Length -ge 128 -and $edid[0] -eq 0) {
      $mfgRaw = ($edid[8] -shl 8) -bor $edid[9]
      $mfg = '{0}{1}{2}' -f
        [char](64 + (($mfgRaw -shr 10) -band 0x1F)),
        [char](64 + (($mfgRaw -shr 5)  -band 0x1F)),
        [char](64 + ($mfgRaw -band 0x1F))
      [PSCustomObject]@{
        Manufacturer = $mfg
        ProductCode  = $edid[11] -shl 8 -bor $edid[10]
        Serial       = ($edid[15] -shl 24) -bor ($edid[14] -shl 16) -bor ($edid[13] -shl 8) -bor $edid[12]
        Instance     = $mon.PSChildName
      }
    }
  }
} | Format-List
```

两份脚本对同一块屏应当给出**相同**的 `Manufacturer` / `ProductCode`。有差异请原样回报两份输出。

---

## W2 · Windows 后端读 `VCP 0x60` 的能力

mac 端已有的现成命令（Windows 端应当同样可用）：

```powershell
python -m dualswitch probe      # 只读探测显示器 + 当前输入源
python -m dualswitch list       # 列出所有显示器
```

**mac 侧实测（同一块 AOC U27N3G6R4B）：**

| 端口 | `0x60` 值 | 备注 |
|---|---|---|
| HDMI 1 | 17 | 实测（切换时抓到） |
| HDMI 2 | 17 | 推断（切两个 HDMI 口只跳一次） |
| DP | 15 | 实测 |
| USB-C | 100 | 实测（重建句柄 8 次，4 次成功全为 100） |

请回报：

1. `probe` 能不能读出输入源（成功 / -118 / 其它错误码）；
2. 读到的数值，以及**当时画面接在哪个口**（HDMI / DP / USB-C）；
3. 连续读 10 次的成功率（mac 侧只有 ~50%，同一通道连续两次 I2C 事务必吞一次）。

> 如果 Windows 端接的是**另一块**显示器，以上数字不用对上，只要回报「能不能读 + 读到的值 + 当前接的哪个口」即可。数值本身也有价值——能判断码表到底有多多样。

---

## W3 · 双 Windows 场景的现状确认（不改代码，只确认现象）

两端都是 Windows 时，现在的实现会把「对方那台 Windows」显示成 **Mac**。mac 端代码已确认（`effective_host()` 只有 `windows`/`mac` 两值 = 平台当角色用）。

请在 Windows 端确认同一现象：

| 检查点 | 预期（现状） |
|---|---|
| 切换页两行端口的目标名 | 一行「Windows 的输入口」，一行「**Mac** 的输入口」（实际是另一台 Win） |
| 该行的图标 | 苹果 logo |
| 托盘菜单文案 | `Mac (USB-C)` / `Windows (HDMI)` |
| 设置页输入源项 | 「Windows 输入源 / **Mac** 输入源」 |
| 配对页图标 | 两个 Windows logo（**这里是对的**，用的是 hello 上报的真实平台） |

确认「切屏功能本身可用、只是文案/图标错」这一点很重要——决定了 P3 是「改名」还是「重做」。

---

## 不需要验证的

剪贴板全链路、鼠标联动（HID++）、键盘跟随：与显示器无关，这次不涉及。

---

## 回报格式

```
W1: Manufacturer=__  ProductCode=__  Name=__  Serial=__   （脚本 A 的结果）
    脚本 B 是否一致：是 / 否，输出为：__
W2: probe 结果=__  （成功 / 错误码）
    读到 0x60=__ ，当前接的口=__
    10 次成功率=__/10
W3: 目标行文案=__  图标=__  托盘=__  设置页=__  配对页=__
```
