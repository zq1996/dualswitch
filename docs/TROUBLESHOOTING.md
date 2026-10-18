# 排障手册

## 1. 按了热键没反应

按顺序排查：

```powershell
dualswitch doctor
```

看 `热键可用性` 一行：

| 结果 | 含义 | 处理 |
|---|---|---|
| `可注册` | 热键本身没问题 | 看日志 `logs/dualswitch.log` 有没有「去抖丢弃」或「切换失败」 |
| `热键已被其它程序占用` | 被抢了 | 关掉占用方（输入法、截图、PowerToys、游戏加速器），或改 `hotkey` |
| `安装键盘钩子失败` | 安全软件拦截 | 加白名单，或把 `hotkey` 改回 `alt+space` 用 register 后端 |

日志位置：`%APPDATA%\dualswitch\logs\dualswitch.log`（macOS：`~/Library/Application Support/dualswitch/logs/`）。

### macOS 专用：先跑现场诊断

```bash
python tools/diag_macos_hotkey.py            # 实时看：按一次热键
python tools/diag_macos_hotkey.py --simulate # 不用真按，自己发一个合成热键
```

结尾会打印 `收到事件 N 个 / 命中 M 次 / tap 被禁用 X 次 / 回调异常 Y 次`，按这个判断：

| 现象 | 含义 | 处理 |
|---|---|---|
| 一个事件都没收到 | tap 没生效：没授权 / 被安全软件拦 / 进程挂起 | 系统设置 → 隐私与安全性 → 辅助功能，勾选「屏幕切换」（列表里显示 `dualswitch`） |
| 收到事件但命中 0 次 | 修饰键不匹配 | 左 Option ≠ 右 Option：配置是 `ralt+space` 就只认右边；想两个都认改成 `alt+space` |
| `tap 被禁用` 次数一直在涨 | 回调里有东西在拖慢（系统 1s 就判定卡住） | 现在会自动重开；看日志有没有别的异常 |
| 进程是老的 | 改了代码但没重打包 | `build.sh` + `install-app.sh`，别只重启 |

> 2026-10-18 修过一次「按一百次一次都不响、日志却很干净」：`CGEventGetIntegerValueField(event,
> kCGKeyboardEventFlags)` 这个字段不存在（pyobjc 里连常量都没有），每次回调抛 `AttributeError`
> 被兜底 `except` 吞掉。现在一律用 `CGEventGetFlags()`，回调异常也会记日志。

## 2. 日志显示「已发送切换指令」但屏幕没动

1. **VCP 值不对**。`dualswitch probe` 看当前值，然后换一个值试：
   `dualswitch switch --to mac` 之后手动改 `sources.mac` 再试。
2. **显示器 DDC/CI 被关了**。AOC：`OSD → 设置 → 其他设置 → DDC/CI → 开启`。
3. **HDR / VRR 干扰**。关掉 HDR、把刷新率降到 60Hz 再试。
4. **线材问题**。HDMI 线太长/转接器可能丢 DDC 包；换短一点的线。

## 3. 能切走，切不回来

Windows 切到 Mac 后，Windows 就看不到显示器了，所以：
- Windows 侧的「回读」必然失败 → 日志出现「回读未确认」，**这是预期的**，不是错误；
- 切回来必须靠 Mac 端的热键，或者在 Mac 上执行 `m1ddc set input 17`。

如果 Mac 端热键也没配好，暂时用 Mac 的「系统设置 → 显示器」或显示器 OSD 手动切回。

## 4. 关窗口后托盘里没有图标，但任务管理器里进程还在

> 全过程调查记录（含已排除的假设、每个 commit 的作用）见
> [`WIN-TRAY-INVESTIGATION.md`](WIN-TRAY-INVESTIGATION.md)。
> **结论已出**（2026-10-18 16:15）：真凶是 exe 继承了 `Low` 完整性标签 —— 见下面 §4.1。

这不是「程序崩了」，而是**托盘图标没进通知区域**。按下面顺序看，两步就能定位。

### 4.1 第一步：看「完整性级别」（最常见的真凶）

每次启动都会写这一行（`dualswitch ui`）：

```
[INFO] 完整性级别：本进程=Low(0x1000) 资源管理器=High(0x3000)（exe=...）
```

