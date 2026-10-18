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

## 4. 托盘图标不出现

```powershell
dualswitch run --no-console     # 看有没有 [WARN] 未安装 pystray
pip install pystray pillow
```

macOS 用 launchd 启动时菜单栏图标可能不显示 —— 这是非交互进程的限制，
改用 `.app` + 登录项启动。

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
