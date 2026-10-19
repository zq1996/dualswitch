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

# ── 翻译裁剪 ───────────────────────────────────────────────────────────────
# Qt 自带 200 个各语言翻译（.qm，约 7MB），本工具只用中文，且代码里没有安装
# QTranslator —— 除 zh_CN 外全是死重量，打出来就删掉。
$qmDir = Join-Path (Split-Path $target -Parent) "_internal\PySide6\Qt\translations"
if (Test-Path $qmDir) {
    $qmAll = @(Get-ChildItem -Path $qmDir -Filter "*.qm")
    $qmDrop = @($qmAll | Where-Object { $_.Name -notlike "*_zh_CN.qm" })
    if ($qmDrop.Count -gt 0) { $qmDrop | Remove-Item -Force }
    Write-Host "翻译只保留中文（zh_CN）：保留 $($qmAll.Count - $qmDrop.Count) 个，删除 $($qmDrop.Count) 个 .qm"
} else {
    Write-Host "[warn] 没找到 $qmDir，跳过翻译裁剪"
}

# ── 完整性标签 ─────────────────────────────────────────────────────────────
# 托盘图标靠 Shell_NotifyIcon 交给 explorer。Windows 不允许**低完整性级别**的进程
# 向更高级别的窗口发消息（UIPI），而本机 explorer 是提权运行的（High）。
# 如果仓库所在的盘/目录被标成了 Low（例如 D:\work 带 (OI)(CI) 的 Low 标签），
# 打出来的 exe 会继承这个标签 → 进程以 Low 启动 → 托盘图标永远不出现，
# 而且 Qt 不报错、pystray 只抛一句 [WinError 5]。
# 这里把产物的标签抬到 High：High 不会提升进程级别（进程级别 = min(用户, 文件)），
# 只是保证它不会被文件标签压在 explorer 之下。
$outDir = Split-Path $target -Parent
if (-not $OneFile) {
    & icacls $outDir /setintegritylevel "(OI)(CI)High" /T /C | Out-Null
    & icacls $target | Select-String "Mandatory" | ForEach-Object { Write-Host "完整性标签：$($_.Line.Trim())" }
} else {
    & icacls $target /setintegritylevel High /C | Out-Null
}

Write-Host ""
Write-Host "产物：$target"
Write-Host "下一步："
Write-Host "  $target probe      # 只读探测，不切屏"
Write-Host "  $target run        # 常驻运行（托盘）"
Write-Host "  $target ui         # 图形界面（不带参数默认就是这个）"
Write-Host "  powershell -ExecutionPolicy Bypass -File packaging\windows\install_autostart.ps1 -Exe `"$target`""
