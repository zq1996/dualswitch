"""`_start_clip_engine` 的启动冒烟。

这个函数把整个引擎启动包在 `try/except` 里，本意是"剪贴板坏了只降级、不影响切屏"。
副作用是：**任何启动期异常都只会变成一行日志**，测试不跑这条路径就永远发现不了。

2026-11-06 就是这样漏掉的：键盘跟随那段把 `log` 写成了局部变量，默认配置下
`python -m dualswitch ui2` 一启动就 UnboundLocalError（还在 except 里再炸一次），
而 pytest 全绿。

所以这里只守两条底线：
1. 默认配置下能拿到引擎（不是 None —— None 意味着某处被 except 吞了）；
2. `clipboard.enabled=false` 时对端代发、切屏前钩子照样挂上（这是该函数文档里
   明确承诺过的行为）。
"""

from __future__ import annotations

import pytest

from dualswitch import cli
from dualswitch.config import Config


class FakeController:
    """只实现 `_start_clip_engine` 会碰到的那几个成员。"""

    def __init__(self) -> None:
        self.before_switch: list = []
        self.remote_switch = None
        self.remote_ready = None
        self.peer_sees_monitor = None
        self.keyboard: object = "未挂载"

    def add_before_switch(self, fn) -> None:  # noqa: ANN001
        self.before_switch.append(fn)

    def set_keyboard(self, receiver) -> None:  # noqa: ANN001
        self.keyboard = receiver


@pytest.fixture
def cfg(tmp_path):
    c = Config()
    c.clipboard.enabled = False     # 别碰真实剪贴板
    c.clipboard.port = 0            # 让内核挑空闲端口，避免和正在跑的应用抢 47823
    c.mouse_switch.enabled = False
    c.keyboard_follow.enabled = False
    return c, tmp_path / "config.json"


def test_engine_starts_with_clipboard_disabled(cfg):
    c, cfg_file = cfg
    controller = FakeController()
    engine = cli._start_clip_engine(c, cfg_file, controller, backend=None)  # noqa: SLF001
    try:
        assert engine is not None, "引擎没起来 —— 这条路径会把失败吞成一行日志"
        assert controller.before_switch, "切屏前钩子没挂上"
        assert controller.remote_switch is not None, "对端代发没挂上"
        assert controller.keyboard is None, "键盘跟随默认关闭，不该建接收器"
    finally:
        if engine is not None:
            engine.stop()


def test_engine_wires_keyboard_receiver_when_enabled(cfg):
    c, cfg_file = cfg
    c.keyboard_follow.enabled = True
    controller = FakeController()
    engine = cli._start_clip_engine(c, cfg_file, controller, backend=None)  # noqa: SLF001
    try:
        assert engine is not None
        receiver = controller.keyboard
        assert receiver is not None, "开了键盘跟随却没把接收器挂到 controller"
        assert hasattr(receiver, "handle_key")
        # 引擎必须把三个回调接上，否则帧进来没人接
        assert engine._on_key == receiver.handle_key            # noqa: SLF001
        assert engine._on_key_state == receiver.handle_state    # noqa: SLF001
        assert engine._on_focus == receiver.handle_focus        # noqa: SLF001
        assert engine.keyboard_stats["received"] == 0
    finally:
        if engine is not None:
            engine.stop()
