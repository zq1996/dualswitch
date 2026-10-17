"""在 Windows 上扫描 VCP 0x60 的候选值，找出能真正切换输入源的那个。

两种模式：
  默认（逐值试，命中即停）
    python tools/scan_input_windows.py --values 26,28

  --sweep（批量扫，每次试完自动切回原通道）
    python tools/scan_input_windows.py --sweep --values 18,19,20,21,22,23,24,25
    用途：找出显示器「认可」的全部输入源值。每次 Set 后停留 ~2 秒再切回，
    你只需要盯着屏幕看哪一次出现了目标机器的画面。

  --hold（停在某个值 N 秒，用于肉眼确认，然后自动切回）
    python tools/scan_input_windows.py --hold 15 --dwell 12 --delay 20
    用途：读回不可靠（比如返回 -118 这类乱码）时，用眼睛判断。
    --delay 让脚本先等一会儿再动手，方便你先把视线移到屏幕上。

判据：发出 Set 后回读 0x60。读数变化 = 显示器接受该值；读数不变 = 该值被拒绝。

⚠️ 会真的切换画面，可能短暂黑屏。--sweep / --hold 都会自动切回。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dualswitch.ddc.base import DDCError  # noqa: E402
from dualswitch.ddc.windows import WindowsBackend  # noqa: E402

DEFAULT_VALUES = [26, 28, 15, 16, 18, 19]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--values", default=",".join(map(str, DEFAULT_VALUES)))
    parser.add_argument("--vcp", type=lambda s: int(s, 0), default=0x60)
    parser.add_argument("--monitor", default="AOC", help="显示器匹配子串")
    parser.add_argument("--wait", type=float, default=0.9, help="Set 之后等待多久再回读")
    parser.add_argument("--sweep", action="store_true", help="每个值试完自动切回原通道")
    parser.add_argument("--dwell", type=float, default=2.0, help="--sweep 下每个值停留秒数")
    parser.add_argument("--hold", type=int, default=None, help="停在该值 N 秒后自动切回（肉眼确认用）")
    parser.add_argument("--delay", type=float, default=0.0, help="--hold 开始前先等待的秒数")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    values = [int(v) for v in args.values.split(",") if v.strip()]
    backend = WindowsBackend(vcp_code=args.vcp, monitor_match=args.monitor)

    try:
        base = backend.get_input()
    except DDCError as exc:
        print(f"[FAIL] 读不到当前输入源：{exc}")
        return 2

    print(f"显示器: {backend.last_monitor.label() if backend.last_monitor else '?'}")
    print(f"当前 VCP 0x{args.vcp:02X} = {base}")
    print(f"候选值: {values}")
    print("-" * 58)

    if args.dry_run:
        for v in values:
            print(f"将发送 Set 0x{args.vcp:02X} = {v}")
        return 0

    if args.delay > 0:
        print(f"{args.delay:.0f} 秒后开始切换，请把视线移到屏幕上…")
        for remaining in range(int(args.delay), 0, -1):
            print(f"  倒计时 {remaining}s", end="\r", flush=True)
            time.sleep(1)
        print(" " * 24, end="\r")

    def read() -> int | None:
        try:
            return backend.get_input()
        except DDCError:
            return None

    def restore() -> None:
        try:
            backend.set_input(base)
        except DDCError:
            pass

    # ---------------------------------------------------------- 停在某个值上
    if args.hold is not None:
        print(f"切到 {args.hold}，停留 {args.dwell:.0f} 秒后自动切回 {base}")
        try:
            backend.set_input(args.hold)
        except DDCError as exc:
            print(f"[FAIL] Set 失败：{exc}")
            return 2
        remaining = int(args.dwell)
        while remaining > 0:
            print(f"  剩 {remaining}s", end="\r", flush=True)
            time.sleep(1)
            remaining -= 1
        print(" " * 20, end="\r")
        restore()
        time.sleep(0.8)
        print(f"已切回 {base}，回读 {read()}")
        print(f"请回答：刚才停在 {args.hold} 的 {args.dwell:.0f} 秒里，屏幕是什么？")
        print("  出现 Mac 画面 → 这个值就是 USB-C")
        print("  黑屏 / 无信号 → 这个口没接设备")
        return 0

    # ---------------------------------------------------------- 批量扫描
    if args.sweep:
        accepted: list[int] = []
        for v in values:
            if v == base:
                continue
            print(f"{v:>3}  试…", end="", flush=True)
            try:
                backend.set_input(v)
            except DDCError as exc:
                print(f" Set 失败：{exc}")
                continue
            time.sleep(args.wait)
            cur = read()
            if cur is None or cur != base:
                accepted.append(v)
                print(f" 接受（回读 {cur if cur is not None else '失败'}）", end="")
            else:
                print(" 拒绝", end="")
            time.sleep(max(args.dwell - args.wait, 0.0))
            restore()
            time.sleep(0.7)
            print(" → 已切回")

        print("-" * 58)
        print(f"被接受的值: {accepted if accepted else '无'}")
        print(f"已切回 {base}，回读 {read()}")
        if accepted:
            print()
            print("请回答：试到哪个值时，屏幕出现了目标机器的画面？")
            print("  有画面 → 那就是目标输入源的值，写进配置")
            print("  黑屏   → 那个口没接设备，换个值再试")
        else:
            print("[未命中] 全部被拒绝：该接口不接受输入源切换，或真实值不在候选里。")
            return 1
        return 0

    # ---------------------------------------------------------- 逐值试，命中即停
    hit: int | None = None
    for v in values:
        if v == base:
            print(f"{v:>3}  跳过（就是当前值）")
            continue
        print(f"{v:>3}  发送中…", end="", flush=True)
        try:
            backend.set_input(v)
        except DDCError as exc:
            print(f" Set 失败：{exc}")
            continue
        time.sleep(args.wait)
        try:
            cur = backend.get_input()
        except DDCError as exc:
            print(f" 回读失败（{exc}）→ 显示器可能已离开当前通道，判定为命中")
            hit = v
            break
        if cur != base:
            print(f" 回读变成 {cur} → 命中")
            hit = v
            break
        print(f" 回读仍为 {cur} → 未生效")

    print("-" * 58)
    if hit is None:
        print("[未命中] 所有候选值都被忽略。")
        print("  可能原因：显示器不接受通过当前接口切换输入源，或真实值不在候选里。")
        print(f"  可继续尝试：--values 3,4,5,6,7,20,21,22,23,24,25")
        return 1

    print(f"[命中] VCP 0x{args.vcp:02X} = {hit}")
    print(f"  如果画面已切到目标机器，把它写进配置 sources 对应项。")
    print(f"  如果画面是黑的，说明 {hit} 是个没接信号的输入口，用显示器 OSD 切回来。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
