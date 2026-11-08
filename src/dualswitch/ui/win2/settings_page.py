"""设置页（设计 §7）：分组小标题 + 卡片，改动**即时生效**（直接写 config）。

五组（与 mac2 同构）：显示器 / 鼠标跟随 / 通用 / 剪贴板 / 热键。
早先的「高级」组（探测显示器 / 查看日志 / 打开配置目录）已按需求删掉 ——
要查环境走 CLI：`dualswitch probe` / `dualswitch doctor`。

键帽行点击进入录制态（Esc 取消）；存储上限是值显（当前用量 / 上限）+ 可输入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
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
from .widgets import (
    ElidedLabel,
    KeyCaps,
    ToggleSwitch,
    label,
    settings_row,
)

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
class SlotPicker(QWidget):
    """1 / 2 / 3 三个小方块单选。0 = 三个都不选中（槽位还没配）。

    不用 `Segmented`：那是给「标准 / 自动」这类两三字选项的，一个数字塞进去
    被撑成 80px 宽的按钮，行里看着像占了半行。
    """

    changed = Signal(int)

    def __init__(self, value: int = 0, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[int, QPushButton] = {}
        current = int(value or 0)
        for n in (1, 2, 3):
            btn = QPushButton(str(n))
            btn.setObjectName("slotBtn")
            btn.setCheckable(True)
            btn.setFixedSize(34, 28)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setChecked(n == current)
            btn.clicked.connect(lambda _=False, v=n: self._pick(v))
            lay.addWidget(btn)
            self._group.addButton(btn)
            self._buttons[n] = btn

    def _pick(self, value: int) -> None:
        self.changed.emit(value)

    def value(self) -> int:
        for n, btn in self._buttons.items():
            if btn.isChecked():
                return n
        return 0

    def set_value(self, value: int) -> None:
        for n, btn in self._buttons.items():
            btn.setChecked(n == int(value or 0))


class MonitorEntry(QFrame):
    """「显示器配置 ›」整行可点：设计稿 ④ 的 openrow（右侧是说明 + 折角箭头）。"""

    clicked = Signal()

    def __init__(self, title: str, sub: str, parent=None):
        super().__init__(parent)
        t = theme.tokens()
        self.setObjectName("monitorEntry")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setStyleSheet(
            "QFrame#monitorEntry{background:transparent;border:0;}"
            f"QFrame#monitorEntry:hover{{background:rgba(78,135,255,0.04);}}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(15, 9, 15, 9)
        row.setSpacing(10)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.title = label(title, "setTitle")
        self.sub = label(sub, "setSub")
        col.addWidget(self.title)
        col.addWidget(self.sub)
        row.addLayout(col, 1)
        action = label("查看与标定", "setSub")
        row.addWidget(action, 0, Qt.AlignVCenter)
        chev = label("›", "setSub")
        chev.setStyleSheet(f"color:{t.ink3};font-size:13px;background:transparent;")
        row.addWidget(chev, 0, Qt.AlignVCenter)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def click(self) -> None:  # 与 QPushButton 同名，调用方/测试不用区分
        self.clicked.emit()


class SettingsPage(QWidget):
    saved = Signal(object)                 # Config
    clearRequested = Signal()              # 「存储上限」旁边的清理按钮
    recordingChanged = Signal(bool)        # 热键录制中 → 调用方暂停全局热键
    toastRequested = Signal(str)
    monitorConfigRequested = Signal()      # 「显示器配置 ›」子页入口

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

        # ---- 显示器（V3 的主设置，放最前） ----
        self.edit_match = QLineEdit(self.cfg.monitor_match or "")
        self.edit_match.setObjectName("textEdit")
        self.edit_match.setFixedSize(120, 28)
        self.edit_match.setPlaceholderText("（自动取第一台）")
        self.edit_match.editingFinished.connect(self._apply)
        val_backend = label(self.cfg.ddc_backend, "setVal")
        profile = self.cfg.active_profile()
        bits = [profile.name or "外接显示器", f"{len(profile.ports)} 个输入口"]
        calibrated = profile.is_calibrated() or profile.key in self.cfg.monitors
        bits.append("已标定" if calibrated else "未标定")
        self.btn_monitor = MonitorEntry("显示器配置", " · ".join(bits))
        self.btn_monitor.clicked.connect(self.monitorConfigRequested)
        sec_monitor = self._section("显示器", [
            self.btn_monitor,
            settings_row("显示器匹配", "按设备名或 device_id 的片段定位这台屏", self.edit_match),
            settings_row("DDC 后端", "auto / dxva2 / command / mock", val_backend),
        ])

        # ---- 鼠标跟随（Easy-Switch 槽位）----
        # 以前只能手改 config.json 的 mouse_switch；槽位没配时切屏页的开关是隐藏的，
        # 等于「有个功能但打不开」。这里把配置摊到界面上：两端槽位 + 切换延时。
        # 开关仍留在切换页（跟随卡片那一行），避免两处控件互相覆盖。
        ms = getattr(self.cfg, "mouse_switch", None)
        self.pick_slot_local = SlotPicker(int(getattr(ms, "local_slot", 0) or 0))
        self.pick_slot_local.changed.connect(self._on_slot_changed)
        self.pick_slot_peer = SlotPicker(int(getattr(ms, "peer_slot", 0) or 0))
        self.pick_slot_peer.changed.connect(self._on_slot_changed)
        self.edit_settle = QLineEdit(str(int(getattr(ms, "settle_ms", 800) or 0)))
        self.edit_settle.setObjectName("numEdit")
        self.edit_settle.setFixedSize(66, 28)
        self.edit_settle.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.edit_settle.setValidator(QIntValidator(0, 5000, self))
        self.edit_settle.editingFinished.connect(self._apply)
        sec_mouse = self._section("鼠标跟随", [
            settings_row(
                "本机槽位", "鼠标底部 Easy-Switch 键上的 1·2·3，本机接在哪个",
                self.pick_slot_local,
            ),
            settings_row(
                "对端槽位", "另一台电脑占的槽位，不能和本机相同",
                self.pick_slot_peer,
            ),
            settings_row("切换延时", "切屏成功后等多久再把鼠标推过去", self._settle_control()),
        ])

        # ---- 通用（§7.2）----
        self.sw_notify = ToggleSwitch(self.cfg.notify)
        self.sw_notify.clicked.connect(self._apply)
        self.sw_tray = ToggleSwitch(self.cfg.tray)
        self.sw_tray.clicked.connect(self._apply)
        sec_general = self._section("通用", [
            settings_row("切换通知", "切换成功或失败时弹出系统通知", self.sw_notify),
            settings_row("关闭窗口后仍在托盘运行", "关闭后关窗即退出", self.sw_tray),
        ])

        # ---- 剪贴板（§7.3）----
        self.sw_clip = ToggleSwitch(self.cfg.clipboard.enabled)
        self.sw_clip.clicked.connect(self._apply)
        self.sw_hover = ToggleSwitch(getattr(self.cfg.clipboard, "hover_preview", True))
        self.sw_hover.clicked.connect(self._apply)
        # 用 QLineEdit 而不是 QSpinBox：QSpinBox 一旦被 QSS 改边框，内部输入框宽度
        # 会被算成 1px（文字完全看不见）。这里自己校验范围，改动后回车/失焦即保存。
        self.edit_storage = QLineEdit(str(int(getattr(self.cfg.clipboard, "storage_limit_mb", 200))))
        self.edit_storage.setObjectName("numEdit")
        self.edit_storage.setFixedSize(54, 28)
        self.edit_storage.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.edit_storage.setValidator(QIntValidator(10, 5000, self))
        self.edit_storage.editingFinished.connect(self._apply)
        sec_clip = self._section("剪贴板", [
            settings_row("启用跨端剪贴板", "文字与图片自动同步到另一台", self.sw_clip),
            settings_row("悬浮预览", "鼠标悬浮在缩略图上时展开预览窗", self.sw_hover),
            settings_row("存储上限", "超过上限自动丢弃最旧的记录", self._storage_control()),
        ])

        # ---- 热键（§7.4）----
        self.hk_main = HotkeyKeycaps(self.cfg.hotkey)
        self.hk_main.captured.connect(lambda spec: self._apply(hotkey=spec))
        self.hk_main.recordingChanged.connect(self.recordingChanged)
        self.hk_clip = HotkeyKeycaps(windows_clip_hotkey(self.cfg.clipboard_hotkey))
        self.hk_clip.captured.connect(lambda spec: self._apply(clipboard_hotkey=spec))
        self.hk_clip.recordingChanged.connect(self.recordingChanged)
        self.sw_strict = ToggleSwitch("ralt" in parse_hotkey(self.cfg.hotkey).modifiers)
        self.sw_strict.clicked.connect(self._apply)
        sec_hotkey = self._section("热键", [
            settings_row("全局热键", "按下即切换画面", self.hk_main),
            settings_row("剪贴板热键", "呼出剪贴板页并聚焦搜索", self.hk_clip),
            settings_row("严格区分左右 Alt", "打开后左 Alt 不触发（改用低级键盘钩子）", self.sw_strict),
        ])

        for section in (sec_monitor, sec_mouse, sec_general, sec_clip, sec_hotkey):
            lay.addWidget(section)
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

    def _persist_active_profile(self, data: dict) -> None:
        """把当前屏的 profile 一起落盘。

        P1 起切换走 `profile.ports + wiring`；只写旧 sources 会在 from_dict 里被
        profile 的真值覆盖掉，用户会觉得“改了没生效”。端口码值的编辑入口在
        「显示器配置」子页，这里只负责保存时把它写回，并把 `active_monitor`
        指到这块屏。
        """
        profile = getattr(self.cfg, "_active_profile", None)
        if profile is None:
            return
        data["active_monitor"] = profile.key
        monitors = data.setdefault("monitors", {})
        monitors[profile.key] = {
            "key": profile.key,
            "name": profile.name,
            "vcp": profile.vcp,
            "ports": dict(profile.ports),
            "readback": profile.readback,
            "serial": profile.serial,
            "year": profile.year,
            "week": profile.week,
            "calibrated_at": profile.calibrated_at,
            "notes": profile.notes,
            "builtin": False,
        }

    # --- 鼠标跟随 ---
    def _on_slot_changed(self, _value: int) -> None:
        """两端不能同一个槽位：撞了就退回原值，别让界面显示一个存不下去的组合。"""
        local = self.pick_slot_local.value()
        peer = self.pick_slot_peer.value()
        if local and local == peer:
            ms = getattr(self.cfg, "mouse_switch", None)
            self.pick_slot_local.set_value(int(getattr(ms, "local_slot", 0) or 0))
            self.pick_slot_peer.set_value(int(getattr(ms, "peer_slot", 0) or 0))
            self.toastRequested.emit("本机和对端不能选同一个槽位")
            return
        self._apply()

    def _settle_control(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.edit_settle, 0, Qt.AlignVCenter)
        row.addWidget(label("ms", "setVal"), 0, Qt.AlignVCenter)
        return box

    def _settle_value(self) -> int:
        """切换延时 → 整数（非法/越界夹到 0–5000）。"""
        try:
            value = int(str(self.edit_settle.text()).strip() or 0)
        except ValueError:
            ms = getattr(self.cfg, "mouse_switch", None)
            value = int(getattr(ms, "settle_ms", 800) or 0)
        return max(0, min(5000, value))

    def _storage_limit_value(self) -> int:
        """存储上限输入框 → 整数（非法/越界就夹到 10–5000）。"""
        try:
            value = int(str(self.edit_storage.text()).strip() or 0)
        except ValueError:
            value = getattr(self.cfg.clipboard, "storage_limit_mb", 200)
        return max(10, min(5000, value))

    def _storage_control(self) -> QWidget:
        """存储上限：可编辑的数字框 + 旁边一个「清理」按钮。

        宽度要卡死：这一行右侧是「已用 0.0 MB + 输入框 + MB + 清理」四段，
        字号一变大就会顶出行宽（2026-10-22 用户截图里清理按钮盖住了 MB）。
        所以这里数字框收窄、间距压到 6，`已用` 用 ElidedLabel 让位。

        **按钮只卡高度、不卡宽度**：`ghostBtn` 的 QSS 带 `padding: 0 16px` +
        `font-size: 13px`，两个汉字就要 58px；早先写死 52px 时 `sizeHint` 被压掉，
        Qt 把「清理」两字挤在一个 18px 的框里画 —— 2026-10-22 用户截图里的
        「清理按钮被遮挡」就是文字被裁（不是被别的控件盖住）。
        交给 sizeHint 算宽度（QSS 的 padding 会算进去），再加一个下限保持视觉对齐。
        """
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.btn_clean = QPushButton("清理")
        self.btn_clean.setObjectName("ghostBtn")
        self.btn_clean.setFixedHeight(28)
        self.btn_clean.setMinimumWidth(52)
        self.btn_clean.setCursor(Qt.PointingHandCursor)
        self.btn_clean.setToolTip("清空本机剪贴板记录")
        self.btn_clean.clicked.connect(self.clearRequested)
        self.lbl_used = label("", "setVal")
        self.lbl_used.setToolTip("已用容量 / 存储上限")
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
                # 「切换模式」已从界面移除：保留盘上原值，不改写用户已有选择。
                switch_policy=getattr(self.cfg, "switch_policy", "standard"),
                notify=self.sw_notify.isChecked(),
                tray=self.sw_tray.isChecked(),
                # 外观固定浅色（Windows2 与 mac2 同一决定）
                appearance="light",
                hotkey=hotkey,
                # 严格左右 Alt 只有低级键盘钩子后端做得到
                hotkey_backend="hook" if strict else "auto",
                # 设置页不再暴露这两项（与 mac 端对齐）：沿用盘上现值，不改写。
                swallow_hotkey=bool(getattr(self.cfg, "swallow_hotkey", True)),
                clipboard_hotkey=clip_hotkey,
                monitor_match=self.edit_match.text().strip(),
                # 输入源码值只在「显示器配置」里改；这里原样保留（已由 profile+wiring 同步）
                sources=dict(self.cfg.sources),
            )
            self._persist_active_profile(data)
            clip = dict(data.get("clipboard") or {})
            clip.update(
                enabled=self.sw_clip.isChecked(),
                hover_preview=self.sw_hover.isChecked(),
                storage_limit_mb=self._storage_limit_value(),
            )
            data["clipboard"] = clip
            local_slot = self.pick_slot_local.value()
            peer_slot = self.pick_slot_peer.value()
            if local_slot and local_slot == peer_slot:
                # 两端同一个槽位 = 打开鼠标跟随后两边互相把对方推走，先拦住
                self.toastRequested.emit("本机和对端不能选同一个槽位")
                return
            mouse = dict(data.get("mouse_switch") or {})
            mouse.update(
                local_slot=local_slot,
                peer_slot=peer_slot,
                settle_ms=self._settle_value(),
            )
            data["mouse_switch"] = mouse
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
