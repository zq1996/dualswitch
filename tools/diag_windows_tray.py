"""Windows 托盘注册现场诊断 —— 不需要打包，直接用解释器跑。

用法（在仓库根目录）：

    .venv\\Scripts\\python.exe tools\\diag_windows_tray.py
    .venv\\Scripts\\python.exe tools\\diag_windows_tray.py --keep 20

它按顺序打印这些东西，出问题基本上一眼能看出断在哪一步：

1. 进程信息：可执行文件、是否提权、会话 ID、`Shell_TrayWnd` 窗口是否存在
   （`Shell_TrayWnd = 0` 就等于「这个会话里没有任务栏」，托盘图标必然加不进去）
2. `LoadImage` 能不能把包里的 .ico 读成 HICON
3. `Shell_NotifyIcon(NIM_ADD)` 的返回值 **和 GetLastError()**
4. 加进去之后 `Shell_NotifyIconGetRect` 报的位置（拿不到 = 被藏进 `^` 溢出区）
5. 收尾：`NIM_DELETE`

`--keep N`：图标保留 N 秒再删，方便你盯着托盘看它到底出不出来。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _process_info() -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    sid = wintypes.DWORD()
    kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    try:
        elevated = bool(shell32.IsUserAnAdmin())
    except Exception:
        elevated = "?"

    tray_wnd = user32.FindWindowW("Shell_TrayWnd", None)
    shell_wnd = user32.GetShellWindow()
    print(f"可执行文件  : {sys.executable}")
    print(f"会话 ID     : {sid.value}（0 = 服务会话，托盘不可能工作）")
    print(f"是否提权    : {elevated}")
    print(f"Shell_TrayWnd: 0x{tray_wnd:X}（0 = 这个会话里没有任务栏）")
    print(f"GetShellWindow: 0x{shell_wnd:X}（0 = 桌面上没有 shell 窗口）")


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        print("仅在 Windows 上有意义（当前平台：%s）" % sys.platform)
        return 2

    parser = argparse.ArgumentParser(description="Windows 托盘注册诊断")
    parser.add_argument("--keep", type=float, default=8.0, help="图标保留多少秒（默认 8）")
    parser.add_argument("--icon", default="", help="指定 .ico 路径（默认用包里的）")
    args = parser.parse_args(argv)

    _process_info()
    print("-" * 60)

    icon = Path(args.icon) if args.icon else ROOT / "src" / "dualswitch" / "ui" / "assets" / "screen-switch.ico"
    print(f"图标文件    : {icon}（存在={icon.exists()}）")

    from dualswitch.ui.chrome import win32tray

    item = win32tray.try_create(icon, lambda: print("（双击回调触发）"))
    if item is None:
        print("结论：托盘注册失败，具体错误码看上面 [dualswitch.tray] 的日志行。")
        return 1

    print(f"NIM_ADD 结果: 成功；is_visible={item.is_visible()} 位置={item.geometry() or '未知（可能被藏进 ^ 溢出区）'}")
    print(f"图标会在托盘里保留 {args.keep:.0f} 秒 —— 现在去看一眼任务栏右下角（别忘了点 ^）。")
    try:
        time.sleep(args.keep)
    except KeyboardInterrupt:
        print("\n提前结束。")
    item.hide()
    print("已清理（NIM_DELETE）。")
    print("-" * 60)
    print("判读：")
    print("  ① 这里能出图标、打包版不能 → 问题在打包/进程环境，不在代码；")
    print("  ② 这里也出不来但 NIM_ADD 返回成功 → 图标被藏起来了，去「选择哪些图标显示在任务栏上」找；")
    print("  ③ NIM_ADD 失败 → 看 GetLastError：5=拒绝访问（安全软件/策略），1400=窗口句柄无效，1460=超时")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
