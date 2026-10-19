# Windows 托盘图标不显示 —— 调查交接

> ✅ **已闭环（2026-10-18 16:15，Windows 侧）**：真凶不是代码，是 **exe 继承了 `Low` 完整性标签**。
> `D:\work` 带 `Low Mandatory Level` 且 `(OI)(CI)` 继承 → `dist\dualswitch\dualswitch.exe` 继承了它 →
> Windows 以**低完整性级别**启动进程 → 本机 explorer 是提权的 `High` → UIPI 直接丢掉
> `Shell_NotifyIcon`，所以 shell 里连登记记录都没有（本文第 3 节第 2 条强证据正是这个原因）。
> 同一份 exe 只改标签的对照：`Low` 无图标 / `Medium` 仍无 / `High` 出现（`位置=2256,1400 24x40`）。
> 修复与判定入口见 `docs/TROUBLESHOOTING.md` §4.1、`src/dualswitch/winenv.py`、
> `tools/fix_integrity.py`；`packaging/windows/build.ps1` 打包后会自动把产物标签抬到 High。
> 下面保留原始调查过程，供复盘「为什么会排查这么久」。
>
> 写于 2026-10-18 15:20（macOS 侧，无法直接调试 Windows，所有结论都来自用户回传的日志/截图）。
> 接手的人请先读第 3 节「当前判定」和第 8 节「下一步行动」，其余是按需查阅的证据。
> 仓库：`D:\work\workbuddy\dualswitch`（Windows 侧），当前应停在 `e8646cd`。

---

## 1. 环境与构建方式

