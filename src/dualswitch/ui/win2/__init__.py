"""Windows v2 界面（宽窗口 + 侧栏导航，设计见 `docs/UI-WINDOWS2-DESIGN.md`）。

与 `ui/mac2/**` 是**同一套产品、同一套骨架，换一副 Windows 的壳**：
780×580 + 188 侧栏 + 三页 + 30 底栏；常驻入口从菜单栏图标换成托盘；
快捷键 Alt+Space / Ctrl+Shift+V；打开方式换成资源管理器 / 默认看图 / 默认浏览器。

入口：`python -m dualswitch ui2` → win32 走这里，darwin 走 `ui/mac2`（cli 里分发）。
旧 `ui` 树一行不动。

模块地图：

    theme.py          令牌 + QSS（字体/圆角/caption/ink3 与 mac2 不同）+ 文字键帽
    widgets.py        KeyCap / ToggleSwitch / Segmented / 缩略图资源
    window.py         自绘标题栏（40px）+ 侧栏三页 + 底栏
    switch_page.py    切换页 hero（三态 + 强制切 + 设备卡）
    clipboard_page.py 剪贴板页（行解剖 + 四态药丸 + 筛选 + 双态空态 + 键盘导航）
    preview.py        悬浮预览窗（450ms / 220ms + 尺寸上限）
    openers.py        打开方式按类型分派（默认看图 / 资源管理器 / 默认浏览器）
    apps.py           exe 路径 → QFileIconProvider 图标（14px）
    settings_page.py  五组设置（含「高级」组）
    tray.py           QSystemTrayIcon + QMenu（QWidgetAction 自绘行）
    runner.py         run_ui2（组装 + 轮询 + toast + 确认遮罩 + 通知分流）
"""

from .runner import run_ui2

__all__ = ["run_ui2"]
