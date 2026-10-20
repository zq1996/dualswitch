"""兼容模块：真正的实现已经搬进 `ui/mac/` 或 `ui/win/`（两端各一套，互不共享）。

保留这个名字只是为了让现有写法继续可用：

    from dualswitch.ui.runner import Something      # 测试、工具脚本里到处都是
    from dualswitch.ui import runner

导入本模块时会把 `sys.modules["dualswitch.ui.runner"]` 换成当前平台树的同名模块，
所以拿到的是**同一个模块对象**（`isinstance` / 模块级状态都正常），不是复制品。

新代码请直接写 `from dualswitch.ui import platform_ui; ui = platform_ui()`，
别在这里加任何实现 —— 这个文件两端都不该改。
"""

from __future__ import annotations

import sys as _sys

from . import platform_ui as _pick

_sys.modules[__name__] = getattr(_pick(), "runner")
