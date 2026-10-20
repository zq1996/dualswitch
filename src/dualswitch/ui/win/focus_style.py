"""去掉 Windows 风格的「焦点虚线框」。

## 为什么

用户点一下按钮，按钮就拿到了键盘焦点，Windows 风格紧接着在它里面画一圈灰色虚线
（`QStyle::PE_FrameFocusRect`）。这个界面里几乎所有控件都是**点一下就执行**的动作
（切屏 / 打开主界面 / 复制到本机 / 返回），点完之后那圈虚线：

  · 不代表"选中"——本页面的选中另有自己的视觉（左边的箭头、卡片高亮）；
  · 不代表"还能键盘操作"——这些动作都另有热键，控件本身 Tab 过去没意义；
  · 反而像"点到一半卡住了 / 还差一步确认"，是纯噪音。

## 怎么去

`QProxyStyle` 拦掉 `PE_FrameFocusRect`，什么都不画。
**不是**给每个控件设 `setFocusPolicy(Qt::NoFocus)`：那样键盘就彻底够不到控件了
（Tab 导航、Enter 触发会一起失效），而且每加一个新控件都要记得再写一次。

焦点本身还在这儿，后续要恢复键盘可达性只改这一个文件。

## 所有权注意

`QProxyStyle` 只引用底座、不持有它。底座是 `app.style()` 的返回值 —— 一旦 Python 侧
把它回收，而 C++ 侧还拿着指针，就是 use-after-free（实测堆损坏 0xc0000374）。
所以这里**自己攥着一份引用**，`install()` 也把壳子挂到 app 上，双保险。
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle


class NoFocusRectStyle(QProxyStyle):
    """吃掉焦点虚线框的样式壳子；其余绘制全部照旧。"""

    def __init__(self, base=None):
        super().__init__(base)
        # 攥住底座：Python 一旦回收它，C++ 侧就是野指针
        self._base_ref = base

    def drawPrimitive(self, element, option, painter, widget=None) -> None:  # noqa: ANN001, N802
        if element == QStyle.PE_FrameFocusRect:
            return
        super().drawPrimitive(element, option, painter, widget)


def install(app: QApplication) -> None:
    """把样式壳子套在应用当前样式**外面**（最外层，别被别人的壳子挡在后面）。

    放在当前样式外面而不是里面：万一某天 Qt 给应用样式再包一层
    （比如 QStyleSheetStyle），外层壳子仍然先拿到 `PE_FrameFocusRect`，
    不会因为内层把它吃了而漏过去。

    幂等：重复调用不会无限套娃。
    """
    current = app.style()
    if isinstance(current, NoFocusRectStyle):
        return
    proxy = NoFocusRectStyle(current)
    app.setStyle(proxy)
    # QApplication 的 setStyle 会接管壳子的所有权；再挂一份到 app 上，
    # 免得任何一方提前回收（底座引用由壳子自己攥着）
    app._dualswitch_focus_style = proxy          # type: ignore[attr-defined]
