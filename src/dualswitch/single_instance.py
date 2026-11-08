"""单实例保护：第二次启动不再开一个新窗口，而是把已经在跑的那个叫到前台。

## 为什么需要

2026-11-08 早上的现场：用户双击 `start-ui3.bat` 两次（第一次「点了没反应」），
结果**两个实例同时活着**：

* 两个进程各自监听 47833（`SO_REUSEADDR` 允许双绑），日志里出现两次「链路已启动」；
* 各自往托盘注册一个图标（日志里两次「托盘图标（首次注册）」，位置 2184 / 2208）；
* 抢同一个全局热键：先起的那个 `Ctrl+Shift+V` 注册失败，后起的成功；
* 共用一份 `config.json`，谁最后保存谁生效。

而且「关窗口」只是收进托盘（`hide()`），进程不退出 —— 用户以为关掉了，其实还在跑，
再启动一次就是两个。窗口默认都居中在同一位置，两个窗口叠在一起，看着像「点了没反应」。

## 怎么判断「已经有一个在跑」

**不能用 `QLocalServer.listen()` 的成败当锁**：Windows 的命名管道本身支持多实例，
第二个 `listen()` 同名照样成功（2026-11-08 实测：连开四个进程，四个都「抢到了名额」）。
所以锁用**命名互斥量**（`CreateMutexW` + `ERROR_ALREADY_EXISTS`），这是 Windows 上
唯一可靠的跨进程单实例原语；进程退出（含崩溃）时句柄自动释放，不会留残留。

`QLocalServer` 只负责第二件事：**通知**。抢到互斥量的那个实例监听一个管道名，
后来者连上去写一句 `show`，它就把窗口叫到前台。

名字里带 `APP_NAME`（`dualswitch-v3`），所以 V2 与 V3 互不影响，可以并存。
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .config import APP_NAME

log = logging.getLogger("dualswitch.single_instance")

#: 互斥量名 / 管道名：跟配置目录同名，V2（dualswitch）/ V3（dualswitch-v3）天然隔离
MUTEX_NAME = f"{APP_NAME}-ui-single"
SERVER_NAME = f"{APP_NAME}-ui"

ERROR_ALREADY_EXISTS = 183
_CONNECT_TIMEOUT_MS = 400

#: 互斥量句柄的强引用：进程活着期间必须一直持有，否则等于自己把锁放掉
_MUTEX: list = []


def _already_running(name: str = MUTEX_NAME) -> bool:
    """用命名互斥量判断是否已有实例。拿不到互斥量时按「没有」处理（宁可多开）。

    `name` 可传入别的名字：用例要用随机名，否则跑测试时会跟正在运行的界面互斥。
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        log.warning("单实例：创建互斥量失败（%s），按无保护继续", err)
        return False
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return True
    _MUTEX.append(handle)          # 攥住，进程退出时由系统回收
    return False


def notify_existing(timeout_ms: int = _CONNECT_TIMEOUT_MS) -> bool:
    """叫醒已经在跑的实例（让它把窗口显示到前台）。没人在跑返回 False。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    try:
        sock.write(b"show\n")
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
    finally:
        sock.disconnectFromServer()
    return True


def acquire(name: str = MUTEX_NAME) -> QLocalServer | None:
    """抢占单实例名额。抢到返回 server（调用方要保留引用），已被占用返回 None。"""
    if _already_running(name):
        log.info("单实例：%s 已有实例在跑", name)
        return None

    server = QLocalServer()
    # 管道只用于「叫回窗口」：listen 失败不影响单实例语义（互斥量已经拿到了）
    if not server.listen(SERVER_NAME):
        log.warning("单实例：管道 %s 建不起来（%s），第二次启动将无法叫回窗口",
                    SERVER_NAME, server.errorString())
    return server


__all__ = ["MUTEX_NAME", "SERVER_NAME", "acquire", "notify_existing"]
