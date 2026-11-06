# 开机自启（无需登录密码）：任务计划程序版
#
# ⚠️ 本文件必须保存为 **UTF-8 with BOM**（PowerShell 5.1 会把无 BOM 的 UTF-8 当 GBK 读，
#    中文注释会让脚本解析失败，详见 build.ps1 顶部说明）。
#
# 和 install_autostart.ps1（启动文件夹快捷方式，登录后才启动）不同，这里用任务计划程序：
#
#   DualSwitchBoot   开机即启动：系统触发（ONSTART），SYSTEM 账户，不存密码、不用登录。
#                    跑无界面引擎（run --no-tray --no-console）：
#                      - 显示器切换（DDC）/ 对端代发切屏，登录前就可用；
#                      - 与 Mac 对端的配对链路（TCP）保持在线。
#                    SYSTEM 看不到用户剪贴板 → 剪贴板内容同步在登录前不生效（链路照常）。
#
#   DualSwitchLogon  用户登录后：先结束后台 SYSTEM 实例，再拉起完整界面（ui2）。
#                    剪贴板同步必须跑在交互会话里，同时避免两个实例抢端口/热键。
#
# 两件事都装好后的行为：开机（未登录）= 引擎已可用；登录后约 5~8 秒 = 完整界面接管。
#
# ⚠️ 装了这个就不要再装 install_autostart.ps1（启动文件夹快捷方式），否则登录后双实例打架。
#
# 用法（管理员 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File install_autostart_service.ps1
#   可选参数：-Exe <dualswitch.exe 路径>  -Config <config.json 路径>
#   不传参数时自动找 ..\..\dist\dualswitch\dualswitch.exe 和 %APPDATA%\dualswitch\config.json
#
# 取消：同目录 uninstall_autostart_service.ps1

param(
    [string]$Exe = "",
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"

# ---- 管理员检查（创建 SYSTEM 任务需要）----
$ident = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$ident).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "请用管理员身份运行：创建 SYSTEM 开机任务需要管理员权限" }

# ---- 定位 exe ----
if (-not $Exe) {
    $cand = Join-Path $PSScriptRoot "..\..\dist\dualswitch\dualswitch.exe"
    if (Test-Path $cand) { $Exe = $cand }
}
if (-not $Exe -or -not (Test-Path $Exe)) { throw "找不到 dualswitch.exe，请用 -Exe <路径> 指定" }
$Exe = (Resolve-Path $Exe).Path

# ---- 定位配置（开机任务以 SYSTEM 运行，%APPDATA% 不指向用户目录，必须显式传路径）----
if (-not $Config) { $Config = Join-Path $env:APPDATA "dualswitch\config.json" }
if (-not (Test-Path $Config)) { throw "配置文件不存在：$Config（请先正常跑一次程序完成配对，再装自启）" }
$Config = (Resolve-Path $Config).Path

# ---- 生成动作脚本：放 %ProgramData%\DualSwitch（重建 dist 不会丢）----
$dir = Join-Path $env:ProgramData "DualSwitch"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$bootCmd = Join-Path $dir "boot.cmd"
@"
@echo off
rem DualSwitch 开机引擎（登录前，SYSTEM 会话）。引擎日志在：
rem   C:\Windows\System32\config\systemprofile\AppData\Roaming\dualswitch\logs
"$Exe" run --no-tray --no-console --config "$Config"
"@ | Set-Content -Path $bootCmd -Encoding ASCII

$logonCmd = Join-Path $dir "logon.cmd"
@"
@echo off
rem 结束开机引擎（SYSTEM 实例），换用户会话里的完整界面（ui2）
taskkill /IM dualswitch.exe /F >nul 2>&1
timeout /t 3 /nobreak >nul
start "" "$Exe" ui2
"@ | Set-Content -Path $logonCmd -Encoding ASCII

$logonVbs = Join-Path $dir "logon.vbs"
@"
' 任务计划直接跑 logon.cmd 登录时会闪黑框，套一层 wscript 隐藏窗口
CreateObject("WScript.Shell").Run """$logonCmd""", 0, False
"@ | Set-Content -Path $logonVbs -Encoding ASCII

# ---- 注册两个计划任务 ----
# /TR 引号规则：PS 5.1 往原生程序传 \" 转义引号会被搅乱（实测引号被吞、结尾多个反斜杠，
# schtasks 报「无效参数」）。我们自己的动作脚本固定放 %ProgramData%\DualSwitch\（无空格），
# 所以直接传裸路径最稳；万一 %ProgramData% 本身带空格，用 8.3 短路径兜底。
function _TaskPath([string]$p) {
    if ($p -notmatch ' ') { return $p }
    $fso = New-Object -ComObject Scripting.FileSystemObject
    return $fso.GetFolder((Split-Path -Parent $p)).ShortPath + "\" + (Split-Path -Leaf $p)
}

$trBoot = _TaskPath $bootCmd
schtasks /Create /F /TN "DualSwitchBoot" /SC ONSTART /DELAY 0000:15 /RU SYSTEM /RL HIGHEST /TR $trBoot
if ($LASTEXITCODE -ne 0) { throw "注册 DualSwitchBoot 失败（schtasks 退出码 $LASTEXITCODE）" }

$trLogon = "wscript.exe " + (_TaskPath $logonVbs)
schtasks /Create /F /TN "DualSwitchLogon" /SC ONLOGON /DELAY 0000:05 /RL HIGHEST /TR $trLogon
if ($LASTEXITCODE -ne 0) { throw "注册 DualSwitchLogon 失败（schtasks 退出码 $LASTEXITCODE）" }

Write-Host ""
Write-Host "已注册开机自启（无需登录密码）："
Write-Host "  DualSwitchBoot  开机约 15 秒后启动无界面引擎（SYSTEM，登录前切屏/代发可用）"
Write-Host "  DualSwitchLogon 登录约 5 秒后结束后台实例并拉起完整界面（剪贴板同步在用户会话里恢复）"
Write-Host "动作脚本目录：$dir（exe 挪位置后重跑本脚本即可）"
Write-Host "引擎日志（SYSTEM）：C:\Windows\System32\config\systemprofile\AppData\Roaming\dualswitch\logs"
Write-Host "取消自启：powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\uninstall_autostart_service.ps1`""