| 项 | 值 |
|---|---|
| 系统 | **Windows 10**（不是 Win11） |
| 用户 | `Administrator`，仓库在 `D:\work\workbuddy\dualswitch` |
| 打包 | `powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1`（默认 `--onedir --windowed`，`--clean`） |
| 产物 | `dist\dualswitch\dualswitch.exe`（约 5.4 MB）+ `_internal\`，内部带 PySide6（pip 装的是**最新** `PySide6-Essentials`）、pystray、Pillow |
| 开发跑法（正常） | `start-ui.bat` → `pythonw.exe -m dualswitch ui` |
| 日志 | 默认 `%APPDATA%\dualswitch\logs\dualswitch.log`；可用 `DUALSWITCH_LOG=<路径>` 覆盖（本轮调试都靠它） |
| 显示器 | AOC Q2790PC 单屏，VCP 0x60 切 HDMI(17)/USB-C(18) |
| UI 栈 | PySide6；Windows 端托盘 = `QSystemTrayIcon`，窗口是自绘无边框 |

---

## 2. 现象与最小复现

**正常**：`start-ui.bat` 启动 → 托盘出现图标 → 点窗口右上角关闭 → 窗口消失、进程常驻、托盘图标还在 → 右键菜单可切换/退出。

**异常**：双击 `dist\dualswitch\dualswitch.exe` → 主窗口正常显示、切换功能一切正常 →
**托盘里自始至终没有图标**（托盘区、`^` 溢出区都没有）→ 点关闭后窗口消失，进程仍在任务管理器里，但没有任何入口能把它叫回来。

复现步骤：

```powershell
cd D:\work\workbuddy\dualswitch
Get-Process dualswitch -ErrorAction SilentlyContinue | Stop-Process -Force
$env:DUALSWITCH_LOG = "D:\work\workbuddy\dualswitch\tray-diag.log"
Start-Process .\dist\dualswitch\dualswitch.exe
Start-Sleep -Seconds 10
Get-Content -Encoding UTF8 $env:DUALSWITCH_LOG -Tail 25
```

---

## 3. 当前判定（截至 `e8646cd`）

**这个进程的托盘注册没有被系统接受，问题不在图标资源、也不在某一个 UI 框架。**

证据强度排序：

1. **两套互不相干的实现同时失灵**（强证据）
   - `QSystemTrayIcon`：`托盘可用=True 图标可见=True 图标为空=False 尺寸=[16..256]`，但 `位置=未知`；
   - `pystray`（`dualswitch run` 那条老路，同一台机器上以前是能出图标的）：日志报 `pystray 托盘已启动（兜底实现）`，托盘仍然什么都没有。
2. **系统「选择哪些图标显示在任务栏上」列表里没有 `dualswitch.exe` 的条目**（强证据）
   - 列表里只有一条 `Python` + 副标题 `屏幕切换 · 未启用（双击图标切换）` + 开关「开」；
   - `pythonw.exe` 的 FileDescription 就是 `Python`，所以那是 `start-ui.bat` 那条路的旧条目；
   - 我们打包的 exe，FileDescription 是「屏幕切换 - 双机显示器输入源切换」，**列表里根本不存在这一行**。
   - 含义：shell 里连登记记录都没有 → `Shell_NotifyIcon(NIM_ADD)` 没成功（或者成功即被移除）。
3. **`isVisible()` 不能当判据**（方法论，已写进代码注释）
   - 它只表示「调用过 `show()`」；注册失败照样 True。
   - `geometry()` 才来自系统（`Shell_NotifyIconGetRect`）。Qt 上游提交
     `ca851b3`（"Unify behavior of QSystemTrayIcon::geometry for hidden icons on Windows"）
     说明：Windows 上图标藏在 `^` 溢出区时 `geometry()` 现在统一返回 `QRect()`。
   - 所以「位置为空」有两种含义：**被藏进溢出区** 或 **压根没这一格**。结合第 2 条，本例是后者。

---

## 4. 实验时间线（做了什么 → 看到什么）

| 时间 | 实验 | 结果 |
|---|---|---|
| 13:4x | 读代码找「关窗口时删图标」的路径 | 没有。`closeEvent` 只 `hide()`，`status.hide()` 只在 `quit()` 里 |
| 14:12 | 加托盘注册诊断日志后重打包（`e275852`→`17d7adc`） | 日志里 `图标为空=False 图标可见=True 位置=未知` |
| 14:13 / 14:24 | 双击 exe 两次 | 起了两个进程；`%APPDATA%` 的日志**一行没写** |
| 14:36 | 用 `DUALSWITCH_LOG` 指到 D 盘再跑 | 日志正常写出 → 定位到「日志通道」与「托盘」是两件事 |
| 14:5x | 用户查系统「选择哪些图标显示在任务栏上」 | 只有 `Python` 一条，**没有 dualswitch.exe** |
| 15:04 | 用户查日志文件属性 | 30922 字节 / `Archive` / PowerShell `Add-Content` 成功 → 文件可写，**exe 就是不写**（未解，见 §7） |
| 15:17 | 重打包（含 Qt 告警进日志 + 托盘重建 + 换 pystray 兜底） | `重建`/`重新注册` 后位置仍为 `未知`；切到 pystray 后仍无图标；日志里**没有任何 `[Qt]` 告警** |

---

## 5. 已经排除的假设

| 假设 | 为什么排除 |
|---|---|
| `.ico` 坏了 / 没打进包 | `应用图标：...\_internal\dualswitch\ui\assets\screen-switch.ico（尺寸 16x16…256x256）`，`图标为空=False`；本地也逐帧验证过 16×16 的 alpha 正常、不透明区覆盖整格 |
| Qt 缺少 `imageformats/qico` 插件 | 若缺，`QIcon` 会是空图标；实测非空且有 9 档尺寸 |
| 关闭逻辑把图标删了 | 全仓库没有任何在关窗时移除图标的地方；`hide()` 只出现在 `quit()` |
| `withdraw_to_tray` / 关闭路径问题 | 已改为统一走 `closeEvent`，并加了「托盘不可用就最小化」的保护，与图标不显示无关 |
| 图标被 Win11 收进溢出区 | 系统是 **Windows 10**；溢出区用户已翻过；且系统设置列表里没有本程序条目 |
| 图标文件路径带中文/空格 | 路径 `D:\work\workbuddy\dualswitch\...`，全 ASCII |
| 进程被 DPI/无边框窗口影响 | 窗口渲染、热键、DDC 全部正常 |
| 只是 Qt 的问题 | pystray 同样出不来（两套实现同时失灵） |

---

## 6. 代码现状（Windows 侧托盘相关）

### 6.1 调用链

```
cli.cmd_ui
 └─ ui.runner.UiRunner.__init__
     ├─ _install_qt_log_handler()        # Qt 的告警写进我们的日志（前缀 [Qt]）
     ├─ load_app_icon()                  # ico 读不出来时用代码画的图标兜底
     ├─ window.show()                    # 注意：先亮窗口
     └─ _build_status(hotkey_label)
         ├─ chrome.build_status_item()   # Windows = chrome/win.py::QtStatusItem
         ├─ _check_status_item()         # is_available()==False → 关窗口改最小化
         └─ QTimer 2.5s → _verify_status_item()
             ├─ 有位置 → return
             ├─ 没位置 → _retry_status_item(1) → recreate()  # 整只重建 QSystemTrayIcon
             ├─ 1.5s → _confirm_status_item(1) → 再 retry(2) → reassert()  # hide/show
             └─ 1.5s → _confirm_status_item(2) → _switch_to_fallback_status()
                 └─ chrome.build_fallback_status_item()
                     ├─ win32tray.try_create()      # ① 直接 Shell_NotifyIconW（带 GetLastError）
                     └─ PystrayStatusItem(...)      # ② pystray
