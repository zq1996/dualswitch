"""设置页（设计 §7）：分组小标题 + 卡片，改动**即时生效**（直接写 config）。

五组：通用 / 剪贴板 / 热键 / 显示器 / **高级**。
「高级」是 Windows2 相对 mac2 唯一的**新增分组**（§7.5）：探测显示器、查看日志、
打开配置目录 —— 这三个调试入口从托盘日常菜单里移出来，日常菜单保持干净，
排障时又能找到。

键帽行点击进入录制态（Esc 取消）；存储上限是值显（当前用量 / 上限）+ 可输入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...config import Config, ConfigError, from_dict, save as save_config
from ...hotkey.base import HotkeyError, parse_hotkey
from . import theme
from .widgets import KeyCaps, Segmented, ToggleSwitch, label, settings_row

#: 剪贴板热键在 Windows 上的默认值。配置里的默认值是 mac 口径 `cmd+shift+v`
#: （`cmd` 解析成 Win 键），Windows 端按设计 §2 用 `Ctrl+Shift+V`。
CLIP_HOTKEY_DEFAULT = "ctrl+shift+v"


def windows_clip_hotkey(spec: str) -> str:
    """把 mac 口径的 `cmd+shift+v` 归一成 Windows 的 `ctrl+shift+v`。

    只换修饰键写法（cmd/command/win/meta/super → ctrl），其它组合原样保留 ——
    用户在 mac 上配的 `cmd+alt+v` 到 Windows 就是 `ctrl+alt+v`，不会丢键。
    """
    tokens = [t for t in (spec or "").replace(" ", "").split("+") if t]
    if not tokens:
        return CLIP_HOTKEY_DEFAULT
    out: list[str] = []
    for token in tokens:
        low = token.lower()
        out.append("ctrl" if low in ("cmd", "command", "win", "meta", "super") else token)
    return "+".join(out)


# --------------------------------------------------------------------------- #
# 键帽录制行
# --------------------------------------------------------------------------- #
class HotkeyKeycaps(QWidget):
    """展示键帽；点击进入录制态，按下组合键即写入，Esc 取消。"""

    captured = Signal(str)            # hotkey spec
    recordingChanged = Signal(bool)

    _SPECIAL = {
        Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter",
        Qt.Key_Tab: "tab", Qt.Key_Escape: "escape", Qt.Key_Backspace: "backspace",
        Qt.Key_Delete: "delete", Qt.Key_Up: "up", Qt.Key_Down: "down",
        Qt.Key_Left: "left", Qt.Key_Right: "right",
    }

    def __init__(self, spec: str, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._recording = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._caps = KeyCaps(theme.keycap_labels(self._describe()))
        self._hint = QLabel("请按下组合键…（Esc 取消）")
        self._hint.setObjectName("setSub")
        self._hint.setVisible(False)
        lay.addWidget(self._caps)
        lay.addWidget(self._hint)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.ClickFocus)

    def _describe(self) -> str:
        try:
            return parse_hotkey(self._spec).describe()
        except HotkeyError:
            return self._spec

    def spec(self) -> str:
        return self._spec

    def set_spec(self, spec: str) -> None:
        self._spec = spec
        self._caps.set_keys(theme.keycap_labels(self._describe()))

    def is_recording(self) -> bool:
        return self._recording

    def stop_recording(self) -> None:
        self._set_recording(False)

    def _set_recording(self, on: bool) -> None:
        if on == self._recording:
            return
        self._recording = on
        self._caps.setVisible(not on)
        self._hint.setVisible(on)
        if on:
            self.setFocus(Qt.MouseFocusReason)
        self.recordingChanged.emit(on)

    def mousePressEvent(self, event):  # noqa: N802
        self._set_recording(not self._recording)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            return
        if key == Qt.Key_Escape:
            self._set_recording(False)
            return
        mods = []
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
            self._hint.setText("至少需要一个修饰键" if not mods else "这个组合不支持")
            QTimer.singleShot(1300, lambda: self._hint.setText("请按下组合键…（Esc 取消）"))
            return
        self._spec = spec
        self._set_recording(False)
        self.set_spec(spec)
        self.captured.emit(spec)


# --------------------------------------------------------------------------- #
# 设置页
# --------------------------------------------------------------------------- #
class SettingsPage(QWidget):
    saved = Signal(object)                 # Config
    clearRequested = Signal()              # 「存储上限」旁边的清理按钮
    recordingChanged = Signal(bool)        # 热键录制中 → 调用方暂停全局热键
    toastRequested = Signal(str)
    probeRequested = Signal()              # 高级：探测显示器 / 读取输入源
    openLogRequested = Signal()            # 高级：查看日志
    openConfigDirRequested = Signal()      # 高级：打开配置目录

    def __init__(self, cfg: Config, config_file: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.config_file = Path(config_file)
        self._build()

    # ------------------------------------------------------------------ 版面
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(20, 18, 20, 24)
        lay.setSpacing(20)

        # ---- 通用（§7.2）----
        self.seg_policy = Segmented(
            [("standard", "标准"), ("force", "强制")],
            getattr(self.cfg, "switch_policy", "standard"),
        )
        self.seg_policy.changed.connect(self._apply)
        self.sw_notify = ToggleSwitch(self.cfg.notify)
        self.sw_notify.clicked.connect(self._apply)
        self.sw_tray = ToggleSwitch(self.cfg.tray)
        self.sw_tray.clicked.connect(self._apply)
        lay.addWidget(self._section("通用", [
            settings_row("切换模式", "标准 = 先本地后请对端代发；强制 = 直接发 Set", self.seg_policy),
            settings_row("切换完成通知", "切换成功后弹出系统通知", self.sw_notify),
            settings_row("关闭窗口后仍在托盘运行", "Windows 端默认开启；关闭后关窗即退出", self.sw_tray),
        ]))

        # ---- 剪贴板（§7.3）----
        self.sw_clip = ToggleSwitch(self.cfg.clipboard.enabled)
        self.sw_clip.clicked.connect(self._apply)
        self.sw_hover = ToggleSwitch(getattr(self.cfg.clipboard, "hover_preview", True))
        self.sw_hover.clicked.connect(self._apply)
        # 用 QLineEdit 而不是 QSpinBox：QSpinBox 一旦被 QSS 改边框，内部输入框宽度
        # 会被算成 1px（文字完全看不见）。这里自己校验范围，改动后回车/失焦即保存。
        self.edit_storage = QLineEdit(str(int(getattr(self.cfg.clipboard, "storage_limit_mb", 200))))
        self.edit_storage.setObjectName("numEdit")
        self.edit_storage.setFixedSize(62, 28)
        self.edit_storage.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.edit_storage.setValidator(QIntValidator(10, 5000, self))
        self.edit_storage.editingFinished.connect(self._apply)
        lay.addWidget(self._section("剪贴板", [
            settings_row("启用跨端剪贴板", "文字与图片自动同步到另一台", self.sw_clip),
            settings_row("悬浮预览", "鼠标悬浮在缩略图上时展开预览窗", self.sw_hover),
            settings_row("存储上限", "超过上限自动丢弃最旧的记录", self._storage_control()),
        ]))

        # ---- 热键（§7.4）----
        self.hk_main = HotkeyKeycaps(self.cfg.hotkey)
        self.hk_main.captured.connect(lambda spec: self._apply(hotkey=spec))
        self.hk_main.recordingChanged.connect(self.recordingChanged)
        self.hk_clip = HotkeyKeycaps(windows_clip_hotkey(self.cfg.clipboard_hotkey))
        self.hk_clip.captured.connect(lambda spec: self._apply(clipboard_hotkey=spec))
        self.hk_clip.recordingChanged.connect(self.recordingChanged)
        self.sw_strict = ToggleSwitch("ralt" in parse_hotkey(self.cfg.hotkey).modifiers)
        self.sw_strict.clicked.connect(self._apply)
        self.sw_swallow = ToggleSwitch(getattr(self.cfg, "swallow_hotkey", True))
        self.sw_swallow.clicked.connect(self._apply)
        lay.addWidget(self._section("热键", [
            settings_row("全局热键", "按下即切换画面", self.hk_main),
            settings_row("剪贴板热键", "呼出剪贴板页并聚焦搜索", self.hk_clip),
            settings_row("严格区分左右 Alt", "打开后左 Alt 不触发（改用低级键盘钩子）", self.sw_strict),
            settings_row(
                "阻止 Alt+Space 弹出系统菜单",
                "只在钩子后端生效；关掉会先弹系统菜单再切换",
                self.sw_swallow,
            ),
        ]))

        # ---- 显示器（§7.5）----
        self.edit_match = QLineEdit(self.cfg.monitor_match or "")
        self.edit_match.setObjectName("textEdit")
        self.edit_match.setFixedSize(120, 28)
        self.edit_match.setPlaceholderText("（自动取第一台）")
        self.edit_match.editingFinished.connect(self._apply)
        val_backend = label(self.cfg.ddc_backend, "setVal")
        lay.addWidget(self._section("显示器", [
            settings_row("显示器匹配", "按设备名或 device_id 的片段定位这台屏", self.edit_match),
            settings_row("DDC 后端", "auto / dxva2 / command / mock", val_backend),
            settings_row("输入源映射", "27 会被这块屏拒绝，别照抄网上的值", self._sources_control()),
        ]))

        # ---- 高级（§7.5，Windows2 新增组）----
        lay.addWidget(self._section("高级", [
            settings_row(
                "探测显示器 / 读取输入源",
                "结果写进日志，并在窗口里提示",
                self._action_button("运行", self.probeRequested),
            ),
            settings_row("查看日志", "用默认编辑器打开当前日志文件", self._action_button("打开", self.openLogRequested)),
            settings_row("打开配置目录", "在资源管理器中定位配置文件", self._action_button("打开", self.openConfigDirRequested)),
        ]))

        lay.addStretch(1)
        self._refresh_storage()
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

    @staticmethod
    def _section(title: str, rows: list[QWidget]) -> QWidget:
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        head = label(title, "setSec")
        head.setContentsMargins(4, 0, 0, 0)
        col.addWidget(head)
        card = QFrame()
        card.setObjectName("setCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        for i, row in enumerate(rows):
            if i:
                sep = QFrame()
                sep.setObjectName("setSep")
                sep.setFixedHeight(1)
                card_lay.addWidget(sep)
            card_lay.addWidget(row)
        col.addWidget(card)
        return wrap

    @staticmethod
    def _action_button(text: str, signal) -> QPushButton:  # noqa: ANN001
        btn = QPushButton(text)
        btn.setObjectName("ghostBtn")
        btn.setFixedSize(56, 28)
        btn.setFont(theme.qfont(11.5, 600))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(signal)
        return btn

    def _sources_control(self) -> QWidget:
        """输入源映射：`Windows [17]  Mac [18]` 两个数字框（§7.5）。"""
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.edit_win_src = QLineEdit(str(int(self.cfg.sources["windows"])))
        self.edit_mac_src = QLineEdit(str(int(self.cfg.sources["mac"])))
        for edit in (self.edit_win_src, self.edit_mac_src):
            edit.setObjectName("numEdit")
            edit.setFixedSize(48, 28)
            edit.setAlignment(Qt.AlignCenter)
            edit.setValidator(QIntValidator(0, 255, self))
            edit.editingFinished.connect(self._apply)
        row.addWidget(label("Windows", "setSub"), 0, Qt.AlignVCenter)
        row.addWidget(self.edit_win_src, 0, Qt.AlignVCenter)
        row.addSpacing(6)
        row.addWidget(label("Mac", "setSub"), 0, Qt.AlignVCenter)
        row.addWidget(self.edit_mac_src, 0, Qt.AlignVCenter)
        return box

    def _storage_limit_value(self) -> int:
        """存储上限输入框 → 整数（非法/越界就夹到 10–5000）。"""
        try:
            value = int(str(self.edit_storage.text()).strip() or 0)
        except ValueError:
            value = getattr(self.cfg.clipboard, "storage_limit_mb", 200)
        return max(10, min(5000, value))

    def _source_value(self, edit: QLineEdit, host: str) -> int:
        try:
            value = int(str(edit.text()).strip() or 0)
        except ValueError:
            value = int(self.cfg.sources[host])
        return max(0, min(255, value))

    def _storage_control(self) -> QWidget:
        """存储上限：可编辑的数字框 + 旁边一个「清理」按钮。"""
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.btn_clean = QPushButton("清理")
        self.btn_clean.setObjectName("ghostBtn")
        self.btn_clean.setFixedSize(56, 28)
        self.btn_clean.setFont(theme.qfont(11.5, 600))
        self.btn_clean.setCursor(Qt.PointingHandCursor)
        self.btn_clean.setToolTip("清空本机剪贴板记录")
        self.btn_clean.clicked.connect(self.clearRequested)
        self.lbl_used = label("", "setVal")
        unit = label("MB", "setVal")
        row.addWidget(self.lbl_used, 0, Qt.AlignVCenter)
        row.addWidget(self.edit_storage, 0, Qt.AlignVCenter)
        row.addWidget(unit, 0, Qt.AlignVCenter)
        row.addWidget(self.btn_clean, 0, Qt.AlignVCenter)
        return box

    def _refresh_storage(self) -> None:
        used = 0.0
        try:
            for rec in getattr(self.engine_records(), "__iter__", lambda: [])():
                used += getattr(rec, "size", 0) or 0
        except Exception:  # noqa: BLE001
            pass
        self.used_mb = used / 1048576
        lbl = getattr(self, "lbl_used", None)
        if lbl is not None:
            lbl.setText(f"已用 {self.used_mb:.1f} MB")

    def set_engine_records(self, records) -> None:  # noqa: ANN001
        self._records = records
        self._refresh_storage()

    def engine_records(self):  # noqa: ANN202
        return getattr(self, "_records", [])

    # ------------------------------------------------------------------ 保存
    @staticmethod
    def _with_strict_modifier(spec: str, strict: bool) -> str:
        tokens = [t for t in spec.replace(" ", "").split("+") if t]
        if strict:
            return "+".join("ralt" if t.lower() in ("alt", "option", "opt") else t for t in tokens)
        return "+".join("alt" if t.lower() in ("ralt", "rightalt", "right_option") else t for t in tokens)

    def _apply(self, _checked: bool = False, **overrides) -> None:
        """任一控件变化 → 拼 dict → 校验 → 落盘 → saved(cfg)。"""
        self.hk_main.stop_recording()
        self.hk_clip.stop_recording()
        strict = self.sw_strict.isChecked()
        try:
            hotkey = overrides.get(
                "hotkey",
                self._with_strict_modifier(self.hk_main.spec(), strict),
            )
            parse_hotkey(hotkey)
            clip_hotkey = windows_clip_hotkey(
                overrides.get("clipboard_hotkey", self.hk_clip.spec())
            )
            parse_hotkey(clip_hotkey)
            data = self.cfg.to_dict()
            data.update(
                switch_policy=self.seg_policy.value(),
                notify=self.sw_notify.isChecked(),
                tray=self.sw_tray.isChecked(),
                # 外观固定浅色（Windows2 与 mac2 同一决定）
                appearance="light",
                hotkey=hotkey,
                # 严格左右 Alt 只有低级键盘钩子后端做得到
                hotkey_backend="hook" if strict else "auto",
                swallow_hotkey=self.sw_swallow.isChecked(),
                clipboard_hotkey=clip_hotkey,
                monitor_match=self.edit_match.text().strip(),
                sources={
                    "windows": self._source_value(self.edit_win_src, "windows"),
                    "mac": self._source_value(self.edit_mac_src, "mac"),
                },
            )
            clip = dict(data.get("clipboard") or {})
            clip.update(
                enabled=self.sw_clip.isChecked(),
                hover_preview=self.sw_hover.isChecked(),
                storage_limit_mb=self._storage_limit_value(),
            )
            data["clipboard"] = clip
            validated = from_dict(data)
            save_config(validated, self.config_file)
        except (ConfigError, HotkeyError, ValueError) as exc:
            self.toastRequested.emit(f"保存失败：{exc}")
            return
        self.cfg = validated
        self.saved.emit(validated)

    def stop_recording(self) -> None:
        self.hk_main.stop_recording()
        self.hk_clip.stop_recording()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.stop_recording()
