"""去掉 Windows 风格的「焦点虚线框」。

## 为什么

用户点一下按钮，按钮就拿到了键盘焦点，Windows 风格紧接着在它里面画一圈灰色虚线
（`QStyle::PE_FrameFocusRect`）。这个界面里几乎所有控件都是**点一下就执行**的动作
（切屏 / 复制 / 清理 / 取消），点完之后那圈虚线：

  · 不代表"选中"——选中态另有自己的视觉（主色实底、状态灯、卡片高亮）；
  · 不代表"还能键盘操作"——这些动作都另有热键，控件本身 Tab 过去没意义；
  · 反而像"点到一半卡住了 / 还差一步确认"，是纯噪音。

2026-10-22 用户截图点了三处：清空确认遮罩的「取消」、设置页的「清理」、剪贴板工具钮。

## 怎么去

`QProxyStyle` 拦掉 `PE_FrameFocusRect`，什么都不画。
**不是**给每个控件设 `setFocusPolicy(Qt::NoFocus)`：那样键盘就彻底够不到控件了
（Tab 导航、Enter 触发会一起失效），而且每加一个新控件都要记得再写一次。

输入框的焦点提示不受影响：它们本来就是靠 QSS 的 `:focus` 换描边色（`#pinInput:focus`
之类），不是靠这圈虚线。

## 所有权注意

`QProxyStyle` 只引用底座、不持有它。底座是 `app.style()` 的返回值 —— 一旦 Python 侧
把它回收，而 C++ 侧还拿着指针，就是 use-after-free（v1 实测堆损坏 0xc0000374）。
所以这里**自己攥着一份引用**，`install()` 也把壳子挂到 app 上，双保险。
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

#: 壳子的强引用（模块级）：QProxyStyle 被 Python 回收后 C++ 侧就是野指针，
#: 而 `app.setStyle()` 的所有权语义在不同 Qt 版本上并不一致，两边都留一份最稳。
_KEEP_ALIVE: list = []


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

    幂等判定**不能用 `isinstance(app.style(), NoFocusRectStyle)`**：一旦设过 QSS，
    Qt 会把应用样式包进内部的 `QStyleSheetStyle`，`app.style()` 只能报出壳子的基类
    （QCommonStyle）—— 于是每次调用都会再造一个壳子，而旧壳子的 C++ 对象会被销毁，
    样式链里就留下野指针（2026-10-22 实测：第二次 install 后旧壳子
    `already deleted`）。所以这里查自己挂在 app 上的那份引用。
    """
    existing = getattr(app, "_dualswitch_focus_style", None)
    if isinstance(existing, NoFocusRectStyle):
        return
    proxy = NoFocusRectStyle(app.style())
    app.setStyle(proxy)
    # QApplication 的 setStyle 会接管壳子的所有权；再挂一份到 app 上，
    # 免得任何一方提前回收（底座引用由壳子自己攥着）
    app._dualswitch_focus_style = proxy          # type: ignore[attr-defined]
    _KEEP_ALIVE.append(proxy)                    # 双保险：模块级也留一份


__all__ = ["NoFocusRectStyle", "install"]