```

### 6.2 提交清单（时间正序）

| commit | 内容 | 性质 |
|---|---|---|
| `e275852` | 托盘注册结果进日志；空图标兜底；`withdraw_to_tray`（托盘不可用→最小化）；关闭按钮改走 `closeEvent` | 诊断 + 保护 |
| `563d723` | 启动日志加「可执行文件」（路径/时间戳/大小） | 诊断 |
| `17d7adc` | 修 `.gitignore` 行尾注释 | 杂项 |
| `5952810` | 启动日志的「可执行文件」带 mtime + 大小 | 诊断 |
| `964e8aa` | **日志写不了时静默失败** → 退回 `%TEMP%\dualswitch.log` | 真 bug 修复 |
| `4b441eb` | 自检改看 `geometry()`（不再信 `isVisible()`）；空→重注册；复检报错点名系统开关 | 真 bug 修复 |
| `bb9c40e` | `qInstallMessageHandler` 收 Qt 告警；托盘改到「窗口 show() 之后」创建；`QtStatusItem.recreate()` | 修复尝试 + 诊断 |
| `086607e` | 启动日志写清「日志落在哪 / 文件 handler 几个 / DUALSWITCH_LOG」 | 诊断 |
| `0cfedfe` | Qt 托盘不灵时自动换 pystray（`PystrayStatusItem`，菜单由 `MenuEntry` 翻译，双击=切换） | 兜底 |
| `e8646cd` | `ui/chrome/win32tray.py`（自己调 `Shell_NotifyIconW`，打印 `GetLastError`）+ `tools/diag_windows_tray.py` | 兜底 + 诊断工具 |

### 6.3 关键文件

| 文件 | 作用 |
|---|---|
| `src/dualswitch/ui/chrome/win.py` | `QtStatusItem`（Qt 托盘）、`PystrayStatusItem`（pystray 兜底）、`build_status_item()`、`build_fallback_status_item()` |
| `src/dualswitch/ui/chrome/win32tray.py` | 直接 `Shell_NotifyIconW` 的实现；失败会把 `GetLastError` 写进日志 |
| `src/dualswitch/ui/runner.py` | 自检 / 重试 / 换实现的编排（`_verify_status_item`、`_retry_status_item`、`_confirm_status_item`、`_switch_to_fallback_status`） |
| `src/dualswitch/ui/statusitem.py` | 两端共享的 `MenuEntry` / `build_menu_entries()`（`default=True` 的那条 = 双击触发） |
| `src/dualswitch/ui/window.py` | `withdraw_to_tray`：托盘不可用时关窗口 = 最小化，不再制造「无入口进程」 |
| `packaging/windows/build.ps1` | 默认 onedir；`-Console` 可出带控制台的版本（Qt 的 stderr 能看到） |
| `tools/diag_windows_tray.py` | **不打包**直接测托盘注册，打印会话 ID / 提权 / `Shell_TrayWnd` / `NIM_ADD` 返回值 + 错误码 |

---

## 7. 未解决 / 未验证

1. **`%APPDATA%\dualswitch\logs\dualswitch.log` 不更新（exe）**
   - 文件不是只读、不是被锁（PowerShell 能写，30922 字节，`Archive`）；
   - pythonw 能写（最后一条 13:40:43），同一个 exe 从 14:13 起不再写；
   - `964e8aa` 之后若真的写不进去，会在 `%TEMP%\dualswitch.log` 留一条「默认日志不可写」；
   - 现在启动日志里有一行 `日志：<实际路径>（默认位置 …，文件 handler N 个，DUALSWITCH_LOG=…）`，
     **handler 为 0** 就说明日志根本没接上文件。这条线还没被验证过。
2. **`win32tray.py` 只在 macOS 上做过 import 级验证**，没在 Windows 真机跑过。
   第一次跑请用 `tools/diag_windows_tray.py`（不打包，好排查），别直接上 exe。
3. **pystray 的 `Icon.run()` 在子线程里是否真的起来了**没有独立验证
   （只证明线程启动了）。若 win32tray 也失败，建议在 pystray 路径上加 try/except 记录。

---

## 8. 下一步行动（按优先级）

### 第 0 步：先分清「机器问题」还是「打包问题」

```powershell
cd D:\work\workbuddy\dualswitch
git pull                          # 到 e8646cd

