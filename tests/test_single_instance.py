"""单实例保护：命名互斥量能分辨「已经有一个在跑」。

只测锁原语本身（纯 Win32 调用）。管道那半边要跨进程，交给手测：
`ui3` 连开两次，第二次应当只打印「已经在运行」并退出 0。
"""

from __future__ import annotations

import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="命名互斥量只在 Windows 上实现")

from dualswitch import single_instance  # noqa: E402


def test_mutex_sees_second_instance():
    """同名互斥量第二次创建会拿到 ERROR_ALREADY_EXISTS —— 这就是「已有一个在跑」。

    用随机名字：用真名的话，界面开着时跑测试会互相干扰（2026-11-08 实测）。
    """
    name = f"dualswitch-test-{uuid.uuid4().hex}"
    assert single_instance._already_running(name) is False
    assert single_instance._already_running(name) is True


def test_acquire_takes_and_releases_slot():
    """抢到名额返回 server；进程内再抢一次返回 None（互斥量已被自己持有）。"""
    name = f"dualswitch-test-{uuid.uuid4().hex}"
    server = single_instance.acquire(name)
    assert server is not None
    assert single_instance.acquire(name) is None
