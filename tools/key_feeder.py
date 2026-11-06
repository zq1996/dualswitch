"""键盘跟随的喂键工具 —— 不需要 Windows 也能验收 Mac 这一侧。

三种用法，覆盖不同深度：

    # ① 本地直喂：只验「映射 + 状态机 + 注入」这条链（最快，什么都不用连）
    python3 tools/key_feeder.py --text "Hello, world!"

    # ② 走真实链路：像 Windows 那样连过去发 T_KEY（验协议 + 引擎分发 + 注入）
    python3 tools/key_feeder.py --connect 192.168.1.20 --psk <config 里的 psk> \
        --device <对端 config 里记的 windows device_id> --text "abc"

    # ③ 阶段 0 环境探测：注入重复行为、辅助功能授权、安全输入
    python3 tools/key_feeder.py --probe

跑之前先把光标点进 TextEdit / 浏览器输入框 —— 注入是往"当前焦点应用"打的。

⚠️ 首次运行需要「辅助功能」授权（系统设置 → 隐私与安全性 → 辅助功能）。
   没授权时本工具会明确告诉你并退出非零，不会静默什么都不做。
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dualswitch.keyboard import KeyboardReceiver, secure_input_enabled  # noqa: E402
from dualswitch.keyboard.inject_macos import MacInjector  # noqa: E402
from dualswitch.keyboard.keymap import (  # noqa: E402
    HID_LSHIFT,
    MOD_LSHIFT,
    usage_for_char,
)
from dualswitch.sync import protocol as P  # noqa: E402


# --------------------------------------------------------------------------- #
# 文本 / 指令 → 事件
# --------------------------------------------------------------------------- #
def text_to_events(text: str) -> list[tuple[int, bool, int]]:
    """文本 → [(usage, down, mods)]。Shift 走真实的修饰键事件，不合成字符。

    映射不到的非 ASCII 直接跳过并提示 —— 中文要靠对端输入法自己合成，
    这里只负责把拼音字母打过去。
    """
    events: list[tuple[int, bool, int]] = []
    for ch in text:
        hit = usage_for_char(ch)
        if hit is None:
            print(f"跳过无法映射的字符：{ch!r}（只支持 US 布局 ASCII）", file=sys.stderr)
            continue
        usage, need_shift = hit
        mods = MOD_LSHIFT if need_shift else 0
        if need_shift:
            events.append((HID_LSHIFT, True, MOD_LSHIFT))
        events.append((usage, True, mods))
        events.append((usage, False, mods))
        if need_shift:
            events.append((HID_LSHIFT, False, 0))
    return events


def parse_keys(spec: str) -> list[tuple[int, bool, int]]:
    """`0x04:1 0x04:0 0xE1:1` —— usage:down，down=1 按下 / 0 抬起。"""
    events = []
    for token in spec.replace(",", " ").split():
        if not token:
            continue
        usage_s, _, down_s = token.partition(":")
        try:
            usage = int(usage_s, 0)
            down = bool(int(down_s or "1"))
        except ValueError as exc:
            raise SystemExit(f"看不懂的按键：{token!r}（应为 usage:down，如 0x04:1）") from exc
        events.append((usage, down, 0))
    return events


# --------------------------------------------------------------------------- #
# ① 本地直喂
# --------------------------------------------------------------------------- #
def feed_local(events: list[tuple[int, bool, int]], *, delay_ms: float,
               mod_map: str, dedupe: bool) -> int:
    receiver = KeyboardReceiver(mod_map=mod_map, dedupe_repeat=dedupe)
    ok, why = receiver.available()
    if not ok:
        print(f"注入不可用：{why}", file=sys.stderr)
        return 2
    if receiver.injector.trusted() is False:
        print("没有辅助功能授权，注入不会生效："
              "系统设置 → 隐私与安全性 → 辅助功能", file=sys.stderr)
        return 3

    injected = 0
    for seq, (usage, down, mods) in enumerate(events):
        header = P.key_header(epoch=1, seq=seq, usage=usage, down=down, mods=mods)
        if receiver.handle_key(header):
            injected += 1
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
    print(f"喂了 {len(events)} 个事件，注入 {injected} 个")
    print(f"stats={receiver.stats}")
    return 0


# --------------------------------------------------------------------------- #
# ② 走真实链路
# --------------------------------------------------------------------------- #
class LinkFeeder:
    """极简客户端：HELLO → 收 HELLO_OK → 发 T_FOCUS + T_KEY。

    刻意不依赖 `sync.transport`：那是给常驻进程用的，带重连和选举。
    这里要的是"像捕获端那样发帧"，越薄越好排查。

    `device` 必须填**对端（Mac）config 里记的那个 windows device_id**：
    配对之后对端会做身份绑定，陌生 device_id 会被判"已与另一台设备配对"而拒连。
    """

    def __init__(self, host: str, port: int, psk: bytes, *, device: str,
                 timeout: float = 4.0) -> None:
        self.addr = (host, port)
        self.psk = psk
        self.device_id = device
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.reader = P.FrameReader(psk)
        self.counter = 0

    def __enter__(self) -> "LinkFeeder":
        sock = socket.create_connection(self.addr, timeout=self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(self.timeout)
        self.sock = sock
        self._send(P.T_HELLO, P.hello_header(self.device_id, "feeder", self.psk))
        self._wait_ready()
        return self

    def __exit__(self, *exc) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _send(self, msg_type: int, header: dict, payload: bytes = b"") -> None:
        assert self.sock is not None
        frame = P.encode_frame(msg_type, header, payload, key=self.psk,
                               counter=self.counter)
        self.counter += 1
        self.sock.sendall(frame)

    def _wait_ready(self) -> None:
        assert self.sock is not None
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            for frame in self.reader.feed(data):
                if frame.type == P.T_HELLO_OK:
                    if not frame.header.get("ok"):
                        raise SystemExit(
                            f"对端拒绝握手：{frame.header.get('reason') or '未知'}"
                            "（两台 PIN 不一致？--device 填的不是它记的那个 device_id？）"
                        )
                    return
                if frame.type == P.T_HELLO:
                    self._send(P.T_HELLO_OK, {
                        "device": self.device_id, "host": "feeder",
                        "ok": True, "reason": "ok",
                    })
        raise SystemExit("握手超时：对端没回 HELLO_OK（端口 / 配对 / 防火墙？）")

    def focus(self, target: str, epoch: int = 1, caps: bool | None = None) -> None:
        self._send(P.T_FOCUS, P.focus_header(epoch, target, caps=caps))

    def press(self, epoch: int, seq: int, usage: int, down: bool, mods: int = 0) -> None:
        self._send(P.T_KEY, P.key_header(epoch, seq, usage, down, mods))


def feed_network(args, events: list[tuple[int, bool, int]]) -> int:
    host, _, port_s = args.connect.partition(":")
    port = int(port_s) if port_s else 47823
    if args.psk:
        psk = bytes.fromhex(args.psk)
    elif args.pin:
        psk = P.derive_key(args.pin)
    else:
        print("需要 --psk（config.json 里的 psk）或 --pin（配对时的 6 位码）", file=sys.stderr)
        return 2
    if not args.device:
        print("需要 --device：填对端（Mac）config.clipboard.peer_device 的值", file=sys.stderr)
        return 2

    with LinkFeeder(host, port, psk, device=args.device) as link:
        link.focus("mac", epoch=1)
        for seq, (usage, down, mods) in enumerate(events):
            link.press(1, seq, usage, down, mods)
            if args.delay_ms:
                time.sleep(args.delay_ms / 1000.0)
    print(f"已通过链路发送 {len(events)} 个按键到 {host}:{port}")
    return 0


# --------------------------------------------------------------------------- #
# ③ 阶段 0 探测
# --------------------------------------------------------------------------- #
def _count_keydowns(seconds: float) -> tuple[int, str]:
    """在 `seconds` 秒内数一下 keydown 次数（只监听、不拦截）。"""
    try:
        import Quartz  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return 0, f"没有 Quartz：{exc}"

    counts = [0]

    def _cb(proxy, type_, event, refcon):  # noqa: ANN001, ARG001
        if type_ == Quartz.kCGEventKeyDown:
            counts[0] += 1
        return event

    mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly, mask, _cb, None,
    )
    if tap is None:
        return 0, "无法创建事件 tap（需要「输入监控」授权）"
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source,
                              Quartz.kCFRunLoopDefaultMode)
    Quartz.CGEventTapEnable(tap, True)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.02, False)
    try:
        Quartz.CGEventTapEnable(tap, False)
    except Exception:  # noqa: BLE001
        pass
    return counts[0], ""


def run_probe() -> int:
    receiver = KeyboardReceiver()
    print("=" * 62)
    print("① 环境")
    ok, why = receiver.available()
    print(f"  Quartz           : {'可用' if ok else '不可用 —— ' + why}")
    if not ok:
        return 2
    print(f"  辅助功能授权     : {receiver.injector.trusted()}（None = 查不出）")
    if receiver.injector.trusted() is not True:
        print("      → 没授权就注入不了。跑一次 `--request-access` 触发系统弹窗，")
        print("        或手动去 系统设置 → 隐私与安全性 → 辅助功能")
    print(f"  CapsLock         : {receiver.injector.caps_lock_on()}")
    print(f"  安全输入（当前） : {secure_input_enabled()}")

    print("=" * 62)
    print("② 注入的 keydown 会不会自动重复 —— 决定 repeat_mode")
    print("   接下来长按一次 'a'（约 1.2 秒），请**不要碰键盘**。")
    time.sleep(1.0)

    box: dict[str, object] = {"n": 0, "err": ""}

    def _worker() -> None:
        box["n"], box["err"] = _count_keydowns(1.7)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    time.sleep(0.2)

    usage, _ = usage_for_char("a") or (0x04, False)
    receiver.handle_key(P.key_header(1, 0, usage, True, 0))
    time.sleep(1.2)
    receiver.handle_key(P.key_header(1, 1, usage, False, 0))
    worker.join(timeout=3.0)

    count = int(box["n"] or 0)
    err = str(box["err"] or "")
    if err:
        print(f"   量不到：{err}")
        print("   → 手动判断：在 TextEdit 里长按一个键，出一个 a 还是一串 a")
    elif count <= 1:
        print(f"   1.2 秒内共 {count} 次 keydown")
        print("   → **不会**自动重复 ⇒ repeat_mode=forward（源端重复原样转发）")
    else:
        print(f"   1.2 秒内共 {count} 次 keydown")
        print("   → **会**自动重复 ⇒ repeat_mode=dedupe（必须丢掉源端重复，否则翻倍）")

    print("=" * 62)
    print("③ 还需要人工确认的三件事")
    print("   a) 锁屏界面：注入预期失败 —— 解锁请用 MacBook 自带键盘")
    print("   b) 安全输入：在密码框 / 开了「安全键盘输入」的终端里跑一次 --text")
    print("   c) 输入法：输入源切到拼音，喂 `nihao`，确认候选窗在 Mac 上弹出")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="键盘跟随喂键工具")
    ap.add_argument("--text", default="", help="要注入的文本（US 布局 ASCII）")
    ap.add_argument("--keys", default="", help="直接指定 usage:down，如 0x04:1 0x04:0")
    ap.add_argument("--connect", default="", help="host[:port]，走真实链路发 T_KEY")
    ap.add_argument("--psk", default="", help="config.json 里的 psk（hex）")
    ap.add_argument("--pin", default="", help="配对时的 6 位码（与 --psk 二选一）")
    ap.add_argument("--device", default="", help="对端记录的 device_id（链路模式必填）")
    ap.add_argument("--delay-ms", type=float, default=8.0, help="每个事件之间的间隔")
    ap.add_argument("--mod-map", default="position", choices=("position", "semantic"))
    ap.add_argument("--dedupe-repeat", action="store_true", help="丢掉重复的 keydown")
    ap.add_argument("--probe", action="store_true", help="阶段 0 环境探测")
    ap.add_argument("--request-access", action="store_true",
                    help="触发系统「辅助功能」授权弹窗")
    args = ap.parse_args(argv[1:])

    if args.request_access:
        print(f"授权结果：{MacInjector().request_access()}（去系统设置里勾选本程序最稳）")
        return 0

    if args.probe:
        return run_probe()

    if args.keys:
        events = parse_keys(args.keys)
    elif args.text:
        events = text_to_events(args.text)
    else:
        ap.print_help()
        return 2

    if not events:
        print("没有可发送的事件", file=sys.stderr)
        return 2
    if args.connect:
        return feed_network(args, events)
    return feed_local(events, delay_ms=args.delay_ms, mod_map=args.mod_map,
                      dedupe=args.dedupe_repeat)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