# 用解释器直接跑（不打包）。没有 .venv 就用 workbuddy 那个 python
.venv\Scripts\python.exe tools\diag_windows_tray.py --keep 20
```

它会打印：可执行文件、**会话 ID**、**是否提权**、**`Shell_TrayWnd` 句柄**、
`LoadImage` 结果、`NIM_ADD` 返回值 + `GetLastError`、图标位置，然后保留 20 秒让你肉眼确认。

判读：

| 结果 | 含义 | 走向 |
|---|---|---|
| 图标出来了 | 解释器进程能注册 → **问题在打包/进程环境** | 第 1 步 |
| `NIM_ADD` 成功但没图标 | 图标被藏起来 | 去 `^` / 「选择哪些图标显示在任务栏上」找，找到后拖出来 |
| `NIM_ADD` 失败 | 拿到错误码 | 第 2 步 |
| `会话 ID ≠ 1` 或 `Shell_TrayWnd = 0` | 进程不在交互式桌面 | 直接定位（服务/其他会话启动） |

### 第 1 步：对比「打包版」与「解释器」

1. 重打包并运行 exe，日志里搜 `[dualswitch.tray]` 与 `[Qt]`：
   - 有没有 `Shell_NotifyIcon(NIM_ADD) 失败：GetLastError=…`（`e8646cd` 之后才有这行）；
   - 有没有 `已用自己的 Win32 调用把图标加进通知区域`。
2. 若解释器能、exe 不能，重点查这几项（按可能性排）：

   ```powershell
   # 安全软件（360/火绒/腾讯管家/EDR 都会拦「新程序」的托盘注册与开机项）
   Get-Process | Where-Object { $_.Name -match '360|huorong|HipsTray|QQPC|ZhuDongFangYu|usysdiag|wsctrl|kingsoft|baidu|avp|edr' } | Select-Object Name,Id,Path

   # 进程完整性级别 / 会话（需要同一个 shell 里对比两个进程）
   whoami /groups | Select-String "Mandatory Label"
   (Get-Process -Id $PID).SessionId

   # exe 的兼容性设置（右键属性→兼容性；或注册表）
   Get-ItemProperty "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers" -ErrorAction SilentlyContinue
   ```
3. Windows 10 的「按 exe 路径记的托盘偏好」缓存：只清本程序那一项再重启 explorer（不要全删）：

   ```powershell
   Get-ChildItem "HKCU:\Control Panel\NotifyIconSettings" | ForEach-Object {
       $p = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).ExecutablePath
       if ($p -like "*dualswitch.exe") { Write-Host "删除 $p"; Remove-Item $_.PSPath -Recurse -Force }
   }
   Stop-Process -Name explorer -Force
   ```

### 第 2 步：如果 `NIM_ADD` 真的返回失败

`GetLastError` 常见值：

| 值 | 含义 | 处理方向 |
|---|---|---|
| 5 | 拒绝访问 | 安全软件/组策略拦截 → 加白名单，或换用户/关防护验证 |
| 1400 | 窗口句柄无效 | 窗口创建问题（`win32tray` 的 `CreateWindowExW` 结果会先打出来） |
| 1460 | 超时 | shell 没响应 → 重启 explorer |
| 0 | 无错误但返回 FALSE | 参数/结构体版本问题，核对 `cbSize` 与 `uFlags` |

### 第 3 步：其它可选验证

- 打一个带控制台的版本看 Qt 原话：`build.ps1 -Console`，在 cmd 里前台运行。
- `dualswitch.exe run`（老的 pystray 常驻模式）在这台机器上到底有没有图标 ——
  这条能直接判断「是不是所有 exe 进程都注册不上」。**这是最有价值的一条对照实验，
  之前一直没做过。**

---

## 9. 命令速查

```powershell
# 打包 / 重打包
Get-Process dualswitch -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item -Recurse -Force dist\dualswitch
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

