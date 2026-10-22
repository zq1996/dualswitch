"""焦点虚线框：必须被去掉，但不许把键盘可达性一起删掉。

背景：Windows 风格会在点过的按钮里画一圈灰色虚线（`PE_FrameFocusRect`）。
这个界面几乎全是"点一下就执行"的动作，那圈虚线既不表示选中、也不表示还能键盘操作，
纯噪音。做法是样式壳子拦掉这一个 primitive，**不给控件设 NoFocus**。
"""

from __future__ import annotations

import os
import sys

import pytest

# 旧 ui 样式壳已随 ui/mac 一起冻结；mac 上不再运行。
if sys.platform == "darwin":
    pytest.skip("旧 ui/mac 已由 ui/mac2 取代", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QProxyStyle, QStyle, QStyleOption  # noqa: E402

from dualswitch.ui import theme  # noqa: E402
from dualswitch.ui.focus_style import NoFocusRectStyle, install  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def style_installed(app):
    """装上壳子就不再拆。生产和本套件都一样：壳子常驻，别换来换去。

    （早先的用例测完会把原样式塞回去，结果 `QApplication::setStyle` 的 ownership
    语义会把同一个对象交两次手，后面 `SettingsWindow` 建到一半就崩在堆上。）
    """
    install(app)
    return app.style()


# 让壳子和底座活过用例函数：Python 一回收底座，C++ 侧就是野指针（实测堆损坏 0xc0000374）
_KEEPALIVE: list = []


class _SpyStyle(QProxyStyle):
    """记录下传给底层的 primitive。

    **不调用 super().drawPrimitive**：这里用的是手搓的 `QStyleOption`，
    缺字段的原语交给原生样式画会踩 Qt 的野内存（实测 0xc0000374 堆损坏）。
    本测试只关心"路由到没到底层"，不关心画成什么样。
    """

    def __init__(self, base):
        super().__init__(base)
        self._base_ref = base
        self.seen: list[int] = []

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: ANN001, N802
        self.seen.append(int(element))


def _focus_option() -> QStyleOption:
    opt = QStyleOption()
    opt.rect = QRect(10, 10, 40, 16)
    opt.state |= QStyle.State_HasFocus
    return opt


def test_focus_rect_is_swallowed(app, style_installed):
    spy = _SpyStyle(app.style())
    target = NoFocusRectStyle(spy)
    _KEEPALIVE.extend((spy, target))
    image = QImage(80, 40, QImage.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    try:
        target.drawPrimitive(QStyle.PE_FrameFocusRect, _focus_option(), painter, None)
    finally:
        painter.end()
    assert int(QStyle.PE_FrameFocusRect) not in spy.seen, "焦点虚框没有被拦截"


def test_other_primitives_pass_through(app, style_installed):
    """只吃焦点框，别的绘制一律照原样 —— 否则整个界面的描边都会消失。"""
    spy = _SpyStyle(app.style())
    target = NoFocusRectStyle(spy)
    _KEEPALIVE.extend((spy, target))
    image = QImage(80, 40, QImage.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    try:
        for element in (QStyle.PE_Frame, QStyle.PE_FrameButtonBevel, QStyle.PE_PanelLineEdit):
            target.drawPrimitive(element, _focus_option(), painter, None)
    finally:
        painter.end()
    for element in (QStyle.PE_Frame, QStyle.PE_FrameButtonBevel, QStyle.PE_PanelLineEdit):
        assert int(element) in spy.seen


def test_install_wraps_current_style_and_is_idempotent(app, style_installed):
    base = style_installed.baseStyle()
    install(app)                      # 再来一次不许套娃
    assert app.style() is style_installed
    assert style_installed.baseStyle() is base


def test_clicked_button_keeps_focus(app, style_installed):
    """拦截虚框不等于没收焦点：键盘 / Tab 的可用性必须保留。"""
    app.setStyleSheet(theme.qss())
    button = QPushButton("探测显示器")
    button.setObjectName("row")
    button.show()
    app.processEvents()
    button.setFocus()
    app.processEvents()
    assert button.hasFocus(), "按钮点不到 / Focus 被没收了"
    button.grab()                     # 保证被壳子包着也能正常画
