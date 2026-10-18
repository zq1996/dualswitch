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

这不是「程序崩了」，而是**托盘图标没进通知区域**。先把日志翻到这两行（每次启动都会写）：

```
[INFO] 应用图标：...\assets\screen-switch.ico（尺寸 16x16、32x32、48x48、256x256）
[INFO] 托盘图标（首次注册）：托盘可用=True 图标可见=True 图标为空=False 尺寸=[...] 位置=...
[INFO] 常驻图标检查：可见=True
```

先确认这几行**在哪一次启动里**：`图形界面启动` 下面紧跟的一行是
`可执行文件：...（打包版=True/False，资源目录=...）` —— 排查打包版时最常见的坑是
「拿到的是旧日志 / 跑的是旧 exe」，先对齐这一行再往下看，省掉一整轮来回。

| 日志表现 | 含义 | 处理 |
|---|---|---|
| `图标为空=True` | `.ico` 没读出来（Qt 缺 `imageformats/qico` 插件，或 `assets/` 没打进包） | 现在会自动换「代码画的图标」并记 ERROR；重打包时确认 `--add-data ...assets;dualswitch\ui\assets` 生效 |
| `可见=False` | 图标压根没注册给系统 | 程序 2.5 秒后会自动重注册一次；仍失败说明被安全软件/远程桌面拦了 |
| `位置=未知` | **注册了但系统没给它留格子**（`is_visible()` 是 Qt 自己的标记，不可信 | 3 秒后自动重注册；仍为空见下面「缓存坏了」 |
| `托盘可用=False` | 这台机器没有通知区域 | 程序会自动改成「关窗口 = 最小化」，不再隐藏（否则没有任何入口） |
| 三项都正常但桌面上看不到 | 图标被系统关掉/收进「隐藏的图标」 | 见下面两节 |

> 判定口径：**只看 `位置`，不要看 `可见`**。Windows 打包版实测过
> `可见=True 图标为空=False 尺寸齐全` 但托盘里什么都没有 —— Qt 的 `isVisible()`
> 只表示「我调用过 show()」，注册失败照样 True；`geometry()` 取自系统的
> `Shell_NotifyIconGetRect`，拿不到矩形就是通知区域里没有这一格。

### 4.1 系统把这个 exe 的托盘图标关掉了

设置 → 个性化 → 任务栏 → 通知区域 → **「选择哪些图标显示在任务栏上」** →
找到 `dualswitch` / 「屏幕切换」，把开关打成 **开**。

> 这一项是**按可执行文件路径**记的：`pythonw.exe`（`start-ui.bat` 那条路）早就被允许，
> 所以源码方式正常；新打出来的 `dualswitch.exe` 是"新应用"，可能带着「关」的状态。

### 4.2 通知区域缓存坏了（图标"消失"但注册成功）

只清本程序的记录，不动别的应用的托盘偏好：

```powershell
Stop-Process -Name dualswitch -Force -ErrorAction SilentlyContinue
Get-ChildItem "HKCU:\Control Panel\NotifyIconSettings" | ForEach-Object {
    $p = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).ExecutablePath
    if ($p -like "*dualswitch.exe") { Write-Host "删除缓存项：$p"; Remove-Item $_.PSPath -Recurse -Force }
}
Stop-Process -Name explorer -Force   # 任务栏会自动重启
```

然后重新启动程序。`NotifyIconSettings` 里存着每个 exe 的托盘图标快照与显示偏好，
换构建后偶尔会留下一个空快照 —— 表现正是「进程在、注册说成功、托盘里没有」。

> 另一种情况是图标被收进了 `^` 溢出面板（Win11 对新 exe 默认如此）：点 `^` 拖出来一次就固定了。

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
