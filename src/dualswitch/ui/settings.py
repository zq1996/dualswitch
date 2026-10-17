"""设置窗口：常规 / 热键 / 显示器 / 高级，字段与 config.json 一一对应。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QIntValidator
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, ConfigError, from_dict, save as save_config
from ..hotkey.base import HotkeyError, parse_hotkey
from . import theme
from .widgets import Combo, ElidedLabel, StatusBar, TitleBar, ToggleSwitch, label, soft_shadow


# --------------------------------------------------------------------------- #
# 基础行
# --------------------------------------------------------------------------- #
def group_row(title: str, desc: str, control: QWidget, last: bool = False, wrap: bool = True) -> QFrame:
    row = QFrame()
    row.setObjectName("gRowLast" if last else "gRow")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(12)

    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(3)
    col.addWidget(label(title, "gTitle"))
    if desc:
        # 长路径等单行内容用中间省略，避免在奇怪的位置断行
        d = label(desc, "gDesc") if wrap else ElidedLabel(desc, "gDesc")
        d.setWordWrap(wrap)
        col.addWidget(d)
    lay.addLayout(col, 1)
    lay.addWidget(control, 0, Qt.AlignVCenter)
    return row


def group_box(*rows: QWidget) -> QFrame:
    box = QFrame()
    box.setObjectName("group")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(1, 1, 1, 1)
    lay.setSpacing(1)
    for r in rows:
        lay.addWidget(r)
    return box


class Segmented(QFrame):
    """两段/三段选择器。"""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str, parent=None):
        super().__init__(parent)
        self.setObjectName("tabs")
        self.setFixedHeight(30)
        self.setFixedWidth(len(options) * 76 + 6)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._buttons: dict[str, QPushButton] = {}
        self._value = current
        for value, text in options:
            btn = QPushButton(text)
            btn.setObjectName("tab")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            btn.setChecked(value == current)
            btn.clicked.connect(lambda _=False, v=value: self.set_value(v, emit=True))
            lay.addWidget(btn, 1)
            self._buttons[value] = btn

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        self._value = value
        for v, btn in self._buttons.items():
            btn.setChecked(v == value)
        if emit:
            self.changed.emit(value)


class HotkeyRecorder(QPushButton):
    """点击后进入录制态，按下组合键即写入。

    录制期间必须暂停全局热键：否则按到「当前热键」会被系统直接吞掉去触发切换，
    录制窗口根本收不到这个按键。
    """

    captured = Signal(str)
    recordingChanged = Signal(bool)     # True=进入录制（调用方暂停全局热键）

    _SPECIAL = {
        Qt.Key_Space: "space",
        Qt.Key_Return: "enter",
        Qt.Key_Enter: "enter",
        Qt.Key_Tab: "tab",
        Qt.Key_Escape: "escape",
        Qt.Key_Backspace: "backspace",
        Qt.Key_Delete: "delete",
        Qt.Key_Insert: "insert",
        Qt.Key_Home: "home",
        Qt.Key_End: "end",
        Qt.Key_PageUp: "pageup",
        Qt.Key_PageDown: "pagedown",
        Qt.Key_Up: "up",
        Qt.Key_Down: "down",
        Qt.Key_Left: "left",
        Qt.Key_Right: "right",
    }

    def __init__(self, spec: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ghost")
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._spec = spec
        self._recording = False
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        if self._recording:
            self.setText("请按下组合键…")
            return
        try:
            raw = parse_hotkey(self._spec).describe()
            text = " + ".join(raw.replace(" ", "").split("+"))
        except HotkeyError:
            text = self._spec
        self.setText(text)

    def spec(self) -> str:
        return self._spec

    def set_spec(self, spec: str) -> None:
        self._spec = spec
        self._refresh()

    # ------------------------------------------------------------------ 录制态
    def set_recording(self, on: bool) -> None:
        if on == self._recording:
            return
        self._recording = on
        self._hint_timer.stop()
        if on:
            self.setFocus(Qt.MouseFocusReason)
        self._refresh()
        self.recordingChanged.emit(on)

    def is_recording(self) -> bool:
        return self._recording

    def stop_recording(self) -> None:
        """取消录制（切页签、窗口失焦、保存前都要调用，否则热键一直停着）。"""
        self.set_recording(False)

    def _flash_hint(self, text: str) -> None:
        self.setText(text)
        self._hint_timer.start(1300)

    def mousePressEvent(self, event):  # noqa: N802
        self.set_recording(not self._recording)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            return
        if key == Qt.Key_Escape:
            self.set_recording(False)
            return

        mods: list[str] = []
        m = event.modifiers()
        if m & Qt.ControlModifier:
            mods.append("ctrl")
        if m & Qt.AltModifier:
            mods.append("alt")
        if m & Qt.ShiftModifier:
            mods.append("shift")
        if m & Qt.MetaModifier:
            mods.append("win")

        token = self._SPECIAL.get(key)
        if token is None:
            text = event.text().strip().lower()
            if len(text) == 1 and (text.isalnum() or text in "`-=[]\\;',./"):
                token = text
            elif Qt.Key_F1 <= key <= Qt.Key_F24:
                token = f"f{key - Qt.Key_F1 + 1}"
        if token is None:
            return

        spec = "+".join(mods + [token])
        try:
            parse_hotkey(spec)
        except HotkeyError:
            # 之前是静默忽略，用户会以为「按了没反应」，给一句提示
            self._flash_hint("至少需要一个修饰键" if not mods else "这个组合不支持")
            return
        self._spec = spec
        self.set_recording(False)
        self.captured.emit(spec)


# --------------------------------------------------------------------------- #
# 设置窗口
# --------------------------------------------------------------------------- #
class SettingsWindow(QWidget):
    saved = Signal(object)          # Config
    openConfigDirRequested = Signal()
    probeRequested = Signal()       # 「检测」显示器
    recordingChanged = Signal(bool)  # 热键录制开始/结束 → 调用方暂停/恢复全局热键

    def __init__(self, cfg: Config, config_file: Path, hotkey_label: str, version: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.config_file = Path(config_file)

        self.setWindowTitle("屏幕切换 · 设置")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(theme.WIN_W, theme.SETTINGS_H)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.root = QFrame()
        self.root.setObjectName("root")
        outer.addWidget(self.root)
        soft_shadow(self.root, "#0F172A", blur=38, dy=14, alpha=52)

        root_lay = QVBoxLayout(self.root)
        root_lay.setContentsMargins(1, 1, 1, 1)
        root_lay.setSpacing(0)

        self.titlebar = TitleBar("屏幕切换 · 设置", show_settings=False)
        self.titlebar.closeClicked.connect(self.hide)
        self.titlebar.minimizeClicked.connect(self.showMinimized)
        root_lay.addWidget(self.titlebar)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(18, 14, 18, 12)
        body_lay.setSpacing(12)

        # 页签
        tabs = QFrame()
        tabs.setObjectName("tabs")
        tabs.setFixedHeight(42)
        tabs_lay = QHBoxLayout(tabs)
        tabs_lay.setContentsMargins(5, 5, 5, 5)
        tabs_lay.setSpacing(3)
        self.stack = QStackedWidget()
        self._tab_buttons: list[QPushButton] = []
        for idx, name in enumerate(["常规", "热键", "显示器", "高级"]):
            btn = QPushButton(name)
            btn.setObjectName("tab")
            btn.setCheckable(True)
            btn.setChecked(idx == 0)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            btn.clicked.connect(lambda _=False, i=idx: self._select(i))
            tabs_lay.addWidget(btn, 1)          # 四等分，选中态不再只包住文字
            self._tab_buttons.append(btn)
        body_lay.addWidget(tabs)

        self.stack.addWidget(self._page_common())
        self.stack.addWidget(self._page_hotkey())
        self.stack.addWidget(self._page_display())
        self.stack.addWidget(self._page_advanced())
        body_lay.addWidget(self.stack, 1)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        self.error.setStyleSheet(
            "color:#8C2222;background:#FDF1F1;border:1px solid #F4D6D6;"
            "border-radius:8px;padding:8px 10px;font-size:11.5px;"
        )
        body_lay.addWidget(self.error)

        root_lay.addWidget(body, 1)

        footer = QWidget()
        footer.setObjectName("footer")
        footer.setAttribute(Qt.WA_StyledBackground, True)   # QWidget 子类默认不画 QSS 背景
        footer.setFixedHeight(54)
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(18, 0, 18, 0)
        foot_lay.setSpacing(8)
        btn_reset = QPushButton("恢复默认")
        btn_reset.setObjectName("ghost")
        btn_reset.setFixedHeight(32)
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._reset)
        btn_save = QPushButton("保存")
        btn_save.setObjectName("solid")
        btn_save.setFixedHeight(32)
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save)
        foot_lay.addWidget(btn_reset, 0, Qt.AlignVCenter)
        foot_lay.addStretch(1)
        foot_lay.addWidget(btn_save, 0, Qt.AlignVCenter)
        root_lay.addWidget(footer)

        self.statusbar = StatusBar()
        self.statusbar.text.setText(f"配置：{self.config_file}")
        self.statusbar.ver.setText(version)
        root_lay.addWidget(self.statusbar)

    # ------------------------------------------------------------------ 页面
    def _select(self, index: int) -> None:
        self.recorder.stop_recording()      # 切页签就结束录制，别让全局热键一直停着
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == index)

    def _page_common(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.seg_mode = Segmented(
            [("other", "切到另一台"), ("toggle", "两侧翻转")], self.cfg.switch_mode
        )
        self.sw_notify = ToggleSwitch(self.cfg.notify)
        self.sw_readback_notify = ToggleSwitch(self.cfg.readback.notify_on_unconfirmed)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 2000)
        self.slider.setSingleStep(50)
        self.slider.setValue(self.cfg.debounce_ms)
        self.slider.setFixedWidth(112)
        self.debounce_label = label(f"{self.cfg.debounce_ms}", "gTitle")
        self.debounce_label.setFixedWidth(42)
        self.debounce_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider.valueChanged.connect(lambda v: self.debounce_label.setText(str(v)))

        debounce_box = QWidget()
        dlay = QHBoxLayout(debounce_box)
        dlay.setContentsMargins(0, 0, 0, 0)
        dlay.setSpacing(8)
        dlay.addWidget(self.slider)
        dlay.addWidget(self.debounce_label)

        lay.addWidget(group_box(
            group_row("切换模式", "按一次切到另一台，或在两侧来回翻转", self.seg_mode),
            group_row("切换后弹出通知", "失败或未确认时提示", self.sw_notify),
            group_row("回读未确认时提示", "切走后回读必然失败，默认只记日志", self.sw_readback_notify),
            group_row("连按去抖", "两次触发的最小间隔（毫秒）", debounce_box, last=True),
        ))
        lay.addStretch(1)
        return page

    def _page_hotkey(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.recorder = HotkeyRecorder(self.cfg.hotkey)
        self.recorder.recordingChanged.connect(self.recordingChanged)
        self.sw_strict = ToggleSwitch(self.cfg.hotkey_backend == "hook")
        self.sw_swallow = ToggleSwitch(self.cfg.swallow_hotkey)
        self.sw_tray = ToggleSwitch(self.cfg.tray)

        lay.addWidget(group_box(
            group_row("切换热键", "点击后按下新组合键，保存即生效", self.recorder),
            group_row("严格区分左右 Alt", "改用低级键盘钩子，只认右 Alt", self.sw_strict),
            group_row("吞掉热键", "阻止 Alt + Space 继续弹出系统菜单", self.sw_swallow),
            group_row("开机后常驻托盘", "关闭窗口时不退出程序", self.sw_tray, last=True),
        ))
        lay.addStretch(1)
        return page

    def _page_display(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        hint = label(
            "这一页决定程序操作哪台显示器，以及切到 Windows / Mac 时各把输入源设成哪个值。",
            "pageHint",
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # 关键字 + 检测按钮：点检测会把系统枚举到的显示器列出来，照着填即可
        self.edit_match = QLineEdit(self.cfg.monitor_match)
        self.edit_match.setObjectName("text")
        self.edit_match.setFixedWidth(96)
        btn_probe = QPushButton("检测")
        btn_probe.setObjectName("ghost")
        btn_probe.setProperty("role", "probe")
        btn_probe.setFixedHeight(30)
        btn_probe.setCursor(Qt.PointingHandCursor)
        btn_probe.setToolTip("列出系统枚举到的显示器，关键字填其中能唯一识别这台屏的片段")
        btn_probe.clicked.connect(self.probeRequested)
        match_box = QWidget()
        ml = QHBoxLayout(match_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(8)
        ml.addWidget(self.edit_match)
        ml.addWidget(btn_probe)

        self.edit_win = self._num_edit(self.cfg.sources["windows"])
        self.edit_mac = self._num_edit(self.cfg.sources["mac"])

        pair = QWidget()
        pl = QHBoxLayout(pair)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        for caption, edit in (("Windows", self.edit_win), ("Mac", self.edit_mac)):
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(3)
            edit.setFixedWidth(56)
            cap = label(caption, "gDesc")
            cap.setAlignment(Qt.AlignCenter)
            col.addWidget(edit, 0, Qt.AlignHCenter)
            col.addWidget(cap, 0, Qt.AlignHCenter)
            pl.addLayout(col)

        lay.addWidget(group_box(
            group_row(
                "显示器匹配关键字",
                "按设备名或 device_id 的片段定位这台屏，匹配不到就用第一台",
                match_box,
            ),
            group_row(
                "输入源映射",
                "切到该机器时设成此值（VCP 0x60）",
                pair,
                last=True,
            ),
        ))
        lay.addStretch(1)
        return page

    def _page_advanced(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.combo_backend = Combo(["auto", "dxva2", "m1ddc", "command", "mock"])
        self.combo_backend.setCurrentText(self.cfg.ddc_backend)
        self.combo_backend.setFixedWidth(116)
        self.sw_dryrun = ToggleSwitch(self.cfg.dry_run)
        self.sw_readback = ToggleSwitch(self.cfg.readback.enabled)

        btn_open = QPushButton("打开配置文件")
        btn_open.setObjectName("ghost")
        btn_open.setFixedHeight(30)
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(self.openConfigDirRequested)

        lay.addWidget(group_box(
            group_row("DDC 后端", "auto 会按平台自动选择", self.combo_backend),
            group_row("演练模式", "只记日志，不真正发送 Set", self.sw_dryrun),
            group_row("回读校验", "发出后异步读一次 VCP 0x60", self.sw_readback),
            group_row("配置文件", str(self.config_file), btn_open, last=True, wrap=False),
        ))
        lay.addStretch(1)
        return page

    def _num_edit(self, value: int) -> QLineEdit:
        edit = QLineEdit(str(value))
        edit.setObjectName("num")
        edit.setAlignment(Qt.AlignCenter)
        edit.setValidator(QIntValidator(0, 255, edit))
        return edit

    # ------------------------------------------------------------------ 保存
    def _reset(self) -> None:
        self.recorder.stop_recording()
        defaults = Config()
        self.seg_mode.set_value(defaults.switch_mode)
        self.sw_notify.setChecked(defaults.notify)
        self.sw_readback_notify.setChecked(defaults.readback.notify_on_unconfirmed)
        self.slider.setValue(defaults.debounce_ms)
        self.recorder.set_spec(defaults.hotkey)
        self.sw_strict.setChecked(False)
        self.sw_swallow.setChecked(defaults.swallow_hotkey)
        self.sw_tray.setChecked(defaults.tray)
        self.edit_match.setText(defaults.monitor_match)
        self.edit_win.setText(str(defaults.sources["windows"]))
        self.edit_mac.setText(str(defaults.sources["mac"]))
        self.combo_backend.setCurrentText(defaults.ddc_backend)
        self.sw_dryrun.setChecked(defaults.dry_run)
        self.sw_readback.setChecked(defaults.readback.enabled)

    def _save(self) -> None:
        self.recorder.stop_recording()
        try:
            win = int(self.edit_win.text() or 0)
            mac = int(self.edit_mac.text() or 0)
            if win == mac:
                raise ConfigError("Windows 与 Mac 的输入源值不能相同")

            # 关键：不要就地修改 self.cfg。这份对象就是运行中的配置，
            # 就地改会让 runner 的「旧值 vs 新值」比较失效（热键永远不会重建），
            # 校验失败时还会把运行状态带脏。这里先拼一份 dict 再交给 from_dict 校验。
            data = self.cfg.to_dict()
            data.update(
                switch_mode=self.seg_mode.value(),
                notify=self.sw_notify.isChecked(),
                debounce_ms=int(self.slider.value()),
                hotkey=self.recorder.spec(),
                hotkey_backend="hook" if self.sw_strict.isChecked() else "auto",
                swallow_hotkey=self.sw_swallow.isChecked(),
                tray=self.sw_tray.isChecked(),
                monitor_match=self.edit_match.text().strip() or "AOC",
                sources={"windows": win, "mac": mac},
                ddc_backend=self.combo_backend.currentText(),
                dry_run=self.sw_dryrun.isChecked(),
            )
            readback = dict(data.get("readback") or {})
            readback.update(
                enabled=self.sw_readback.isChecked(),
                notify_on_unconfirmed=self.sw_readback_notify.isChecked(),
            )
            data["readback"] = readback

            validated = from_dict(data)
            save_config(validated, self.config_file)
        except (ConfigError, HotkeyError, ValueError) as exc:
            self.error.setText(f"保存失败：{exc}")
            self.error.setVisible(True)
            return

        self.error.setVisible(False)
        self.saved.emit(validated)
        self.hide()

    def closeEvent(self, event: QCloseEvent):  # noqa: N802
        event.ignore()
        self.hide()

    def hideEvent(self, event):  # noqa: N802
        # 窗口收起来时若还在录制，全局热键会一直处于暂停状态
        self.recorder.stop_recording()
        super().hideEvent(event)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        # 点到别处（比如弹窗、别的窗口）也算放弃录制
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.recorder.stop_recording()
