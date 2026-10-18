#!/usr/bin/env python3
"""闸三：本机剪贴板格式矩阵。

    python tools/clip_selftest.py                 # 当前平台
    python tools/clip_selftest.py --role mac       # 指定平台（跑在别的平台会直接报错）
    python tools/clip_selftest.py --fast           # 跳过 4K 大图
    python tools/clip_selftest.py --json           # 给 CI 用

真正干活的是 `dualswitch.clipboard.selfcheck.run_matrix`，这里只是薄壳 +
`--role` 的平台校验，保证"在 Windows 上误用 mac 后端"这种低级错误立刻暴露。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dualswitch.clipboard import create_backend  # noqa: E402
from dualswitch.clipboard.selfcheck import run_matrix  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机剪贴板格式矩阵")
    parser.add_argument("--role", choices=["auto", "mac", "windows"], default="auto")
    parser.add_argument("--fast", action="store_true", help="跳过 4K 大图")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.role != "auto":
        want = "macos" if args.role == "mac" else "windows"
        backend = create_backend(want)
        if getattr(backend, "name", "none") != want:
            print(f"[FAIL] 这台机器拿不到 {args.role} 后端（实际 {getattr(backend, 'name', 'none')}）")
            return 2
        backend.close()

    results = run_matrix(include_big=not args.fast)
    if args.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(result.line())
        failed = [r for r in results if r.status == "FAIL"]
        manual = [r for r in results if r.status == "MANUAL"]
        print("-" * 70)
        print(
            f"{len(results) - len(failed) - len(manual)} PASS / "
            f"{len(failed)} FAIL / {len(manual)} MANUAL"
        )
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
