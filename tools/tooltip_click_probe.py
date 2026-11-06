"""探针：真实鼠标悬停后，**原生 QToolTip 会不会挡住下面的按钮**？

背景（2026-11-06）：切换页把长提示从自绘浮层改成 `setToolTip()` 之后，用户报
「又出现一个黑浮窗，点下面的按钮没反应」。怀疑原生 tooltip 窗口（topmost popup，
不带 `WS_EX_TRANSPARENT`）压在下一行按钮上，点击被它吃掉。

这个脚本走**真实路径**：真实鼠标移动 → Qt 自己弹 tooltip（不是 showText 强制）→
真实鼠标点击被 tooltip 覆盖的那个点 → 看按钮的 clicked 有没有触发。

安全：全程只在脚本自己的窗口里动鼠标；动之前先确认窗口在前台，不在前台就
`SetForegroundWindow`，还不行就直接退出（绝不盲点）。

    <venv python> tools/tooltip_click_probe.py
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPoint, QRect, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dualswitch.ui.win2 import theme  # noqa: E402

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [
    ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
]
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_MOVE = 0x0001

TEXT = (
    "「本机发送」直接把 DDC 命令发给显示器：\n"
    "画面在哪一边都能切，不需要先判断当前输入。\n"
    "行尾纸飞机是兜底通道——本机 DDC 不可用时（驱动 / 权限 / 线材）让对端代发。"
)

clicks: list[str] = []


def rect_of(hwnd: int) -> QRect:
    rc = (ctypes.c_long * 4)()
    user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))
    return QRect(rc[0], rc[1], rc[2] - rc[0], rc[3] - rc[1])


def find_tooltip_hwnd() -> int:
    found: list[int] = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
            if "Qt" in buf.value and "ToolTip" in buf.value:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else 0


def click_at(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.qss())

    win = QWidget()
    win.setObjectName("root")
    win.setWindowTitle("tooltip click probe")
    win.resize(520, 260)
    win.move(700, 380)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(20, 20, 20, 20)
    lay.setSpacing(8)

    top = QPushButton("本机发送 17")
    top.setObjectName("primary")
    top.setFixedSize(150, 38)
    top.setToolTip(TEXT)
    top.clicked.connect(lambda: clicks.append("top"))

    bottom = QPushButton("本机发送 18")
    bottom.setObjectName("primary")
    bottom.setFixedSize(150, 38)
    bottom.clicked.connect(lambda: clicks.append("bottom"))

    lay.addWidget(top)
    lay.addWidget(bottom)
    lay.addStretch(1)
    win.show()
    win.raise_()
    win.activateWindow()

    hwnd = int(win.winId())
    state: dict[str, object] = {}

    tries = {"n": 0}

    def step1_hover_top() -> None:
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            tries["n"] += 1
            if tries["n"] > 10:                     # 抢不到前台就放弃，绝不盲点
                print("拿不到前台焦点，退出（没有点击任何东西）", flush=True)
                app.quit()
                return
            user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            QTimer.singleShot(300, step1_hover_top)
            return
        p = top.mapToGlobal(QPoint(top.width() // 2, top.height() // 2))
        user32.SetCursorPos(p.x(), p.y())
        # **必须注入真实 MOVE**：SetCursorPos 只挪光标，不产生 WM_MOUSEMOVE，
        # Qt 收不到鼠标移动就不会触发 QEvent.ToolTip（实测：tooltip 根本没弹）。
        user32.mouse_event(MOUSEEVENTF_MOVE, 2, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_MOVE, -2, 0, 0, 0)
        QTimer.singleShot(1800, step2_measure)      # 等 Qt 自己弹 tooltip

    def step2_measure() -> None:
        tip_hwnd = find_tooltip_hwnd()
        tip_rect = rect_of(tip_hwnd) if tip_hwnd else QRect()
        b_rect = QRect(bottom.mapToGlobal(QPoint(0, 0)), bottom.size())
        inter = tip_rect.intersected(b_rect) if tip_hwnd else QRect()
        state["tip"] = tip_rect
        state["bottom"] = b_rect
        state["inter"] = inter
        print(flush=True) if False else print(f"tooltip 窗口 = {tip_rect.x()},{tip_rect.y()} {tip_rect.width()}x{tip_rect.height()}")
        print(f"下方按钮     = {b_rect.x()},{b_rect.y()} {b_rect.width()}x{b_rect.height()}")
        if not inter.isEmpty():
            print(f"**重叠** = {inter.x()},{inter.y()} {inter.width()}x{inter.height()}"
                  f"（{inter.width() * inter.height()} px²）")
        else:
            print("重叠 = 无（tooltip 没盖住下按钮）")

        # 点重叠区域的中心；没重叠就点按钮中心
        target = inter.center() if not inter.isEmpty() else b_rect.center()
        state["target"] = target
        print(f"将点击 ({target.x()},{target.y()}) —— "
              f"{'重叠区中心' if not inter.isEmpty() else '按钮中心'}")
        QTimer.singleShot(150, step3_click)

    def step3_click() -> None:
        t = state["target"]
        assert isinstance(t, QPoint)
        click_at(t.x(), t.y())
        QTimer.singleShot(300, step4_report)

    def step4_report() -> None:
        print(f"点击后收到的 clicked = {clicks or '（无 —— 点击被吃掉了）'}")
        app.quit()

    QTimer.singleShot(500, step1_hover_top)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
