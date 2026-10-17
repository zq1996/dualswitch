"""端到端热键测试（不会切换画面）。

流程：以 --dry-run 启动常驻进程 → 合成按键 → 检查日志里是否出现
「[dry-run] 本应发送 Set」。

--strict 模式额外验证需求文档 2.2 的可选增强：
    左 Alt + Space  不应触发
    右 Alt + Space  应触发

dry-run 只记录不发送，因此显示器不会切换。
仅 Windows 可用（依赖 keybd_event）。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_SPACE = 0x20
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


def _key(vk: int, up: bool, extended: bool) -> None:
    user32 = ctypes.WinDLL("user32")
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    user32.keybd_event(vk, 0, flags, 0)


def send_alt_space(right: bool) -> None:
    vk_alt = VK_RMENU if right else VK_LMENU
    _key(vk_alt, False, right)
    time.sleep(0.04)
    _key(VK_SPACE, False, False)
    time.sleep(0.06)
    _key(VK_SPACE, True, False)
    time.sleep(0.03)
    _key(vk_alt, True, right)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="验证 ralt+space 严格匹配")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("该脚本仅支持 Windows")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="dualswitch-e2e-"))
    cfg_path = tmp / "config.json"
    log_path = tmp / "dualswitch.log"
    config = {
        "hotkey": "ralt+space" if args.strict else "alt+space",
        "dry_run": True,
        "tray": False,
        "readback": {"enabled": False},
        "log_level": "DEBUG",
    }
    cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["DUALSWITCH_CONFIG"] = str(cfg_path)
    env["DUALSWITCH_LOG"] = str(log_path)

    proc = subprocess.Popen(
        [sys.executable, "-m", "dualswitch", "run", "--no-tray", "--dry-run", "--no-console"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(ROOT),
    )

    def log_text() -> str:
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def hits() -> int:
        return log_text().count("[dry-run] 本应发送 Set")

    try:
        deadline = time.time() + 15
        while time.time() < deadline and "热键已启用" not in log_text():
            time.sleep(0.2)
        if "热键已启用" not in log_text():
            print("[FAIL] 进程未能在 15s 内启用热键")
            print(log_text()[-2000:])
            return 1

        print("热键已启用：", [ln for ln in log_text().splitlines() if "热键已启用" in ln][-1])

        failures = 0
        if args.strict:
            print("阶段 1：发送左 Alt + Space（应被忽略）...")
            send_alt_space(right=False)
            time.sleep(0.9)
            if hits() == 0:
                print("[OK] 左 Alt 未触发，严格模式生效")
            else:
                print("[FAIL] 左 Alt 也触发了，未区分左右 Alt")
                failures += 1
            print("阶段 2：发送右 Alt + Space（应触发）...")
            send_alt_space(right=True)
            time.sleep(0.9)
            if hits() >= 1:
                print("[OK] 右 Alt 触发成功")
            else:
                print("[FAIL] 右 Alt 未触发")
                failures += 1
        else:
            send_alt_space(right=False)
            time.sleep(1.2)
            if hits() >= 1:
                print("[OK] 热键触发成功，dry-run 分支已执行")
            else:
                print("[FAIL] 未捕获到热键触发")
                failures += 1

        print("-" * 60)
        print("\n".join(line for line in log_text().splitlines() if "dry-run" in line or "热键" in line))
        return 0 if failures == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
