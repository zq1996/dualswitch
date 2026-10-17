# 开机自启（Windows）：在「启动」文件夹放一个快捷方式
#
# 两种用法：
#   1) 打包后： powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Exe "D:\apps\dualswitch.exe"
#   2) 源码运行：powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -UsePython
#
# 取消：同目录 uninstall_autostart.ps1

param(
    [string]$Exe = "",
    [switch]$UsePython,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "dualswitch.lnk"

if ($Exe) {
    $target = (Resolve-Path $Exe).Path
    $arguments = "run"
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
    # -m 方式启动需要 src 在 PYTHONPATH 里；这里直接用 cmd 设置环境再启动，避免污染全局
    $arguments = "-c `"import sys; sys.path.insert(0, r'$root\src'); from dualswitch.cli import main; sys.exit(main(['run']))`""
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
$lnk.WindowStyle = 7   # 最小化，避免弹黑框
$lnk.Description = "dualswitch 双系统显示器切换助手"
$lnk.Save()

Write-Host "已创建开机自启快捷方式：$lnkPath"
Write-Host "目标：$target $arguments"
Write-Host "如需取消：powershell -ExecutionPolicy Bypass -File uninstall_autostart.ps1"
