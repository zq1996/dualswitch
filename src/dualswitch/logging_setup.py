"""日志：按天轮转 + 自动清理（默认只留 3 天），带毫秒时间戳。

## 文件在哪

    macOS:   ~/Library/Application Support/dualswitch/logs/dualswitch.log
    Windows: %APPDATA%/dualswitch/logs/dualswitch.log

（真实路径由 `config.config_dir()` 决定；每次启动第一屏日志里会打出「日志：…」。）

## 记录规则

* 格式：`YYYY-MM-DD HH:MM:SS.mmm LEVEL [logger] message`，级别默认 INFO；
* **事件驱动，没有定时心跳**：切屏、回读、归属判定、剪贴板连接状态变化才写；
* 所以「文件多大」取决于发生了多少事；正常使用一天 1MB 上下，异常时（比如
  对端反复重连刷屏）可能几 MB —— 这正是下面要做轮转和清理的原因。

## 轮转与清理（2026-11-14 起）

* 每天 0 点把 `dualswitch.log` 轮转成 `dualswitch.log.YYYY-MM-DD`，
  只保留最近 `RETENTION_DAYS`（3）份；
* 每次**启动**再扫一遍日志目录：删掉超过 3 天的旧文件，连老版本按大小轮转
  留下的 `dualswitch.log.1/.2/.3` 一起收拾；
* 另加一道总量闸：日志目录总大小超过 `TOTAL_CAP_BYTES`（32MB）时，从最旧的
  开始删，直到降下来；
* **正在写的那个 `dualswitch.log` 永远不删**（删了进程还在往里写，白删）。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import tempfile
import time
from pathlib import Path

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

#: 日志文件名（不含轮转后缀）
LOG_NAME = "dualswitch.log"
#: 保留几天
RETENTION_DAYS = 3
#: 日志目录总大小上限（超过就从最旧的开始删）
TOTAL_CAP_BYTES = 32 * 1024 * 1024

_configured = False


def _file_handler(path: Path, formatter: logging.Formatter) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 按天轮转：TimedRotatingFileHandler 自己在轮转时删掉超过 backupCount 的旧文件
    handler = logging.handlers.TimedRotatingFileHandler(
        path,
        when="midnight",
        backupCount=RETENTION_DAYS,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    return handler


def log_files(directory: str | Path) -> list[Path]:
    """日志目录里的 `dualswitch.log*`（当前文件 + 各代轮转文件），按名字排序。"""
    target = Path(directory)
    try:
        return sorted(p for p in target.glob(f"{LOG_NAME}*") if p.is_file())
    except OSError:
        return []


def purge_old_logs(
    directory: str | Path,
    *,
    days: int = RETENTION_DAYS,
    max_total_bytes: int = TOTAL_CAP_BYTES,
) -> list[Path]:
    """删掉超期 / 超量的旧日志，返回被删的文件。

    两条规则，都**不碰**正在写的 `dualswitch.log`：

    1. 修改时间早于 `days` 天前 → 删；
    2. 剩下的加起来还超过 `max_total_bytes` → 从最旧的开始删，直到降下来。

    任何单个文件删不掉（权限 / 正被占用）都跳过，不影响其它文件，也不抛异常 ——
    清理日志这种收尾工作绝不能挡住程序启动。
    """
    target = Path(directory)
    current = target / LOG_NAME
    cutoff = time.time() - max(0, int(days)) * 86400
    removed: list[Path] = []
    survivors: list[tuple[float, int, Path]] = []

    for path in log_files(target):
        if path.name == LOG_NAME:          # 当前文件：永远留着
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                continue
        else:
            survivors.append((stat.st_mtime, stat.st_size, path))

    try:
        total = current.stat().st_size if current.exists() else 0
    except OSError:
        total = 0
    total += sum(size for _mtime, size, _p in survivors)
    for _mtime, size, path in sorted(survivors):
        if total <= max_total_bytes:
            break
        try:
            path.unlink()
            removed.append(path)
            total -= size
        except OSError:
            continue
    return removed


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
            fallback = Path(tempfile.gettempdir()) / LOG_NAME
            try:
                root.addHandler(_file_handler(fallback, formatter))
            except OSError:
                root.warning("日志文件不可写（%s）：%s，兜底也失败", log_file, exc)
            else:
                root.warning(
                    "默认日志不可写（%s）：%s —— 本次已改写到 %s", log_file, exc, fallback
                )
        else:
            # 启动顺手清一遍超期日志（进程可能一跑几天，中间只有轮转在删当天的备份）
            removed = purge_old_logs(log_file.parent)
            if removed:
                root.info(
                    "清理旧日志 %d 个（保留 %d 天）：%s",
                    len(removed),
                    RETENTION_DAYS,
                    "、".join(p.name for p in removed),
                )

    root.propagate = False
    _configured = True
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"dualswitch.{name}")
