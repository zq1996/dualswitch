"""热键解析与后端抽象。

热键字符串语法：`修饰键+修饰键+主键`，大小写不敏感，例如
    alt+space        （Windows 默认；RegisterHotKey 不区分左右 Alt）
    ralt+space       （严格右 Alt，需要低级键盘钩子）
    ctrl+shift+f9
    cmd+option+space （macOS 写法，cmd 等价 win，option 等价 alt）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


class HotkeyError(RuntimeError):
    """热键解析失败 / 注册失败 / 缺少系统权限。"""


# 修饰键别名 -> 规范名
_MODIFIER_ALIASES = {
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "lalt": "alt",
    "leftalt": "alt",
    "left_alt": "alt",
    "ralt": "ralt",
    "rightalt": "ralt",
    "right_alt": "ralt",
    "rightoption": "ralt",
    "right_option": "ralt",
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "win": "win",
    "cmd": "win",
    "command": "win",
    "super": "win",
    "meta": "win",
}

# 主键别名 -> 规范名
_KEY_ALIASES = {
    "space": "space",
    "spacebar": "space",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "esc": "escape",
    "escape": "escape",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pgup": "pageup",
    "pagedown": "pagedown",
    "pgdn": "pagedown",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "`": "`",
    "-": "-",
    "=": "=",
    "[": "[",
    "]": "]",
    "\\": "\\",
    ";": ";",
    "'": "'",
    ",": ",",
    ".": ".",
    "/": "/",
}

_PUNCT = {"`", "-", "=", "[", "]", "\\", ";", "'", ",", ".", "/"}


def _normalize_key(token: str) -> str:
    low = token.strip().lower()
    if low in _KEY_ALIASES:
        return _KEY_ALIASES[low]
    if len(low) == 1 and (low.isalpha() or low.isdigit() or low in _PUNCT):
        return low
    if low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        return low
    raise HotkeyError(f"无法识别的主键: {token!r}")


@dataclass(frozen=True)
class Hotkey:
    modifiers: tuple[str, ...]
    key: str

    @property
    def strict_right_alt(self) -> bool:
        return "ralt" in self.modifiers

    @property
    def has_alt(self) -> bool:
        return "alt" in self.modifiers or "ralt" in self.modifiers

    def describe(self) -> str:
        names = {"alt": "Alt", "ralt": "Right Alt", "ctrl": "Ctrl", "shift": "Shift", "win": "Win"}
        parts = [names.get(m, m.title()) for m in self.modifiers]
        parts.append(self.key.upper() if len(self.key) == 1 else self.key.title())
        return "+".join(parts)

    def mac_describe(self) -> str:
        names = {"alt": "Option", "ralt": "Right Option", "ctrl": "Control", "shift": "Shift", "win": "Command"}
        parts = [names.get(m, m.title()) for m in self.modifiers]
        parts.append("Space" if self.key == "space" else self.key.upper() if len(self.key) == 1 else self.key.title())
        return "+".join(parts)


def parse_hotkey(spec: str) -> Hotkey:
    if not isinstance(spec, str) or not spec.strip():
        raise HotkeyError("热键不能为空")
    raw = spec.replace(" ", "")
    tokens = [t for t in raw.split("+") if t]
    if not tokens:
        raise HotkeyError(f"热键格式错误: {spec!r}")

    modifiers: list[str] = []
    key: str | None = None
    for token in tokens:
        low = token.lower()
        if low in _MODIFIER_ALIASES:
            name = _MODIFIER_ALIASES[low]
            if name in modifiers:
                raise HotkeyError(f"修饰键重复: {token!r}")
            modifiers.append(name)
        else:
            if key is not None:
                raise HotkeyError(f"只允许一个主键，多出了 {token!r}")
            key = _normalize_key(token)

    if key is None:
        raise HotkeyError(f"热键缺少主键: {spec!r}")
    if not modifiers:
        raise HotkeyError("热键至少需要一个修饰键（例如 alt+space）")
    # 顺序固定，便于日志稳定
    order = {"ctrl": 0, "alt": 1, "ralt": 2, "shift": 3, "win": 4}
    modifiers.sort(key=lambda m: order.get(m, 9))
    return Hotkey(modifiers=tuple(modifiers), key=key)


# --------------------------------------------------------------------------- #
# Windows 虚拟键码
# --------------------------------------------------------------------------- #
_VK_BASE = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}


def to_vk(key: str) -> int:
    """把规范主键名转成 Windows 虚拟键码。"""
    if key in _VK_BASE:
        return _VK_BASE[key]
    if len(key) == 1 and key.isalpha():
        return ord(key.upper())
    if len(key) == 1 and key.isdigit():
        return ord(key)
    if key.startswith("f") and key[1:].isdigit():
        return 0x70 + int(key[1:]) - 1
    raise HotkeyError(f"无法映射到虚拟键码: {key!r}")


_VK_MODS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}


def to_win_modifiers(hotkey: Hotkey) -> int:
    """RegisterHotKey 的 fsModifiers。ralt 无法区分，退化为 MOD_ALT。"""
    mods = 0
    for m in hotkey.modifiers:
        mods |= _VK_MODS["alt" if m == "ralt" else m]
    return mods


# --------------------------------------------------------------------------- #
# macOS 虚拟键码（Carbon kVK_*）
# --------------------------------------------------------------------------- #
_MAC_KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43,
    "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50,
    "backspace": 51, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "delete": 117, "left": 123, "right": 124, "down": 125, "up": 126,
}


def to_mac_keycode(key: str) -> int:
    try:
        return _MAC_KEYS[key]
    except KeyError as exc:
        raise HotkeyError(f"macOS 上暂不支持主键: {key!r}") from exc


# --------------------------------------------------------------------------- #
# 后端接口
# --------------------------------------------------------------------------- #
@runtime_checkable
class HotkeyBackend(Protocol):
    name: str
    error: str | None

    def start(self) -> None:
        """启动监听。失败抛 HotkeyError（附可操作提示）。"""

    def stop(self) -> None:
        ...

    def describe(self) -> str:
        ...


TriggerCallback = Callable[[], None]
