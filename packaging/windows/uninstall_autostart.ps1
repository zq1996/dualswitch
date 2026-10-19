# 取消 Windows 开机自启

$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "dualswitch.lnk"

if (Test-Path $lnkPath) {
    Remove-Item $lnkPath -Force
    Write-Host "已删除：$lnkPath"
} else {
    Write-Host "未找到自启快捷方式，无需处理。"
}
