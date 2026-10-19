# 开机自启（Windows）：在「启动」文件夹放一个快捷方式
#
# ⚠️ 本文件必须保存为 **UTF-8 with BOM**（PowerShell 5.1 会把无 BOM 的 UTF-8 当 GBK 读，
#    中文注释会让脚本解析失败，详见 build.ps1 顶部说明）。
#
# 默认启动**完整界面**（`ui`：托盘 + 主窗口 + 剪贴板页 + 设置页），与 mac 的登录项
# 启动 `.app` 一致。以前这里传的是 `run`（pystray 老托盘，没有任何窗口）——
# 自启后用户只能看到一个旧托盘，新界面得自己去双击，等于两端行为不一致。
#
# 三种用法：
#   1) 打包后： powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Exe "D:\apps\dualswitch\dualswitch.exe"
#   2) 源码运行：powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -UsePython
#   3) 只要托盘不要窗口：上面任一命令再加 -TrayOnly
#
# 其它开关：
#   -Minimized      登录后窗口最小化（不抢焦点），托盘图标照常在
#   -Python <路径>   源码模式下手动指定解释器
#
# 取消：同目录 uninstall_autostart.ps1

param(
    [string]$Exe = "",
    [switch]$UsePython,
    [string]$Python = "",
    [switch]$TrayOnly,
    [switch]$Minimized
)

$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "dualswitch.lnk"

# `ui` = 完整界面（不带参数默认也是它，这里写出来是为了让快捷方式自解释）
# `run` = 无窗口常驻（pystray 托盘 + 热键），只有 -TrayOnly 时才用
$mode = if ($TrayOnly) { "run" } else { "ui" }
$windowStyle = if ($TrayOnly -or $Minimized) { 7 } else { 1 }   # 7 = 最小化，1 = 正常

if ($Exe) {
    $target = (Resolve-Path $Exe).Path
    $arguments = $mode
    $workdir = Split-Path -Parent $target
}
elseif ($UsePython) {
    if (-not $Python) {
        $candidates = @(
            "C:\Users\Administrator\.workbuddy-ai\binaries\python\envs\default\Scripts\pythonw.exe",
            (Join-Path (Split-Path -Parent $PSScriptRoot) "..\.venv\Scripts\pythonw.exe")
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) { $Python = (Resolve-Path $c).Path; break }
        }
    }
    if (-not $Python -or -not (Test-Path $Python)) {
        throw "找不到 pythonw.exe，请用 -Python <路径> 指定"
    }
    $root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $target = $Python
    # -m 方式启动需要 src 在 PYTHONPATH 里；这里直接用 -c 设置环境再启动，避免污染全局
    $arguments = "-c `"import sys; sys.path.insert(0, r'$root\src'); from dualswitch.cli import main; sys.exit(main(['$mode']))`""
    $workdir = $root
}
else {
    throw "请指定 -Exe <dualswitch.exe 路径> 或 -UsePython"
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $target
$lnk.Arguments = $arguments
$lnk.WorkingDirectory = $workdir
$lnk.WindowStyle = $windowStyle
$lnk.Description = if ($mode -eq "ui") { "dualswitch 双系统显示器切换助手（界面）" } else { "dualswitch 双系统显示器切换助手（托盘）" }
$lnk.Save()

Write-Host "已创建开机自启快捷方式：$lnkPath"
Write-Host "目标：$target $arguments"
$modeDesc = if ($mode -eq "ui") { "完整界面 + 托盘" } else { "仅托盘，无窗口" }
Write-Host "模式：$mode（$modeDesc）"
Write-Host "如需取消：powershell -ExecutionPolicy Bypass -File uninstall_autostart.ps1"