# 跑（日志写到 D 盘，绕开 %APPDATA% 那个不更新的文件）
$env:DUALSWITCH_LOG = "D:\work\workbuddy\dualswitch\tray-diag.log"
Remove-Item $env:DUALSWITCH_LOG -ErrorAction SilentlyContinue
Start-Process .\dist\dualswitch\dualswitch.exe
Start-Sleep -Seconds 10
Get-Content -Encoding UTF8 $env:DUALSWITCH_LOG -Tail 25

# 只挑关键行
Select-String -Path $env:DUALSWITCH_LOG -Pattern "\[Qt\]|\[dualswitch.tray\]|可执行文件|文件 handler" -Encoding UTF8

# 托盘注册现场诊断（不打包）
.venv\Scripts\python.exe tools\diag_windows_tray.py --keep 20

# 应急退出
taskkill /IM dualswitch.exe /F
```

> **`Get-Content` 一定要带 `-Encoding UTF8`**：日志是 UTF-8，PowerShell 5.1 默认按 GBK 读，
> 中文会变成 `鐢ㄦ埛...`。这不是日志坏了。

---

## 10. 陷阱清单（本轮踩过的）

1. **`QSystemTrayIcon.isVisible()` 不能当判据** —— 它只反映「调用过 show()」。
   判断注册是否真的成功，看 `geometry()`（空 = 溢出区 / 没有这一格）或自己调 API 的返回值。
2. **`QIcon(path)` 读不出格式时不抛异常，返回空图标** —— 空图标在托盘里就是「什么都没有」，
   而任务栏因为用 exe 自带图标资源，看不出来。现在会兜底成代码画的图标并记 ERROR。
3. **日志写不了时曾经完全静默**（`root.warning` 没有 handler 接）→ 现在退回 `%TEMP%`。
4. **「日志没更新」会让人拿旧日志推理** —— 所以启动横幅加了「可执行文件 + 时间戳 + 大小」，
   先对齐这一行再往下看。
5. **别用 `%.len` 之类的相对路径判断资源** —— 打包后用 `sys.executable` / `_MEIPASS`，
   现成函数是 `theme.asset()` / `theme.app_icon()`。
6. **`pythonw` 与打包 exe 的差异是系统按 exe 路径记偏好**，所以「源码跑得好好的」不能证明打包版没问题。

---

## 11. 一句话总结给下一位

> 图标资源、Qt、关闭逻辑都没问题；是**这个 exe 进程的托盘注册没被系统接受**（系统设置列表里连条目都没有），
> 且两套独立实现（`QSystemTrayIcon`、`pystray`）同时失灵。
> 先用 `tools/diag_windows_tray.py` 判断「解释器进程能不能注册」，再拿 `NIM_ADD` 的 `GetLastError`
> 决定是查安全软件/会话，还是查打包环境。`dualswitch.exe run` 有没有图标，是最省事的一条对照实验。
