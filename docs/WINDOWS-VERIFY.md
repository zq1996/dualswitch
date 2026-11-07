# Windows 端验证任务（换屏 / profile 方案前置）

> 2026-11-07 · 给 Windows 端。**这一轮不改代码，只要数据。**
> 背景：mac 端换了显示器（AOC Q2790R3 → AOC U27N3G6R4B），发现 `VCP 0x60` 的接口码跟屏走、而且读不出来只能标定。
> 想做成「按屏配置」之前，**必须先确认 Windows 端能不能拿到同一套显示器身份**。拿不到，方案就得换。

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
