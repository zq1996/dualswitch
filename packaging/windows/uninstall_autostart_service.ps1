# 取消「无需登录的开机自启」（install_autostart_service.ps1 的逆操作）
#
# ⚠️ 本文件必须保存为 **UTF-8 with BOM**（PowerShell 5.1 会把无 BOM 的 UTF-8 当 GBK 读）。

$ErrorActionPreference = "Continue"

foreach ($t in @("DualSwitchBoot", "DualSwitchLogon")) {
    schtasks /Delete /TN $t /F > $null 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "已删除计划任务：$t"
    } else {
        Write-Host "计划任务不存在（跳过）：$t"
    }
}

$dir = Join-Path $env:ProgramData "DualSwitch"
if (Test-Path $dir) {
    Remove-Item $dir -Recurse -Force
    Write-Host "已删除动作脚本目录：$dir"
}

Write-Host "完成。注意：这只撤掉任务计划版自启，启动文件夹快捷方式（install_autostart.ps1 装的）另用 uninstall_autostart.ps1 撤。"
