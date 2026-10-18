"""日志：文件轮转 + 控制台，带毫秒时间戳。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import tempfile
from pathlib import Path

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured = False


def _file_handler(path: Path, formatter: logging.Formatter) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    return handler


def setup(level: str = "INFO", path: str | Path | None = None, console: bool = True) -> logging.Logger:
    """初始化根 logger，重复调用安全。"""
    global _configured
    root = logging.getLogger("dualswitch")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    if _configured:
        return root

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    if console:
        # Windows 控制台可能是 GBK，避免中文日志直接抛 UnicodeEncodeError
        stream = sys.stderr
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    if path is not None:
        log_file = Path(path)
        try:
            root.addHandler(_file_handler(log_file, formatter))
        except OSError as exc:
            # 打包版没有控制台：这里一旦静默，程序就变成「有界面、零日志」，
            # 事后排查完全瞎（2026-10-18 在 Windows 打包版上正是栽在这）。
            # 所以退回临时目录再开一份，并把「原路径为什么写不了」记在兜底文件里。
            fallback = Path(tempfile.gettempdir()) / "dualswitch.log"
            try:
                root.addHandler(_file_handler(fallback, formatter))
            except OSError:
                root.warning("日志文件不可写（%s）：%s，兜底也失败", log_file, exc)
            else:
                root.warning(
                    "默认日志不可写（%s）：%s —— 本次已改写到 %s", log_file, exc, fallback
                )

    root.propagate = False
    _configured = True
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"dualswitch.{name}")
