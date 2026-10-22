"""Windows v2 运行器：窗口三页 + 托盘 + 热键 + 切换控制器接到一起（设计 §10.1）。

线程模型沿用 app.SwitchController：
  · 界面上点按钮 → 后台线程 switch_now()（阻塞约 65ms）
  · 热键触发     → controller.trigger() 自己入队，UI 只轮询状态
UI 线程只做绘制，不碰 DDC。「强制切」绕过本地优先策略直接发 Set（§5.3）。

Windows 特有的三件事都在这里：
  · 通知分流（§4.3）：窗口在前台 → 窗口内 toast；不在前台 → 托盘气泡，避免弹两次
  · 清空确认是**窗口内遮罩**（§8），不新建窗口
  · 托盘不可用时关窗 = 最小化 + 启动提示（§4.1）
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import __version__, logging_setup
from ...config import SOURCE_UNKNOWN, save as save_config
from ..clipboard_feed import LocalClipFeed
from . import focus_style, openers, theme, tray
from .clipboard_page import ClipboardPage
from .pairing_page import ConfirmRequest, PairingPage
from .settings_page import SettingsPage
from .switch_page import SwitchPage
from .window import PAGE_CLIP, PAGE_PAIR, PAGE_SETTINGS, PAGE_SWITCH, MainWindow

log = logging_setup.get("ui")


# --------------------------------------------------------------------------- #
# Toast（窗口内轻提示，§9：底部居中 / ink 底 / 11.5px / r8 / 1900ms）
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
            f"background:{t.ink};color:{t.card};font-size:11.5px;font-weight:600;"
            "padding:7px 12px;border-radius:8px;"
        )
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, ms: int = 1900) -> None:
        """同刻只留一条（§9）：新 toast 直接顶掉旧的。"""
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
# 清空确认遮罩（§8：覆盖整窗，不新建窗口）
# --------------------------------------------------------------------------- #
class ConfirmOverlay(QWidget):
    """窗口内确认遮罩：360 卡片 + 危险色确认按钮，`Esc` 取消、`Enter` 确认。

    遮罩覆盖整个窗口（含侧栏/底栏）。焦点默认在「取消」上，防手滑。

    2026-10-22：从「只能清空剪贴板」泛化成通用确认 —— 配对页的
    「替换配对」/「解除配对」也走这里（`UI-WINDOWS2-DESIGN.md` §14.1 要求用窗口内
    遮罩而不是 QDialog），所以标题/说明/按钮文字都由调用方给。
    """

    CARD_W = 360

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("confirmMask")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._on_confirm = None
        self.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.card = QFrame()
        self.card.setObjectName("confirmCard")
        self.card.setFixedWidth(self.CARD_W)
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(20, 18, 20, 16)
        card_lay.setSpacing(10)
        self.title = QLabel("", self.card)
        self.title.setObjectName("confirmTitle")
        self.title.setFont(theme.qfont(13.5, 650))
        self.sub = QLabel("", self.card)
        self.sub.setObjectName("confirmSub")
        self.sub.setFont(theme.qfont(11.5))
        self.sub.setWordWrap(True)
        card_lay.addWidget(self.title)
        card_lay.addWidget(self.sub)
        card_lay.addSpacing(6)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        self.btn_cancel = QPushButton("取消", self.card)
        self.btn_cancel.setObjectName("ghostBtn")
        self.btn_cancel.setFixedHeight(30)
        self.btn_cancel.setMinimumWidth(72)
        self.btn_cancel.setFont(theme.qfont(12, 600))
        self.btn_cancel.setCursor(Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_ok = QPushButton("清空", self.card)
        self.btn_ok.setObjectName("dangerBtn")     # 破坏性操作：红色，不是主色蓝
        self.btn_ok.setFixedHeight(30)
        self.btn_ok.setMinimumWidth(72)
        self.btn_ok.setFont(theme.qfont(12, 650))
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        self.btn_ok.clicked.connect(self.confirm)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        card_lay.addLayout(btns)

        # 焦点在「取消」上时也要吃 Esc / Enter（按钮本身不处理这两个键）
        self.btn_cancel.installEventFilter(self)
        self.btn_ok.installEventFilter(self)

        row.addWidget(self.card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    # ---- 显示 ----
    def ask(self, title: str, sub: str, confirm_text: str, on_confirm) -> None:  # noqa: ANN001
        self._on_confirm = on_confirm
        self.title.setText(title)
        self.sub.setText(sub)
        self.btn_ok.setText(confirm_text)
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(0, 0, parent.width(), parent.height())
        self.show()
        self.raise_()
        self.btn_cancel.setFocus(Qt.OtherFocusReason)   # 焦点默认在「取消」

    def cancel(self) -> None:
        self._on_confirm = None
        self.hide()

    def confirm(self) -> None:
        callback = self._on_confirm
        self._on_confirm = None
        self.hide()
        if callback is not None:
            callback()

    def is_open(self) -> bool:
        return self.isVisible()

    # ---- 事件 ----
    def mousePressEvent(self, event):  # noqa: N802
        """点遮罩（卡片以外）= 取消。"""
        if not self.card.geometry().contains(event.pos()):
            self.cancel()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancel()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.confirm()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        """`Esc` = 取消、`Enter` = 确认（焦点在按钮上时也生效，§8）。"""
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.cancel()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.confirm()
                return True
        return super().eventFilter(obj, event)


# --------------------------------------------------------------------------- #
# 运行器
# --------------------------------------------------------------------------- #
class UiRunner2(QObject):
    #: 工作线程 → UI 线程的回调通道。
    #: **不要在工作线程里用 `QTimer.singleShot(0, ...)`**：那个线程没有事件循环，
    #: 定时器永远不触发（强制切/探测的结果就再也回不到界面上）。信号走队列连接才稳。
    uiCall = Signal(object)

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
        self._first_switch_hint_shown = False

        self.app = QApplication.instance() or QApplication([])
        self.app.setApplicationName("屏幕切换")
        self.app.setApplicationDisplayName("屏幕切换")
        self.app.setQuitOnLastWindowClosed(False)

        font = QFont()
        font.setFamilies(theme.FONT_FAMILIES)
        font.setPixelSize(int(round(theme.APP_FONT_PX)))
        self.app.setFont(font)
        # 外观固定浅色（§2「主题」）
        theme.set_appearance("light")
        self.app.setStyleSheet(theme.qss())
        # 点过的按钮不留灰色虚线焦点框（噪音，见 ui/win2/focus_style.py）
        focus_style.install(self.app)

        self.icon = theme.app_icon()
        self.app.setWindowIcon(self.icon)

        hotkey_label = hotkey.describe() if hotkey else "未启用"
        self.window = MainWindow(cfg, hotkey_label, f"v{__version__}", self._monitor_name())
        self.window.setWindowIcon(self.icon)

        self.switch_page = SwitchPage(cfg, hotkey_label, self._monitor_name())
        self.clip_feed = clip_engine if clip_engine is not None else LocalClipFeed(cfg)
        self.clipboard_page = ClipboardPage(
            self.clip_feed,
            open_external=openers.WindowsOpeners(),
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
        self.confirm = ConfirmOverlay(self.window)

        self._busy = False
        self._hotkey_suspended = False
        self._quitting = False
        self._hotkey_spec = cfg.hotkey
        self._seen_history = len(controller.history)
        self._seen_source = controller.source_state
        self.uiCall.connect(self._run_ui_call, Qt.QueuedConnection)
        self.window.switchRequested.connect(lambda: self.do_switch())
        self.window.forceRequested.connect(self.do_force)
        self.window.deviceClicked.connect(lambda host: self.do_switch())
        self.window.hiddenToBackground.connect(self._enter_background)
        self.window.quitRequested.connect(self.quit_application)
        self.window.pageChanged.connect(self._on_page_changed)
        self._last_page = PAGE_SWITCH
        self._pair_text = ""
        self.pairing_page.toastRequested.connect(self.toast.show_text)
        self.pairing_page.pairedChanged.connect(self._on_paired_changed)
        self.pairing_page.confirmRequested.connect(self._ask_confirm)
        self.switch_page.clipboardRequested.connect(lambda: self.show_page(PAGE_CLIP))
        self.switch_page.toastRequested.connect(self.toast.show_text)
        self.clipboard_page.toastRequested.connect(self.toast.show_text)
        self.clipboard_page.pausedChanged.connect(self._on_clip_paused)
        self.clipboard_page.clearConfirmRequested.connect(self._ask_clear)
        self.settings_page.toastRequested.connect(self.toast.show_text)
        self.settings_page.saved.connect(self.on_config_saved)
        self.settings_page.clearRequested.connect(
            self.clipboard_page.clear_records_with_confirm
        )
        self.settings_page.recordingChanged.connect(self._on_recording_changed)
        self.settings_page.probeRequested.connect(self.do_probe)
        self.settings_page.openLogRequested.connect(self.open_log)
        self.settings_page.openConfigDirRequested.connect(self.open_config_dir)

        # 首帧就用控制器已经确认过的状态
        self.switch_page.set_state(self._seen_source)
        if self._seen_source == SOURCE_UNKNOWN:
            self.switch_page.set_note("插上外接屏会自动恢复", ok=False)

        self.status = None
        self._build_tray()
        self._ensure_hotkey_backend()
        self._clip_hotkey = None
        self._build_clipboard_hotkey()
        self._refresh_menu()
        self._refresh_hotkey_ui()
        self._refresh_conn_ui()
        self._refresh_pair_ui()

        # 配对状态（侧栏状态灯 + 托盘菜单右侧文字）1s 刷一次；
        # 与 mac2 同频，比 250ms 的 _poll_state 轻（pairing_status() 会读引擎锁）。
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
        if self.status is not None:
            # 托盘图标注册完 3 秒再报一次「看得见吗、在哪」（排查「进程在、图标没了」）
            QTimer.singleShot(3000, self._check_tray)
        if warning:
            self.window.switch_page.set_note(f"热键未启用：{warning}", ok=False)
        if self.status is None:
            # 托盘不可用：关窗后只能从任务栏恢复（§4.1）
            QTimer.singleShot(400, lambda: self.toast.show_text("托盘不可用，关窗后请从任务栏恢复", 3200))

    # ------------------------------------------------------------------ 组装
    def _run_ui_call(self, fn) -> None:  # noqa: ANN001
        """在工作线程里 `uiCall.emit(...)` → 回到 UI 线程执行。"""
        try:
            fn()
        except Exception:  # noqa: BLE001 - 回调里出错不能把事件循环带崩
            log.exception("UI 回调执行失败")

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

    def _build_tray(self) -> None:
        self.status = tray.build_status_item(self.icon, self.show_window)
        if self.status is not None:
            self.status.set_tooltip(
                tray.tooltip_text(self._hotkey_label(), self.controller.source_state)
            )
            self.window.withdraw_to_tray = bool(self.cfg.tray)
            self.window.quit_on_close = not bool(self.cfg.tray)
        else:
            log.warning("托盘不可用：关窗改为最小化，功能入口只剩热键与任务栏")
            self.window.withdraw_to_tray = False
            self.window.quit_on_close = False

    # ------------------------------------------------------------------ 动作
    def _ensure_hotkey_backend(self) -> None:
        """启动时的热键是 cli 用 `auto` 建的（RegisterHotKey）。

        若按设计要「吞掉 Alt+Space」，这里换成低级键盘钩子后端（只做一次，
        失败就继续用原来的 RegisterHotKey，切屏能力不受影响）。
        """
        try:
            from ...hotkey import parse_hotkey, resolve_backend_name

            need_hook = (
                self.hotkey is not None
                and resolve_backend_name(self.cfg) == "register"
                and bool(getattr(self.cfg, "swallow_hotkey", False))
                and parse_hotkey(self.cfg.hotkey).has_alt
            )
            if need_hook:
                log.info("按「阻止 Alt+Space 弹出系统菜单」改用键盘钩子后端")
                self._rebuild_hotkey(self.cfg)
        except Exception:  # noqa: BLE001 - 换后端失败不该拦住启动
            log.debug("启动时切换热键后端失败", exc_info=True)

    def _check_tray(self) -> None:
        """托盘图标自检：不可见就重新注册一次，再不行就重建（§12 风险 3）。"""
        if self.status is None:
            return
        try:
            visible = self.status.is_visible()
            log.info("常驻图标检查：可见=%s 位置=%s", visible, self.status.geometry() or "未知")
            if not visible:
                log.warning("托盘图标不可见，重新注册")
                self.status.reassert()
                QTimer.singleShot(1500, self._recreate_tray_if_needed)
        except Exception:  # noqa: BLE001
            log.debug("托盘图标自检失败", exc_info=True)

    def _recreate_tray_if_needed(self) -> None:
        if self.status is None:
            return
        try:
            if self.status.is_visible():
                return
            log.warning("重新注册仍不可见，整只重建托盘图标")
            self.status.recreate()
        except Exception:  # noqa: BLE001
            log.debug("重建托盘图标失败", exc_info=True)

    def _enter_background(self) -> None:
        """Windows 没有程序坞；收进托盘只是隐藏窗口。

        关窗 = 离开配对页：显示中的配对码立即失效（§12.5），否则码会在托盘里白挂 5 分钟。
        """
        if getattr(self, "pairing_page", None) is not None:
            self.pairing_page.deactivate()
        log.debug("窗口收进托盘")

    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        if self.window.current_page() == PAGE_PAIR:
            self.pairing_page.activate()

    def hide_window(self) -> None:
        self.window.hide()

    def show_page(self, index: int) -> None:
        self.window.show_page(index)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        if index == PAGE_CLIP:
            self.clipboard_page.refresh()
            self.clipboard_page.search_edit.setFocus(Qt.ShortcutFocusReason)

    # ---- 配对电脑（INTERACTIONS §12.3：侧栏 / 托盘 / 剪贴板引导共用一个入口）----
    def open_pair_page(self) -> None:
        self.show_page(PAGE_PAIR)

    def _on_page_changed(self, index: int) -> None:
        """切页即生命周期：进配对页 activate，离开 deactivate（码作废、不落盘）。"""
        previous, self._last_page = self._last_page, index
        if index == PAGE_PAIR:
            self.pairing_page.activate()
        elif previous == PAGE_PAIR:
            self.pairing_page.deactivate()

    def _on_paired_changed(self, paired: bool) -> None:  # noqa: ARG002
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

    def _ask_confirm(self, request: ConfirmRequest) -> None:
        """配对页的「替换配对 / 解除配对」→ 窗口内遮罩（§14.1，不弹 QDialog）。"""
        self.confirm.ask(
            request.title, request.sub, request.confirm_text, request.on_confirm
        )

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
        """强制切：跳过「本地优先、失败才请对端」，直接本机发 DDC Set（§5.3）。"""
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
                self.uiCall.emit(lambda: self.toast.show_text(f"已强制切换（Set {value}）"))
            except Exception as exc:  # noqa: BLE001
                log.error("强制切换失败：%s", exc)
                self.uiCall.emit(
                    lambda: self.switch_page.set_note(f"强制切换失败：{exc}", ok=False, bad=True)
                )
            finally:
                self.uiCall.emit(lambda: self._force_done(target))

        threading.Thread(target=work, name="ui-force", daemon=True).start()

    def _force_done(self, target: str) -> None:
        self._busy = False
        self.switch_page.set_busy(False)
        self.switch_page.set_state(target)
        self._refresh_menu()

    # ---- 高级组（§7.5）----
    def _ask_clear(self, count: int) -> None:
        """剪贴板页 🗑 / 设置页「清理」→ 窗口内遮罩（§8）。"""
        self.confirm.ask(
            f"清空本机全部 {count} 条记录？",
            "只清空本机列表，不影响另一台机器；此操作不可撤销。",
            "清空",
            self.clipboard_page.clear_confirmed,
        )

    def do_probe(self) -> None:
        """探测显示器 / 读取输入源：结果进日志 + toast（不阻塞界面）。"""
        def work() -> None:
            lines: list[str] = []
            try:
                monitors = self.backend.list_monitors()
                for mon in monitors:
                    lines.append(f"{getattr(mon, 'description', '') or '显示器'} "
                                 f"({getattr(mon, 'device_id', '') or '-'})")
                if not monitors:
                    lines.append("未枚举到外接显示器")
            except Exception as exc:  # noqa: BLE001
                log.exception("探测显示器失败")
                lines.append(f"探测失败：{exc}")
            try:
                current = self.backend.get_input()
                lines.append(f"当前输入源 {current}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"读取输入源失败：{exc}")
            for line in lines:
                log.info("[探测] %s", line)
            text = "；".join(lines) if lines else "没有结果"
            self.uiCall.emit(lambda: self.toast.show_text(f"探测完成：{text}", 3200))

        threading.Thread(target=work, name="ui-probe", daemon=True).start()

    def open_log(self) -> None:
        self._open_path(self.log_file, "日志")

    def open_config_dir(self) -> None:
        self._open_path(self.cfg_file.parent, "配置目录")

    def _open_path(self, path: Path, what: str) -> None:
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
            self.toast.show_text(f"已打开{what}")
        except Exception as exc:  # noqa: BLE001
            log.exception("打开%s失败：%s", what, path)
            self.toast.show_text(f"打开{what}失败：{exc}")

    # ------------------------------------------------------------------ 状态同步
    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.ApplicationActivate:
            if not self._quitting and self.window.isHidden():
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
        if self.clip_poll is not None:
            self.clip_poll.setInterval(0 if paused else 500)

    def _build_clipboard_hotkey(self) -> None:
        """`Ctrl+Shift+V` 全局热键 → 切到剪贴板页并聚焦搜索（§7.4）。"""
        from .settings_page import windows_clip_hotkey

        spec = windows_clip_hotkey(getattr(self.cfg, "clipboard_hotkey", "") or "")
        self._clip_hotkey = None
        if not spec:
            return
        try:
            hk = self._spawn_hotkey(spec, lambda: self.show_page(PAGE_CLIP))
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
            from ...hotkey.windows import RegisterHotkeyBackend

            return RegisterHotkeyBackend(hk, callback)
        except Exception:  # noqa: BLE001 - 非 Windows 环境退回手动后端（测试/开发机）
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
        self._refresh_tooltip()

    def _remote_ready(self) -> bool:
        probe = self.controller.remote_ready
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001
            return False

    def _foreground(self) -> bool:
        """窗口是否在前台（决定走窗口内 toast 还是系统通知，§4.3）。"""
        try:
            return bool(self.window.isVisible() and self.window.isActiveWindow())
        except Exception:  # noqa: BLE001
            return False

    def _notify(self, message: str) -> None:
        """窗口不在前台 → 托盘气泡；在前台 → 窗口内 toast。失败时两条都留痕。"""
        if self._foreground():
            self.toast.show_text(message)
            return
        if self.status is not None and bool(getattr(self.cfg, "notify", True)):
            self.status.notify("屏幕切换", message)
        else:
            log.info("[通知] %s", message)

    def _apply_result(self, result) -> None:  # noqa: ANN001
        if not result.ok:
            self.switch_page.set_note("切换失败，详见日志", ok=False, bad=True)
            if self.status is not None and bool(getattr(self.cfg, "notify", True)):
                self.status.notify("屏幕切换", f"切换失败：{result.error}")
            else:
                self.toast.show_text(f"切换失败：{result.error}")
            return
        if result.dry_run:
            self.switch_page.set_note("[演练] 未真正发送指令", ok=True)
            return
        if getattr(result, "via", "local") == "remote":
            peer = "Windows" if self.cfg.other_host() == "windows" else "Mac"
            self.switch_page.set_note(f"已由 {peer} 端代发 · 回读跳过（屏不在本机）", ok=True)
            self._notify(f"已切换到 {peer}")
            return
        ms = result.set_call_ms
        suffix = {
            "confirmed": "回读已确认",
            "unconfirmed": "回读未确认（已知现象）",
        }.get(result.readback, "指令已发送")
        self.switch_page.set_note(f"上次切换 {ms:.1f} ms · {suffix}", ok=True)
        label = "Mac" if result.target == "mac" else "Windows"
        if not self._first_switch_hint_shown:
            # §13.3 默认不弹确认，只在首次切换后提示一次「怎么切回来」
            self._first_switch_hint_shown = True
            self._notify(f"已切换到 {label} · {ms:.1f} ms · {self._hotkey_label()} 可切回")
            return
        self._notify(f"已切换到 {label} · {ms:.1f} ms")

    def _refresh_conn_ui(self) -> None:
        """侧栏 footer 的连接状态（底栏 2026-10-22 已删除，状态只在这一处）。"""
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
            # 托盘第 7 行「配对电脑…」：与侧栏同一个入口、同一份状态文字（§14.3）
            "pair": self.open_pair_page,
            "pair_state": lambda: self.pairing_page.status_text(),
            "quit": self.quit_application,
            "remote_ready": self._remote_ready,
        }

    def _refresh_menu(self) -> None:
        if self.status is None:
            return
        entries = tray.build_menu_entries(
            self.cfg,
            self.controller.source_state,
            self._hotkey_label(),
            self._status_actions(),
        )
        try:
            self.status.set_menu(entries)
        except Exception:  # noqa: BLE001
            log.exception("刷新托盘菜单失败")

    def _refresh_tooltip(self) -> None:
        """tooltip 跟着热键与画面归属走（§4.1，250ms 轮询）。"""
        if self.status is None:
            return
        text = tray.tooltip_text(self._hotkey_label(), self.controller.source_state)
        if text != getattr(self, "_shown_tooltip", ""):
            self._shown_tooltip = text
            self.status.set_tooltip(text)

    # ------------------------------------------------------------------ 配置 / 热键
    def _hotkey_label(self) -> str:
        return self.hotkey.describe() if self.hotkey else "未启用"

    def _refresh_hotkey_ui(self) -> None:
        label = self._hotkey_label()
        if label == getattr(self, "_shown_hotkey", ""):
            return
        self._shown_hotkey = label
        self.switch_page.set_hotkey(label)
        # 侧栏自己决定显示「热键 ⌥Space」还是「热键未启用」（§14 跟随项）
        self.window.set_side_hotkey(label)
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
        # 「关闭窗口后仍在托盘运行」即时生效
        if self.status is not None:
            self.window.withdraw_to_tray = bool(cfg.tray)
            self.window.quit_on_close = not bool(cfg.tray)
        self._refresh_hotkey_ui()
        self._refresh_menu()
        self._refresh_conn_ui()
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
        from ...hotkey import HotkeyError

        if self.hotkey is not None:
            try:
                self.hotkey.stop()
            except Exception:  # noqa: BLE001
                pass
            self.hotkey = None
        try:
            hk = self._create_hotkey(cfg)
            hk.start()
            self.hotkey = hk
            self._hotkey_spec = cfg.hotkey
            self._retry_on_activate = False
            log.info("热键已重建：%s（后端 %s）", hk.describe(), hk.name)
        except HotkeyError as exc:
            log.error("新热键注册失败：%s", exc)
            self.switch_page.set_note(f"热键未启用：{exc}", ok=False)
        self._refresh_hotkey_ui()

    def _create_hotkey(self, cfg):  # noqa: ANN001, ANN202
        """按设计 §12 风险 1 选后端：Alt+Space 要**吞键**，否则会先弹系统菜单。

        `auto` 解析成 `register` 时 RegisterHotKey 吞不掉系统菜单，所以只要用户开着
        「阻止 Alt+Space 弹出系统菜单」且热键带 Alt，就走低级键盘钩子；
        钩子装不上（安全软件拦截）就退回 RegisterHotKey，并在界面上说清楚。
        """
        from ...hotkey import create_hotkey, parse_hotkey, resolve_backend_name

        hk = parse_hotkey(cfg.hotkey)
        name = resolve_backend_name(cfg)
        if name == "register" and bool(getattr(cfg, "swallow_hotkey", False)) and hk.has_alt:
            try:
                from ...hotkey.windows import LowLevelHookBackend

                return LowLevelHookBackend(hk, self.controller.trigger, swallow=True)
            except Exception as exc:  # noqa: BLE001 - 钩子不可用不影响切屏
                log.warning("键盘钩子不可用（%s），退回 RegisterHotKey：%s 会先弹系统菜单",
                            exc, hk.describe())
                self.toast.show_text("热键钩子不可用：Alt+Space 会先弹系统菜单", 3200)
        return create_hotkey(cfg, self.controller.trigger)

    def retry_hotkey(self) -> None:
        self._retry_on_activate = False
        self._rebuild_hotkey(self.cfg)

    def quit_application(self) -> None:
        """唯一退出路径：侧栏「退出应用」/ 托盘菜单 / 设置关掉托盘后关窗（§13.3）。"""
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

    #: 兼容旧调用点（设置页 / 事件过滤器里还叫 quit）
    quit = quit_application

    def run(self) -> int:
        return self.app.exec()


def run_ui2(cfg, cfg_file, log_file, backend, controller, hotkey, warning=None,
            clip_engine=None) -> int:
    runner = UiRunner2(cfg, cfg_file, log_file, backend, controller, hotkey, warning,
                       clip_engine=clip_engine)
    return runner.run()
