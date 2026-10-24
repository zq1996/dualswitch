"""Apple Silicon DDC 直读通道：绕开 m1ddc 的 Get-VCP 校验和 bug。

背景（2026-10-24 实测破案，详见 .workbuddy/memory/2026-10-24.md J 段）：

m1ddc 底层库 AppleSiliconDDC 的 **Get-VCP 请求校验和漏了 VESA 规范要求的
0x51 子地址字节**（种子只取 0x6E），而 Set-VCP 是对的 —— 于是这台 AOC
「写永远成功、读永远 -118」。宽容的显示器容忍畸形请求，严格的显示器
直接拒绝。旧文档把 -118 记成「这台屏 USB-C 下不响应 DDC 读请求」是误诊。
参考：MonitorSwitch-macOnly 仓库 docs/ddc-ci.md 对该 bug 的逐字节分析。

本模块用 ctypes 直调 CoreDisplay 的私有 IOAVService API：

    IOAVServiceCreateWithService(alloc, dcpAVServiceProxy)
    IOAVServiceWriteI2C(svc, 0x37, 0x51, buf, len)   # chip=0x37 data=0x51
    IOAVServiceReadI2C (svc, 0x37, 0x51, buf, len)

协议帧（2026-10-24 在 Q2790R3 上抓到的真值）：

    Get-VCP 请求（4 字节）:
        [0x82, 0x01, vcp, chk]
        chk = 0x6E ^ 0x51 ^ 0x82 ^ 0x01 ^ vcp     # 0x6E=0x37<<1 写地址

    回复（11 字节，先等 ≥40ms 再读）:
        [0x51, 0x88, 0x02, RC, vcp, t, mh, ml, cur_h, cur_l, chk]
        首字节是 0x51（子地址回显）——文档说 0x6E，别硬校验首字节；
        chk = XOR(bytes[0..9]) ^ 0x6F              # 0x6F=0x37<<1|1 读地址
        有效的判据：校验和过 && RC==0 && vcp 回显一致。
        当前值 = (cur_h << 8) | cur_l。

句柄选择：IORegistry 里每块屏一个 `DCPAVServiceProxy` 节点；内建屏的
proxy 对 I2C 全零回复（校验和/RC 过不了），所以「逐个试、谁先给出有效
回复就用谁并记住」即可选中外接屏。实测 Q2790R3：
    画面在 Windows → 读到 17（HDMI）；画面在 Mac → 读到 18（USB-C），
    1~2 次尝试内成功。

只实现读（Get-VCP）。写继续走 m1ddc（它的 Set 校验和是对的）。
"""

from __future__ import annotations

import logging
import sys
import threading
import time

log = logging.getLogger("dualswitch.ddc.avservice")

# I2C 地址（DDC/CI 规范）
I2C_CHIP_WRITE = 0x37          # 显示器 DDC 芯片地址
I2C_DATA = 0x51                # 子地址（DDC/CI 消息盒）
READ_ADDR_XOR = 0x6F           # 0x37<<1 | 1 —— 回复校验和的种子

# Get-VCP 帧
GET_OPCODE = 0x82              # 「Get VCP Feature」
REPLY_LEN = 11                 # 回复定长 11 字节
REPLY_MIN_GAP_S = 0.05         # 写请求到读回复之间至少等 40ms（规范），留余量


def build_get_vcp_request(code: int) -> bytes:
    """构造 Get-VCP 请求帧（VESA 规范校验和 —— m1ddc 缺的就是 0x51 这一拍）。"""
    body = bytes([GET_OPCODE, 0x01, int(code) & 0xFF])
    chk = 0x6E ^ I2C_DATA
    for byte in body:
        chk ^= byte
    return body + bytes([chk & 0xFF])


def parse_get_vcp_reply(reply: bytes, code: int = 0x60) -> int | None:
    """解析 Get-VCP 回复，返回当前值；任何一步不对都返回 None。

    判据按「宁缺毋假」排序：长度 → 校验和 → 结果码 RC → VCP 回显。
    首字节不做硬校验（实测 Q2790R3 回 0x51，文档口径是 0x6E，两家都对）。
    """
    if len(reply) != REPLY_LEN:
        return None
    csum = READ_ADDR_XOR
    for byte in reply[:10]:
        csum ^= byte
    if csum != reply[10]:
        return None
    if reply[3] != 0x00:                     # RC：0x00=OK，0x01=不支持该 VCP
        return None
    if reply[4] != (int(code) & 0xFF):       # 回显必须与请求一致
        return None
    return (reply[8] << 8) | reply[9]


