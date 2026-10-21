"""macOS v2 界面（宽窗口 + 侧栏导航，INTERACTIONS-v2 实现）。

与 `ui/mac/**` 的关系：**并行的两套**，v2 验收通过前旧树一行不动。
入口是 `python -m dualswitch ui2` → `runner.run_ui2`。

模块地图（设计依据 `design/redesign/INTERACTIONS-v2.md`）：

    theme.py         视觉令牌 + QSS（明/暗）+ 键帽解析
    widgets.py       KeyCap / ToggleSwitch / Segmented / 缩略图资源
    window.py        780×580 主窗口 + 侧栏三页 + 底栏
    switch_page.py   切换页 hero（三态 + 强制切 + 设备卡）
    clipboard_page.py 剪贴板页（行解剖 + 四态药丸 + 筛选 + 双态空态）
    preview.py       悬浮预览窗（450ms 缩略图触发 + 弹簧气泡）
    openers.py       打开方式按类型分派（图片/文件/链接/文字）
    settings_page.py 设置页分组（即时生效）
    statusmenu.py    菜单栏弹出菜单 8 行结构
    runner.py        run_ui2
"""
