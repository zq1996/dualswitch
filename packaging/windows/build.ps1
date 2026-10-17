# 打包 Windows 可执行程序
# 用法（在项目根目录执行）：
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# 产物：dist\dualswitch\dualswitch.exe（整个文件夹一起拷走 / 建快捷方式）
#
# 默认 --onedir：启动不需要往 %TEMP% 解压，本机实测 onefile 双击有大约三成概率报
# "Could not create temporary directory!"（系统镜像/安全软件间歇性拦截 Temp 建目录）。
# 只有确认目标机器没有这个问题时，才用 -OneFile 换成单文件。

param(
    [string]$Python = "",
    [switch]$Console,
    [switch]$OneFile
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
$layout = if ($OneFile) { "--onefile" } else { "--onedir" }
$target = if ($OneFile) { "$root\dist\dualswitch.exe" } else { "$root\dist\dualswitch\dualswitch.exe" }

& $Python -m PyInstaller `
    --noconfirm --clean `
    --name dualswitch `
    $layout `
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
Write-Host "产物：$target"
Write-Host "下一步："
Write-Host "  $target probe      # 只读探测，不切屏"
Write-Host "  $target run        # 常驻运行（托盘）"
Write-Host "  $target ui         # 图形界面（不带参数默认就是这个）"
Write-Host "  powershell -ExecutionPolicy Bypass -File packaging\windows\install_autostart.ps1 -Exe `"$target`""
