"""按键状态机：按下集合、重复键判据、全量释放。

这是"连键 / 卡修饰键"的唯一防线。三条规矩（`docs/KEYBOARD-BRIDGE.md` §8）：

1. **抬起永远放行，按下才需要判据。** 所以"没记录过的 keyup"照样转发 ——
   多抬一次不会出错，少抬一次就是卡键。
2. **重复键由源端决定要不要发。** 这里只提供 `dedupe_repeat` 开关：
   macOS 若对注入的 keydown 自动重复，就必须去重，否则一个字变两个。
   （到底会不会重复，靠 `tools/key_feeder.py --probe` 实测，见 §16-①）
3. **切换焦点时全量释放。** 把"还按着"的键一个不落地抬起来，专治
   "按住 Shift 切屏 → Mac 上 Shift 永远卡住"。
"""

from __future__ import annotations

from dataclasses import dataclass

from .keymap import MODIFIER_USAGES, USAGE_TO_MOD_BIT


@dataclass(frozen=True)
class KeyEvent:
    """一次按键。`mods` 是事件发生时的 HID 修饰键位图（含这一下本身）。"""

    usage: int
    down: bool
    mods: int = 0
    epoch: int = 0
    seq: int = 0
    ts: float = 0.0

    @property
    def is_modifier(self) -> bool:
        return self.usage in MODIFIER_USAGES

    def to_header(self) -> dict[str, int]:
        return {
            "e": self.epoch, "s": self.seq, "u": self.usage,
            "d": 1 if self.down else 0, "m": self.mods,
        }


class KeyState:
    """对端视角的"现在有哪些键按着"。

    刻意用 dict 当有序集合：`release_all()` 按**按下顺序的逆序**抬，
    修饰键通常最先按下，所以最后抬 —— 和真人松手顺序一致。
    """

    def __init__(self, *, dedupe_repeat: bool = False) -> None:
        self.dedupe_repeat = bool(dedupe_repeat)
        self._down: dict[int, int] = {}      # usage -> 最近一次的 mods
        self.repeats_dropped = 0
        self.orphan_ups = 0

    # ------------------------------------------------------------------ 只读
    @property
    def down_usages(self) -> frozenset[int]:
        return frozenset(self._down)

    def is_down(self, usage: int) -> bool:
        return usage in self._down

    def bitmap(self) -> list[int]:
        """当前按下的 usage 列表（T_KEYSTATE 用，通常 0–3 个）。"""
        return sorted(self._down)

    def mods_bitmap(self) -> int:
        """当前按下的修饰键位图。"""
        bits = 0
        for usage in self._down:
            bits |= USAGE_TO_MOD_BIT.get(usage, 0)
        return bits

    # ------------------------------------------------------------------ 主入口
    def apply(self, ev: KeyEvent) -> list[KeyEvent]:
        """喂一个事件，返回**应该真正注入**的事件列表（可能为空）。"""
        if ev.down:
            if ev.usage in self._down:
                if self.dedupe_repeat:
                    self.repeats_dropped += 1
                    return []
                # 不去重：重复的 keydown 要照发，但对端会把它当"再一次按下"。
                # 这是刻意的 —— 源端 Windows 的重复速率就是用户设置的那个速率。
                self._down[ev.usage] = ev.mods
                return [ev]
            self._down[ev.usage] = ev.mods
            return [ev]

        # keyup
        if ev.usage not in self._down:
            # 没记录过的抬起：照样发。抬多了无害，抬少了就是卡键。
            self.orphan_ups += 1
            return [ev]
        del self._down[ev.usage]
        return [ev]

    def release_all(self) -> list[KeyEvent]:
        """抬起所有按着的键。返回的事件 `mods` 是**抬起后剩余**的修饰键位图。"""
        if not self._down:
            return []
        emitted: list[KeyEvent] = []
        for usage in reversed(list(self._down)):
            del self._down[usage]
            emitted.append(
                KeyEvent(usage=usage, down=False, mods=self.mods_bitmap())
            )
        return emitted

    def reset(self) -> None:
        """直接清空，不产生任何事件（用在对端已消失、抬了也没人收的时候）。"""
        self._down.clear()

    def __len__(self) -> int:
        return len(self._down)


__all__ = ["KeyEvent", "KeyState"]
