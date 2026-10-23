"""v2 运行器：窗口三页 + 菜单栏菜单 + 热键 + 切换控制器 接到一起。

线程模型沿用 app.SwitchController：
  · 界面上点按钮 → 后台线程 switch_now()（阻塞约 65ms）
  · 热键触发     → controller.trigger() 自己入队，UI 只轮询状态
UI 线程只做绘制，不碰 DDC。「强制切」绕过本地优先策略直接发 Set（§2.3）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ... import __version__, logging_setup
from ...config import SOURCE_UNKNOWN, save as save_config
from ..clipboard_feed import LocalClipFeed
from . import openers, statusmenu, theme
from .clipboard_page import ClipboardPage
from .pairing_page import PairingPage
from .settings_page import SettingsPage
from .switch_page import SwitchPage
from .window import PAGE_CLIP, PAGE_PAIR, PAGE_SETTINGS, PAGE_SWITCH, MainWindow

log = logging_setup.get("ui")


# --------------------------------------------------------------------------- #
# Toast（窗口内轻提示，INTERACTIONS 各处 toast 文案的落点）
# --------------------------------------------------------------------------- #
class Toast(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignCenter)
        t = theme.tokens()
        self._label.setStyleSheet(
            f"background:{t.ink};color:{t.card};font-size:12px;font-weight:600;"
            "padding:10px 16px;border-radius:11px;"
        )
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, ms: int = 1900) -> None:
        self._label.setText(text)
        self._label.adjustSize()
        w, h = self._label.width() + 2, self._label.height() + 2
        pw = self.parentWidget().width()
        ph = self.parentWidget().height()
        self.setGeometry((pw - w) // 2, ph - h - 32, w, h)
        self._label.setGeometry(0, 0, w, h)
        self.raise_()
        self.show()
        self._timer.start(ms)


# --------------------------------------------------------------------------- #
# 运行器
# --------------------------------------------------------------------------- #
class UiRunner2(QObject):
    #: 剪贴板热键的回调跑在 **CGEventTap 的后台线程**里，那里绝对不能碰 Qt：
    #: 跨线程操作 QWidget 会立刻把进程干掉（2026-10-23 用户实测：一按热键就崩）。
    #: 回调只 emit 这个信号，靠 Qt 的 queued connection 送回主线程再动界面。
    clipboardHotkeyPressed = Signal()

    def __init__(self, cfg, cfg_file: Path, log_file: Path, backend, controller, hotkey,
                 warning=None, clip_engine=None):
        super().__init__()
        self.cfg = cfg
        self.cfg_file = Path(cfg_file)
        self.log_file = Path(log_file)
        self.backend = backend
        self.controller = controller
        self.hotkey = hotkey
        self.warning = warning
        self.clip_engine = clip_engine

        self.app = QApplication.instance() or QApplication([])
        self.app.setApplicationName("屏幕切换")
        self.app.setApplicationDisplayName("屏幕切换")
        self.app.setQuitOnLastWindowClosed(False)

        font = QFont()
        font.setFamilies(theme.FONT_FAMILIES)
        font.setPixelSize(int(round(theme.APP_FONT_PX)))
        self.app.setFont(font)
        # 外观固定浅色：用户 2026-10-21 明确不要深色模式（设置页已移除该项）
        theme.set_appearance("light")
        self.app.setStyleSheet(theme.qss())

        self.icon = theme.app_icon()
        self.app.setWindowIcon(self.icon)

        hotkey_label = hotkey.describe() if hotkey else "未启用"
        self.window = MainWindow(cfg, hotkey_label, f"v{__version__}", self._monitor_name())
        self.window.setWindowIcon(self.icon)

        self.switch_page = SwitchPage(cfg, hotkey_label, self._monitor_name())
        self.clip_feed = clip_engine if clip_engine is not None else LocalClipFeed(cfg)
        self.clipboard_page = ClipboardPage(
            self.clip_feed,
            open_external=openers.MacOpeners(),
            hover_enabled=lambda: bool(
                getattr(getattr(self.cfg, "clipboard", None), "hover_preview", True)
            ),
        )
        self.settings_page = SettingsPage(cfg, self.cfg_file)
        self.pairing_page = PairingPage(
            clip_engine, default_port=cfg.clipboard.effective_peer_port()
        )
        self.window.attach_pages(
            self.switch_page, self.clipboard_page, self.settings_page, self.pairing_page
        )
        # 配对落盘要用「当前」cfg（设置页保存后 runner 会换成新对象）
        if self.clip_engine is not None:
            self.clip_engine.set_config_saver(lambda: save_config(self.cfg, self.cfg_file))

        self.toast = Toast(self.window)

        self._busy = False
        self._hotkey_suspended = False
        self._quitting = False
        self._hotkey_spec = cfg.hotkey
        self._seen_history = len(controller.history)
        self._seen_source = controller.source_state
        self.window.switchRequested.connect(lambda: self.do_switch())
        self.window.forceRequested.connect(self.do_force)
        self.window.deviceClicked.connect(lambda host: self.do_switch())
        self.window.hiddenToBackground.connect(self._enter_background)
        self.window.pageChanged.connect(self._on_page_changed)
        self.window.quitRequested.connect(self.quit_application)
        self.pairing_page.toastRequested.connect(self.toast.show_text)
        self.pairing_page.pairedChanged.connect(self._on_paired_changed)
        self._last_page = PAGE_SWITCH
        self._pair_text = ""
        self.switch_page.clipboardRequested.connect(lambda: self.show_page(PAGE_CLIP))
        self.switch_page.toastRequested.connect(self.toast.show_text)
        self.clipboard_page.toastRequested.connect(self.toast.show_text)
        self.clipboard_page.pausedChanged.connect(self._on_clip_paused)
        self.settings_page.toastRequested.connect(self.toast.show_text)
        self.settings_page.saved.connect(self.on_config_saved)
        self.settings_page.clearRequested.connect(
            self.clipboard_page.clear_records_with_confirm
        )
        self.settings_page.recordingChanged.connect(self._on_recording_changed)

        # 首帧就用控制器已经确认过的状态
        self.switch_page.set_state(self._seen_source)
        if self._seen_source == SOURCE_UNKNOWN:
            self.switch_page.set_note("插上外接屏会自动恢复", ok=False)

        self.status = None
        self._build_status()
        self._build_app_menu()
        self._clip_hotkey = None
        # 热键回调在 tap 线程：只允许发信号，界面操作一律回主线程
        self.clipboardHotkeyPressed.connect(self._on_clipboard_hotkey)
        self._build_clipboard_hotkey()
        self._refresh_menu()
        self._refresh_hotkey_ui()
        self._refresh_conn_ui()
        self._refresh_pair_ui()

        self.pair_timer = QTimer(self)
        self.pair_timer.setInterval(1000)
        self.pair_timer.timeout.connect(self._refresh_pair_ui)
        self.pair_timer.start()

        self.poll = QTimer(self)
        self.poll.setInterval(250)
        self.poll.timeout.connect(self._poll_state)
        self.poll.start()

        self.clip_timer = QTimer(self)
        self.clip_timer.setInterval(300)
        self.clip_timer.timeout.connect(self._refresh_clipboard)
        self.clip_timer.start()

        self.clip_poll = None
        if self.clip_feed is not clip_engine:
            self.clip_poll = QTimer(self)
            self.clip_poll.setInterval(500)
            self.clip_poll.timeout.connect(self._poll_local_clip)
            self.clip_poll.start()

        self.app.installEventFilter(self)
        self._center(self.window)
        self.window.show()
        if warning:
            self.window.switch_page.set_note(f"热键未启用：{warning}", ok=False)

    # ------------------------------------------------------------------ 组装
    def _center(self, win) -> None:  # noqa: ANN001
        screen = self.app.primaryScreen().availableGeometry()
        win.move(
            screen.center().x() - win.width() // 2,
            screen.center().y() - win.height() // 2,
        )

    def _monitor_name(self) -> str:
        if self.cfg.monitor_match:
            return self.cfg.monitor_match
        try:
            monitors = self.backend.list_monitors()
        except Exception:  # noqa: BLE001
            return ""
        return (monitors[0].description or "") if monitors else ""

    def _build_status(self) -> None:
        icon_path = theme.asset("menubar.png")
        self.status = statusmenu.build_status_item(
            icon_path, self._on_status_click, self.show_window
        )
        if self.status is not None:
            self.status.set_tooltip(f"屏幕切换 · {self._hotkey_label()}（单击图标打开菜单）")
        else:
            log.warning("菜单栏状态项不可用（AppKit 缺失），只能从窗口操作")
            self.window.withdraw_to_tray = False

    def _build_app_menu(self) -> None:
        """全局菜单栏：设置 ⌘, / 剪贴板 ⌘⇧V / 隐藏 ⌘H / 退出 ⌘Q。"""
        from PySide6.QtGui import QAction, QKeySequence
        from PySide6.QtWidgets import QMenu, QMenuBar

        bar = QMenuBar()
        app_menu = QMenu("屏幕切换", bar)

        act_clip = QAction("剪贴板", self.window)
        act_clip.setShortcut(QKeySequence("Ctrl+Shift+V"))
        act_clip.triggered.connect(lambda: self.show_page(PAGE_CLIP))
        app_menu.addAction(act_clip)

        act_settings = QAction("设置…", self.window)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(lambda: self.show_page(PAGE_SETTINGS))
        app_menu.addAction(act_settings)
        app_menu.addSeparator()

        act_hide = QAction("隐藏 屏幕切换", self.window)
        act_hide.setShortcut(QKeySequence("Ctrl+H"))
        act_hide.triggered.connect(self.hide_window)
        app_menu.addAction(act_hide)

        act_quit = QAction("退出 屏幕切换", self.window)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.quit_application)
        app_menu.addAction(act_quit)

        bar.addMenu(app_menu)
        self.menubar = bar

    # ------------------------------------------------------------------ 动作
    def _enter_background(self) -> None:
        # 关窗 = 离开配对页：显示中的配对码立即失效（§12.5）
        if getattr(self, "pairing_page", None) is not None:
            self.pairing_page.deactivate()
        from ..chrome import current as chrome

        chrome().set_dock_visible(False)

    def _leave_background(self) -> None:
        from ..chrome import current as chrome

        chrome().set_dock_visible(True)

    def show_window(self) -> None:
        self._leave_background()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        if self.window.current_page() == PAGE_PAIR:
            self.pairing_page.activate()
        from ..chrome import current as chrome

        chrome().activate(self.window)

    def hide_window(self) -> None:
        self.window.hide()
        self._enter_background()

    def show_page(self, index: int) -> None:
        self._leave_background()
        self.window.show_page(index)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        from ..chrome import current as chrome

        chrome().activate(self.window)
        if index == PAGE_CLIP:
            self.clipboard_page.refresh()

    def open_pair_page(self) -> None:
        """侧栏 / 菜单 / 剪贴板引导共用的唯一入口（§12.3）。"""
        self.show_page(PAGE_PAIR)

    def _on_page_changed(self, index: int) -> None:
        previous, self._last_page = self._last_page, index
        if index == PAGE_PAIR:
            self.pairing_page.activate()
        elif previous == PAGE_PAIR:
            self.pairing_page.deactivate()

    def _on_paired_changed(self, paired: bool) -> None:
        self.clipboard_page.mark_dirty()
        self._refresh_pair_ui()
        self._refresh_menu()

    def _refresh_pair_ui(self) -> None:
        if getattr(self, "pairing_page", None) is None:
            return
        text = self.pairing_page.status_text()
        self.window.nav_pair.set_status_dot(self.pairing_page.status_tone(), text)
        if text != self._pair_text:
            self._pair_text = text
            self._refresh_menu()

    def do_switch(self, target: str | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        other = "Mac" if self.cfg.other_host() == "mac" else "Windows"
        self.switch_page.set_busy(True, other)
        self.switch_page.set_note(f"正在发送 DDC Set · 目标 {other}", ok=False)

        def work() -> None:
            try:
                self.controller.switch_now(target, source="ui")
            except Exception:  # noqa: BLE001
                log.exception("界面触发的切换出现异常")

        threading.Thread(target=work, name="ui-switch", daemon=True).start()

    def do_force(self) -> None:
        """强制切：跳过「本地优先、失败才请对端」，直接本机发 DDC Set（§2.3）。"""
        if self._busy:
            return
        host = self.controller.source_state
        if host == SOURCE_UNKNOWN:
            self.toast.show_text("未检测到显示器，无法强制切换")
            return
        target = "mac" if host == "windows" else "windows"
        value = self.cfg.target_value(target)
        self._busy = True
        self.switch_page.set_busy(True, "Mac" if target == "mac" else "Windows")
        self.switch_page.set_note(f"强制发送 DDC Set · 目标 {value}", ok=False)

        def work() -> None:
            try:
                self.backend.set_input(value)
                self.switch_page.toastRequested.emit(f"已强制切换（Set {value}）")
            except Exception as exc:  # noqa: BLE001
                log.error("强制切换失败：%s", exc)
                QTimer.singleShot(
                    0, lambda: self.switch_page.set_note(f"强制切换失败：{exc}", ok=False, bad=True)
                )
            finally:
                QTimer.singleShot(0, lambda: self._force_done(target))

        threading.Thread(target=work, name="ui-force", daemon=True).start()

    def _force_done(self, target: str) -> None:
        self._busy = False
        self.switch_page.set_busy(False)
        self.switch_page.set_state(target)
        self._refresh_menu()

    def _on_status_click(self) -> None:
        """菜单栏图标单击 = 弹菜单（INTERACTIONS §1：左右键都弹菜单）。"""
        pass    # MacStatusItem 自己弹

    # ------------------------------------------------------------------ 状态同步
    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.ApplicationActivate:
            if not self._quitting and self.window.isHidden():
                self._leave_background()
                self.window.showNormal()
                if self.window.current_page() == PAGE_PAIR:
                    self.pairing_page.activate()
            if self._retry_on_activate and self.hotkey is None and not self._hotkey_suspended:
                QTimer.singleShot(250, self.retry_hotkey)
        return super().eventFilter(obj, event)

    _retry_on_activate = False

    def _refresh_clipboard(self) -> None:
        if self.clipboard_page is None:
            return
        if self.window.on_clipboard_page():
            self.clipboard_page.refresh()
        # 侧栏计数 / 切换页徽章无论在哪页都要跟
        records = self.clip_feed.records() if self.clip_feed is not None else []
        abnormal = sum(1 for r in records if r.state in ("fail", "local"))
        self.window.set_nav_count(len(records))
        self.switch_page.set_clip_count(len(records), abnormal)
        self.settings_page.set_engine_records(records)

    def _on_clip_paused(self, paused: bool) -> None:
        """暂停记录：引擎在页面里已 set_paused；本地采集模式再停一次轮询。"""
        # 同时暂停 / 恢复 LocalClipFeed 的轮询（engine 模式下 clip_poll 为 None）
        if self.clip_poll is not None:
            self.clip_poll.setInterval(0 if paused else 500)

    def _on_clipboard_hotkey(self) -> None:
        """真正在主线程执行的那一半（信号已把调用从 tap 线程投递过来）。"""
        self.show_page(PAGE_CLIP)

    def _build_clipboard_hotkey(self) -> None:
        """⌘⇧V 全局热键 → 切到剪贴板页（INTERACTIONS §4.1 剪贴板热键）。"""
        spec = getattr(self.cfg, "clipboard_hotkey", "") or ""
        self._clip_hotkey = None
        if not spec:
            return
        try:
            # 回调只 emit，不直接调 show_page —— tap 线程碰 Qt 会崩
            hk = self._spawn_hotkey(spec, self.clipboardHotkeyPressed.emit)
            hk.start()
            self._clip_hotkey = hk
            log.info("剪贴板热键已注册：%s", hk.describe())
        except Exception as exc:  # noqa: BLE001 - 注册失败不影响主功能
            log.warning("剪贴板热键注册失败（%s）：%s", spec, exc)

    def _spawn_hotkey(self, spec: str, callback):  # noqa: ANN001, ANN202
        from ...hotkey import parse_hotkey
        from ...hotkey.mock import ManualHotkeyBackend

        hk = parse_hotkey(spec)
        try:
            from ...hotkey.macos import QuartzEventTapBackend

            return QuartzEventTapBackend(hk, callback, swallow=False)
        except Exception:  # noqa: BLE001 - 非 mac 平台退回手动后端
            return ManualHotkeyBackend(hk, callback)

    def _poll_local_clip(self) -> None:
        if getattr(self.clipboard_page, "paused", False):
            return
        try:
            self.clip_feed.poll()
        except Exception:  # noqa: BLE001
            log.debug("读取剪贴板失败", exc_info=True)

    def _poll_state(self) -> None:
        changed = False
        self._refresh_hotkey_ui()
        remote_ok = self._remote_ready()
        self.switch_page.set_remote_available(remote_ok)
        source = self.controller.source_state
        if source != self._seen_source:
            self._seen_source = source
            self.switch_page.set_state(source)
            self._refresh_menu()
            self._refresh_conn_ui()
            changed = True
            if source == SOURCE_UNKNOWN:
                self.switch_page.set_note("插上外接屏会自动恢复", ok=False)
            elif len(self.controller.history) == self._seen_history:
                label = "Windows" if source == "windows" else "Mac"
                self.switch_page.set_note(f"已同步外部切换 · 当前 {label}", ok=True)

        history = self.controller.history
        if len(history) > self._seen_history:
            self._seen_history = len(history)
            result = history[-1]
            self._busy = False
            self.switch_page.set_busy(False)
            changed = True
            self._apply_result(result)
        if changed:
            self._refresh_menu()

    def _remote_ready(self) -> bool:
        probe = self.controller.remote_ready
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001
            return False

    def _apply_result(self, result) -> None:  # noqa: ANN001
        if not result.ok:
            self.switch_page.set_note("切换失败，详见日志", ok=False, bad=True)
            self.toast.show_text(f"切换失败：{result.error}")
            return
        if result.dry_run:
            self.switch_page.set_note("[演练] 未真正发送指令", ok=True)
            return
        if getattr(result, "via", "local") == "remote":
            peer = "Windows" if self.cfg.other_host() == "windows" else "Mac"
            self.switch_page.set_note(f"已由 {peer} 端代发 · 回读跳过（屏不在本机）", ok=True)
        else:
            ms = result.set_call_ms
            suffix = {
                "confirmed": "回读已确认",
                "unconfirmed": "回读未确认（已知现象）",
            }.get(result.readback, "指令已发送")
            self.switch_page.set_note(f"上次切换 {ms:.1f} ms · {suffix}", ok=True)
        label = "Mac" if result.target == "mac" else "Windows"
        self.toast.show_text(f"已切换到 {label}")

    def _refresh_conn_ui(self) -> None:
        connected = self._remote_ready()
        peer = "Windows" if self.cfg.other_host() == "windows" else "Mac"
        if connected:
            self.window.set_side_status(f"已连接 {peer}", ok=True)
        else:
            self.window.set_side_status("对端离线 · 仅本机", ok=False)

    # ------------------------------------------------------------------ 菜单
    def _status_actions(self) -> dict:
        return {
            "open": self.show_window,
            "switch": lambda: self.do_switch(),
            "force": lambda: self.do_force(),
            "clipboard": lambda: self.show_page(PAGE_CLIP),
            "settings": lambda: self.show_page(PAGE_SETTINGS),
            "pair": self.open_pair_page,
            "pair_state": lambda: self.pairing_page.status_text(),
            "quit": self.quit_application,
            "remote_ready": self._remote_ready,
        }

    def _refresh_menu(self) -> None:
        if self.status is None:
            return
        entries = statusmenu.build_menu_entries(
            self.cfg,
            self.controller.source_state,
            self._hotkey_label(),
            self._status_actions(),
        )
        try:
            self.status.set_menu(entries)
        except Exception:  # noqa: BLE001
            log.exception("刷新常驻菜单失败")

    # ------------------------------------------------------------------ 配置 / 热键
    def _hotkey_label(self) -> str:
        return self.hotkey.describe() if self.hotkey else "未启用"

    def _refresh_hotkey_ui(self) -> None:
        label = self._hotkey_label()
        if label == getattr(self, "_shown_hotkey", ""):
            return
        self._shown_hotkey = label
        self.switch_page.set_hotkey(label)
        self.window.set_side_hotkey(label)
        if self.status is not None:
            self.status.set_tooltip(f"屏幕切换 · {label}（单击图标打开菜单）")
        self._refresh_menu()

    def on_config_saved(self, cfg) -> None:  # noqa: ANN001
        old_hotkey = self._hotkey_spec
        old_clip_on = bool(
            getattr(getattr(self.cfg, "clipboard", None), "enabled", True)
        )
        self.cfg = cfg
        # 引擎读的是自己持有的 cfg 引用：换配置要跟着换，否则「保留历史 / 图片开关」
        # 这类设置改了不生效。
        if self.clip_engine is not None:
            self.clip_engine.cfg = cfg.clipboard
        self.controller.cfg = cfg
        self.switch_page.cfg = cfg
        self.settings_page.cfg = cfg
        # 外观固定浅色：用户 2026-10-21 明确不要深色模式（设置页已移除该项）
        theme.set_appearance("light")
        from . import icons

        icons.clear_cache()
        self.app.setStyleSheet(theme.qss())
        self.toast = Toast(self.window)
        self.clipboard_page.mark_dirty()
        self.clipboard_page.refresh()
        self.switch_page.set_state(self.controller.source_state)
        if cfg.hotkey != old_hotkey or self.hotkey is None:
            self._rebuild_hotkey(cfg)
        if self.clip_engine is not None and bool(cfg.clipboard.enabled) != old_clip_on:
            self._toggle_clip_engine(bool(cfg.clipboard.enabled))
        self._refresh_hotkey_ui()
        self._refresh_menu()
        self.toast.show_text("设置已生效")

    def _toggle_clip_engine(self, enabled: bool) -> None:
        """设置页「启用跨端剪贴板」：关掉就停引擎，打开就重启。"""
        engine = self.clip_engine
        if engine is None:
            return
        try:
            if enabled:
                engine.start()
                self.toast.show_text("跨端剪贴板已启用")
            else:
                engine.stop()
                self.toast.show_text("跨端剪贴板已关闭")
        except Exception as exc:  # noqa: BLE001
            log.exception("切换剪贴板引擎失败")
            self.toast.show_text(f"剪贴板引擎切换失败：{exc}")
        self.clipboard_page.mark_dirty()
        self.clipboard_page.refresh()

    def _on_recording_changed(self, recording: bool) -> None:
        if recording:
            self._suspend_hotkey()
        else:
            QTimer.singleShot(350, self._resume_hotkey)

    def _suspend_hotkey(self) -> None:
        if self._hotkey_suspended or self.hotkey is None:
            return
        try:
            self.hotkey.stop()
        except Exception:  # noqa: BLE001
            log.exception("暂停全局热键失败")
        self._hotkey_suspended = True

    def _resume_hotkey(self) -> None:
        if not self._hotkey_suspended:
            return
        if self.settings_page.hk_main.is_recording() or self.settings_page.hk_clip.is_recording():
            return
        if self._quitting:
            return
        self._hotkey_suspended = False
        self._rebuild_hotkey(self.cfg)

    def _rebuild_hotkey(self, cfg) -> None:  # noqa: ANN001
        from ...hotkey import HotkeyError, create_hotkey

        if self.hotkey is not None:
            try:
                self.hotkey.stop()
            except Exception:  # noqa: BLE001
                pass
            self.hotkey = None
        try:
            hk = create_hotkey(cfg, self.controller.trigger)
            hk.start()
            self.hotkey = hk
            self._hotkey_spec = cfg.hotkey
            self._retry_on_activate = False
            log.info("热键已重建：%s（后端 %s）", hk.describe(), hk.name)
        except HotkeyError as exc:
            log.error("新热键注册失败：%s", exc)
            self.switch_page.set_note(f"热键未启用：{exc}", ok=False)
        self._refresh_hotkey_ui()

    def retry_hotkey(self) -> None:
        self._retry_on_activate = False
        self._rebuild_hotkey(self.cfg)

    def quit_application(self) -> None:
        """唯一退出路径：按钮 / 菜单⌘Q / 菜单栏菜单都走这里（INTERACTIONS §13.3）。"""
        log.info("用户从界面退出")
        self._quitting = True
        try:
            self.pair_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.pairing_page.deactivate()
        except Exception:  # noqa: BLE001
            log.debug("取消配对失败", exc_info=True)
        try:
            self.clip_timer.stop()
            if self.clip_poll is not None:
                self.clip_poll.stop()
            if self.clip_engine is not None:
                # 退出按钮必须真的停同步引擎，而不是只停本地采集
                self.clip_engine.stop()
            else:
                self.clip_feed.close()
        except Exception:  # noqa: BLE001
            log.debug("停止剪贴板采集失败", exc_info=True)
        try:
            self.controller.stop()
        finally:
            if self.hotkey is not None:
                try:
                    self.hotkey.stop()
                except Exception:  # noqa: BLE001
                    pass
            if getattr(self, "_clip_hotkey", None) is not None:
                try:
                    self._clip_hotkey.stop()
                except Exception:  # noqa: BLE001
                    pass
            if self.status is not None:
                try:
                    self.status.hide()
                except Exception:  # noqa: BLE001
                    pass
            self.window.quit_completely()
            self.app.quit()

    def run(self) -> int:
        return self.app.exec()


def run_ui2(cfg, cfg_file, log_file, backend, controller, hotkey, warning=None,
            clip_engine=None) -> int:
    runner = UiRunner2(cfg, cfg_file, log_file, backend, controller, hotkey, warning,
                       clip_engine=clip_engine)
    return runner.run()
