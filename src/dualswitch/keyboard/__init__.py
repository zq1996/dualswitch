"""键盘跟随切屏的按键通道。

图纸见 `docs/KEYBOARD-BRIDGE.md`。分工：

| 文件 | 端 | 职责 |
|---|---|---|
| `keymap.py` | 共享 | HID usage ↔ macOS kVK / Windows VK、修饰键策略、字符表 |
| `state.py` | 共享 | 按键状态机：按下集合、重复键判据、全量释放 |
| `inject_macos.py` | macOS | **唯一碰 Quartz 的地方**，可注入假实现以便跨平台测试 |
| `receiver.py` | 注入端（macOS） | 收 `T_KEY` 帧 → 状态机 → 注入 |
| `hook_windows.py` | Windows | **进程唯一**的 `WH_KEYBOARD_LL` + 分发器（热键与键盘桥共用） |
| `capture_windows.py` | Windows | 捕获 + 吞键闸门 + epoch + 心跳 + 全量释放 |

两端各是一半：**键盘物理挂在 Windows（捕获端），Mac 只负责把按键注回系统**。
方向只此一条 —— 反过来会同时撞上 Windows UIPI 和 macOS 安全输入两条硬伤。

入口是 `create_endpoint()`：按平台返回捕获端或注入端，`cli.py` 只调这一个函数，
不需要知道两端类名。设计上的一条硬规矩：**模块 import 期不许碰系统 API**，
平台专有模块（`inject_macos` / `capture_windows`）只在工厂里按需 import。
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

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
    usage_for_vk,
    vk_for,
)
from .receiver import KeyboardReceiver
from .state import KeyEvent, KeyState

log = logging.getLogger(__name__)


def create_endpoint(
    cfg: Any,
    *,
    hotkey: Any = None,
    on_release: Callable[[], None] | None = None,
    rtt_probe: Callable[[], bool | None] | None = None,
) -> Any:
    """按平台建键盘端点：Windows 捕获端 / macOS 注入端。

    两端接口刻意对齐（`enabled` / `available()` / `describe()` / `set_enabled()` /
    `on_focus()` / `last_rtt_ms`），界面与 `cli.py` 不需要分支。

    注入端**总是**创建（哪怕开关关着）：界面要靠它显示状态、读往返延迟；
    捕获端同理，但只有在 `enabled` 时才装钩子（不吞键时挂个全局钩子是白扰民）。
    """
    kf = getattr(cfg, "keyboard_follow", None)
    if sys.platform == "win32":
        from .capture_windows import KeyboardCapture

        hotkey_vk, hotkey_mods = _chord_vk(getattr(cfg, "hotkey", ""))
        release_vk, release_mods = _chord_vk(
            getattr(kf, "release_chord", "") if kf is not None else ""
        )
        return KeyboardCapture(
            None,
            local_host="windows",
            peer_host="mac",
            hotkey_vk=hotkey_vk,
            hotkey_mods=hotkey_mods,
            release_vk=release_vk,
            release_mods=release_mods,
            key_state_hz=int(getattr(kf, "key_state_hz", 10) or 10),
            enabled=bool(getattr(kf, "enabled", False)),
            on_release_request=on_release,
            rtt_probe=rtt_probe,
        )

    return KeyboardReceiver(
        mod_map=getattr(kf, "mod_map", MOD_MAP_POSITION) if kf is not None else MOD_MAP_POSITION,
        dedupe_repeat=(getattr(kf, "repeat_mode", "auto") == "dedupe"),
        align_caps=bool(getattr(kf, "align_caps", True)),
        enabled=bool(getattr(kf, "enabled", False)),
    )


def _chord_vk(spec: str) -> tuple[int | None, tuple[str, ...]]:
    """`"ctrl+alt+shift+k"` → `(0x4B, ("ctrl", "alt", "shift"))`。

    解析失败返回 `(None, ())` —— 白名单条目失效只会导致"这个组合会被转发"，
    不至于让程序起不来；而配置层已经校验过格式。
    """
    text = (spec or "").strip()
    if not text:
        return None, ()
    try:
        from ..hotkey.base import parse_hotkey, to_vk

        hk = parse_hotkey(text)
        return int(to_vk(hk.key)), tuple(hk.modifiers)
    except Exception:  # noqa: BLE001 - 解析不了就当作没有白名单
        log.debug("白名单组合解析失败：%r", spec, exc_info=True)
        return None, ()


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
    "create_endpoint",
    "is_modifier",
    "secure_input_enabled",
    "usage_for_vk",
    "vk_for",
]
