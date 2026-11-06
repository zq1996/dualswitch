#!/usr/bin/env python
"""阶段 2 验收工具（Windows）：逐键打印「吞了没有、闸门四条各是什么状态」。

用途见 `docs/KEYBOARD-BRIDGE.md` §14 阶段 2 的验收标准：

    python tools/key_tap_probe.py            # 只观察，不吞键（安全）
    python tools/key_tap_probe.py --swallow  # 真吞键（画面在 Mac 时才敢开）

**默认不吞**：这个工具的主要价值是让用户/开发者**看见**闸门的四个条件各自
成不成立。真要验"吞掉之后对面能不能收到"，配 `--swallow` + 对端跑
`tools/key_feeder.py` 的反向（或直接看 Mac 上有没有打出字）。

安全阀：`--swallow` 下按 `Ctrl+Alt+Shift+K`（或 Ctrl+C）立刻停止吞键。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if sys.platform != "win32":  # pragma: no cover - 平台守卫
    print("这个工具只能在 Windows 上跑（它探的是 WH_KEYBOARD_LL）。")
    raise SystemExit(2)

from dualswitch.config import Config, load  # noqa: E402
from dualswitch.keyboard.hook_windows import (  # noqa: E402
    KBDLLHOOKSTRUCT,
    caps_lock_on,
    shared_hook,
)
from dualswitch.keyboard.keymap import MODIFIER_USAGES, usage_for_vk  # noqa: E402


def _describe_gate(cfg) -> list[tuple[str, bool, str]]:
    kf = cfg.keyboard_follow
    return [
        ("开关打开", bool(kf.enabled), "config.json 的 keyboard_follow.enabled"),
        ("画面在 Mac", False, "本工具不连引擎，这一条恒 False（真运行时由切屏钩子推进）"),
        ("链路活着", False, "需要引擎：300ms 内有成功发送"),
        ("不是注入事件", True, "LLKHF_INJECTED 的事件永远放行"),
        ("不在白名单", True, f"热键 {cfg.hotkey} / 紧急停止 {kf.release_chord}"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="键盘捕获端闸门探针")
    ap.add_argument("--swallow", action="store_true", help="真的吞键（默认只观察）")
    ap.add_argument("--config", default=None, help="配置文件路径（默认自动找）")
    args = ap.parse_args()

    try:
        cfg = load(args.config) if args.config else Config()
    except Exception as exc:  # noqa: BLE001
        print(f"读配置失败（用默认值）：{exc}")
        cfg = Config()

    print("闸门四条（真实运行时逐条都要 True 才吞键）：")
    for name, ok, why in _describe_gate(cfg):
        print(f"  [{'x' if ok else ' '}] {name:<12} {why}")
    print()
    print("CapsLock =", "开" if caps_lock_on() else "关", "| 现在开始按键（Ctrl+C 退出）")
    if args.swallow:
        print("!! --swallow：命中闸门条件的键会被**吞掉**，Ctrl+Alt+Shift+K 解除")
    print("-" * 62)

    swallow = {"on": args.swallow}
    counters = {"seen": 0, "would": 0}

    def on_key(vk: int, down: bool, kb: KBDLLHOOKSTRUCT) -> bool:
        counters["seen"] += 1
        usage = usage_for_vk(vk)
        injected = kb.injected
        mod = "MOD" if usage in MODIFIER_USAGES else ""
        print(
            f"  {'↓' if down else '↑'} vk=0x{vk:02X} usage="
            f"{'--' if usage is None else f'0x{usage:02X}'} {mod:<3} "
            f"injected={int(injected)} flags=0x{kb.flags:02X} "
            f"→ {'吞' if (swallow['on'] and not injected and usage is not None) else '放行'}"
        )
        if swallow["on"] and not injected and usage is not None:
            counters["would"] += 1
            return True
        return False

    hook = shared_hook()
    hook.acquire(on_key, name="key-tap-probe")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        hook.release(on_key)
    print("-" * 62)
    print(f"共 {counters['seen']} 个事件，吞掉 {counters['would']} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
