"""PyInstaller 打包入口（src 布局下需要一个具体脚本文件）。

双击 exe 时不带参数 → 直接进图形界面（这才是"程序"该有的样子）；
带参数时按 CLI 语义执行，例如 `dualswitch.exe probe` / `dualswitch.exe doctor`。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _crash_report(exc: BaseException) -> None:
    """windowed 模式没有控制台，异常要落盘 + 弹窗，否则用户只看到"双击没反应"。"""
    text = "".join(traceback.format_exception(exc))
    # 先尽量打到 stderr（有控制台时能直接看到完整堆栈）
    try:
        print("\n=== 启动失败 ===\n" + text, file=sys.stderr)
    except Exception:  # pragma: no cover
        pass

    written: str | None = None
    for candidate in _log_paths():
        try:
            from pathlib import Path

            path = Path(candidate)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n=== 启动失败 ===\n" + text)
            written = str(path)
            break
        except Exception:  # pragma: no cover - 换下一个候选位置
            continue

    detail = f"{type(exc).__name__}: {exc}\n\n{text[-1500:]}"
    if written:
        detail += f"\n\n已写入：{written}"

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, detail, "屏幕切换 · 启动失败", 0x10)
        except Exception:  # pragma: no cover
            pass


def _log_paths() -> list[str]:
    """崩溃日志候选位置：默认日志 → exe 所在目录 → 临时目录。"""
    candidates: list[str] = []
    try:
        from dualswitch.config import log_path

        candidates.append(str(log_path()))
    except Exception:  # pragma: no cover
        pass
    try:
        candidates.append(str(Path(sys.executable).with_name("dualswitch-crash.log")))
    except Exception:  # pragma: no cover
        pass
    candidates.append(r"C:\dualswitch-crash.log")
    return candidates


def _selfcheck() -> int:
    """打包自检：确认界面栈真的进了包（构建脚本调用，`--selfcheck`）。

    动态导入（`importlib` + 字符串拼模块名）会被 PyInstaller 静默漏掉：
    源码方式跑得好好的，双击打出来的 .app / .exe 却立刻闪退，日志里只有一句
    `ModuleNotFoundError`。这里把界面相关的模块逐个 import 一遍，缺了就报错退出。
    """
    modules = [
        "dualswitch.ui.chrome.base",
        "dualswitch.ui.chrome.mac",
        "dualswitch.ui.chrome.win",
        "dualswitch.ui.theme",
        "dualswitch.ui.widgets",
        "dualswitch.ui.statusitem",
        "dualswitch.ui.window",
        "dualswitch.ui.settings",
        "dualswitch.ui.runner",
        # 间接导入的三个（runner / 函数内 import）：漏掉它们时双击就是闪退
        "dualswitch.ui.clipboard_page",
        "dualswitch.ui.clipboard_feed",
        "dualswitch.ui.focus_style",
        # 剪贴板同步链路（引擎 + 协议），同样没有顶层入口 import
        "dualswitch.sync.engine",
        "dualswitch.sync.protocol",
        "dualswitch.sync.transport",
    ]
    if sys.platform == "win32":
        # 只存在于 Windows 的兜底托盘：`build_fallback_status_item` 里函数内导入
        modules.append("dualswitch.ui.chrome.win32tray")
        modules.append("dualswitch.clipboard.windows")
        # v2 界面树（Windows 端默认界面，2026-10-22 起与 mac2 同级）。
        # runner 是入口，其余靠它静态 import；这里逐个点名，任何一个漏收都会让
        # 「双击闪退」在自检阶段就暴露（含新增的配对页）。
        modules += [
            "dualswitch.ui.win2",
            "dualswitch.ui.win2.runner",
            "dualswitch.ui.win2.window",
            "dualswitch.ui.win2.widgets",
            "dualswitch.ui.win2.theme",
            "dualswitch.ui.win2.icons",
            "dualswitch.ui.win2.iconlib",
            "dualswitch.ui.win2.switch_page",
            "dualswitch.ui.win2.clipboard_page",
            "dualswitch.ui.win2.settings_page",
            "dualswitch.ui.win2.pairing_page",
            "dualswitch.ui.win2.preview",
            "dualswitch.ui.win2.tray",
            "dualswitch.ui.win2.focus_style",
            "dualswitch.ui.win2.openers",
            "dualswitch.ui.win2.apps",
        ]
    elif sys.platform == "darwin":
        # v2 界面树（macOS 默认界面）。runner 是入口，其余靠它静态 import；
        # 这里逐个点名，任何一个漏收都会让「双击闪退」在自检阶段就暴露。
        modules += [
            "dualswitch.ui.mac2",
            "dualswitch.ui.mac2.runner",
            "dualswitch.ui.mac2.window",
            "dualswitch.ui.mac2.widgets",
            "dualswitch.ui.mac2.theme",
            "dualswitch.ui.mac2.icons",
            "dualswitch.ui.mac2.iconlib",
            "dualswitch.ui.mac2.switch_page",
            "dualswitch.ui.mac2.clipboard_page",
            "dualswitch.ui.mac2.settings_page",
            "dualswitch.ui.mac2.pairing_page",
            "dualswitch.ui.mac2.preview",
            "dualswitch.ui.mac2.statusmenu",
            "dualswitch.ui.mac2.openers",
            "dualswitch.ui.mac2.apps",
        ]
    failures: list[str] = []
    for name in modules:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001 - 自检要把所有问题一次报全
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    if failures:
        print("自检失败：\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1

    from dualswitch.ui import chrome

    print(f"自检通过：{len(modules)} 个界面模块可导入，当前平台外观 = {chrome.current().NAME}")
    return 0


def main() -> int:
    import os

    # 构建脚本的自检入口：只验证导入，不起界面
    if "--selfcheck" in sys.argv[1:]:
        return _selfcheck()

    # 诊断用：设 DUALSWITCH_FAULTDUMP=<文件> 后，若 N 秒内没退出就 dump 所有线程堆栈
    dump = os.environ.get("DUALSWITCH_FAULTDUMP")
    if dump:
        import faulthandler

        after = float(os.environ.get("DUALSWITCH_FAULT_AFTER", "12"))
        with open(dump, "w", encoding="utf-8", buffering=1) as fh:
            faulthandler.dump_traceback_later(after, file=fh, exit=True)

    from dualswitch.cli import main as cli_main

    # 双击启动时用哪套界面：两端都已切到 v2（ui2，宽窗口 + 侧栏 + 配对页）。
    # macOS 2026-10-22 切；Windows 同日跟随（用户拍板「侧栏全跟」，win2 四页齐备）。
    # v1 仍可用 `dualswitch ui` 显式启动，只是不再是双击默认。
    default_ui = "ui2"
    argv = sys.argv[1:] or [default_ui]
    try:
        return int(cli_main(argv) or 0)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 打包后没有控制台，必须自己兜住
        _crash_report(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
