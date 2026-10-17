# 打包 Windows 单文件可执行程序
# 用法（在项目根目录执行）：
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# 产物：dist\dualswitch.exe

param(
    [string]$Python = "",
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

if (-not $Python) {
    $candidates = @(
        "C:\Users\Administrator\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe",
        (Join-Path $root ".venv\Scripts\python.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $Python = $c; break }
    }
}
if (-not $Python -or -not (Test-Path $Python)) {
    throw "找不到 Python 解释器，请用 -Python <路径> 指定"
}

Write-Host "使用 Python: $Python"
& $Python -m pip install --quiet --upgrade pyinstaller pystray pillow PySide6-Essentials

$mode = if ($Console) { "--console" } else { "--windowed" }

& $Python -m PyInstaller `
    --noconfirm --clean `
    --name dualswitch `
    --onefile `
    $mode `
    --paths "$root\src" `
    --icon "$root\design\icon\screen-switch.ico" `
    --version-file "$root\packaging\windows\version_info.txt" `
    --add-data "$root\src\dualswitch\ui\assets;dualswitch\ui\assets" `
    --hidden-import "pystray._win32" `
    --hidden-import "PIL.ImageDraw" `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root\build" `
    "$root\packaging\entrypoint.py"

if ($LASTEXITCODE -ne 0) { throw "打包失败" }

Write-Host ""
Write-Host "产物：$root\dist\dualswitch.exe"
Write-Host "下一步："
Write-Host "  .\dist\dualswitch.exe probe      # 只读探测，不切屏"
Write-Host "  .\dist\dualswitch.exe run        # 常驻运行（托盘）"
Write-Host "  .\dist\dualswitch.exe ui         # 图形界面"
Write-Host "  powershell -ExecutionPolicy Bypass -File packaging\windows\install_autostart.ps1 -Exe .\dist\dualswitch.exe"
