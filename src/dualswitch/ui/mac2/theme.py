"""v2 界面主题：视觉令牌 + QSS（明/暗两套）+ 字体与键帽解析。

`design/redesign/index-v2.html` 的 CSS 变量是这套 token 的真源；这里只做
「HTML 变量 → Qt QSS / Python 常量」的翻译，不新增设计。

**字号一律用像素**（设计稿是 CSS px）。Qt 的 point 单位在 96dpi 下会被放大到
1.33 倍，之前整窗字大一号、行高发胖就是这个原因 —— 这里用 `setPixelSize()`
和 QSS 的 `px` 两条路统一到设计稿的像素尺度。

外观三态：跟随系统 / 浅色 / 深色（INTERACTIONS §0.2）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from ..chrome import current as _chrome

_C = _chrome()

# --------------------------------------------------------------------------- #
# 几何（INTERACTIONS §7 视觉规格速查）
# --------------------------------------------------------------------------- #
WIN_W, WIN_H = 780, 580          # 整窗（含系统标题栏覆盖区）
TITLEBAR_H = 40                  # 设计稿 .win-bar 高度
SIDEBAR_W = 188
FOOTER_H = 30
TOOLBAR_H = 40                   # 标题栏工具钮条（与 .win-bar 同高）
CONTENT_PAD_H, CONTENT_PAD_V = 18, 20
RADIUS_WIN = 16
RADIUS_CARD = 12
RADIUS_CTRL = 10
RADIUS_PILL = 6

KEY_W, KEY_H, KEY_R = 18, 18, 5
KEY_WIDE_W = 30                  # Space 这类宽键
KEY_GAP = 3
KEY_FONT_PX = 10

THUMB = 46                       # 剪贴板缩略图
THUMB_R = 10
BADGE = 22                       # 来源系统角标（19 太小看不清，用户 2026-10-21 要求放大）
ROW_H = 68                       # 剪贴板行高（46 图 + 两行字 + padding）
TOOL_ROW_H = 44
SEARCH_H = 34
ICON_BTN = 34
ACT_BTN = 25

# 悬浮预览窗（Frset 参数，INTERACTIONS §3.4）
PV_OPEN_DELAY_MS = 450           # 只悬停缩略图才计时
PV_CLOSE_DELAY_MS = 220
PV_IMG_MAX_W, PV_IMG_MAX_H = 412, 330
PV_TXT_MAX_W, PV_TXT_MAX_H = 392, 270
#: 预览窗正文最多渲染多少字。**不是视觉截断**：正常长度的文本一律完整显示
#: （超长时预览窗长到屏幕高度、内部可滚动）；只有超过这个量级才切掉，
#: 免得几 MB 的日志让 QLabel 排版把机器拖卡。
PV_TEXT_MAX_CHARS = 20000
PV_CODE_MAX_W, PV_CODE_MAX_H = 400, 290
PV_CARD_MAX_W = 380
PV_RADIUS = 13

MENU_W = 242                     # 菜单栏弹出菜单宽

FONT_FAMILIES = list(_C.PALETTE.font_families)
APP_FONT_PX = 12.5               # 全局默认字号（= 设计稿正文 12.5px）
BODY_FS = 12.5                   # 行题字号
META_FS = 10
MONO_FAMILIES = ["SF Mono", "JetBrains Mono", "Menlo", "monospace"]

# 设计稿 §7 字号档
FS_PAGE = 27                     # hero 平台名
FS_ROW = 12.5                    # 行题
FS_META = 10                     # 元信息
FS_SECTION = 10.5                # 分组小标题
FS_SMALL = 11.5


# --------------------------------------------------------------------------- #
# 视觉令牌
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tokens:
    name: str
    accent: str
    accent_deep: str
    accent_soft: str
    ink: str
    ink2: str
    ink3: str
    line: str
    line_soft: str
    card: str
    card2: str
    card3: str
    sidebar: str
    sidebar_active: str
    ok: str
    ok_bg: str
    run: str
    run_bg: str
    idle: str
    idle_bg: str
    danger: str
    danger_bg: str
    warn: str
    warn_bg: str
    on_accent: str
    key_bg: str
    key_border: str
    key_fg: str
    shadow: str


LIGHT = Tokens(
    name="light",
    accent="#4E87FF", accent_deep="#2E63E8", accent_soft="rgba(78,135,255,33)",
    ink="#14181F", ink2="#4B5566", ink3="#8A93A3",
    line="#E3E7EE", line_soft="#EDF0F5",
    card="#FFFFFF", card2="#F7F9FC", card3="#F0F3F8",
    sidebar="#F7F8FB", sidebar_active="#E8EEFB",
    ok="#0E7C4A", ok_bg="rgba(14,124,74,28)",
    run="#2E63E8", run_bg="rgba(46,99,232,28)",
    idle="#7C8698", idle_bg="rgba(124,134,152,33)",
    danger="#A82B2B", danger_bg="rgba(168,43,43,28)",
    warn="#B4690E", warn_bg="rgba(180,105,14,28)",
    on_accent="#FFFFFF",
    key_bg="#F0F3F8", key_border="#E3E7EE", key_fg="#4B5566",
    shadow="rgba(16,24,40,26)",
)

DARK = Tokens(
    name="dark",
    accent="#6E9BFF", accent_deep="#98BCFF", accent_soft="rgba(110,155,255,46)",
    ink="#E9EDF3", ink2="#AEB7C5", ink3="#78828F",
    line="#2B323D", line_soft="#242A34",
    card="#1B2028", card2="#222833", card3="#2A313D",
    sidebar="#1B2028", sidebar_active="rgba(110,155,255,46)",
    ok="#4FD18B", ok_bg="rgba(79,209,139,38)",
    run="#7FA8FF", run_bg="rgba(127,168,255,38)",
    idle="#8B95A5", idle_bg="rgba(139,149,165,46)",
    danger="#F08585", danger_bg="rgba(240,133,133,38)",
    warn="#E2B566", warn_bg="rgba(226,181,102,38)",
    on_accent="#FFFFFF",
    key_bg="#2A313D", key_border="#2B323D", key_fg="#AEB7C5",
    shadow="rgba(0,0,0,120)",
)

_CURRENT: Tokens = LIGHT


def tokens() -> Tokens:
    """当前生效的令牌（随 set_appearance 切换）。"""
    return _CURRENT


def set_appearance(mode: str) -> Tokens:
    """mode: system | light | dark。system 跟随 NSAppearance / Qt 调色板。"""
    global _CURRENT
    _CURRENT = resolve_appearance(mode)
    return _CURRENT


def resolve_appearance(mode: str) -> Tokens:
    if mode == "light":
        return LIGHT
    if mode == "dark":
        return DARK
    return DARK if _system_is_dark() else LIGHT


def _system_is_dark() -> bool:
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return False
        scheme = app.styleHints().colorScheme()
        from PySide6.QtCore import Qt

        return scheme == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - 主题探测失败时按浅色
        return False


# --------------------------------------------------------------------------- #
# 颜色
# --------------------------------------------------------------------------- #
_RGBA_RE = None


def qcolor(value: str, alpha: float | None = None):
    """令牌串 → QColor。

    QSS 能解析 `rgba(78,135,255,33)`，但 **QColor 不能**（PySide6 6.12 直接判无效，
    画出来是纯黑）。自绘控件一律走这个函数，别直接 `QColor(token)`。
    """
    global _RGBA_RE
    import re

    from PySide6.QtGui import QColor

    if alpha is not None:
        c = qcolor(value)
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        return c
    text = (value or "").strip()
    if text.startswith("#") or text.lower() in ("transparent", "white", "black"):
        return QColor(text)
    if _RGBA_RE is None:
        _RGBA_RE = re.compile(r"rgba?\(([^)]*)\)", re.IGNORECASE)
    m = _RGBA_RE.match(text)
    if not m:
        return QColor(text)
    parts = [p.strip() for p in m.group(1).split(",")]
    try:
        r, g, b = (int(round(float(parts[i]))) for i in range(3))
        a = 255
        if len(parts) > 3:
            raw = parts[3]
            a = int(round(float(raw[:-1]) * 2.55)) if raw.endswith("%") else int(round(float(raw)))
        return QColor(r, g, b, max(0, min(255, a)))
    except (ValueError, IndexError):
        return QColor(text)


#: 剪贴板类型瓷砖配色（取自 Frset `clipboard_type_text/url/color` 的实色底 + 白字形）。
#: text=靛蓝 #657DFF、url=雾蓝 #6B7BD3、color=桃红 #FF55AA；file/unknown 是浅灰底灰字形。
TYPE_TILES = {
    "light": {
        "text": ("#657DFF", "#FFFFFF"),
        "code": ("#7A5AF8", "#FFFFFF"),
        "link": ("#6B7BD3", "#FFFFFF"),
        "image": ("#4E87FF", "#FFFFFF"),
        "file": ("#E9ECF3", "#8A93A3"),
        "unknown": ("#E9ECF3", "#8A93A3"),
    },
    "dark": {
        "text": ("#5A70E8", "#FFFFFF"),
        "code": ("#6D4EE0", "#FFFFFF"),
        "link": ("#5C6BC0", "#FFFFFF"),
        "image": ("#3F76E0", "#FFFFFF"),
        "file": ("#2A313D", "#8B95A5"),
        "unknown": ("#2A313D", "#8B95A5"),
    },
}


def type_tile(kind: str) -> tuple[str, str]:
    """类型 → (底色, 字形色)。"""
    table = TYPE_TILES.get(_CURRENT.name, TYPE_TILES["light"])
    return table.get(kind, table["unknown"])


# --------------------------------------------------------------------------- #
# 字体（像素尺度）
# --------------------------------------------------------------------------- #
def qfont(px: float = APP_FONT_PX, weight: int | None = None, mono: bool = False):
    """按设计稿像素字号造字体；`weight` 用 400/550/650/700。"""
    from PySide6.QtGui import QFont

    f = QFont()
    f.setFamilies(MONO_FAMILIES if mono else FONT_FAMILIES)
    f.setPixelSize(max(1, int(round(px))))
    if weight is not None:
        f.setWeight(QFont.Weight(int(weight)))
    return f


# --------------------------------------------------------------------------- #
# 资源
# --------------------------------------------------------------------------- #
def asset(name: str) -> Path:
    """产品资源：ui/assets/（两端共用）→ 仓库 design/icon/ → 打包 _MEIPASS。"""
    here = Path(__file__).resolve().parent          # ui/mac2
    candidates = [here.parent / "assets" / name]
    for parent in here.parents[:5]:
        candidates.append(parent / "design" / "icon" / name)
        candidates.append(parent / "design" / "icon" / "png" / name)
    candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "assets" / name)
    candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "dualswitch" / "ui" / "assets" / name)
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError(f"找不到资源 {name}")


# --------------------------------------------------------------------------- #
# 键帽解析：Hotkey 描述串 → 每键一个键帽（INTERACTIONS §0.3）
# --------------------------------------------------------------------------- #
#: 描述词 → 键帽字形（mac 系统叫法优先）
GLYPHS = {
    "command": "⌘", "cmd": "⌘", "win": "⌘", "meta": "⌘",
    "control": "⌃", "ctrl": "⌃",
    "option": "⌥", "alt": "⌥", "opt": "⌥",
    "right option": "⌥", "right alt": "⌥", "ralt": "⌥",
    "shift": "⇧",
    "space": "Space", "spacebar": "Space",
    "enter": "↩", "return": "↩", "tab": "⇥", "escape": "⎋",
    "backspace": "⌫", "delete": "⌦",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "pageup": "⇞", "pagedown": "⇟", "home": "↖", "end": "↘",
}

WIDE_KEYS = {"space", "enter", "return", "tab", "backspace", "delete", "escape",
             "pageup", "pagedown", "home", "end"}


def keycap_labels(describe: str) -> list[str]:
    """`Right Option+Space` → [`⌥`, `Space`]；`Command+Shift+V` → [`⌘`,`⇧`,`V`]。

    只按 `+` 切分（`Right Option` 本身带空格），空片段丢掉。
    """
    if not describe or describe == "未启用":
        return []
    out: list[str] = []
    for part in describe.split("+"):
        token = part.strip()
        if not token:
            continue
        glyph = GLYPHS.get(token.lower())
        out.append(glyph or (token.upper() if len(token) == 1 else token))
    return out


def is_wide_key(label: str) -> bool:
    return label.lower() in WIDE_KEYS or label == "Space"


# --------------------------------------------------------------------------- #
# QSS
# --------------------------------------------------------------------------- #
def qss(t: Tokens | None = None) -> str:
    t = t or _CURRENT
    return f"""
    /* ---------- 基础 ---------- */
    QWidget {{
        color: {t.ink};
        font-size: {APP_FONT_PX}px;
        background: transparent;
    }}
    QToolTip {{
        background: {t.ink}; color: {t.card}; border: 0; padding: 5px 8px;
        font-size: 11px; border-radius: 6px;
    }}
    #root {{ background: {t.card}; border: 0; }}
    #pageStack {{ background: {t.card}; border: 0; }}

    /* ---------- 滚动条（9px、无箭头、圆角滑块） ---------- */
    QScrollArea {{ background: transparent; border: 0; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {t.line}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t.ink3}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}

    /* ---------- 侧栏 ---------- */
    #sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.line_soft}; }}
    #navSec {{
        color: {t.ink3}; font-size: 9.5px; font-weight: 700;
        letter-spacing: 0.6px; padding: 8px 10px 5px 10px;
    }}
    QPushButton#nav {{
        background: transparent; border: 0; border-radius: 8px;
        color: {t.ink2}; font-size: 12.5px; font-weight: 550;
        text-align: left; padding: 0 10px;
    }}
    QPushButton#nav:hover {{ background: {t.card3}; color: {t.ink}; }}
    QPushButton#nav:checked {{ background: {t.accent}; color: {t.on_accent}; font-weight: 620; }}
    #navLabel {{ color: {t.ink2}; font-size: 12.5px; font-weight: 550; }}
    #navLabelOn {{ color: {t.on_accent}; font-size: 12.5px; font-weight: 620; }}
    #navCnt {{
        background: {t.accent_soft}; color: {t.accent_deep};
        font-size: 9.5px; font-weight: 700; border-radius: 8px; padding: 1px 6px;
    }}
    #navCntOn {{ background: rgba(255,255,255,0.25); color: {t.on_accent};
        font-size: 9.5px; font-weight: 700; border-radius: 8px; padding: 1px 6px; }}
    #navFoot {{ border-top: 1px solid {t.line_soft}; }}
    #navFootText {{ color: {t.ink3}; font-size: 10px; }}

    /* ---------- 标题栏工具钮 ---------- */
    QPushButton#toolBtn {{
        background: transparent; border: 0; border-radius: 7px;
    }}
    QPushButton#toolBtn:hover {{ background: {t.card3}; }}
    QPushButton#toolBtn:checked {{ background: {t.accent_soft}; }}

    /* ---------- 键帽 ---------- */
    #keycap {{
        background: {t.key_bg}; border: 1px solid {t.key_border}; border-radius: 5px;
        color: {t.key_fg}; font-size: 10px; font-weight: 650;
    }}
    #keycapOnAccent {{
        background: rgba(255,255,255,0.20); border: 1px solid rgba(255,255,255,0.28);
        border-radius: 5px; color: #FFFFFF; font-size: 10px; font-weight: 650;
    }}
    #keycapGhost {{
        background: transparent; border: 0; color: {t.ink3};
        font-size: 10px; font-weight: 650;
    }}

    /* ---------- 切换页 hero ---------- */
    #hero {{
        background: {t.card2}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px;
    }}
    #heroLabel {{
        color: {t.ink3}; font-size: 11px; font-weight: 650; letter-spacing: 0.6px;
    }}
    #heroName {{ color: {t.ink}; font-size: 27px; font-weight: 700; }}
    #heroSub {{ color: {t.ink2}; font-size: 12px; }}
    #heroNoteOk {{ color: {t.ok}; font-size: 11px; }}
    #heroNoteDim {{ color: {t.ink2}; font-size: 11px; }}
    #heroNoteBad {{ color: {t.danger}; font-size: 11px; }}
    QPushButton#primary {{
        background: {t.accent}; color: {t.on_accent}; border: 0;
        border-radius: {RADIUS_CTRL}px; font-size: 13.5px; font-weight: 650;
    }}
    QPushButton#primary:hover {{ background: {t.accent_deep}; }}
    QPushButton#primary:pressed {{ background: {t.accent_deep}; }}
    QPushButton#primary:disabled {{
        background: {t.card3}; color: {t.ink2}; border: 1px solid {t.line};
    }}
    QPushButton#ghostBtn {{
        background: {t.card}; color: {t.ink2}; border: 1px solid {t.line};
        border-radius: {RADIUS_CTRL}px; font-size: 12.5px; font-weight: 600;
        padding: 0 16px;
    }}
    QPushButton#ghostBtn:hover {{ border-color: {t.accent}; color: {t.accent_deep}; }}
    QPushButton#dangerBtn {{
        background: {t.danger}; color: #FFFFFF; border: 0;
        border-radius: {RADIUS_CTRL}px; font-size: 12px; font-weight: 650; padding: 0 14px;
    }}
    QPushButton#dangerBtn:hover {{ background: {t.danger}; }}
    QPushButton#dangerBtn:pressed {{ background: {t.danger}; }}
    QPushButton#ghostBtn:disabled {{
        color: {t.ink3}; border-color: {t.line_soft}; background: {t.card};
    }}

    /* ---------- 药丸 ---------- */
    #pillRun {{ background: {t.ok_bg}; color: {t.ok}; border-radius: 999px;
        font-size: 10px; font-weight: 650; padding: 3px 9px; }}
    #pillWarn {{ background: {t.warn_bg}; color: {t.warn}; border-radius: 999px;
        font-size: 10px; font-weight: 650; padding: 3px 9px; }}
    QPushButton#statOk, QLabel#statOk {{ background: {t.ok_bg}; color: {t.ok};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 9.5px; font-weight: 700;
        padding: 3px 8px; }}
    QPushButton#statRun, QLabel#statRun {{ background: {t.run_bg}; color: {t.run};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 9.5px; font-weight: 700;
        padding: 3px 8px; }}
    QPushButton#statIdle, QLabel#statIdle {{ background: {t.idle_bg}; color: {t.idle};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 9.5px; font-weight: 700;
        padding: 3px 8px; }}
    QPushButton#statBad, QLabel#statBad {{ background: {t.danger_bg}; color: {t.danger};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 9.5px; font-weight: 700;
        padding: 3px 8px; }}
    QPushButton#statIdle:hover {{ background: {t.idle_bg}; color: {t.ink2}; }}
    QPushButton#statBad:hover {{ background: {t.danger_bg}; color: {t.danger}; }}

    /* ---------- 设备卡 ---------- */
    #devActive {{
        background: {t.accent_soft}; border: 1px solid {t.accent};
        border-radius: {RADIUS_CARD}px;
    }}
    #devOff, #devDead {{
        background: {t.card}; border: 1px solid {t.line};
        border-radius: {RADIUS_CARD}px;
    }}
    #devOff:hover {{ border-color: {t.accent}; }}
    #devName {{ color: {t.ink}; font-size: 13.5px; font-weight: 650; }}
    #devNameDim {{ color: {t.ink2}; font-size: 13.5px; font-weight: 650; }}
    #devSub {{ color: {t.ink3}; font-size: 11px; }}
    #devSubDim {{ color: {t.ink3}; font-size: 11px; opacity: 0.9; }}
    #devTagOn {{ background: {t.accent_soft}; color: {t.accent_deep};
        border-radius: 999px; font-size: 10px; font-weight: 650; padding: 3px 8px; }}
    #devTagOff {{ background: {t.idle_bg}; color: {t.ink3};
        border-radius: 999px; font-size: 10px; font-weight: 650; padding: 3px 8px; }}

    /* ---------- 快速操作行 ---------- */
    #rowsCard {{ background: {t.card}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px; }}
    #secLabel {{ color: {t.ink3}; font-size: 10.5px; font-weight: 700;
        letter-spacing: 0.5px; }}
    QPushButton#quickRow {{ background: transparent; border: 0; text-align: left;
        border-radius: {RADIUS_CARD}px; }}
    QPushButton#quickRow:hover {{ background: {t.card2}; }}
    #rowTitle {{ color: {t.ink}; font-size: 12.5px; font-weight: 550; }}
    #rowSub {{ color: {t.ink3}; font-size: 10.5px; }}
    #rowHint {{ color: {t.ink3}; font-size: 10px; font-family: "{MONO_FAMILIES[0]}"; }}
    #chev {{ color: {t.ink3}; font-size: 13px; }}

    /* ---------- 剪贴板页 ---------- */
    #searchBox {{
        background: {t.card2}; border: 1px solid {t.line}; border-radius: 9px;
    }}
    QLineEdit#searchEdit {{
        background: transparent; border: 0; color: {t.ink}; font-size: 12px;
        selection-background-color: {t.accent}; selection-color: #FFFFFF;
    }}
    QPushButton#iconBtn {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 9px;
        padding: 0;
    }}
    QPushButton#iconBtn:hover {{ border-color: {t.accent}; background: {t.accent_soft}; }}
    QPushButton#iconBtn:pressed {{ background: {t.accent_soft}; }}
    QPushButton#iconBtnOn {{
        background: {t.accent}; border: 1px solid {t.accent}; border-radius: 9px;
    }}
    QPushButton#chip {{
        background: {t.card}; color: {t.ink2}; border: 1px solid {t.line};
        border-radius: 999px; font-size: 11px; font-weight: 550; padding: 0 11px;
    }}
    QPushButton#chip:hover {{ border-color: {t.accent}; color: {t.accent_deep}; }}
    QPushButton#chipOn {{
        background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent};
        border-radius: 999px; font-size: 11px; font-weight: 550; padding: 0 11px;
    }}
    #clipList {{ background: {t.card}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px; }}
    #clipRow {{ background: transparent; border: 0; }}
    #clipSep {{ background: {t.line_soft}; border: 0; }}
    #clipTitle {{ color: {t.ink}; font-size: 12.5px; font-weight: 550; }}
    #clipTitleMono {{ color: {t.ink}; font-size: 11px; font-weight: 500;
        font-family: "{MONO_FAMILIES[0]}"; }}
    #clipMeta {{ color: {t.ink3}; font-size: 10px; }}
    #clipMetaSrc {{ color: {t.ink2}; font-size: 10px; font-weight: 700; }}
    #clipDot {{ color: {t.ink3}; font-size: 10px; }}
    #clipProg {{ background: {t.run_bg}; border-radius: 1px; }}
    #clipProgFill {{ background: {t.run}; border-radius: 1px; }}
    #emptyTitle {{ color: {t.ink2}; font-size: 13.5px; font-weight: 650; }}
    #emptyText {{ color: {t.ink3}; font-size: 11.5px; }}
    QPushButton#actBtn {{
        background: {t.card3}; border: 0; border-radius: 6px; padding: 0;
    }}
    QPushButton#actBtn:hover {{ background: {t.accent}; }}
    QPushButton#actBtn:pressed {{ background: {t.accent_deep}; }}
    QPushButton#actBtnDanger {{
        background: {t.card3}; border: 0; border-radius: 6px; padding: 0;
    }}
    QPushButton#actBtnDanger:hover {{ background: {t.danger}; }}

    /* ---------- 预览窗 ---------- */
    #pvRoot {{ background: {t.card}; border-radius: {PV_RADIUS}px; }}
    #pvHead {{ background: {t.card2}; border-bottom: 1px solid {t.line_soft};
        border-top-left-radius: {PV_RADIUS}px; border-top-right-radius: {PV_RADIUS}px; }}
    #pvTitle {{ color: {t.ink}; font-size: 11.5px; font-weight: 650; }}
    #pvBody {{ background: {t.card}; }}
    #pvText {{ color: {t.ink2}; font-size: 11.5px; }}
    #pvCode {{
        background: {t.card2}; border: 1px solid {t.line_soft}; border-radius: 9px;
        color: {t.ink}; font-size: 10.5px; font-family: "{MONO_FAMILIES[0]}";
    }}
    #pvLink {{
        background: {t.accent_soft}; border-radius: 9px; color: {t.accent_deep};
        font-size: 10.5px; font-family: "{MONO_FAMILIES[0]}";
    }}
    #pvFileCard {{ background: {t.card2}; border: 1px solid {t.line_soft};
        border-radius: 12px; }}
    #pvFileName {{ color: {t.ink}; font-size: 12px; font-weight: 650; }}
    /* 预览窗正文滚动条：白底上 handle 用 ink3，不然 line 那种浅灰等于看不见 */
    #pvScroll {{ background: transparent; border: 0; }}
    #pvScroll QScrollBar:vertical {{ width: 7px; background: transparent; }}
    #pvScroll QScrollBar::handle:vertical {{ background: {t.ink3}; border-radius: 3.5px; }}
    #pvFileDir {{ color: {t.ink3}; font-size: 10px; }}
    #pvFoot {{ background: {t.card2}; border-top: 1px solid {t.line_soft};
        border-bottom-left-radius: {PV_RADIUS}px;
        border-bottom-right-radius: {PV_RADIUS}px; }}
    #pvMeta {{ color: {t.ink3}; font-size: 9.5px; }}
    #pvNote {{ color: {t.ink3}; font-size: 9.5px; }}
    QPushButton#pvBtn {{
        background: {t.card3}; color: {t.ink2}; border: 0; border-radius: 7px; padding: 0;
    }}
    QPushButton#pvBtn:hover {{ background: {t.line}; }}
    QPushButton#pvBtnPri {{ background: {t.accent}; border: 0; border-radius: 7px; padding: 0; }}
    QPushButton#pvBtnPri:hover {{ background: {t.accent_deep}; }}

    /* ---------- 设置页 ---------- */
    #setSec {{ color: {t.ink3}; font-size: 10.5px; font-weight: 700;
        letter-spacing: 0.5px; }}
    #setCard {{ background: {t.card2}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px; }}
    #setRow {{ background: transparent; }}
    #setSep {{ background: {t.line_soft}; border: 0; }}
    #setTitle {{ color: {t.ink}; font-size: 12.5px; font-weight: 550; }}
    #setSub {{ color: {t.ink3}; font-size: 10.5px; }}
    #setVal {{ color: {t.ink3}; font-size: 11.5px; font-family: "{MONO_FAMILIES[0]}"; }}
    #segTrack {{ background: {t.card3}; border: 1px solid {t.line}; border-radius: 8px; }}
    QPushButton#segBtn {{
        background: transparent; border: 0; border-radius: 6px;
        color: {t.ink2}; font-size: 10.5px; padding: 0 10px;
    }}
    QPushButton#segBtn:hover {{ color: {t.ink}; }}
    QPushButton#segBtn:checked {{ background: {t.accent}; color: {t.on_accent}; font-weight: 650; }}
    QPushButton#recordBtn {{
        background: transparent; border: 1px dashed {t.line}; border-radius: 8px;
        color: {t.ink2}; font-size: 11px; padding: 0 10px;
    }}
    QPushButton#recordBtn:hover {{ border-color: {t.accent}; color: {t.accent_deep}; }}

    /* ---------- 数字框（存储上限） ---------- */
    QLineEdit#numEdit {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 8px;
        color: {t.ink}; font-size: 11.5px; font-family: "{MONO_FAMILIES[0]}";
        padding: 0 8px;
    }}
    QLineEdit#numEdit:focus {{ border-color: {t.accent}; }}

    /* ---------- 底栏 ---------- */
    #footer {{ background: {t.card2}; border-top: 1px solid {t.line_soft}; }}
    #footerText {{ color: {t.ink3}; font-size: 10.5px; }}
    """
