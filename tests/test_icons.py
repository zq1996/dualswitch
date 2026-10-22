"""图标资源守卫：design/icon 里的母版 ↔ ui/assets 里的运行时副本必须一致。

背景（2026-10-22）：换了一版矢量图标，只更新了 `design/icon/screen-switch.ico`，
而程序取图标走 `theme.asset()`，它**先查 `ui/assets/`**，于是源码运行和打包产物
都还在用几天前的旧文件 —— 现象是「新图标生成了，但 Dock 还是旧的」，很费时间。

这份用例不碰 Qt、不分平台：两端都在跑，谁的图标副本落后了都会红。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "design" / "icon"
RUNTIME = ROOT / "src" / "dualswitch" / "ui" / "assets"

APP_ICONS = ("screen-switch.ico", "screen-switch.icns")


@pytest.mark.parametrize("name", APP_ICONS)
def test_app_icon_runtime_copy_matches_master(name):
    master = DESIGN / name
    runtime = RUNTIME / name
    assert master.exists(), f"母版缺失：{master}（先跑 design/icon/build_icon.py）"
    assert runtime.exists(), (
        f"运行时副本缺失：{runtime}\n"
        "跑一遍 design/icon/build_icon.py —— 它会自动同步到 ui/assets。"
    )
    assert runtime.read_bytes() == master.read_bytes(), (
        f"{runtime.name} 与 design/icon 里的母版不一致（旧图标）。\n"
        "跑一遍 design/icon/build_icon.py 重新出图并同步，别手工 cp。"
    )


def test_menubar_template_icon_present():
    """菜单栏模板图：18pt 显示，出 36px（@2x）。丢了托盘就只剩一个空按钮。"""
    path = RUNTIME / "menubar.png"
    assert path.exists(), f"菜单栏模板图缺失：{path}"
    head = path.read_bytes()[:33]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "menubar.png 不是 PNG"
    width, height = struct.unpack(">II", head[16:24])
    assert (width, height) == (36, 36), f"模板图应为 36×36（18pt@2x），实际 {width}×{height}"
