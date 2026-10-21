"""重画 apple / windows 来源角标图标（替换设计稿里那套不好看的）。

输出 256×256 RGBA PNG 到本目录：
  apple-logo.png / apple-logo-dim.png
  windows-logo.png / windows-logo-dim.png

与 src/dualswitch/ui/assets/ 同名同规格，确认后可直接覆盖。
用 PySide6 QPainter 反锯齿绘制，形状为自绘几何（非官方素材拷贝）。
"""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/zhqi/.workbuddy/binaries/python/envs/default/lib/python3.13/site-packages")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QApplication

OUT = Path(__file__).resolve().parent

S = 256


def new_img() -> QImage:
    img = QImage(S, S, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    return img


def draw_apple(color: QColor) -> QImage:
    """自绘苹果轮廓：双叶身体 − 右侧咬痕 + 顶部叶子。"""
    img = new_img()
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(color)

    body = QPainterPath()
    # 顶部凹口中心 → 右半边顺时针 → 底部 → 左半边
    body.moveTo(128, 86)
    body.cubicTo(150, 52, 190, 46, 208, 74)
    body.cubicTo(228, 104, 220, 156, 197, 190)
    body.cubicTo(180, 216, 154, 232, 134, 216)
    body.cubicTo(130, 213, 130, 213, 128, 213)
    body.cubicTo(126, 213, 126, 213, 122, 216)
    body.cubicTo(102, 232, 76, 216, 59, 190)
    body.cubicTo(36, 156, 28, 104, 48, 74)
    body.cubicTo(66, 46, 106, 52, 128, 86)
    body.closeSubpath()

    # 右侧咬痕
    bite = QPainterPath()
    bite.addEllipse(QPointF(224, 112), 40, 40)
    body = body.subtracted(bite)

    p.drawPath(body)

    # 顶部叶子（绕自身中心旋转 -35°，略收窄）
    leaf = QPainterPath()
    leaf.addEllipse(QRectF(-32, -11, 64, 22))
    t = QTransform()
    t.translate(152, 42)
    t.rotate(-35)
    leaf = t.map(leaf)
    p.drawPath(leaf)

    p.end()
    return img


def draw_windows(color: QColor) -> QImage:
    """自绘四格窗：2×2 小圆角方块，格距 10（圆角大会像三叶草，故用 3px）。"""
    img = new_img()
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(color)

    r = 3
    gap = 10
    half = (S - gap) / 2  # 123
    for (x, y) in [(0, 0), (half + gap, 0), (0, half + gap), (half + gap, half + gap)]:
        p.drawRoundedRect(QRectF(x, y, half, half), r, r)

    p.end()
    return img


def main() -> None:
    # 需要 QApplication 才能用 QPainter（offscreen 即可）
    app = QApplication.instance() or QApplication(["-platform", "offscreen"])

    ink = QColor("#1D222B")       # 苹果：近黑（与现版一致的语义）
    ink_dim = QColor("#B9C0CB")   # 置灰
    win = QColor("#2B7CD3")       # 窗标：蓝
    win_dim = QColor("#B9C0CB")

    jobs = [
        (draw_apple, ink, "apple-logo.png"),
        (draw_apple, ink_dim, "apple-logo-dim.png"),
        (draw_windows, win, "windows-logo.png"),
        (draw_windows, win_dim, "windows-logo-dim.png"),
    ]
    for fn, color, name in jobs:
        img = fn(color)
        path = OUT / name
        img.save(str(path))
        print("wrote", path.name, img.width(), "x", img.height())


if __name__ == "__main__":
    main()
