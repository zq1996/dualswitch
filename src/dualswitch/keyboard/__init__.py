"""键盘跟随切屏的按键通道。

图纸见 `docs/KEYBOARD-BRIDGE.md`。分工：

| 文件 | 端 | 职责 |
|---|---|---|
| `keymap.py` | 共享 | HID usage ↔ macOS virtual keycode、修饰键策略、字符表 |
| `state.py` | 共享 | 按键状态机：按下集合、重复键判据、全量释放 |
| `inject_macos.py` | macOS | **唯一碰 Quartz 的地方**，可注入假实现以便跨平台测试 |
| `receiver.py` | 注入端 | 收 `T_KEY` 帧 → 状态机 → 注入 |

捕获端（`capture_windows.py`）本轮未实现。所以这个包现在只有"收"的一半，
但已经可以用 `tools/key_feeder.py` 端到端验证。

设计上的一条硬规矩：**模块 import 期不许碰系统 API**。`inject_macos` 的 Quartz
是懒加载的，这样非 mac 平台也能 import 本包并跑纯逻辑用例 —— 否则对端跑全量测试
会在收集阶段就炸（同 `sync/` 的处理方式）。
"""

from __future__ import annotations

from .inject_macos import MacInjector, secure_input_enabled
from .keymap import (
    HID_LALT,
    HID_LCTRL,
    HID_LGUI,
    HID_LSHIFT,
    HID_RALT,
    HID_RCTRL,
    HID_RGUI,
    HID_RSHIFT,
    MOD_MAP_POSITION,
    MOD_MAP_SEMANTIC,
    cg_flags,
    is_modifier,
    vk_for,
)
from .receiver import KeyboardReceiver
from .state import KeyEvent, KeyState

__all__ = [
    "HID_LALT",
    "HID_LCTRL",
    "HID_LGUI",
    "HID_LSHIFT",
    "HID_RALT",
    "HID_RCTRL",
    "HID_RGUI",
    "HID_RSHIFT",
    "MOD_MAP_POSITION",
    "MOD_MAP_SEMANTIC",
    "KeyEvent",
    "KeyState",
    "KeyboardReceiver",
    "MacInjector",
    "cg_flags",
    "is_modifier",
    "secure_input_enabled",
    "vk_for",
]
