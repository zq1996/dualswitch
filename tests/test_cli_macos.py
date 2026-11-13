"""SIGTERM / SIGHUP 退出路径（macOS 专有）。

`cli._install_term_handler` 在 win32 上直接返回（Windows 用信号的方式不一样），
所以这个文件的用例只在 darwin 上有意义。

**为什么单独测这个**：退出路径看着无聊，但它是 2026-11-13 那次「用例全绿、进程
却 exit=139」的现场。handler 是在 Qt 的 C++ 事件派发里被调起来的，`raise
SystemExit` 一出来 PySide 就直接进解释器退出，挨个析构 Python 侧的 QWidget ——
父对象先被销毁的那个 SIGSEGV，还在 atexit 里刷一串
`Error calling Python override of QObject::eventFilter` 递归。
所以退出必须由我们自己「恢复外接屏 → 冲刷日志 → os._exit」，不把控制权交回去。
"""

from __future__ import annotations

import os
import signal
import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("SIGTERM 处理器只在 POSIX 上安装", allow_module_level=True)

from dualswitch import cli  # noqa: E402


def test_term_handler_restores_display_then_hard_exits(monkeypatch):
    captured: dict[str, object] = {}

    def fake_signal(signum, handler):
        captured[signum] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(os, "_exit", lambda code: captured.setdefault("exit", code))
    monkeypatch.setattr(cli.logging, "shutdown", lambda: captured.setdefault("flushed", True))

    import dualswitch.display_power as dp

    restored: list[bool] = []
    monkeypatch.setattr(dp, "restore_pending", lambda: restored.append(True))

    cli._install_term_handler()
    handler = captured[signal.SIGTERM]
    handler(signal.SIGTERM, None)

    assert restored == [True], "信号退出前必须先把外接屏恢复（否则留一块黑屏）"
    assert captured.get("flushed") is True, "os._exit 不走 atexit，日志得先冲刷"
    assert captured.get("exit") == 128 + int(signal.SIGTERM), (
        "没走 os._exit —— 交回解释器后 PySide 会析构全部 QWidget，父对象先死的那个 SIGSEGV"
    )
