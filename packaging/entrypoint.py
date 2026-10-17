"""PyInstaller 打包入口（src 布局下需要一个具体脚本文件）。

双击 exe 时不带参数 → 直接进图形界面（这才是"程序"该有的样子）；
带参数时按 CLI 语义执行，例如 `dualswitch.exe probe` / `dualswitch.exe doctor`。
"""

from __future__ import annotations

import sys
import traceback


def _crash_report(exc: BaseException) -> None:
    """windowed 模式没有控制台，异常要落盘 + 弹窗，否则用户只看到"双击没反应"。"""
    try:
        from pathlib import Path

        from dualswitch.config import log_path

        path = Path(log_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n=== 启动失败 ===\n")
            fh.write("".join(traceback.format_exception(exc)))
        detail = f"{type(exc).__name__}: {exc}\n\n日志：{path}"
    except Exception:  # pragma: no cover - 兜底里的兜底
        detail = f"{type(exc).__name__}: {exc}"

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, detail, "屏幕切换 · 启动失败", 0x10)
        except Exception:  # pragma: no cover
            pass


def main() -> int:
    from dualswitch.cli import main as cli_main

    argv = sys.argv[1:] or ["ui"]
    try:
        return int(cli_main(argv) or 0)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 打包后没有控制台，必须自己兜住
        _crash_report(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
