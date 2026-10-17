"""dualswitch —— 双系统单显示器切换助手。

在当前可见主机上按一个热键，盲发 DDC/CI Set 指令（VCP 0x60），
把显示器的输入源在 Windows(HDMI) 与 Mac(USB-C) 之间切换。
"""

__version__ = "1.0.0"
APP_NAME = "dualswitch"

__all__ = ["__version__", "APP_NAME"]