**本进程级别低于资源管理器 → 图标 100% 不会出现，跟代码无关。**
Windows 的 UIPI 不允许低完整性级别的进程向更高级别的窗口发消息，而托盘图标正是
靠给 explorer 发 `Shell_NotifyIcon` 实现的。explorer 在本机是提权运行的（High），
所以程序也必须是 High 才能注册图标。

| 日志表现 | 含义 | 处理 |
|---|---|---|
| `本进程=Low` / `Medium`，`资源管理器=High` | exe 带了低完整性标签，或从低级别进程启动 | 见下面的修复命令 |
| 两者相同（都是 High / 都是 Medium） | 级别没问题，往下看 §4.2 | — |

修复（把 exe 所在目录的标签抬到 High，需要管理员）：

```powershell
icacls "D:\work\workbuddy\dualswitch\dist\dualswitch" /setintegritylevel (OI)(CI)High /T /C
```

或直接跑仓库里的工具：

```powershell
python tools\fix_integrity.py --self                          # 看两个级别
python tools\fix_integrity.py --path dist\dualswitch --set High
```

> **为什么会这样**：本仓库位于 `D:\work`，该目录带 `Low Mandatory Level` 标签并且是
> `(OI)(CI)` 继承的 —— 在它里面**新建**的文件（包括 PyInstaller 打出来的 exe）都会继承
> Low。带 Low 标签的 exe 被启动时，Windows 会把进程的完整性级别压到 Low。于是
> `Shell_NotifyIcon` 被 UIPI 静默丢弃：Qt 不抛异常（`isVisible()` 照样 True），
> pystray 只抛一句 `[WinError 5] 拒绝访问`。
> `packaging/windows/build.ps1` 现在会在打包后自动把产物标签抬到 High，
> 所以**重新打包一次就能好**；`High` 不会提升进程级别（进程级别 = min(用户, 文件)），
> 只是保证它不被文件标签压在 explorer 之下。
>
> 2026-10-18 实测对照：Low → 图标不出现；Medium → 仍不出现（explorer 是 High）；
> High → `位置=2256,1400 24x40`，图标出现。

### 4.2 第二步：看托盘自检那几行

```
[INFO] 应用图标：...\assets\screen-switch.ico（尺寸 16x16、32x32、48x48、256x256）
[INFO] 托盘图标（首次注册）：托盘可用=True 图标可见=True 图标为空=False 尺寸=[...] 位置=...
[INFO] 常驻图标检查：可见=True
```

先确认这几行**在哪一次启动里**：`图形界面启动` 下面紧跟的一行是
`可执行文件：...（打包版=True/False，资源目录=...）` —— 排查打包版时最常见的坑是
「拿到的是旧日志 / 跑的是旧 exe」，先对齐这一行再往下看。

| 日志表现 | 含义 | 处理 |
|---|---|---|
| `图标为空=True` | `.ico` 没读出来（Qt 缺 `imageformats/qico` 插件，或 `assets/` 没打进包） | 会自动换「代码画的图标」并记 ERROR；重打包时确认 `--add-data ...assets;dualswitch\ui\assets` 生效 |
| `可见=False` | 图标压根没注册给系统 | 程序 2.5 秒后会自动重注册一次；仍失败说明被安全软件/远程桌面拦了 |
| `位置=未知` | **注册了但系统没给它留格子**（`is_visible()` 是 Qt 自己的标记，不可信） | 3 秒后自动重注册；仍为空见下面「缓存坏了」 |
| `托盘可用=False` | 这台机器没有通知区域 | 程序会自动改成「关窗口 = 最小化」，不再隐藏（否则没有任何入口） |
| 三项都正常但桌面上看不到 | 图标被系统关掉/收进「隐藏的图标」 | 见 §4.4 / §4.5 |

> 判定口径：**只看 `位置`，不要看 `可见`**。Windows 打包版实测过
> `可见=True 图标为空=False 尺寸齐全` 但托盘里什么都没有 —— Qt 的 `isVisible()`
> 只表示「我调用过 show()」，注册失败照样 True；`geometry()` 取自系统的
> `Shell_NotifyIconGetRect`，拿不到矩形就是通知区域里没有这一格。
>
> 另外：**首次注册拿不到位置是正常的**，程序自己重注册一次之后就出现了
> （实测 High 级别下：首次 `位置=未知` → 第 2 次重试 `位置=2256,1400 24x40`）。
> 所以不要只看第一行就下结论。

