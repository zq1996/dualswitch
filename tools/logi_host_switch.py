"""罗技 HID++ 主机切换（Easy-Switch）工具 —— Windows / macOS 通用。

用法:
    python tools/logi_host_switch.py probe           # 只读：查主机槽位
    python tools/logi_host_switch.py switch 2        # 切到 2 号槽（1-based）

注意:
  * 命令只能由鼠标当前连接的那台电脑发出。切走后本机再也够不着它。
  * 切换成功 = 设备立刻断开，没有回复，这是正常现象，不是失败。
  * 目标槽位必须已配对，否则鼠标失联，只能按物理 Easy-Switch 键恢复。
"""
from __future__ import annotations

import sys
import time

import hid

VID_LOGITECH = 0x046D
VENDOR_USAGE_PAGES = (0xFF00, 0xFF43)
REPORT_LONG = 0x11
DEVICE_INDEX_DIRECT = 0xFF

FEATURE_ROOT = 0x0000
FEATURE_CHANGE_HOST = 0x1814
FEATURE_HOSTS_INFO = 0x1815

SW_ID = 0x01


class LogiHostSwitcher:
    def __init__(self, path: bytes | None = None, device_index: int = DEVICE_INDEX_DIRECT):
        self.device_index = device_index
        self.path = path or self._find_path()
        self.dev = hid.device()
        self.dev.open_path(self.path)
        self.dev.set_nonblocking(0)
        self.change_host_index = self._get_feature(FEATURE_CHANGE_HOST)

    # ---------- 设备发现 ----------
    @staticmethod
    def enumerate_candidates() -> list[dict]:
        out = []
        for d in hid.enumerate(VID_LOGITECH):
            if d["usage_page"] in VENDOR_USAGE_PAGES:
                out.append(d)
        return out

    @classmethod
    def _find_path(cls) -> bytes:
        cands = cls.enumerate_candidates()
        if not cands:
            raise RuntimeError(
                "没找到罗技 HID++ 集合（usage page 0xFF00/0xFF43）。"
                "确认鼠标已连接，且不是被别的程序独占。"
            )
        # 优先取带产品名的那个
        return cands[0]["path"]

    # ---------- 报文 ----------
    def _request(self, feature_index: int, function: int, params: bytes = b"",
                 timeout_ms: int = 700, reads: int = 8) -> bytes | None:
        buf = bytearray(20)
        buf[0] = REPORT_LONG
        buf[1] = self.device_index
        buf[2] = feature_index
        # 官方规范：byte3 高半字节 = Function，低半字节 = Software ID
        buf[3] = ((function & 0x0F) << 4) | (SW_ID & 0x0F)
        buf[4:4 + len(params)] = params
        self.dev.write(bytes(buf))
        for _ in range(reads):
            data = self.dev.read(64, timeout_ms)
            if not data:
                continue
            data = bytes(data)
            if len(data) < 4 or data[0] != REPORT_LONG:
                continue
            if data[2] != feature_index:
                continue
            if (data[3] & 0x0F) != SW_ID:
                continue
            return data
        return None

    def _get_feature(self, feature_id: int) -> int:
        resp = self._request(FEATURE_ROOT, 0x00, feature_id.to_bytes(2, "big"))
        if resp is None:
            raise RuntimeError(f"IRoot.GetFeature(0x{feature_id:04X}) 无回复")
        return resp[4]

    # ---------- 业务 ----------
    def host_info(self) -> tuple[int, int]:
        """返回 (host_count, current_host)，均为 0-based。"""
        resp = self._request(self.change_host_index, 0x00)
        if resp is None:
            raise RuntimeError("getHostInfo 无回复")
        return resp[4], resp[5]

    def set_host(self, slot_1based: int) -> None:
        count, current = self.host_info()
        if not 1 <= slot_1based <= count:
            raise ValueError(f"槽位 {slot_1based} 越界（设备只有 {count} 个主机）")
        if slot_1based - 1 == current:
            print(f"已经停在槽位 {slot_1based}，无需切换")
            return
        print(f"从槽位 {current + 1} 切到 {slot_1based} ...")
        # 成功即断开，写操作本身可能抛异常，属正常
        try:
            buf = bytearray(20)
            buf[0] = REPORT_LONG
            buf[1] = self.device_index
            buf[2] = self.change_host_index
            buf[3] = (0x01 << 4) | SW_ID  # setCurrentHost
            buf[4] = slot_1based - 1
            self.dev.write(bytes(buf))
        except Exception as exc:  # noqa: BLE001
            print(f"  写入时设备已断开（预期行为）: {type(exc).__name__}")
        time.sleep(0.3)
        print("  命令已发出。切换结果只能看目标电脑上鼠标是否可用 —— 无回复是设计如此。")

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("probe", "switch", "list"):
        print(__doc__)
        return 2

    if argv[1] == "list":
        for d in LogiHostSwitcher.enumerate_candidates():
            print(f"pid=0x{d['product_id']:04X} page=0x{d['usage_page']:04X} "
                  f"product={d.get('product_string')}")
        return 0

    sw = LogiHostSwitcher()
    try:
        count, current = sw.host_info()
        print(f"设备: {sw.dev.get_product_string()!r}  "
              f"CHANGE_HOST index=0x{sw.change_host_index:02X}")
        print(f"主机数={count}  当前槽位={current + 1}（1-based）")

        if argv[1] == "probe":
            return 0

        if len(argv) < 3:
            print("用法: switch <1-3>")
            return 2
        sw.set_host(int(argv[2]))
        return 0
    finally:
        sw.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
