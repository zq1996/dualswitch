"""完整性级别（UIPI）相关的判断。

背景：托盘图标靠 `Shell_NotifyIcon` 交给 explorer，Windows 不允许低完整性级别的
进程向更高级别的窗口发消息。2026-10-18 实测：打包目录带 `Low Mandatory Level`
标签 → exe 以 Low 运行 → 图标永远不出现（Qt 不报错，pystray 报 WinError 5）。
"""

from __future__ import annotations

import sys

import pytest

from dualswitch import winenv


def test_describe_known_levels():
    assert winenv.describe(winenv.RID_LOW).startswith("Low")
    assert winenv.describe(winenv.RID_MEDIUM).startswith("Medium")
    assert winenv.describe(winenv.RID_HIGH).startswith("High")
    assert winenv.describe(None) == "未知"
    assert "0x1234" in winenv.describe(0x1234)          # 未知级别也要可读


@pytest.mark.parametrize(
    "ours, shell, expected",
    [
        (winenv.RID_LOW, winenv.RID_HIGH, True),
        (winenv.RID_MEDIUM, winenv.RID_HIGH, True),     # 本机 explorer 是提权的 High
        (winenv.RID_HIGH, winenv.RID_HIGH, False),
        (winenv.RID_HIGH, winenv.RID_MEDIUM, False),
        (None, winenv.RID_HIGH, False),                 # 读不到就别下结论
        (winenv.RID_LOW, None, False),
    ],
)
def test_blocked_predicate(ours, shell, expected):
    assert winenv.blocked(ours, shell) is expected


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 有完整性级别")
def test_integrity_level_readable():
    """当前进程的级别要读得出来（普通用户 Medium、提权 High、受限环境 Low）。"""
    rid = winenv.integrity_level()
    assert rid is None or isinstance(rid, int)
    shell = winenv.shell_integrity_level()
    assert shell is None or isinstance(shell, int)


def test_tray_restriction_shape():
    is_blocked, reason = winenv.tray_restriction()
    assert isinstance(is_blocked, bool)
    assert isinstance(reason, str)
    if is_blocked:                                       # 被挡时必须给出可操作说明
        assert "完整性级别" in reason or "低于资源管理器" in reason


def test_fix_hint_mentions_setintegritylevel():
    hint = winenv.fix_hint()
    assert "setintegritylevel" in hint and "icacls" in hint


# --------------------------------------------------------------------------- #
# 运行时的两条分支：级别不够时不做无用功
# --------------------------------------------------------------------------- #
class _StubRunner:
    """只带上 `_verify_status_item` / `_switch_to_fallback_status` 需要的字段。"""

    _quitting = False
    _tray_fallback = False
    _tray_blocked = True
    _tray_block_reason = "测试：级别低于资源管理器"

    def __init__(self) -> None:
        self.retries: list[int] = []

    def _retry_status_item(self, attempt: int) -> None:  # noqa: ANN001
        self.retries.append(attempt)

    def _status_geometry(self) -> str:                   # pragma: no cover - 不该被调用
        raise AssertionError("级别不够时不该去问位置")


def test_verify_skips_retries_when_integrity_blocked():
    from dualswitch.ui.runner import UiRunner

    stub = _StubRunner()
    UiRunner._verify_status_item(stub)                    # 不抛异常、不重试
    assert stub.retries == [], "级别不够时重试没有意义，Shell_NotifyIcon 一定被挡"


def test_fallback_not_switched_when_integrity_blocked():
    """pystray 也要走 Shell_NotifyIcon，级别不够时换过去只会更糟（还会藏掉 Qt 图标）。"""
    from dualswitch.ui.runner import UiRunner

    stub = _StubRunner()
    assert UiRunner._switch_to_fallback_status(stub) is False


def test_pystray_item_reports_startup_failure():
    """兜底实现起不来时要说出来，别让日志写着「已启动」而图标一个没有。"""
    from dualswitch.ui.chrome.win import PystrayStatusItem

    item = object.__new__(PystrayStatusItem)             # 不跑 __init__，不真起托盘
    item._error = "PermissionError: [WinError 5] 拒绝访问"

    class _Icon:
        visible = True

    item._icon = _Icon()
    assert item.failed is True
    assert item.is_visible() is False, "起不来就不能报可见"
