"""把窗口、常驻入口（mac 菜单栏 / Win 托盘）、热键、切换控制器接到一起。

线程模型沿用 app.SwitchController：
  · 界面上点按钮 → 后台线程 switch_now()（阻塞约 65ms）
  · 热键触发     → controller.trigger() 自己入队，UI 只轮询状态
UI 线程只做绘制，不碰 DDC。

平台差异都收在 `statusitem` 与 `theme` 里，这个文件只走一条主流程：
菜单栏（或托盘）左键/双击 = 切换，右键 = 菜单，窗口关闭 = 收起来但继续常驻。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import logging_setup
from . import theme
from .chrome import current as chrome
from .settings import SettingsWindow
from .window import MainWindow

log = logging_setup.get("ui")


def _status_tip(hotkey_label: str) -> str:
    """常驻图标提示：写清点击能干什么，避免点一下没反应以为坏了。"""
    return f"屏幕切换 · {theme.pretty_hotkey(hotkey_label)}（{theme.CLICK_HINT}）"


def _drawn_icon() -> QIcon:
    """兜底图标：复用托盘那套 Pillow 画的箭头图（画不出来就返回空图标，不阻断启动）。"""
    try:
        from PySide6.QtGui import QImage, QPixmap

        from ..tray import make_icon_image

        image = make_icon_image().resize((64, 64))
        data = image.tobytes("raw", "RGBA")
        qimage = QImage(data, image.width, image.height, QImage.Format_RGBA8888).copy()
        return QIcon(QPixmap.fromImage(qimage))
    except Exception:
        log.exception("生成兜底图标失败")
        return QIcon()


def load_app_icon() -> QIcon:
    """应用图标：包内的 .ico / .icns 优先，读不出来时退回代码画的图标。

    `QIcon(路径)` 遇到「文件在、但 Qt 没装对应 imageformats 插件」时**不抛异常**，
    只返回一个空图标 —— 空图标在托盘里就是「什么都没有」，而窗口图标看不出来
    （任务栏显示的是 exe 自带的图标资源）。打包后少带一个 `qico` 插件，
    就会正好落进这个静默失败的坑，症状是「进程在，托盘没图标」。
    """
    try:
        path = theme.app_icon()
    except FileNotFoundError:
        log.error("找不到应用图标（assets 里没有 .ico / .icns），改用内置绘制图标")
        return _drawn_icon()

    icon = QIcon(str(path))
    if icon.isNull():
        log.error("应用图标读不出来：%s（检查 PySide6/plugins/imageformats/qico），改用内置绘制图标", path)
        return _drawn_icon()

    sizes = [f"{s.width()}x{s.height()}" for s in icon.availableSizes()] or ["无"]
    log.info("应用图标：%s（尺寸 %s）", path, "、".join(sizes))
    return icon


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
        self.app.setApplicationDisplayName("屏幕切换")
        self.app.setQuitOnLastWindowClosed(False)

        font = QFont()
        font.setFamilies(theme.FONT_FAMILIES)
        font.setPointSizeF(theme.APP_FONT_POINT)
        self.app.setFont(font)
        self.app.setStyleSheet(theme.qss())
        self.chrome = chrome()

        self.icon = load_app_icon()
        self.app.setWindowIcon(self.icon)

        hotkey_label = hotkey.describe() if hotkey else "未启用"
        self.window = MainWindow(
            cfg, hotkey_label, self._version(), monitor_name=self._monitor_name()
        )
        self.window.setWindowIcon(self.icon)
        # mac：设置是同一个窗口里的另一页，不另开窗口
        self.settings = SettingsWindow(
            cfg,
            self.cfg_file,
            hotkey_label,
            self._version(),
            parent=self.window if self.chrome.SETTINGS_IN_WINDOW else None,
            embedded=bool(self.chrome.SETTINGS_IN_WINDOW),
        )
        self.settings.setWindowIcon(self.icon)
        if self.chrome.SETTINGS_IN_WINDOW:
            self.window.attach_settings(self.settings)
            self.settings.backRequested.connect(self.close_settings)

        self._busy = False
        self._hotkey_suspended = False
        self._quitting = False
        self._retry_on_activate = False       # 去授权后切回应用就自动重试热键
        self._hotkey_spec = cfg.hotkey
        self._seen_history = len(controller.history)
        self._seen_source = controller.current_source

        self.menubar = None
        self._build_status(hotkey_label)
        self._build_app_menu()
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
            self._show_hotkey_problem(warning)

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

    def _monitor_name(self) -> str:
        """状态卡上显示的显示器名：优先用后端枚举到的真实名字。"""
        if self.cfg.monitor_match:
            return self.cfg.monitor_match
        try:
            monitors = self.backend.list_monitors()
        except Exception:
            return ""
        if not monitors:
            return ""
        return monitors[0].description or ""

    def _wire(self) -> None:
        self.window.switchRequested.connect(lambda: self.do_switch())
        self.window.probeRequested.connect(self.do_probe)
        self.window.doctorRequested.connect(self.do_doctor)
        self.window.openLogRequested.connect(lambda: self.chrome.open_path(self.log_file))
        self.window.openSettingsRequested.connect(self.show_settings)
        # 窗口收起 → mac 上顺手把程序坞图标也收掉（菜单栏图标不受影响）
        self.window.hiddenToBackground.connect(self._enter_background)
        self.settings.saved.connect(self.on_config_saved)
        self.settings.openConfigDirRequested.connect(lambda: self.chrome.open_path(self.cfg_file))
        self.settings.probeRequested.connect(self.do_probe)
        self.settings.recordingChanged.connect(self._on_recording_changed)
        # Dock 图标点击 / Cmd+Tab 回到本应用时，若窗口收起来了就再拿出来
        self.app.installEventFilter(self)

    def _build_status(self, hotkey_label: str) -> None:
        # Windows 返回托盘（双击切换），macOS 返回菜单栏状态项（单击切换）
        self.status = self.chrome.build_status_item(self.icon, self._on_status_click)
        self.status.set_tooltip(_status_tip(hotkey_label))
        self._refresh_menu()
        self._check_status_item()
        # 图标要等事件循环转起来才真正落进通知区域，2.5 秒后回头确认一次
        QTimer.singleShot(2500, self._verify_status_item)

    def _check_status_item(self) -> None:
        """常驻图标是关窗口之后**唯一**的入口：它不可用就不能再「收进托盘」。

        这台机器压根没有系统托盘（远程桌面 / 精简系统）时，如果还按
        「关窗口 = 隐藏」，用户就既看不到窗口也看不到图标，只剩任务管理器。
        """
        probe = getattr(self.status, "is_available", None)
        available = True
        if callable(probe):
            try:
                available = bool(probe())
            except Exception:  # pragma: no cover
                log.exception("检查常驻图标可用性失败")
        if available:
            return

        log.error("系统托盘不可用：关闭窗口后无法从托盘找回程序，改为最小化")
        self.window.withdraw_to_tray = False
        self.window.show_banner(
            "warn",
            "系统托盘不可用，关闭窗口后会最小化到任务栏（不会隐藏）。",
        )

    def _verify_status_item(self) -> None:
        """启动 2.5 秒后回头确认图标真的进了通知区域。

        `QSystemTrayIcon.show()` 注册失败时既不抛异常也不返回错误。所以只能事后查：
        可见 = False → 重新注册一次；可见 = True 但桌面上看不到，说明系统把图标收进了
        「隐藏的图标」溢出面板（Win11 对没出现过的新 exe 默认如此），代码无能为力，
        但这一行日志足以让排障有据可依。
        """
        if self._quitting:
            return
        try:
            visible = bool(self.status.is_visible())
            log.info("常驻图标检查：可见=%s", visible)
            if visible:
                return
            log.warning("常驻图标未注册成功，重新注册一次")
            reassert = getattr(self.status, "reassert", None)
            if callable(reassert):
                reassert()
            else:
                self.status.show()
        except Exception:  # pragma: no cover
            log.exception("常驻图标自检失败")

    def _status_actions(self) -> dict:
        return {
            "open": self.show_window,
            "switch": lambda: self.do_switch(),
            "force": lambda: self.do_switch(self.cfg.effective_host()),
            "probe": self.do_probe,
            "config_dir": lambda: self.chrome.open_path(self.cfg_file.parent),
            "log": lambda: self.chrome.open_path(self.log_file),
            "settings": self.show_settings,
            "quit": self.quit,
        }

    def _refresh_menu(self) -> None:
        from .statusitem import build_menu_entries

        entries = build_menu_entries(
            self.cfg,
            self.controller.current_source,
            self._hotkey_label(),
            self._status_actions(),
        )
        try:
            self.status.set_menu(entries)
        except Exception:  # pragma: no cover
            log.exception("刷新常驻菜单失败")

    def _build_app_menu(self) -> None:
        """全局菜单栏（仅 macOS 有）：设置 ⌘, / 隐藏 ⌘H / 退出 ⌘Q。"""
        self.menubar = self.chrome.build_app_menu(self)

    # ------------------------------------------------------------------ 动作
    def _enter_background(self) -> None:
        """窗口收起来之后的收尾：mac 把程序坞图标一起收掉，只留菜单栏。"""
        if self.chrome.DOCK_FOLLOWS_WINDOW:
            self.chrome.set_dock_visible(False)

    def _leave_background(self) -> None:
        if self.chrome.DOCK_FOLLOWS_WINDOW:
            self.chrome.set_dock_visible(True)

    def show_window(self) -> None:
        self._leave_background()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        self.chrome.activate(self.window)

    def hide_window(self) -> None:
        """⌘H / 菜单「隐藏」：收起窗口但不退出，程序坞图标随之消失。"""
        self.window.hide()
        self._enter_background()

    def show_settings(self) -> None:
        # 内嵌模式：翻到主窗口的设置页；独立模式：把设置窗口拿到前台
        if self.chrome.SETTINGS_IN_WINDOW:
            self._leave_background()
            self.window.show_settings_page()
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            self.chrome.activate(self.window)
            return
        self.settings.showNormal()
        self.settings.raise_()
        self.settings.activateWindow()
        self.chrome.activate(self.settings)

    def close_settings(self) -> None:
        """退出设置：内嵌模式翻回主页面，独立模式收起窗口。"""
        try:
            self.settings.recorder.stop_recording()
        except Exception:  # pragma: no cover - 没有录制器也不影响退出
            pass
        if self.chrome.SETTINGS_IN_WINDOW:
            self.window.show_main_page()
        else:
            self.settings.hide()

    def _on_status_click(self) -> None:
        # macOS 菜单栏左键直接切换；Windows 侧 QtStatusItem 已过滤成只认双击
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
                detail = f"\n\n读取失败：{exc}"
            QTimer.singleShot(0, lambda: self._info("显示器探测", "\n".join(lines) + detail))

        threading.Thread(target=work, name="ui-probe", daemon=True).start()

    def do_doctor(self) -> None:
        host = self.chrome.PLATFORM_LABEL
        hotkey_state = "已启用" if self.hotkey else "未启用"
        lines = [
            f"平台　　：{host}",
            f"本机　　：{self.cfg.effective_host()}",
            f"目标　　：{self.cfg.other_host()}",
            f"热键　　：{self.hotkey.describe() if self.hotkey else '未启用'}（{hotkey_state}）",
            f"DDC 后端：{self.cfg.ddc_backend}",
            f"输入源　：Windows {self.cfg.sources['windows']} / Mac {self.cfg.sources['mac']}",
            f"配置文件：{self.cfg_file}",
            f"日志　　：{self.log_file}",
            f"演练模式：{'开' if self.cfg.dry_run else '关'}",
        ]
        try:
            monitors = self.backend.list_monitors()
            lines.append("显示器　：" + ("; ".join(m.description or m.label() for m in monitors) or "未发现"))
        except Exception as exc:
            lines.append(f"显示器　：枚举失败（{exc}）")
        self._info("环境自检", "\n".join(lines))

    def _info(self, title: str, text: str) -> None:
        box = QMessageBox(self.window)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Information)
        box.exec()

    # ------------------------------------------------------------------ 状态同步
    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.ApplicationActivate and self.chrome.REOPEN_ON_ACTIVATE:
            # 点 Dock 图标 / Cmd+Tab 回来：窗口被收起来就再打开（mac 原生行为）
            if not self._quitting and self.window.isHidden() and not self.settings.isVisible():
                self._leave_background()
                self.window.showNormal()
            # 刚从「辅助功能」设置授权回来：就地重试，不用重启程序
            if self._retry_on_activate and self.hotkey is None and not self._hotkey_suspended:
                QTimer.singleShot(250, self.retry_hotkey)
        return super().eventFilter(obj, event)

    def _poll_state(self) -> None:
        changed = False
        self._refresh_hotkey_ui()      # 兜底：任何路径改了热键，250ms 内界面一定跟上
        source = self.controller.current_source
        if source != self._seen_source:
            self._seen_source = source
            self.window.set_state(source)
            self._refresh_menu()       # 菜单里的「当前画面 / 切换目标」跟着翻
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
        if not self.cfg.notify:
            return
        try:
            self.chrome.notify(self.status, title, message, self.icon)
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------------ 权限
    def _show_hotkey_problem(self, message: str) -> None:
        """热键起不来：mac 上给一句「该给谁授权」+ 一个「去授权」按钮。

        没这个按钮时，用户只会看到「缺少辅助功能权限」然后去设置里翻，
        而 TCC 列表里根本还没有本应用 —— 因为只有主动请求过才会被写进去。
        """
        target = self.chrome.permission_target()
        text = f"热键未启用：{message}"
        if target:
            self._retry_on_activate = True
            self.window.show_banner("warn", text, "去授权", self.go_grant_permission)
        else:
            self.window.show_banner("warn", text)

    def go_grant_permission(self) -> None:
        """打开「辅助功能」面板；用户勾选后切回本应用会自动再试一次。"""
        granted = self.chrome.request_accessibility()
        if granted:
            self.retry_hotkey()
        elif self.chrome.permission_target():
            self.window.show_banner(
                "warn",
                f"已打开「辅助功能」设置：请在列表里打开「{self.chrome.permission_target()}」，"
                "然后切回本窗口，会自动重试。",
            )

    def retry_hotkey(self) -> None:
        """重新注册一次热键（授权后不用重启整个程序）。"""
        self._retry_on_activate = False
        self._rebuild_hotkey(self.cfg)

    # ------------------------------------------------------------------ 配置
    def on_config_saved(self, cfg) -> None:
        # 用"实际注册的热键"来比，而不是 self.cfg —— 配置对象可能被就地改过
        old_hotkey = self._hotkey_spec
        self.cfg = cfg
        self.controller.cfg = cfg
        self.window.cfg = cfg
        self.settings.cfg = cfg
        self.window.set_state(self.controller.current_source)

        # 热键改了才重建，避免每次保存都抢一次注册
        if cfg.hotkey != old_hotkey or self.hotkey is None:
            self._rebuild_hotkey(cfg)

        self._refresh_hotkey_ui()
        self._refresh_menu()
        self._notify("设置已保存", "配置已生效，无需重启。")
        # 内嵌模式：保存完自动回主页面（独立窗口模式保持原样，窗口留着）
        if self.chrome.SETTINGS_IN_WINDOW:
            self.close_settings()

    # ------------------------------------------------------------------ 热键文案
    def _hotkey_label(self) -> str:
        return self.hotkey.describe() if self.hotkey else "未启用"

    def _refresh_hotkey_ui(self) -> None:
        """把"当前实际生效的热键"同步到主页面键帽、状态栏、菜单栏提示与菜单项。

        统一走这里 + 轮询里兜底，避免某条路径漏更新（曾经出现过保存后主页面还是旧热键）。
        """
        label = self._hotkey_label()
        if label == self._shown_hotkey:
            return
        self._shown_hotkey = label
        text = f"热键已启用 · {label}" if self.hotkey else "热键未启用"
        self.window.statusbar.text.setText(text)
        self.window.hero.set_hotkey(label)
        self.status.set_tooltip(_status_tip(label))
        self._refresh_menu()

    def _on_recording_changed(self, recording: bool) -> None:
        """录制热键时暂停全局热键。

        不暂停的话，按到「当前热键」会被系统当成全局热键吞掉，直接触发切换，
        录制窗口根本收不到这个按键（表现为：改回旧热键时画面切走了，也没录上）。
        """
        if recording:
            self._suspend_hotkey()
        else:
            # 延后一点再注册：录制是在 keyPress 里结束的，如果立刻注册，
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
            self._retry_on_activate = False
            log.info("热键已重建：%s（后端 %s）", hk.describe(), hk.name)
            self.window.clear_banner()
        except HotkeyError as exc:
            log.error("新热键注册失败：%s", exc)
            self._show_hotkey_problem(str(exc))
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
            try:
                self.status.hide()
            except Exception:  # pragma: no cover
                pass
            self.window.quit_completely()
            self.app.quit()

    def run(self) -> int:
        return self.app.exec()


def run_ui(cfg, cfg_file, log_file, backend, controller, hotkey, warning=None) -> int:
    runner = UiRunner(cfg, cfg_file, log_file, backend, controller, hotkey, warning)
    return runner.run()
