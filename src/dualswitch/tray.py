"""托盘 / 菜单栏常驻。

依赖 pystray + Pillow（可选）。缺失时由 CLI 自动降级为无界面常驻，
核心切换功能不受影响。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from . import logging_setup

log = logging_setup.get("tray")


def tray_available() -> bool:
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except Exception:
        return False
    return True


def make_icon_image():
    """程序化画一个图标：深色圆角底 + 一对交换箭头。

    除了 pystray 托盘，`ui.runner` 在 `.ico` / `.icns` 读不出来时也拿它当兜底图标 ——
    打包后 imageformats 插件缺失时 `QIcon(路径)` 不报错、只返回空图标，托盘会变成一片空白。
    """
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=14, fill=(31, 36, 48, 255))

    # 上箭头（向右）
    draw.line((16, 24, 44, 24), fill=(120, 220, 200, 255), width=5)
    draw.polygon([(44, 17), (54, 24), (44, 31)], fill=(120, 220, 200, 255))
    # 下箭头（向左）
    draw.line((20, 40, 48, 40), fill=(240, 244, 250, 255), width=5)
    draw.polygon([(20, 33), (10, 40), (20, 47)], fill=(240, 244, 250, 255))
    return img


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # pragma: no cover
        log.warning("打开路径失败 %s: %s", path, exc)


class TrayApp:
    """持有 pystray Icon，并在主线程 run()。"""

    def __init__(self, cfg, controller, backend, config_file: Path, log_file: Path, hotkey_label: str):
        self.cfg = cfg
        self.controller = controller
        self.backend = backend
        self.config_file = Path(config_file)
        self.log_file = Path(log_file)
        self.hotkey_label = hotkey_label
        self.icon = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------ 通知
    def notify(self, title: str, message: str) -> None:
        if not self.cfg.notify or self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception as exc:  # pragma: no cover
            log.debug("托盘通知失败: %s", exc)

    # ------------------------------------------------------------------ 菜单
    def _switch_other(self, icon=None, item=None) -> None:  # noqa: ANN001
        threading.Thread(target=self._do_switch, args=(None,), daemon=True).start()

    def _switch_to(self, target: str):
        def _handler(icon=None, item=None) -> None:  # noqa: ANN001
            threading.Thread(target=self._do_switch, args=(target,), daemon=True).start()

        return _handler

    def _do_switch(self, target: str | None) -> None:
        result = self.controller.switch_now(target, source="tray")
        if not result.ok:
            self.notify("切换失败", result.error or "未知错误")
        elif result.readback == "unconfirmed":
            self.notify("切换指令已发送", "显示器未确认，详见日志。")

    def _probe(self, icon=None, item=None) -> None:  # noqa: ANN001
        try:
            monitors = self.backend.list_monitors()
            lines = [f"{m.index}: {m.label()}" for m in monitors] or ["（无）"]
            detail = ""
            try:
                detail = f"\n当前 VCP 0x{self.cfg.vcp_code:02X} = {self.backend.get_input()}"
            except Exception as exc:
                detail = f"\n读取失败：{exc}"
            self.notify("显示器探测", "\n".join(lines) + detail)
        except Exception as exc:
            self.notify("显示器探测失败", str(exc))

    def _open_config(self, icon=None, item=None) -> None:  # noqa: ANN001
        _open_path(self.config_file.parent)

    def _open_log(self, icon=None, item=None) -> None:  # noqa: ANN001
        if self.log_file.exists():
            _open_path(self.log_file)
        else:
            _open_path(self.log_file.parent)

    def _quit(self, icon=None, item=None) -> None:  # noqa: ANN001
        log.info("用户从托盘退出")
        self._stop_event.set()
        try:
            self.controller.stop()
        finally:
            if self.icon is not None:
                self.icon.stop()

    def _build_menu(self):
        import pystray

        other = self.cfg.other_host()
        other_label = "Mac (USB-C)" if other == "mac" else "Windows (HDMI)"
        this_label = "Windows (HDMI)" if other == "mac" else "Mac (USB-C)"

        return pystray.Menu(
            pystray.MenuItem(f"切换到 {other_label}", self._switch_other, default=True),
            pystray.MenuItem(f"强制切到 {this_label}", self._switch_to(self.cfg.effective_host())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("探测显示器 / 读取当前输入源", self._probe),
            pystray.MenuItem("打开配置目录", self._open_config),
            pystray.MenuItem("查看日志", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"热键：{self.hotkey_label}", None, enabled=False),
            pystray.MenuItem("退出", self._quit),
        )

    # ------------------------------------------------------------------ 运行
    def run(self, warning: str | None = None) -> None:
        import pystray

        self.icon = pystray.Icon(
            "dualswitch",
            icon=make_icon_image(),
            title=f"dualswitch · {self.hotkey_label}",
            menu=self._build_menu(),
        )

        def _setup(icon):  # noqa: ANN001
            icon.visible = True
            if warning:
                self.notify("热键未启用", warning)

        self.icon.run(setup=_setup)
        self._stop_event.set()

    def wait(self) -> None:
        self._stop_event.wait()