class AVServiceI2C:
    """逐个尝试 DCPAVServiceProxy 句柄，用规范校验和做 DDC/CI 读。

    线程安全：检测线程（2s 轮询）和切换回读线程可能同时进来，I2C 事务
    整体加锁。句柄枚举惰性做一次并缓存；上一次成功的句柄优先复用，
    失效（显示器拔线换线）时再回落到全量重试。
    """

    def __init__(self, retries: int = 4):
        self.retries = max(1, int(retries))
        self._lock = threading.Lock()
        self._handles: list[object] | None = None   # None = 还没枚举过
        self._good: object | None = None            # 上次给出有效回复的句柄
        self._lib = None

    # ------------------------------------------------------------------ 底层
    def _ensure_lib(self) -> bool:
        """惰性加载 CoreDisplay / IOKit 并绑好签名。非 macOS 直接 False。"""
        if self._lib is not None:
            return True
        if sys.platform != "darwin":
            return False
        try:
            import ctypes

            cd = ctypes.CDLL(
                "/System/Library/Frameworks/CoreDisplay.framework/CoreDisplay"
            )
            cf = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            iok = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")

            cf.CFAllocatorGetDefault.restype = ctypes.c_void_p
            cf.CFAllocatorGetDefault.argtypes = []

            cd.IOAVServiceCreateWithService.restype = ctypes.c_void_p
            cd.IOAVServiceCreateWithService.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            for fn in ("IOAVServiceReadI2C", "IOAVServiceWriteI2C"):
                getattr(cd, fn).restype = ctypes.c_int
                getattr(cd, fn).argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                ]

            iok.IOServiceNameMatching.restype = ctypes.c_void_p
            iok.IOServiceNameMatching.argtypes = [ctypes.c_char_p]
            iok.IOServiceGetMatchingServices.restype = ctypes.c_int
            iok.IOServiceGetMatchingServices.argtypes = [
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            iok.IOIteratorNext.restype = ctypes.c_uint32
            iok.IOIteratorNext.argtypes = [ctypes.c_uint32]
            iok.IOObjectRelease.restype = ctypes.c_int
            iok.IOObjectRelease.argtypes = [ctypes.c_uint32]

            self._lib = (ctypes, cd, cf, iok)
            return True
        except Exception as exc:  # noqa: BLE001 - 老系统/符号缺失就整体降级
            log.debug("IOAVService 加载失败，DDC 直读不可用：%s", exc)
            self._lib = False
            return False

    def _enumerate_handles(self) -> list[object]:
        """枚举 DCPAVServiceProxy 并各建一个 IOAVService 句柄。"""
        ctypes, cd, cf, iok = self._lib
        handles: list[object] = []
        matching = iok.IOServiceNameMatching(b"DCPAVServiceProxy")
        iterator = ctypes.c_uint32(0)
        if iok.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator)) != 0:
            return handles
        while True:
            service = iok.IOIteratorNext(iterator)
            if not service:
                break
            handle = cd.IOAVServiceCreateWithService(
                cf.CFAllocatorGetDefault(), ctypes.c_void_p(service)
            )
            iok.IOObjectRelease(service)
            if handle:
                handles.append(handle)
        return handles

    def _candidates(self) -> list[object]:
        """候选句柄：上次成功的排最前。"""
        if self._lib is False:
            return []
        if self._handles is None:
            self._handles = self._enumerate_handles() if self._ensure_lib() else []
            if not self._handles:
                log.debug("IORegistry 里没有 DCPAVServiceProxy（Intel 机 / 老系统）")
        if self._good is not None and self._good in self._handles:
            rest = [h for h in self._handles if h is not self._good]
            return [self._good] + rest
        return list(self._handles)

    @staticmethod
    def _i2c_read(lib, handle, length: int) -> tuple[int, bytes]:
        ctypes, cd, _cf, _iok = lib
        buf = (ctypes.c_uint8 * length)()
        rc = cd.IOAVServiceReadI2C(
            ctypes.c_void_p(handle), I2C_CHIP_WRITE, I2C_DATA, buf, length
        )
        return rc, bytes(buf)

    @staticmethod
    def _i2c_write(lib, handle, payload: bytes) -> int:
        ctypes, cd, _cf, _iok = lib
        buf = (ctypes.c_uint8 * len(payload))(*payload)
        return cd.IOAVServiceWriteI2C(
            ctypes.c_void_p(handle), I2C_CHIP_WRITE, I2C_DATA, buf, len(payload)
        )

    # ------------------------------------------------------------------ 接口
    def get_vcp(self, code: int) -> int | None:
        """读一个 VCP 当前值。全部句柄 × 重试都失败返回 None。"""
        request = build_get_vcp_request(code)
        with self._lock:
            if self._lib is False:
                return None
            for handle in self._candidates():
                last_raw = "-"
                for attempt in range(self.retries):
                    rc_w = self._i2c_write(self._lib, handle, request)
                    time.sleep(REPLY_MIN_GAP_S)
                    rc_r, raw = self._i2c_read(self._lib, handle, REPLY_LEN)
                    value = parse_get_vcp_reply(raw, code)
                    if value is not None:
                        self._good = handle
                        if attempt:
                            log.debug("VCP 0x%02X 第 %d 次尝试读成功 = %d", code, attempt + 1, value)
                        return value
                    last_raw = raw.hex()
                    time.sleep(0.1)
                log.debug(
                    "VCP 0x%02X 读失败（写rc=%s 读rc=%s 回复=%s），换下一个句柄",
                    code, rc_w, rc_r, last_raw,
                )
            return None
