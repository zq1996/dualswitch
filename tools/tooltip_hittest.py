"""探针：原生 `QToolTip` 弹出后，**它下面的按钮还点得动吗**？

背景（2026-11-06）：自绘 `TipBubble` 因「四角发黑」被删，切换页按钮改用
`setToolTip()`。用户随即报「又出现一个黑浮窗、点按钮没反应」——需要确认原生
tooltip 窗口是否也会吃掉落在它矩形里的点击（即它是否 `WS_EX_TRANSPARENT`）。

做法：**不注入鼠标**，只做命中测试 —— 用 `WindowFromPoint` 问系统
「(x, y) 这一点归哪个窗口」，再和主窗口 / tooltip 窗口的 hwnd 比对。

    <venv python> tools/tooltip_hittest.py
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from dualswitch.ui.win2 import theme  # noqa: E402

user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [ctypes.c_long * 2]
user32.WindowFromPoint.restype = ctypes.c_void_p
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20

TEXT = (
    "「本机发送」直接把 DDC 命令发给显示器：\n"
    "画面在哪一边都能切，不需要先判断当前输入。\n"
    "行尾纸飞机是兜底通道——本机 DDC 不可用时（驱动 / 权限 / 线材）让对端代发。"
)


def hit(x: int, y: int) -> int:
    pt = (ctypes.c_long * 2)(x, y)
    return int(user32.WindowFromPoint(pt) or 0)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.qss())

    win = QWidget()
    win.setObjectName("root")
    win.resize(520, 260)
    win.move(500, 400)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(20, 20, 20, 20)
    lay.setSpacing(8)

    top = QPushButton("本机发送 17")
    top.setObjectName("primary")
    top.setFixedSize(150, 38)
    top.setToolTip(TEXT)
    bottom = QPushButton("本机发送 18")
    bottom.setObjectName("primary")
    bottom.setFixedSize(150, 38)
    lay.addWidget(top)
    lay.addWidget(bottom)
    lay.addStretch(1)
    win.show()
    win.raise_()
    win.activateWindow()

    main_hwnd = int(win.winId())
    bottom_hwnd = int(bottom.winId())

    def check() -> None:
        bp = bottom.mapToGlobal(QPoint(bottom.width() // 2, bottom.height() // 2))
        brect = (bottom.mapToGlobal(QPoint(0, 0)), bottom.size())
        hx = hit(bp.x(), bp.y())
        tip = QApplication.instance().activeWindow()
        # 找 tooltip 窗口：顶层窗口里标题/类名为 tooltip 的那个
        print(f"主窗口 hwnd      = 0x{main_hwnd:x}")
        print(f"下方按钮 hwnd    = 0x{bottom_hwnd:x}")
        print(f"命中 hwnd        = 0x{hx:x}   （下方按钮中心 {bp.x()},{bp.y()}）")
        print(f"命中是不是按钮本身: {hx == bottom_hwnd}")
        ex = user32.GetWindowLongW(ctypes.c_void_p(hx), GWL_EXSTYLE)
        print(f"命中窗口 exstyle = 0x{ex & 0xFFFFFFFF:x}  "
              f"WS_EX_TRANSPARENT={'有（点得穿）' if ex & WS_EX_TRANSPARENT else '无（会吃掉点击）'}")

        # tooltip 自己的 hwnd（QTipLabel 是顶层窗，遍历顶层窗找可见的 Qt tooltip）
        found = []
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
                if "Qt" in buf.value and "ToolTip" in buf.value:
                    found.append(int(hwnd))
            return True

        user32.EnumWindows(EnumProc(cb), 0)
        for h in found:
            ex2 = user32.GetWindowLongW(ctypes.c_void_p(h), GWL_EXSTYLE)
            print(f"tooltip hwnd 0x{h:x} exstyle=0x{ex2 & 0xFFFFFFFF:x} "
                  f"WS_EX_TRANSPARENT={'有' if ex2 & WS_EX_TRANSPARENT else '无'}")
        _ = tip
        # Qt 层面的命中：widgetAt 会返回**最上层**的控件（含 tooltip 顶层窗）
        at = QApplication.widgetAt(bp)
        print(f"widgetAt(下方按钮中心) = {at!r}  objectName={getattr(at, 'objectName', lambda: '')()}")
        for h in found:
            r = QWidget.window() if False else None
            _ = r
        print(f"下方按钮全局矩形 = {brect[0].x()},{brect[0].y()} {brect[1].width()}x{brect[1].height()}")
        scr = win.screen()
        dpr = scr.devicePixelRatio()
        print(f"窗口可见={win.isVisible()} geometry={win.geometry()} "
              f"screen={scr.name()} dpr={dpr} logicalDpi={scr.logicalDotsPerInch()}")
        px = hit(int(bp.x() * dpr), int(bp.y() * dpr))
        ex3 = user32.GetWindowLongW(ctypes.c_void_p(px), GWL_EXSTYLE) if px else 0
        print(f"[物理坐标 {int(bp.x()*dpr)},{int(bp.y()*dpr)}] 命中 hwnd=0x{px:x} "
              f"exstyle=0x{ex3 & 0xFFFFFFFF:x} 透明={'有' if ex3 & WS_EX_TRANSPARENT else '无'}")
        for h in found:
            r = ctypes.c_long * 4
            user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(r)]
            rc = r()
            user32.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(rc))
            print(f"tooltip 物理矩形 = {rc[0]},{rc[1]} → {rc[2]},{rc[3]}")
        app.quit()

    def pop() -> None:
        QToolTip.showText(
            top.mapToGlobal(QPoint(top.width() // 2, top.height() + 4)), TEXT, top
        )
        QTimer.singleShot(800, check)

    QTimer.singleShot(700, pop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
