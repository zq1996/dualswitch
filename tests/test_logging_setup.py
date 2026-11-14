"""日志轮转与自动清理（`logging_setup`）。

2026-11-14 用户要求「留 3 天就够，别把日志堆成山」。规则见 `logging_setup` 模块注释：
按天轮转 + 启动时清理超期/超量的旧文件，**正在写的那个永远不删**。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import time

import pytest

from dualswitch import logging_setup as ls


@pytest.fixture
def clean_root():
    """`setup()` 是全局单次的：用例前后把根 logger 的 handler 和开关都还原。"""
    root = logging.getLogger("dualswitch")
    saved_handlers = list(root.handlers)
    saved_configured = ls._configured
    root.handlers.clear()
    ls._configured = False
    try:
        yield root
    finally:
        for handler in list(root.handlers):
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass
        root.handlers.clear()
        root.handlers.extend(saved_handlers)
        ls._configured = saved_configured


def _make_log(directory, name: str, days_old: float, size: int = 10):
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - days_old * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_purges_files_older_than_retention(tmp_path):
    current = _make_log(tmp_path, ls.LOG_NAME, days_old=0)
    old_by_size = _make_log(tmp_path, f"{ls.LOG_NAME}.1", days_old=4)     # 老版本按大小轮转的残留
    old_by_date = _make_log(tmp_path, f"{ls.LOG_NAME}.2026-11-09", days_old=5)
    fresh = _make_log(tmp_path, f"{ls.LOG_NAME}.2026-11-13", days_old=1)

    removed = ls.purge_old_logs(tmp_path, days=3)

    assert set(removed) == {old_by_size, old_by_date}
    assert current.exists() and fresh.exists()
    assert not old_by_size.exists() and not old_by_date.exists()


def test_never_deletes_the_file_being_written(tmp_path):
    """当前文件哪怕很久没写过（程序没跑）也不能删 —— 正在写的流还指着它。"""
    current = _make_log(tmp_path, ls.LOG_NAME, days_old=30)
    assert ls.purge_old_logs(tmp_path, days=3) == []
    assert current.exists()


def test_total_cap_deletes_oldest_first(tmp_path):
    _make_log(tmp_path, ls.LOG_NAME, days_old=0, size=100)
    newest = _make_log(tmp_path, f"{ls.LOG_NAME}.2026-11-13", days_old=1, size=100)
    middle = _make_log(tmp_path, f"{ls.LOG_NAME}.2026-11-12", days_old=2, size=100)
    oldest = _make_log(tmp_path, f"{ls.LOG_NAME}.2026-11-11", days_old=2.5, size=100)

    removed = ls.purge_old_logs(tmp_path, days=3, max_total_bytes=210)

    assert removed == [oldest, middle]        # 从最旧的开始删
    assert newest.exists()
    assert ls.purge_old_logs(tmp_path, days=3, max_total_bytes=10**9) == []


def test_setup_rotates_daily_and_keeps_three(tmp_path, clean_root):
    _make_log(tmp_path, f"{ls.LOG_NAME}.2026-11-01", days_old=10)   # 本该被清掉
    log_path = tmp_path / ls.LOG_NAME

    ls.setup("INFO", log_path, console=False)

    handlers = [h for h in clean_root.handlers if isinstance(h, logging.FileHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == ls.RETENTION_DAYS == 3
    assert not (tmp_path / f"{ls.LOG_NAME}.2026-11-01").exists(), "启动时要顺手清掉超期日志"


def test_retention_is_three_days():
    """用户口径：留 3 天够用（改这里之前先问）。"""
    assert ls.RETENTION_DAYS == 3
