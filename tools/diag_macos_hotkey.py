#!/usr/bin/env python3
"""macOS 全局热键现场诊断。

热键不灵时用它区分到底是哪一环断了：

    python tools/diag_macos_hotkey.py            # 实时看按键（按一次 ⌥ Space 试）
    python tools/diag_macos_hotkey.py --simulate # 自己发一个合成热键，不用真按
    python tools/diag_macos_hotkey.py --count 5  # 收满 5 次自动退出

输出里每行都有含义：
    tap 就绪          事件通道建起来了（到这里失败=没授权 / 被安全软件拦）
    FlagsChanged/KeyDown  按键真的进来了（没有=键盘事件没到本进程）
    命中              热键判定通过（有 KeyDown 没命中=修饰键判定不匹配，看下面的 flags）
    tap 被系统禁用    系统把通道关了（已自动重开；次数多说明有东西在拖慢回调）

按 Ctrl-C 退出，结束时打印统计。
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, str(__file__).rsplit("/tools/", 1)[0] + "/src")

from dualswitch import logging_setup  # noqa: E402
from dualswitch.hotkey import parse_hotkey  # noqa: E402
from dualswitch.hotkey.macos import QuartzEventTapBackend, accessibility_granted, permission_message  # noqa: E402

KEYCODE_RIGHT_OPTION = 61


def main() -> int:
    ap = argparse.ArgumentParser(description="macOS 全局热键诊断")
    ap.add_argument("--hotkey", default="ralt+space", help="要测的热键，默认 ralt+space")
    ap.add_argument("--simulate", action="store_true", help="用合成事件自测，不需要真按键")
    ap.add_argument("--count", type=int, default=0, help="命中多少次后退出，0=一直等")
    ap.add_argument("--verbose", action="store_true", help="连每个按键都打印")
    args = ap.parse_args()

    logging_setup.setup("INFO", None, console=True)

    hk = parse_hotkey(args.hotkey)
    print(f"热键：{hk.mac_describe()}    辅助功能：{'已授权' if accessibility_granted() else '未授权'}")
    if not accessibility_granted():
        print(permission_message())
        return 2

    hits: list[float] = []
    backend = QuartzEventTapBackend(hk, lambda: hits.append(time.monotonic()))
    backend.start()

    # 统计用的包装：把"收到什么键"打出来（生产代码不打这么细，诊断时才需要）
    orig = backend.handle_event
    Q = backend._Quartz

    def traced(Q_, tap, event_type, event):
        if args.verbose or event_type not in (Q_.kCGEventKeyUp,):
            if event_type in (Q_.kCGEventKeyDown, Q_.kCGEventFlagsChanged):
                keycode = int(Q_.CGEventGetIntegerValueField(event, Q_.kCGKeyboardEventKeycode))
                flags = int(Q_.CGEventGetFlags(event))
                name = "FlagsChanged" if event_type == Q_.kCGEventFlagsChanged else "KeyDown"
                print(f"  {name:12s} keycode={keycode:<3d} flags=0x{flags:08x}")
        return orig(Q_, tap, event_type, event)

    backend.handle_event = traced  # type: ignore[assignment]

    if not backend._thread or not backend._thread.is_alive():
        print("tap 线程没起来，看上面的报错")
        return 1
    print("tap 就绪，按 Ctrl-C 结束" + ("" if args.simulate else "；现在去按一次热键"))

    if args.simulate:
        time.sleep(0.3)
        print("发送合成热键……")
        # 合成 FlagsChanged（右 Option 按下）→ KeyDown(space)
        ev = Q.CGEventCreateKeyboardEvent(None, KEYCODE_RIGHT_OPTION, True)
        Q.CGEventSetType(ev, Q.kCGEventFlagsChanged)
        Q.CGEventSetFlags(ev, 0x00080000 | 0x40)   # alternate | device right-alt
        Q.CGEventPost(Q.kCGHIDEventTap, ev)

        ev2 = Q.CGEventCreateKeyboardEvent(None, 49, True)
        Q.CGEventSetFlags(ev2, 0x00080000 | 0x40)
        Q.CGEventPost(Q.kCGHIDEventTap, ev2)
        up = Q.CGEventCreateKeyboardEvent(None, 49, False)
        Q.CGEventPost(Q.kCGHIDEventTap, up)
        time.sleep(0.8)
        ok = bool(hits)
        print("合成热键：" + ("命中 ✅" if ok else "没命中 ❌（tap 收到了按键但判定没过，或事件没进 tap）"))
        backend.stop()
        _summary(backend, hits)
        return 0 if ok else 1

    try:
        while True:
            time.sleep(0.2)
            if args.count and len(hits) >= args.count:
                print(f"已命中 {len(hits)} 次，退出")
                break
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C")
    finally:
        backend.stop()
        _summary(backend, hits)
    return 0 if hits else 1


def _summary(backend, hits) -> None:
    print(
        f"\n统计：收到事件 {backend.events_seen} 个 / 命中 {len(hits)} 次 / "
        f"tap 被禁用 {backend.tap_resets} 次 / 回调异常 {backend.callback_errors} 次"
    )
    if backend.events_seen == 0:
        print("→ 一个事件都没收到：tap 没生效（权限 / 安全软件），或进程在后台被挂起")
    elif not hits:
        print("→ 收到事件但没命中：检查热键修饰键是否一致（左 Option 不是右 Option）")


if __name__ == "__main__":
    sys.exit(main())
