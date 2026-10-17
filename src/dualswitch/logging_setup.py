"""日志：文件轮转 + 控制台，带毫秒时间戳。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured = False


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
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:  # 日志不可写不应导致程序起不来
            root.warning("日志文件不可写（%s）：%s", log_file, exc)

    root.propagate = False
    _configured = True
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"dualswitch.{name}")
