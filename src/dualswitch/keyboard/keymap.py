"""HID usage ↔ macOS virtual keycode，以及修饰键策略。

**为什么按 HID usage 传、不按字符传**：传字符要做布局换算、要处理输入法和死键，
而且输入法候选窗会跑到源端去。传 usage 等于"把这块键盘插到对面的 USB 口上"——
对端输入法在对端原生合成，候选窗出现在你正在看的那块屏上。

`USAGE_TO_VK` 是 USB HID Keyboard page (0x07) → Carbon `kVK_*` 的固定表。
**位置到位置的映射，与两端键盘布局无关**：macOS 的 virtual keycode 本身就是
"物理位置"编码，所以中文布局、法语布局都不影响这张表。
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# HID 修饰键 usage（Keyboard page 0x07，0xE0–0xE7）
# --------------------------------------------------------------------------- #
HID_LCTRL = 0xE0
HID_LSHIFT = 0xE1
HID_LALT = 0xE2
HID_LGUI = 0xE3
HID_RCTRL = 0xE4
HID_RSHIFT = 0xE5
HID_RALT = 0xE6
HID_RGUI = 0xE7

MODIFIER_USAGES = frozenset(range(HID_LCTRL, HID_RGUI + 1))

#: HID 修饰键位图（boot protocol 那套，bit0=LCTRL … bit7=RGUI）
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

MOD_MAP_POSITION = "position"
MOD_MAP_SEMANTIC = "semantic"
MOD_MAPS = (MOD_MAP_POSITION, MOD_MAP_SEMANTIC)

# --------------------------------------------------------------------------- #
# macOS virtual keycode（Carbon HIToolbox kVK_*）
# --------------------------------------------------------------------------- #
kVK_ANSI_A = 0x00
kVK_ANSI_S = 0x01
kVK_ANSI_D = 0x02
kVK_ANSI_F = 0x03
kVK_ANSI_H = 0x04
kVK_ANSI_G = 0x05
kVK_ANSI_Z = 0x06
kVK_ANSI_X = 0x07
kVK_ANSI_C = 0x08
kVK_ANSI_V = 0x09
kVK_ISO_Section = 0x0A
kVK_ANSI_B = 0x0B
kVK_ANSI_Q = 0x0C
kVK_ANSI_W = 0x0D
kVK_ANSI_E = 0x0E
kVK_ANSI_R = 0x0F
kVK_ANSI_Y = 0x10
kVK_ANSI_T = 0x11
kVK_ANSI_1 = 0x12
kVK_ANSI_2 = 0x13
kVK_ANSI_3 = 0x14
kVK_ANSI_4 = 0x15
kVK_ANSI_6 = 0x16
kVK_ANSI_5 = 0x17
kVK_ANSI_Equal = 0x18
kVK_ANSI_9 = 0x19
kVK_ANSI_7 = 0x1A
kVK_ANSI_Minus = 0x1B
kVK_ANSI_8 = 0x1C
kVK_ANSI_0 = 0x1D
kVK_ANSI_RightBracket = 0x1E
kVK_ANSI_O = 0x1F
kVK_ANSI_U = 0x20
kVK_ANSI_LeftBracket = 0x21
kVK_ANSI_I = 0x22
kVK_ANSI_P = 0x23
kVK_Return = 0x24
kVK_ANSI_L = 0x25
kVK_ANSI_J = 0x26
kVK_ANSI_Quote = 0x27
kVK_ANSI_K = 0x28
kVK_ANSI_Semicolon = 0x29
kVK_ANSI_Backslash = 0x2A
kVK_ANSI_Comma = 0x2B
kVK_ANSI_Slash = 0x2C
kVK_ANSI_N = 0x2D
kVK_ANSI_M = 0x2E
kVK_ANSI_Period = 0x2F
kVK_Tab = 0x30
kVK_Space = 0x31
kVK_ANSI_Grave = 0x32
kVK_Delete = 0x33
kVK_Escape = 0x35
kVK_RightCommand = 0x36
kVK_Command = 0x37
kVK_Shift = 0x38
kVK_CapsLock = 0x39
kVK_Option = 0x3A
kVK_Control = 0x3B
kVK_RightShift = 0x3C
kVK_RightOption = 0x3D
kVK_RightControl = 0x3E
kVK_Function = 0x3F
kVK_F17 = 0x40
kVK_ANSI_KeypadDecimal = 0x41
kVK_ANSI_KeypadMultiply = 0x43
kVK_ANSI_KeypadPlus = 0x45
kVK_ANSI_KeypadClear = 0x47
kVK_VolumeUp = 0x48
kVK_VolumeDown = 0x49
kVK_Mute = 0x4A
kVK_ANSI_KeypadDivide = 0x4B
kVK_ANSI_KeypadEnter = 0x4C
kVK_ANSI_KeypadMinus = 0x4E
kVK_F18 = 0x4F
kVK_F19 = 0x50
kVK_ANSI_KeypadEquals = 0x51
kVK_ANSI_Keypad0 = 0x52
kVK_ANSI_Keypad1 = 0x53
kVK_ANSI_Keypad2 = 0x54
kVK_ANSI_Keypad3 = 0x55
kVK_ANSI_Keypad4 = 0x56
kVK_ANSI_Keypad5 = 0x57
kVK_ANSI_Keypad6 = 0x58
kVK_ANSI_Keypad7 = 0x59
kVK_F20 = 0x5A
kVK_ANSI_Keypad8 = 0x5B
kVK_ANSI_Keypad9 = 0x5C
kVK_F5 = 0x60
kVK_F6 = 0x61
kVK_F7 = 0x62
kVK_F3 = 0x63
kVK_F8 = 0x64
kVK_F9 = 0x65
kVK_F11 = 0x67
kVK_F13 = 0x69
kVK_F16 = 0x6A
kVK_F14 = 0x6B
kVK_F10 = 0x6D
kVK_F12 = 0x6F
kVK_F15 = 0x71
kVK_Help = 0x72
kVK_Home = 0x73
kVK_PageUp = 0x74
kVK_ForwardDelete = 0x75
kVK_F4 = 0x76
kVK_End = 0x77
kVK_F2 = 0x78
kVK_PageDown = 0x79
kVK_F1 = 0x7A
kVK_LeftArrow = 0x7B
kVK_RightArrow = 0x7C
kVK_DownArrow = 0x7D
kVK_UpArrow = 0x7E

#: CGEventFlags（NSEventModifierFlag*，设备无关位）
FLAG_ALPHA_SHIFT = 0x00010000
FLAG_SHIFT = 0x00020000
FLAG_CONTROL = 0x00040000
FLAG_OPTION = 0x00080000
FLAG_COMMAND = 0x00100000

# --------------------------------------------------------------------------- #
# usage → kVK
# --------------------------------------------------------------------------- #
USAGE_TO_VK: dict[int, int] = {
    # 字母
    0x04: kVK_ANSI_A, 0x05: kVK_ANSI_B, 0x06: kVK_ANSI_C, 0x07: kVK_ANSI_D,
    0x08: kVK_ANSI_E, 0x09: kVK_ANSI_F, 0x0A: kVK_ANSI_G, 0x0B: kVK_ANSI_H,
    0x0C: kVK_ANSI_I, 0x0D: kVK_ANSI_J, 0x0E: kVK_ANSI_K, 0x0F: kVK_ANSI_L,
    0x10: kVK_ANSI_M, 0x11: kVK_ANSI_N, 0x12: kVK_ANSI_O, 0x13: kVK_ANSI_P,
    0x14: kVK_ANSI_Q, 0x15: kVK_ANSI_R, 0x16: kVK_ANSI_S, 0x17: kVK_ANSI_T,
    0x18: kVK_ANSI_U, 0x19: kVK_ANSI_V, 0x1A: kVK_ANSI_W, 0x1B: kVK_ANSI_X,
    0x1C: kVK_ANSI_Y, 0x1D: kVK_ANSI_Z,
    # 数字
    0x1E: kVK_ANSI_1, 0x1F: kVK_ANSI_2, 0x20: kVK_ANSI_3, 0x21: kVK_ANSI_4,
    0x22: kVK_ANSI_5, 0x23: kVK_ANSI_6, 0x24: kVK_ANSI_7, 0x25: kVK_ANSI_8,
    0x26: kVK_ANSI_9, 0x27: kVK_ANSI_0,
    # 控制键
    0x28: kVK_Return, 0x29: kVK_Escape, 0x2A: kVK_Delete, 0x2B: kVK_Tab,
    0x2C: kVK_Space, 0x2D: kVK_ANSI_Minus, 0x2E: kVK_ANSI_Equal,
    0x2F: kVK_ANSI_LeftBracket, 0x30: kVK_ANSI_RightBracket,
    0x31: kVK_ANSI_Backslash,
    # 0x32 Non-US #~：ISO 布局才有的键，按反斜杠处理
    0x32: kVK_ANSI_Backslash,
    0x33: kVK_ANSI_Semicolon, 0x34: kVK_ANSI_Quote, 0x35: kVK_ANSI_Grave,
    0x36: kVK_ANSI_Comma, 0x37: kVK_ANSI_Period, 0x38: kVK_ANSI_Slash,
    0x39: kVK_CapsLock,
    # 功能键
    0x3A: kVK_F1, 0x3B: kVK_F2, 0x3C: kVK_F3, 0x3D: kVK_F4,
    0x3E: kVK_F5, 0x3F: kVK_F6, 0x40: kVK_F7, 0x41: kVK_F8,
    0x42: kVK_F9, 0x43: kVK_F10, 0x44: kVK_F11, 0x45: kVK_F12,
    # PrintScreen / ScrollLock / Pause 在 mac 上没有对应语义，丢掉
    0x49: kVK_Help,            # Insert
    0x4A: kVK_Home,            # Home
    0x4B: kVK_PageUp,          # PageUp
    0x4C: kVK_ForwardDelete,   # Delete
    0x4D: kVK_End,             # End
    0x4E: kVK_PageDown,        # PageDown
    0x4F: kVK_RightArrow,
    0x50: kVK_LeftArrow,
    0x51: kVK_DownArrow,
    0x52: kVK_UpArrow,
    0x53: kVK_ANSI_KeypadClear,   # NumLock → Clear
}

#: 小键盘单独一张（0x54–0x63），别和主键区混
USAGE_TO_VK.update({
    0x54: kVK_ANSI_KeypadDivide,
    0x55: kVK_ANSI_KeypadMultiply,
    0x56: kVK_ANSI_KeypadMinus,
    0x57: kVK_ANSI_KeypadPlus,
    0x58: kVK_ANSI_KeypadEnter,
    0x59: kVK_ANSI_Keypad1, 0x5A: kVK_ANSI_Keypad2, 0x5B: kVK_ANSI_Keypad3,
    0x5C: kVK_ANSI_Keypad4, 0x5D: kVK_ANSI_Keypad5, 0x5E: kVK_ANSI_Keypad6,
    0x5F: kVK_ANSI_Keypad7, 0x60: kVK_ANSI_Keypad8, 0x61: kVK_ANSI_Keypad9,
    0x62: kVK_ANSI_Keypad0, 0x63: kVK_ANSI_KeypadDecimal,
    0x64: kVK_ISO_Section,     # Non-US backslash（ISO 回车左边那颗）
})

#: 修饰键按策略重映射，不进 USAGE_TO_VK
_MODIFIER_VK = {
    MOD_MAP_POSITION: {
        # 用户 2026-11-06 明确：空格左边那颗（Win 键盘的 Alt）在 mac 上是 ⌘，
        # 这样 Alt+C 就是复制，拇指位置对得上。
        HID_LCTRL: kVK_Control,
        HID_RCTRL: kVK_RightControl,
        HID_LSHIFT: kVK_Shift,
        HID_RSHIFT: kVK_RightShift,
        HID_LALT: kVK_Command,
        HID_RALT: kVK_RightCommand,
        HID_LGUI: kVK_Option,
        HID_RGUI: kVK_RightOption,
    },
    MOD_MAP_SEMANTIC: {
        # 按名字：系统键 Win → ⌘，Alt → ⌥
        HID_LCTRL: kVK_Control,
        HID_RCTRL: kVK_RightControl,
        HID_LSHIFT: kVK_Shift,
        HID_RSHIFT: kVK_RightShift,
        HID_LALT: kVK_Option,
        HID_RALT: kVK_RightOption,
        HID_LGUI: kVK_Command,
        HID_RGUI: kVK_RightCommand,
    },
}


def is_modifier(usage: int) -> bool:
    return usage in MODIFIER_USAGES


def vk_for(usage: int, mod_map: str = MOD_MAP_POSITION) -> int | None:
    """HID usage → macOS virtual keycode。映射不到返回 None（调用方丢弃并计数）。"""
    if usage in MODIFIER_USAGES:
        return _MODIFIER_VK.get(mod_map, _MODIFIER_VK[MOD_MAP_POSITION]).get(usage)
    return USAGE_TO_VK.get(usage)


def cg_flags(mods: int, mod_map: str = MOD_MAP_POSITION) -> int:
    """HID 修饰键位图 → CGEventFlags。

    必须**跟着 remap 走**：明明按的是物理 Alt，注入的却是 ⌘，如果 flags 还报
    "Option 按下"，应用看到的就是 ⌥ + 一个"⌘键码"的乱组合，快捷键全错。
    """
    flags = 0
    if mods & (MOD_LCTRL | MOD_RCTRL):
        flags |= FLAG_CONTROL
    if mods & (MOD_LSHIFT | MOD_RSHIFT):
        flags |= FLAG_SHIFT
    if mod_map == MOD_MAP_SEMANTIC:
        cmd, opt = (MOD_LGUI | MOD_RGUI), (MOD_LALT | MOD_RALT)
    else:
        cmd, opt = (MOD_LALT | MOD_RALT), (MOD_LGUI | MOD_RGUI)
    if mods & cmd:
        flags |= FLAG_COMMAND
    if mods & opt:
        flags |= FLAG_OPTION
    return flags


#: 修饰键 usage → 它在位图里的那一位
USAGE_TO_MOD_BIT = {
    HID_LCTRL: MOD_LCTRL, HID_LSHIFT: MOD_LSHIFT, HID_LALT: MOD_LALT,
    HID_LGUI: MOD_LGUI, HID_RCTRL: MOD_RCTRL, HID_RSHIFT: MOD_RSHIFT,
    HID_RALT: MOD_RALT, HID_RGUI: MOD_RGUI,
}

# --------------------------------------------------------------------------- #
# 字符 → (usage, 需要 shift)  —— 只给 tools/key_feeder.py 用
# --------------------------------------------------------------------------- #
#: 只覆盖 US 布局的可打印 ASCII。中文/全角不在这里：那要靠目标机的输入法
#: 自己合成（本方案的原则就是"打到对面去让对面组词"），feeder 只喂拼音字母。
_CHAR_TABLE = {
    "a": (0x04, False), "b": (0x05, False), "c": (0x06, False), "d": (0x07, False),
    "e": (0x08, False), "f": (0x09, False), "g": (0x0A, False), "h": (0x0B, False),
    "i": (0x0C, False), "j": (0x0D, False), "k": (0x0E, False), "l": (0x0F, False),
    "m": (0x10, False), "n": (0x11, False), "o": (0x12, False), "p": (0x13, False),
    "q": (0x14, False), "r": (0x15, False), "s": (0x16, False), "t": (0x17, False),
    "u": (0x18, False), "v": (0x19, False), "w": (0x1A, False), "x": (0x1B, False),
    "y": (0x1C, False), "z": (0x1D, False),
    "1": (0x1E, False), "2": (0x1F, False), "3": (0x20, False), "4": (0x21, False),
    "5": (0x22, False), "6": (0x23, False), "7": (0x24, False), "8": (0x25, False),
    "9": (0x26, False), "0": (0x27, False),
    "A": (0x04, True), "B": (0x05, True), "C": (0x06, True), "D": (0x07, True),
    "E": (0x08, True), "F": (0x09, True), "G": (0x0A, True), "H": (0x0B, True),
    "I": (0x0C, True), "J": (0x0D, True), "K": (0x0E, True), "L": (0x0F, True),
    "M": (0x10, True), "N": (0x11, True), "O": (0x12, True), "P": (0x13, True),
    "Q": (0x14, True), "R": (0x15, True), "S": (0x16, True), "T": (0x17, True),
    "U": (0x18, True), "V": (0x19, True), "W": (0x1A, True), "X": (0x1B, True),
    "Y": (0x1C, True), "Z": (0x1D, True),
    "!": (0x1E, True), "@": (0x1F, True), "#": (0x20, True), "$": (0x21, True),
    "%": (0x22, True), "^": (0x23, True), "&": (0x24, True), "*": (0x25, True),
    "(": (0x26, True), ")": (0x27, True),
    "\n": (0x28, False), "\t": (0x2B, False), " ": (0x2C, False),
    "-": (0x2D, False), "_": (0x2D, True), "=": (0x2E, False), "+": (0x2E, True),
    "[": (0x2F, False), "{": (0x2F, True), "]": (0x30, False), "}": (0x30, True),
    "\\": (0x31, False), "|": (0x31, True),
    ";": (0x33, False), ":": (0x33, True), "'": (0x34, False), '"': (0x34, True),
    "`": (0x35, False), "~": (0x35, True),
    ",": (0x36, False), "<": (0x36, True), ".": (0x37, False), ">": (0x37, True),
    "/": (0x38, False), "?": (0x38, True),
}


def usage_for_char(ch: str) -> tuple[int, bool] | None:
    """字符 → (usage, 是否需要 Shift)。映射不到返回 None。"""
    return _CHAR_TABLE.get(ch)


__all__ = [
    "FLAG_ALPHA_SHIFT",
    "FLAG_COMMAND",
    "FLAG_CONTROL",
    "FLAG_OPTION",
    "FLAG_SHIFT",
    "HID_LALT",
    "HID_LCTRL",
    "HID_LGUI",
    "HID_LSHIFT",
    "HID_RALT",
    "HID_RCTRL",
    "HID_RGUI",
    "HID_RSHIFT",
    "MODIFIER_USAGES",
    "MOD_LALT",
    "MOD_LCTRL",
    "MOD_LGUI",
    "MOD_LSHIFT",
    "MOD_MAPS",
    "MOD_MAP_POSITION",
    "MOD_MAP_SEMANTIC",
    "MOD_RALT",
    "MOD_RCTRL",
    "MOD_RGUI",
    "MOD_RSHIFT",
    "USAGE_TO_MOD_BIT",
    "USAGE_TO_VK",
    "cg_flags",
    "is_modifier",
    "usage_for_char",
    "vk_for",
]
