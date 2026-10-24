"""罗技鼠标 Easy-Switch 主机切换（HID++ 2.0 `CHANGE_HOST` 0x1814）。

设计前提：**这个功能只能锦上添花**。
任何一步失败都必须安静降级——不影响切屏主流程、不让常驻进程抛异常、
不给用户弹窗。切屏是主业务，切鼠标是附加动作。

物理约束（见 docs/FEASIBILITY-LOGI-EASYSWITCH.md §4）：
命令只能由鼠标当前连接的那台电脑发出。所以「把鼠标推出去」只有本机能做；
「把鼠标拉回来」必须由对端发起——本机够不着已经切走的鼠标。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

try:  # hidapi 是可选依赖：没装就整体降级，不影响程序启动
    import hid  # type: ignore[import-untyped]
except Exception:  # noqa: BLE001 - 导入期任何异常都当作"不可用"
    hid = None

VID_LOGITECH = 0x046D
# 罗技设备在不同连接方式下暴露的厂商集合页不一样：
# USB 接收器多为 0xFF00，蓝牙直连（BLE）为 0xFF43。两个都要认。
VENDOR_USAGE_PAGES = (0xFF00, 0xFF43)

REPORT_LONG = 0x11
DEVICE_INDEX_DIRECT = 0xFF
REPORT_LEN = 20

FEATURE_ROOT = 0x0000
FEATURE_CHANGE_HOST = 0x1814

SW_ID = 0x01
FUNC_GET_HOST_INFO = 0x00
FUNC_SET_CURRENT_HOST = 0x01

MAX_HOSTS = 3


@dataclass
class MouseSwitchOutcome:
    """一次切换尝试的结果。`ok=True` 不代表用户已经能用鼠标，只代表命令发出去了。"""

    ok: bool
    reason: str = ""
    sent: bool = False
    slot: int = 0
    skipped: bool = False   # True = 按配置/前置条件主动跳过，不算失败

    def __str__(self) -> str:
        state = "skipped" if self.skipped else ("ok" if self.ok else "failed")
        return f"{state}: {self.reason}" if self.reason else state


class MouseSwitcher:
    """把一只罗技多主机鼠标切到指定 Easy-Switch 槽位。

    刻意不缓存 HID 句柄：蓝牙设备休眠唤醒后旧句柄会失效，每次切换重新枚举
    的代价（几十毫秒）远小于"句柄失效后永久不工作"的代价。
    """

    def __init__(self, cfg) -> None:  # cfg: config.MouseSwitchConfig
        self.cfg = cfg

    # ------------------------------------------------------------------ 对外
    def available(self) -> tuple[bool, str]:
        """能否尝试切换。返回 (可用, 原因)。不可用属于正常状态，调用方静默跳过。"""
        if not getattr(self.cfg, "enabled", False):
            return False, "未启用"
        if hid is None:
            return False, "未安装 hidapi（pip install hidapi）"
        slot = int(getattr(self.cfg, "peer_slot", 0) or 0)
        if not 1 <= slot <= MAX_HOSTS:
            return False, f"peer_slot 未配置或越界（当前 {slot}）"
        return True, ""

    def switch_to(self, slot: int) -> MouseSwitchOutcome:
        """切到 `slot`（1-based）。**任何异常都不会向外抛。**"""
        ok, why = self.available()
        if not ok:
            return MouseSwitchOutcome(False, why, skipped=True, slot=slot)

        if not isinstance(slot, int) or not 1 <= slot <= MAX_HOSTS:
            return MouseSwitchOutcome(False, f"槽位 {slot!r} 非法（只支持 1-{MAX_HOSTS}）")

        try:
            return self._switch(slot)
        except Exception as exc:  # noqa: BLE001 - 兜底：切鼠标永远不能拖垮调用方
            log.warning("切鼠标出现未预期异常（已忽略）：%s: %s", type(exc).__name__, exc)
            return MouseSwitchOutcome(False, f"{type(exc).__name__}: {exc}", slot=slot)

    # ------------------------------------------------------------------ 内部
    def _switch(self, slot: int) -> MouseSwitchOutcome:
        paths = self._enumerate()
        if not paths:
            # 最常见的情况：鼠标正连在另一台电脑上，或压根没连。
            # 这不是错误，是物理约束下的正常状态。
            return MouseSwitchOutcome(False, "本机枚举不到罗技鼠标（可能在对端或未连接）", slot=slot)

        last_reason = ""
        for path in paths:
            outcome = self._try_path(path, slot)
            if outcome is not None:
                return outcome
            last_reason = "设备无响应或不支持主机切换"
        return MouseSwitchOutcome(False, last_reason or "没有可用设备", slot=slot)

    def _enumerate(self) -> list[bytes]:
        """列出候选 HID 路径。鼠标优先，其余罗技设备靠后（避免误切键盘）。"""
        try:
            devices = hid.enumerate(VID_LOGITECH)
        except Exception as exc:  # noqa: BLE001 - 枚举失败等同"没有设备"
            log.debug("枚举罗技设备失败：%s", exc)
            return []

        mouse_like, others = [], []
        for d in devices:
            if d.get("usage_page") not in VENDOR_USAGE_PAGES:
                continue
            name = (d.get("product_string") or "").lower()
            (mouse_like if "mouse" in name else others).append(d["path"])
        return mouse_like + others

    def _try_path(self, path: bytes, slot: int) -> MouseSwitchOutcome | None:
        """在一条路径上尝试。返回 None = 这个设备不合适，换下一个试。"""
        dev = None
        try:
            dev = hid.device()
            dev.open_path(path)
            dev.set_nonblocking(0)
        except Exception as exc:  # noqa: BLE001 - 被占用/无权限：换下一个
            log.debug("打开 HID 设备失败（%r）：%s", path, exc)
            self._safe_close(dev)
            return None

        try:
            feature = self._get_feature(dev, FEATURE_CHANGE_HOST)
            if not feature:
                return None  # 这个设备没有主机切换能力（比如单模鼠标）

            info = self._request(dev, feature, FUNC_GET_HOST_INFO)
            if info is None:
                return None
            host_count, current = info[4], info[5]

            if host_count < 2:
                return MouseSwitchOutcome(
                    False, f"设备只报告 {host_count} 个主机，无法切换", slot=slot
                )
            if slot > host_count:
                return MouseSwitchOutcome(
                    False, f"槽位 {slot} 超过设备主机数 {host_count}（该槽未配对）", slot=slot
                )
            if slot - 1 == current:
                return MouseSwitchOutcome(
                    True, f"已在该槽位（{slot}/{host_count}），无需切换",
                    sent=False, slot=slot,
                )

            return self._send_switch(dev, feature, slot, host_count, current)
        finally:
            self._safe_close(dev)

    def _send_switch(self, dev, feature: int, slot: int, host_count: int,
                     current: int) -> MouseSwitchOutcome:
        """发出切换命令。成功即断开，没有回复——不等待、不重试。"""
        log.info("切鼠标：槽位 %d → %d（共 %d）", current + 1, slot, host_count)
        try:
            self._write(dev, feature, FUNC_SET_CURRENT_HOST, bytes([slot - 1]))
        except Exception as exc:  # noqa: BLE001
            # 设备收到命令会立刻断开，WriteFile 抛错是**预期行为**，不是失败。
            log.debug("切换命令写入时设备断开（预期）：%s: %s", type(exc).__name__, exc)
        return MouseSwitchOutcome(
            True, f"已发送切换到槽位 {slot}（设备将断开重连，1-3 秒）",
            sent=True, slot=slot,
        )

    # ------------------------------------------------------------------ 报文
    def _write(self, dev, feature: int, function: int, params: bytes = b"") -> None:
        buf = bytearray(REPORT_LEN)
        buf[0] = REPORT_LONG
        buf[1] = DEVICE_INDEX_DIRECT
        buf[2] = feature
        # 官方规范：byte3 高半字节 = Function，低半字节 = Software ID。
        # 写反不会报错，只会被当成 GetProtocolVersion（返回 04 05），极难排查。
        buf[3] = ((function & 0x0F) << 4) | (SW_ID & 0x0F)
        buf[4:4 + len(params)] = params
        dev.write(bytes(buf))

    def _request(self, dev, feature: int, function: int, params: bytes = b"",
                 timeout_ms: int = 600, reads: int = 6) -> bytes | None:
        try:
            self._write(dev, feature, function, params)
        except Exception as exc:  # noqa: BLE001
            log.debug("HID++ 写入失败：%s", exc)
            return None

        for _ in range(reads):
            try:
                data = dev.read(64, timeout_ms)
            except Exception as exc:  # noqa: BLE001
                log.debug("HID++ 读取失败：%s", exc)
                return None
            if not data:
                continue
            data = bytes(data)
            if len(data) < 4 or data[0] != REPORT_LONG:
                continue
            if data[2] != feature or (data[3] & 0x0F) != SW_ID:
                continue
            return data
        return None

    def _get_feature(self, dev, feature_id: int) -> int:
        resp = self._request(dev, FEATURE_ROOT, 0x00, feature_id.to_bytes(2, "big"))
        return resp[4] if resp else 0

    @staticmethod
    def _safe_close(dev) -> None:
        if dev is None:
            return
        try:
            dev.close()
        except Exception:  # noqa: BLE001 - 关闭失败无所谓
            pass
