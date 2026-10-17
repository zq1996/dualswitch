"""只读耗时测量：枚举显示器 / 取句柄 / 读 VCP 的耗时分布。

不发送任何 Set 指令，不会切换画面。用于评估 ≤200ms 的指令发出预算。
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dualswitch.config import from_dict  # noqa: E402
from dualswitch.ddc import create_backend  # noqa: E402


def bench(fn, rounds: int = 10) -> tuple[float, float, float]:
    samples = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return min(samples), statistics.median(samples), max(samples)


def main() -> int:
    cfg = from_dict({})
    backend = create_backend(cfg)
    print(f"平台={sys.platform} 后端={backend.name}")

    mn, med, mx = bench(backend.list_monitors)
    print(f"list_monitors       min={mn:6.1f}ms  median={med:6.1f}ms  max={mx:6.1f}ms")

    try:
        mn, med, mx = bench(backend.get_input)
        print(f"get_input(含取句柄)  min={mn:6.1f}ms  median={med:6.1f}ms  max={mx:6.1f}ms")
    except Exception as exc:
        print(f"get_input 失败：{exc}")

    backend.close()
    print("\n注：SetVCPFeature 与 GetVCPFeature 的 DDC 往返耗时同一量级，")
    print("    因此『取句柄 + Set』通常在几十毫秒内，满足 200ms 预算。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