### 4.3 程序会自己换实现（Qt 托盘 → pystray）

日志里出现这行说明 Qt 的托盘没进通知区域，程序已经自动换成 pystray 那条通道：

```
[WARNING] Qt 托盘没进通知区域，已换成兜底实现（pystray），图标应立即出现
```

两条实现最终都要走 `Shell_NotifyIcon`，所以它只在「Qt 这一层出问题」时有意义。
**完整性级别不够时程序不会换**（日志：`不切换兜底托盘：…`）—— 换了也白换，
还会把原本可能可用的 Qt 图标藏起来。

pystray 自己起不来时会记 `pystray 托盘启动失败：…`（典型：`[WinError 5] 拒绝访问`，
就是完整性级别不够），这时程序会保留 Qt 图标、并把「关窗口」改成最小化。

### 4.4 系统把这个 exe 的托盘图标关掉了

设置 → 个性化 → 任务栏 → 通知区域 → **「选择哪些图标显示在任务栏上」** →
找到 `dualswitch` / 「屏幕切换」，把开关打成 **开**。

> 这一项是**按可执行文件路径**记的：`pythonw.exe`（`start-ui.bat` 那条路）早就被允许，
> 所以源码方式正常；新打出来的 `dualswitch.exe` 是"新应用"，可能带着「关」的状态。
> 图标被收进 `^` 溢出面板时也一样 —— 点 `^` 拖出来一次就固定了（Win11 对新 exe 默认如此）。

### 4.5 通知区域缓存坏了（图标"消失"但注册成功）

先停进程，再清本程序的托盘偏好，然后重启 explorer：

```powershell
Stop-Process -Name dualswitch -Force -ErrorAction SilentlyContinue
Stop-Process -Name explorer -Force   # 任务栏会自动重启
```

Windows 11 的偏好在 `HKCU:\Control Panel\NotifyIconSettings`（按 exe 路径分项，
删掉本程序那一项即可）；Windows 10 存在
`HKCU:\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\TrayNotify`
的 `IconStreams` 二进制里，没有单条删除的官方途径 —— 所以 Win10 上先按 §4.4 把开关打开，
不要手改注册表。

卡在这个状态时的应急退出：

```powershell
taskkill /IM dualswitch.exe /F      # 托盘点不到，只能强制结束
```

`dualswitch run --no-console` 是旧的 pystray 常驻模式，用它对比可以确认问题只在「托盘」这一层。

macOS 侧同理：看日志里 `菜单栏状态项：图标=... 有效=True 可见=True` 一行。
用 launchd 启动时菜单栏图标可能不显示 —— 这是非交互进程的限制，改用 `.app` + 登录项启动。

## 5. macOS 提示没有辅助功能权限

`CGEventTapCreate` 返回空、或日志里有「缺少辅助功能权限」：

1. 打开 **系统设置 → 隐私与安全性 → 辅助功能**；
2. 勾选**实际运行程序的二进制**：
   - 从终端跑：勾选 Terminal / iTerm；
   - 从 Python 跑：勾选该 Python 解释器；
   - 打包后：勾选 `dualswitch.app`；
3. 改完要**重启程序**（不是重启系统）。

注意：每次换 Python 解释器路径（比如换 venv），授权都会失效，需要重新勾。

## 6. 开机自启了但没生效

- 检查快捷方式：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\dualswitch.lnk`；
- 用 `-UsePython` 安装的快捷方式，如果移动了项目目录，路径会失效，重装一次；
- 想让程序在登录前就启动 / 延迟启动，改用「任务计划程序」：
  触发器 = 登录时，操作 = 启动 `dualswitch.exe run`，勾选「不管用户是否登录都要运行」会丢失托盘，不建议。

## 7. 内存占用

Python 常驻 + pystray，实测约 25–40MB。需求只要求「以实际观察为准」，未做长期泄漏验证。
如果发现持续增长，`dualswitch run --no-tray` 对比一下，能定位是托盘还是主循环的问题。

## 8. 想临时停用

- 托盘右键 → 退出；
- 或删掉自启快捷方式（`packaging/windows/uninstall_autostart.ps1`）；
- 或把 `hotkey` 改成一个不会按到的组合。
