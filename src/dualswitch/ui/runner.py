"""把窗口、托盘、热键、切换控制器接到一起。

线程模型沿用 app.SwitchController：
  · 界面上点按钮 → 后台线程 switch_now()（阻塞约 65ms）
  · 热键触发     → controller.trigger() 自己入队，UI 只轮询状态
UI 线程只做绘制，不碰 DDC。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from .. import logging_setup
from . import theme
from .settings import SettingsWindow
from .window import MainWindow

log = logging_setup.get("ui")


def _tray_tip(hotkey_label: str) -> str:
    """托盘提示：顺带告诉用户是双击切换，避免单击没反应以为坏了。"""
    return f"屏幕切换 · {hotkey_label}（双击图标切换）"


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


class UiRunner(QObject):
    def __init__(self, cfg, cfg_file: Path, log_file: Path, backend, controller, hotkey, warning=None):
        super().__init__()
        self.cfg = cfg
        self.cfg_file = Path(cfg_file)
        self.log_file = Path(log_file)
        self.backend = backend
        self.controller = controller
        self.hotkey = hotkey
        self.warning = warning

        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setApplicationName("屏幕切换")
        self.app.setQuitOnLastWindowClosed(False)

        font = QFont()
        font.setFamilies(theme.FONT_FAMILIES)
        font.setPointSizeF(9.75)
        self.app.setFont(font)
        self.app.setStyleSheet(theme.qss())

        self.icon = QIcon(str(theme.asset("screen-switch.ico")))
        self.app.setWindowIcon(self.icon)

        hotkey_label = hotkey.describe() if hotkey else "未启用"
        self.window = MainWindow(cfg, hotkey_label, self._version())
        self.window.setWindowIcon(self.icon)
        self.settings = SettingsWindow(cfg, self.cfg_file, hotkey_label, self._version())
        self.settings.setWindowIcon(self.icon)

        self._busy = False
        self._hotkey_suspended = False
        self._quitting = False
        self._hotkey_spec = cfg.hotkey
        self._seen_history = len(controller.history)
        self._seen_source = controller.current_source

        self._build_tray(hotkey_label)
        self._shown_hotkey = hotkey_label      # 启动时界面已经是这个值
        self._wire()

        self.poll = QTimer(self)
        self.poll.setInterval(250)
        self.poll.timeout.connect(self._poll_state)
        self.poll.start()

        self._center(self.window)
        self._center(self.settings)
        self.window.show()
        if warning:
            self.window.show_banner("warn", f"热键未启用：{warning}　可继续用界面或托盘切换。")

    # ------------------------------------------------------------------ 组装
    def _center(self, win) -> None:
        screen = self.app.primaryScreen().availableGeometry()
        win.move(
            screen.center().x() - win.width() // 2,
            screen.center().y() - win.height() // 2,
        )

    def _version(self) -> str:
        from .. import __version__

        return f"v{__version__}"

    def _wire(self) -> None:
        self.window.switchRequested.connect(lambda: self.do_switch())
        self.window.probeRequested.connect(self.do_probe)
        self.window.doctorRequested.connect(self.do_doctor)
        self.window.openLogRequested.connect(lambda: _open_path(self.log_file))
        self.window.openSettingsRequested.connect(self.show_settings)
        self.settings.saved.connect(self.on_config_saved)
        self.settings.openConfigDirRequested.connect(lambda: _open_path(self.cfg_file))
        self.settings.probeRequested.connect(self.do_probe)
        self.settings.recordingChanged.connect(self._on_recording_changed)

        # 托盘左键双击 = 切换；单击不响应（单击太容易误触）
        self.tray.activated.connect(self._on_tray_activated)

    def _build_tray(self, hotkey_label: str) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip(_tray_tip(hotkey_label))

        menu = QMenu()
        act_open = QAction("打开主界面", menu)
        act_open.triggered.connect(self.show_window)
        menu.addAction(act_open)
        menu.addSeparator()

        other = self.cfg.other_host()
        other_label = "Mac (USB-C)" if other == "mac" else "Windows (HDMI)"
        self_label = "Mac (USB-C)" if other == "windows" else "Windows (HDMI)"

        act_switch = QAction(f"切换到 {other_label}", menu)
        act_switch.triggered.connect(lambda: self.do_switch())
        menu.addAction(act_switch)
        act_force = QAction(f"强制切到 {self_label}", menu)
        act_force.triggered.connect(lambda: self.do_switch(self.cfg.effective_host()))
        menu.addAction(act_force)
        menu.addSeparator()

        act_probe = QAction("探测显示器 / 读取输入源", menu)
        act_probe.triggered.connect(self.do_probe)
        menu.addAction(act_probe)
        act_cfg = QAction("打开配置目录", menu)
        act_cfg.triggered.connect(lambda: _open_path(self.cfg_file.parent))
        menu.addAction(act_cfg)
        act_log = QAction("查看日志", menu)
        act_log.triggered.connect(lambda: _open_path(self.log_file))
        menu.addAction(act_log)
        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.show_settings)
        menu.addAction(act_settings)
        menu.addSeparator()

        act_hotkey = QAction(f"热键：{hotkey_label}", menu)
        act_hotkey.setEnabled(False)
        self._act_hotkey = act_hotkey
        menu.addAction(act_hotkey)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)

        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.show()

    # ------------------------------------------------------------------ 动作
    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def show_settings(self) -> None:
        self.settings.showNormal()
        self.settings.raise_()
        self.settings.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.do_switch()

    def do_switch(self, target: str | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        self.window.clear_banner()
        self.window.set_busy(True)
        self.window.set_foot("正在发送切换指令…")

        def work() -> None:
            try:
                self.controller.switch_now(target, source="ui")
            except Exception:  # pragma: no cover
                log.exception("界面触发的切换出现异常")

        threading.Thread(target=work, name="ui-switch", daemon=True).start()

    def do_probe(self) -> None:
        def work() -> None:
            lines: list[str] = []
            try:
                monitors = self.backend.list_monitors()
                lines = [f"[{m.index}] {m.label()}" for m in monitors] or ["（未发现显示器）"]
            except Exception as exc:
                lines = [f"枚举失败：{exc}"]
            detail = ""
            try:
                detail = f"\n\n当前 VCP 0x{self.cfg.vcp_code:02X} = {self.backend.get_input()}（读数仅供参考）"
            except Exception as exc:
                detail = f"\n\n读取失败：{exc}（Q2790PC 的 HDMI 通道已知现象）"
            QTimer.singleShot(0, lambda: self._info("显示器探测", "\n".join(lines) + detail))

        threading.Thread(target=work, name="ui-probe", daemon=True).start()

    def do_doctor(self) -> None:
        host = "Windows" if sys.platform == "win32" else "macOS"
        hotkey_state = "已启用" if self.hotkey else "未启用"
        text = (
            f"平台　　：{host}\n"
            f"本机　　：{self.cfg.effective_host()}\n"
            f"目标　　：{self.cfg.other_host()}\n"
            f"热键　　：{self.hotkey.describe() if self.hotkey else '未启用'}（{hotkey_state}）\n"
            f"DDC 后端：{self.cfg.ddc_backend}\n"
            f"输入源　：Windows {self.cfg.sources['windows']} / Mac {self.cfg.sources['mac']}\n"
            f"配置文件：{self.cfg_file}\n"
            f"日志　　：{self.log_file}\n"
            f"演练模式：{'开' if self.cfg.dry_run else '关'}"
        )
        self._info("环境自检", text)

    def _info(self, title: str, text: str) -> None:
        box = QMessageBox(self.window)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Information)
        box.exec()

    # ------------------------------------------------------------------ 状态同步
    def _poll_state(self) -> None:
        changed = False
        self._refresh_hotkey_ui()      # 兜底：任何路径改了热键，250ms 内界面一定跟上
        source = self.controller.current_source
        if source != self._seen_source:
            self._seen_source = source
            self.window.set_state(source)
            changed = True
            # 没有新的切换记录 = 状态是后台检测纠正的（另一台机器切回来的）
            if len(self.controller.history) == self._seen_history:
                label = "Windows" if source == "windows" else "Mac"
                self.window.set_foot(f"已同步外部切换 · 当前 {label}")

        history = self.controller.history
        if len(history) > self._seen_history:
            self._seen_history = len(history)
            result = history[-1]
            self._busy = False
            self.window.set_busy(False)
            changed = True
            self._apply_result(result)

        if changed:
            self._refresh_led_only()

    def _refresh_led_only(self) -> None:
        host = self.controller.current_source
        self.window.card_win.set_active(host == "windows")
        self.window.card_mac.set_active(host == "mac")

    def _apply_result(self, result) -> None:
        if not result.ok:
            self.window.set_foot("切换失败，详见日志", ok=False)
            self.window.show_banner("error", f"切换失败：{result.error}")
            self._notify("切换失败", result.error or "未知错误")
            return

        self.window.clear_banner()
        if result.dry_run:
            self.window.set_foot("[演练] 未真正发送指令", ok=True)
            return
        ms = result.set_call_ms
        if result.readback == "confirmed":
            self.window.set_foot(f"上次切换 {ms:.1f} ms · 回读已确认")
        elif result.readback == "unconfirmed":
            self.window.set_foot(f"上次切换 {ms:.1f} ms · 回读未确认（已知现象）")
        else:
            self.window.set_foot(f"上次切换 {ms:.1f} ms · 指令已发送")

    def _notify(self, title: str, message: str) -> None:
        if self.cfg.notify and self.tray.isVisible():
            self.tray.showMessage(title, message, self.icon, 4000)

    # ------------------------------------------------------------------ 配置
    def on_config_saved(self, cfg) -> None:
        # 用"实际注册的热键"来比，而不是 self.cfg —— 配置对象可能被就地改过
        old_hotkey = self._hotkey_spec
        self.cfg = cfg
        self.controller.cfg = cfg
        self.window.cfg = cfg
        self.settings.cfg = cfg
        self.window.set_state(self.controller.current_source)

        # 热键或后端变了才重建，避免每次保存都抢一次注册
        if cfg.hotkey != old_hotkey or self.hotkey is None:
            self._rebuild_hotkey(cfg)

        self._refresh_hotkey_ui()
        self._notify("设置已保存", "配置已生效，无需重启。")

    # ------------------------------------------------------------------ 热键文案
    def _hotkey_label(self) -> str:
        return self.hotkey.describe() if self.hotkey else "未启用"

    def _refresh_hotkey_ui(self) -> None:
        """把"当前实际生效的热键"同步到主页面键帽、状态栏、托盘提示。

        统一走这里 + 轮询里兜底，避免某条路径漏更新（曾经出现过保存后主页面还是旧热键）。
        """
        label = self._hotkey_label()
        if label == self._shown_hotkey:
            return
        self._shown_hotkey = label
        text = f"热键已启用 · {label}" if self.hotkey else "热键未启用"
        self.window.statusbar.text.setText(text)
        self.window.hero.set_hotkey(label)
        self._act_hotkey.setText(f"热键：{label}")
        self.tray.setToolTip(_tray_tip(label))

    def _on_recording_changed(self, recording: bool) -> None:
        """录制热键时暂停全局热键。

        不暂停的话，按到「当前热键」会被系统当成全局热键吞掉，直接触发切换，
        录制窗口根本收不到这个按键（表现为：改回旧热键时画面切走了，也没录上）。
        """
        if recording:
            self._suspend_hotkey()
        else:
            # 延后一点再注册：录制是在 keyPress 里结束的，如果立刻 RegisterHotKey，
            # 这一次按下的组合键会被刚注册好的热键再吞一次（表现为录完就切走了）
            QTimer.singleShot(350, self._resume_hotkey)

    def _suspend_hotkey(self) -> None:
        if self._hotkey_suspended or self.hotkey is None:
            return
        try:
            self.hotkey.stop()
        except Exception:  # pragma: no cover
            log.exception("暂停全局热键失败")
        self._hotkey_suspended = True
        log.info("开始录制热键：全局热键已暂停")

    def _resume_hotkey(self) -> None:
        if not self._hotkey_suspended:
            return
        if self.settings.recorder.is_recording():   # 延迟期间又点开了录制
            return
        if self._quitting:                          # 延迟期间程序退出了
            return
        self._hotkey_suspended = False
        log.info("录制结束：恢复全局热键")
        self._rebuild_hotkey(self.cfg)

    def _rebuild_hotkey(self, cfg) -> None:
        from ..hotkey import HotkeyError, create_hotkey

        if self.hotkey is not None:
            try:
                self.hotkey.stop()
            except Exception:  # pragma: no cover
                pass
            self.hotkey = None
        try:
            hk = create_hotkey(cfg, self.controller.trigger)
            hk.start()
            self.hotkey = hk
            self._hotkey_spec = cfg.hotkey     # 记录"实际注册的那一个"，用来判断是否需要重建
            log.info("热键已重建：%s（后端 %s）", hk.describe(), hk.name)
            self.window.clear_banner()
        except HotkeyError as exc:
            log.error("新热键注册失败：%s", exc)
            self.window.show_banner("warn", f"新热键未生效：{exc}")
        self._refresh_hotkey_ui()

    def quit(self) -> None:
        log.info("用户从界面退出")
        self._quitting = True
        try:
            self.controller.stop()
        finally:
            if self.hotkey is not None:
                try:
                    self.hotkey.stop()
                except Exception:  # pragma: no cover
                    pass
            self.tray.hide()
            self.window.quit_completely()
            self.app.quit()

    def run(self) -> int:
        return self.app.exec()


def run_ui(cfg, cfg_file, log_file, backend, controller, hotkey, warning=None) -> int:
    runner = UiRunner(cfg, cfg_file, log_file, backend, controller, hotkey, warning)
    return runner.run()
