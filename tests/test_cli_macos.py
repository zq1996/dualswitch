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


def test_serve_switch_request_goes_through_controller():
    """对端请我切屏必须走控制器（先恢复被停用的屏），不能直连后端。

    2026-11-14 事故：Windows 端切回 Mac 时，Mac 的外接屏被省电模块停用，
    `backend.set_input` 直接报「Could not find a suitable external display」，
    盘面再也没回来。走控制器才会先 `before_switch_to_local()` 恢复屏。
    """
    from dualswitch.cli import serve_switch_request
    from dualswitch.config import from_dict

    cfg = from_dict({"sources": {"windows": 17, "mac": 18}})

    class _Backend:
        def set_input(self, value):          # 直连后端就是这条错误路径
            raise AssertionError("不能绕过控制器直接发 Set")

    class _Controller:
        def __init__(self):
            self.calls: list[tuple[int, str]] = []

        def set_input_local(self, value, source="ui"):
            self.calls.append((value, source))

            class _R:
                ok = True
                error = ""

            return _R()

    ctl = _Controller()
    ok, detail = serve_switch_request(cfg, _Backend(), ctl, 18)

    assert (ok, detail) == (True, "")
    assert ctl.calls == [(18, "remote")]


def test_serve_switch_request_reports_controller_failure():
    from dualswitch.cli import serve_switch_request
    from dualswitch.config import from_dict

    cfg = from_dict({"sources": {"windows": 17, "mac": 18}})

    class _Controller:
        def set_input_local(self, value, source="ui"):
            class _R:
                ok = False
                error = "屏已经被停用"

            return _R()

    ok, detail = serve_switch_request(cfg, object(), _Controller(), 18)
    assert ok is False and detail == "屏已经被停用"
