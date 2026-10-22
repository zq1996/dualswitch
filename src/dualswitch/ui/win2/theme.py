"""Windows v2 界面主题：视觉令牌 + QSS（仅浅色）+ 字体与键帽解析。

真源是 `docs/UI-WINDOWS2-DESIGN.md` §3（尺寸/颜色/字号/键帽）与
`design/windows2/index.html`（原型）。这里只做「HTML 变量 → Qt QSS / Python 常量」
的翻译，不新增设计。

与 mac2 的差异（设计 §2 差异总表）：
  · 字体族换 Segoe UI Variable Text → Segoe UI → Microsoft YaHei UI
  · 元信息灰度提深一档（#8A93A3 → #7A8494，雅黑 10px 比苹方糊）
  · 窗口圆角 16 → 12；新增自绘标题栏（40px）与 caption 按钮 QSS
  · 键帽是**文字键**（Ctrl / Alt / Shift / Win），单键最小宽 22、Space 34
  · 只做浅色一套（与 mac2 同一决定，不引入深色维护面）

**字号一律用像素**（设计稿是 CSS px）。Qt 的 point 单位在 96dpi 下会被放大到
1.33 倍，整窗字大一号、行高发胖 —— 这里用 `setPixelSize()` 和 QSS 的 `px` 两条路
统一到设计稿的像素尺度。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# 几何（设计 §3.1）
# --------------------------------------------------------------------------- #
WIN_W, WIN_H = 780, 580          # 整窗（含自绘标题栏）
TITLEBAR_H = 40                  # 设计稿 .win-bar 高度
#: 侧栏宽度。2026-10-22 用户拍板「跟 mac2」：188 → 156，并且可以收到 56
#: （§14.2 从「待拍板」变成「已拍板」，Windows 侧栏从此与 mac2 同宽同行为）。
SIDEBAR_W = 156
SIDEBAR_W_COLLAPSED = 56         # 收起态：只留图标（内容宽 40 = 56 − 左右各 8）
SIDEBAR_HANDLE_W, SIDEBAR_HANDLE_H = 15, 46   # 边缘收起把手（方案 A，不占布局）
FOOTER_H = 30
TOOLBAR_H = 40
CONTENT_PAD_H, CONTENT_PAD_V = 18, 20
RADIUS_WIN = 12                  # Windows 取 12（系统窗口约 8，自绘 12 不显钝）
RADIUS_CARD = 12
RADIUS_CTRL = 10
RADIUS_PILL = 6

CAPTION_W, CAPTION_H, CAPTION_R = 46, 32, 6   # 最小化 / 关闭（Win11 规格）

KEY_W, KEY_H, KEY_R = 22, 18, 5   # 文字键帽比符号键帽宽（设计 §3.4）
KEY_WIDE_W = 34                   # Space
KEY_GAP = 3
KEY_FONT_PX = 10

THUMB = 46                       # 剪贴板缩略图
THUMB_R = 10
BADGE = 22                       # 来源系统角标
ROW_H = 68                       # 剪贴板行高
TOOL_ROW_H = 44
SEARCH_H = 34
ICON_BTN = 34
ACT_BTN = 25

# 悬浮预览窗（尺寸/时序与 mac2 完全一致，设计 §2「保持一致的」）
PV_OPEN_DELAY_MS = 450           # 只悬停缩略图才计时
PV_CLOSE_DELAY_MS = 220
PV_IMG_MAX_W, PV_IMG_MAX_H = 412, 330
PV_TXT_MAX_W, PV_TXT_MAX_H = 392, 270
#: 预览窗正文最多渲染多少字。**不是视觉截断**：正常长度完整显示，
#: 只有超过这个量级才切掉，免得几 MB 的日志把 QLabel 排版拖卡。
PV_TEXT_MAX_CHARS = 20000
PV_CODE_MAX_W, PV_CODE_MAX_H = 400, 290
PV_CARD_MAX_W = 380
PV_RADIUS = 13

MENU_W = 240                     # 托盘菜单宽（设计 §4.2）
MENU_ROW_H = 32

FONT_FAMILIES = [
    # 2026-10-21 用户反馈「字看不清、有点模糊」：中文界面以雅黑 UI 打头。
    # 之前 Segoe UI Variable Text 打头时，中文全靠回退到雅黑 —— 96dpi 下 12px 的
    # 回退字形又细又糊（可变字体的 weight 轴在像素字号上也占不到便宜）。
    "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Variable Text", "Segoe UI",
    # emoji 不在上面几个字体里：显式列出来，别指望 Qt 的隐式回退
    "Segoe UI Emoji", "sans-serif",
]
APP_FONT_PX = 13                 # 全局默认字号（设计稿 12.5px，Windows 上浮到 13 更清楚）
BODY_FS = 12.5
META_FS = 11
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "monospace"]

# 设计稿字号档（§3.3）
FS_PAGE = 27                     # hero 平台名
FS_ROW = 13                      # 行题
FS_META = 11                     # 元信息
FS_SECTION = 11                  # 分组小标题
FS_SMALL = 12


# --------------------------------------------------------------------------- #
# 视觉令牌（§3.2，浅色唯一一套）
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
    caption_hover: str
    caption_close: str


LIGHT = Tokens(
    name="light",
    accent="#4E87FF", accent_deep="#2E63E8", accent_soft="rgba(78,135,255,33)",
    ink="#14181F", ink2="#4B5566", ink3="#7A8494",     # ← Windows 提深一档
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
    caption_hover="#F0F0F0", caption_close="#C42B1C",
)

_CURRENT: Tokens = LIGHT


def tokens() -> Tokens:
    """当前生效的令牌。Windows2 只有浅色一套（§2「主题」）。"""
    return _CURRENT


def set_appearance(mode: str) -> Tokens:  # noqa: ARG001 - 保留签名，两端调用点一致
    """Windows2 固定浅色；`mode` 只为与 mac2 的调用点保持同名。"""
    global _CURRENT
    _CURRENT = LIGHT
    return _CURRENT


def resolve_appearance(mode: str) -> Tokens:  # noqa: ARG001
    return LIGHT


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


#: 剪贴板类型瓷砖配色（实色底 + 白字形；file/unknown 是浅灰底灰字形）
TYPE_TILES = {
    "light": {
        "text": ("#657DFF", "#FFFFFF"),
        "code": ("#7A5AF8", "#FFFFFF"),
        "link": ("#6B7BD3", "#FFFFFF"),
        "image": ("#4E87FF", "#FFFFFF"),
        "file": ("#E9ECF3", "#7A8494"),
        "unknown": ("#E9ECF3", "#7A8494"),
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
    f.setPixelSize(max(1, int(px + 0.5)))   # 12.5→13、10.5→11：向上取整，别把 12.5 压成 12
    if weight is not None:
        f.setWeight(QFont.Weight(int(weight)))
    return f


# --------------------------------------------------------------------------- #
# 资源
# --------------------------------------------------------------------------- #
def asset(name: str) -> Path:
    """产品资源：ui/assets/（两端共用）→ 仓库 design/icon/ → 打包 _MEIPASS。"""
    here = Path(__file__).resolve().parent          # ui/win2
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


#: 应用图标：Windows 只有 .ico（多档 16…256，exe / 任务栏 / 托盘共用一份）。
#: 新图标由 `design/icon/build_icon.py` 出图并自动同步到 `ui/assets/`，
#: 所以这里**必须**走 `asset()` 而不是直接写 design/ 的路径。
APP_ICON_NAMES = ("screen-switch.ico",)


def app_icon():
    """造应用图标 QIcon。读不到就返回空 QIcon（不抛，托盘/任务栏退回系统默认）。"""
    from PySide6.QtGui import QIcon

    for name in APP_ICON_NAMES:
        try:
            path = asset(name)
        except FileNotFoundError:
            continue
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return QIcon()


# --------------------------------------------------------------------------- #
# 键帽解析：Hotkey 描述串 → 每键一个键帽（§3.4）
# --------------------------------------------------------------------------- #
#: 描述词 → 键帽文字。**Windows 键帽不印符号**，一律写 Ctrl / Alt / Shift / Win。
GLYPHS = {
    "command": "Win", "cmd": "Win", "win": "Win", "meta": "Win", "super": "Win",
    "control": "Ctrl", "ctrl": "Ctrl",
    "option": "Alt", "alt": "Alt", "opt": "Alt",
    "right option": "Alt", "right alt": "Alt", "ralt": "Alt",
    "shift": "Shift",
    "space": "Space", "spacebar": "Space",
    "enter": "Enter", "return": "Enter", "tab": "Tab", "escape": "Esc",
    "backspace": "Backspace", "delete": "Del", "del": "Del",
    "insert": "Ins", "ins": "Ins",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "pageup": "PgUp", "pagedown": "PgDn", "home": "Home", "end": "End",
}

WIDE_KEYS = {"space", "enter", "return", "tab", "backspace", "delete", "escape",
             "pageup", "pagedown", "home", "end", "insert"}


def keycap_labels(describe: str) -> list[str]:
    """`Alt+Space` → [`Alt`, `Space`]；`Ctrl+Shift+V` → [`Ctrl`,`Shift`,`V`]。

    只按 `+` 切分（`Right Alt` 本身带空格），空片段丢掉。
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
    #root {{
        background: {t.card}; border: 1px solid rgba(16,24,40,0.07);
        border-radius: {RADIUS_WIN}px;
    }}
    #pageStack {{ background: {t.card}; border: 0; }}

    /* ---------- 自绘标题栏（§3.1） ---------- */
    #titlebar {{ background: {t.card}; border: 0;
        border-top-left-radius: {RADIUS_WIN}px; border-top-right-radius: {RADIUS_WIN}px; }}
    #tbTitle {{ color: {t.ink}; font-size: 12px; font-weight: 600; }}
    QPushButton#capBtn {{ background: transparent; border: 0; border-radius: {CAPTION_R}px; }}
    QPushButton#capBtn:hover {{ background: {t.caption_hover}; }}
    QPushButton#capClose {{ background: transparent; border: 0; border-radius: {CAPTION_R}px; }}
    QPushButton#capClose:hover {{ background: {t.caption_close}; }}

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
    #sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.line_soft};
        border-bottom-left-radius: {RADIUS_WIN}px; }}
    #navSec {{
        color: {t.ink3}; font-size: 10px; font-weight: 700;
        letter-spacing: 0.6px; padding: 8px 10px 5px 10px;
    }}
    QPushButton#nav {{
        background: transparent; border: 0; border-radius: 8px;
        color: {t.ink2}; font-size: 13px; font-weight: 550;
        text-align: left; padding: 0 10px;
    }}
    QPushButton#nav:hover {{ background: {t.card3}; color: {t.ink}; }}
    QPushButton#nav:checked {{ background: {t.accent}; color: {t.on_accent}; font-weight: 620; }}
    #navFoot {{ border-top: 1px solid {t.line_soft}; }}
    #navFootText {{ color: {t.ink3}; font-size: 11px; }}

    /* ---------- 键帽（文字键） ---------- */
    #keycap {{
        background: {t.key_bg}; border: 1px solid {t.key_border}; border-radius: {KEY_R}px;
        color: {t.key_fg}; font-size: 10px; font-weight: 650;
    }}
    #keycapOnAccent {{
        background: rgba(255,255,255,0.20); border: 1px solid rgba(255,255,255,0.28);
        border-radius: {KEY_R}px; color: #FFFFFF; font-size: 10px; font-weight: 650;
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
        border-radius: {RADIUS_CTRL}px; font-size: 14px; font-weight: 650;
    }}
    QPushButton#primary:hover {{ background: {t.accent_deep}; }}
    QPushButton#primary:pressed {{ background: {t.accent_deep}; }}
    QPushButton#primary:disabled {{
        background: {t.card3}; color: {t.ink2}; border: 1px solid {t.line};
    }}
    QPushButton#ghostBtn {{
        background: {t.card}; color: {t.ink2}; border: 1px solid {t.line};
        border-radius: {RADIUS_CTRL}px; font-size: 13px; font-weight: 600;
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
        color: {t.ink2}; border-color: {t.line}; background: {t.card3};
    }}

    /* ---------- 药丸 ---------- */
    /* 「运行中 / 待确认」药丸是自绘控件（widgets.Pill）：19px 高的控件上
       Qt QSS 的 border-radius 会被整条丢掉，详见 widgets.Pill 的注释。 */
    QPushButton#statOk, QLabel#statOk {{ background: {t.ok_bg}; color: {t.ok};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 10px; font-weight: 700;
        padding: 3px 8px; }}
    QPushButton#statRun, QLabel#statRun {{ background: {t.run_bg}; color: {t.run};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 10px; font-weight: 700;
        padding: 3px 8px; }}
    QPushButton#statIdle, QLabel#statIdle {{ background: {t.idle_bg}; color: {t.idle};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 10px; font-weight: 700;
        padding: 3px 8px; }}
    QPushButton#statBad, QLabel#statBad {{ background: {t.danger_bg}; color: {t.danger};
        border: 0; border-radius: {RADIUS_PILL}px; font-size: 10px; font-weight: 700;
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
    #devName {{ color: {t.ink}; font-size: 14px; font-weight: 650; }}
    #devNameDim {{ color: {t.ink2}; font-size: 14px; font-weight: 650; }}
    #devSub {{ color: {t.ink3}; font-size: 11px; }}
    #devSubDim {{ color: {t.ink3}; font-size: 11px; opacity: 0.9; }}
    #devTagOn {{ background: {t.accent_soft}; color: {t.accent_deep};
        border-radius: 10px; font-size: 11px; font-weight: 650; padding: 3px 8px; }}
    #devTagOff {{ background: {t.idle_bg}; color: {t.ink3};
        border-radius: 10px; font-size: 11px; font-weight: 650; padding: 3px 8px; }}

    /* ---------- 快速操作行 ---------- */
    #rowsCard {{ background: {t.card}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px; }}
    #secLabel {{ color: {t.ink3}; font-size: 11px; font-weight: 700;
        letter-spacing: 0.5px; }}
    QPushButton#quickRow {{ background: transparent; border: 0; text-align: left;
        border-radius: {RADIUS_CARD}px; }}
    QPushButton#quickRow:hover {{ background: {t.card2}; }}
    #rowTitle {{ color: {t.ink}; font-size: 13px; font-weight: 550; }}
    #rowSub {{ color: {t.ink3}; font-size: 11px; }}
    #rowHint {{ color: {t.ink3}; font-size: 11px; font-family: "{MONO_FAMILIES[0]}"; }}
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
        border-radius: 10px; font-size: 11px; font-weight: 550; padding: 0 11px;
    }}
    QPushButton#chip:hover {{ border-color: {t.accent}; color: {t.accent_deep}; }}
    QPushButton#chipOn {{
        background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent};
        border-radius: 10px; font-size: 11px; font-weight: 550; padding: 0 11px;
    }}
    #clipList {{ background: {t.card}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px; }}
    #clipRow {{ background: transparent; border: 0; }}
    #clipSep {{ background: {t.line_soft}; border: 0; }}
    #clipTitle {{ color: {t.ink}; font-size: 13px; font-weight: 550; }}
    #clipTitleMono {{ color: {t.ink}; font-size: 11px; font-weight: 500;
        font-family: "{MONO_FAMILIES[0]}"; }}
    #clipMeta {{ color: {t.ink3}; font-size: 11px; }}
    #clipMetaSrc {{ color: {t.ink2}; font-size: 11px; font-weight: 700; }}
    #clipDot {{ color: {t.ink3}; font-size: 11px; }}
    #clipProg {{ background: {t.run_bg}; border-radius: 1px; }}
    #clipProgFill {{ background: {t.run}; border-radius: 1px; }}
    #emptyTitle {{ color: {t.ink2}; font-size: 14px; font-weight: 650; }}
    #emptyText {{ color: {t.ink3}; font-size: 12px; }}
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
    #pvTitle {{ color: {t.ink}; font-size: 12px; font-weight: 650; }}
    #pvBody {{ background: {t.card}; }}
    #pvText {{ color: {t.ink2}; font-size: 12px; }}
    #pvCode {{
        background: {t.card2}; border: 1px solid {t.line_soft}; border-radius: 9px;
        color: {t.ink}; font-size: 11px; font-family: "{MONO_FAMILIES[0]}";
    }}
    #pvLink {{
        background: {t.accent_soft}; border-radius: 9px; color: {t.accent_deep};
        font-size: 11px; font-family: "{MONO_FAMILIES[0]}";
    }}
    #pvFileCard {{ background: {t.card2}; border: 1px solid {t.line_soft};
        border-radius: 12px; }}
    #pvFileName {{ color: {t.ink}; font-size: 12px; font-weight: 650; }}
    #pvScroll {{ background: transparent; border: 0; }}
    #pvScroll QScrollBar:vertical {{ width: 7px; background: transparent; }}
    #pvScroll QScrollBar::handle:vertical {{ background: {t.ink3}; border-radius: 3.5px; }}
    #pvFileDir {{ color: {t.ink3}; font-size: 10px; }}
    #pvFoot {{ background: {t.card2}; border-top: 1px solid {t.line_soft};
        border-bottom-left-radius: {PV_RADIUS}px;
        border-bottom-right-radius: {PV_RADIUS}px; }}
    #pvMeta {{ color: {t.ink3}; font-size: 10px; }}
    #pvNote {{ color: {t.ink3}; font-size: 10px; }}
    QPushButton#pvBtn {{
        background: {t.card3}; color: {t.ink2}; border: 0; border-radius: 7px; padding: 0;
    }}
    QPushButton#pvBtn:hover {{ background: {t.line}; }}
    QPushButton#pvBtnPri {{ background: {t.accent}; border: 0; border-radius: 7px; padding: 0; }}
    QPushButton#pvBtnPri:hover {{ background: {t.accent_deep}; }}

    /* ---------- 设置页 ---------- */
    #setSec {{ color: {t.ink3}; font-size: 11px; font-weight: 700;
        letter-spacing: 0.5px; }}
    #setCard {{ background: {t.card2}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px; }}
    #setRow {{ background: transparent; }}
    #setSep {{ background: {t.line_soft}; border: 0; }}
    #setTitle {{ color: {t.ink}; font-size: 13px; font-weight: 550; }}
    #setSub {{ color: {t.ink3}; font-size: 11px; }}
    #setVal {{ color: {t.ink3}; font-size: 12px; font-family: "{MONO_FAMILIES[0]}"; }}
    #segTrack {{ background: {t.card3}; border: 1px solid {t.line}; border-radius: 8px; }}
    QPushButton#segBtn {{
        background: transparent; border: 0; border-radius: 6px;
        color: {t.ink2}; font-size: 11px; padding: 0 10px;
    }}
    QPushButton#segBtn:hover {{ color: {t.ink}; }}
    QPushButton#segBtn:checked {{ background: {t.accent}; color: {t.on_accent}; font-weight: 650; }}
    QPushButton#recordBtn {{
        background: transparent; border: 1px dashed {t.line}; border-radius: 8px;
        color: {t.ink2}; font-size: 11px; padding: 0 10px;
    }}
    QPushButton#recordBtn:hover {{ border-color: {t.accent}; color: {t.accent_deep}; }}

    /* ---------- 数字框 / 单行输入 ---------- */
    QLineEdit#numEdit, QLineEdit#textEdit {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 8px;
        color: {t.ink}; font-size: 12px; font-family: "{MONO_FAMILIES[0]}";
        padding: 0 8px;
    }}
    QLineEdit#numEdit:focus, QLineEdit#textEdit:focus {{ border-color: {t.accent}; }}

    /* ---------- 底栏 ---------- */
    #footer {{ background: {t.card2}; border-top: 1px solid {t.line_soft};
        border-bottom-left-radius: {RADIUS_WIN}px;
        border-bottom-right-radius: {RADIUS_WIN}px; }}
    #footerText {{ color: {t.ink3}; font-size: 11px; }}

    /* ---------- 窗口内确认遮罩（§8） ---------- */
    #confirmMask {{ background: rgba(16,24,40,0.28); }}
    #confirmCard {{ background: {t.card}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px; }}
    #confirmTitle {{ color: {t.ink}; font-size: 14px; font-weight: 650; }}
    #confirmSub {{ color: {t.ink3}; font-size: 12px; }}

    /* ---------- 托盘菜单（QMenu + QWidgetAction 自绘行，§4.2） ---------- */
    QMenu#trayMenu {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 10px;
        padding: 6px;
    }}
    #menuRow {{ background: transparent; border-radius: 6px; }}
    #menuRowHover {{ background: {t.accent}; border-radius: 6px; }}

    /* ---------- 配对电脑页（INTERACTIONS §12，2026-10-22 跟 mac2） ----------
       状态胶囊 22 高 → 圆角写 10（**不能写 11 或 999**：Qt 遇到「圆角 > 半高」
       会整条丢掉，见 §3.3.1 与 tests/test_ui_win2.py::test_qss_has_no_oversized_radius）。 */
    #pairPillOk, #pairPillIdle, #pairPillBad {{
        font-size: 10px; font-weight: 700; padding: 4px 9px;
        border-radius: 10px;
    }}
    #pairPillOk {{ background: {t.ok_bg}; color: {t.ok}; }}
    #pairPillIdle {{ background: {t.idle_bg}; color: {t.idle}; }}
    #pairPillBad {{ background: {t.danger_bg}; color: {t.danger}; }}
    #pairCard {{
        background: {t.card2}; border: 1px solid {t.line_soft};
        border-radius: {RADIUS_CARD}px;
    }}
    #pairCardTitle {{ color: {t.ink}; font-size: 13px; font-weight: 650; }}
    #pairHint {{ color: {t.ink3}; font-size: 11px; }}
    #pairHelp {{ color: {t.ink3}; font-size: 11px; }}
    #pairSteps {{ color: {t.ink2}; font-size: 11.5px; }}
    QPushButton#linkBtn {{
        background: transparent; border: 0; text-align: left;
        color: {t.accent_deep}; font-size: 11.5px; font-weight: 600; padding: 5px 0;
    }}
    QPushButton#linkBtn:hover {{ text-decoration: underline; }}
    QFrame#peerRow {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 11px;
    }}
    QFrame#peerRow:hover {{ border-color: {t.accent}; }}
    #peerName {{ color: {t.ink}; font-size: 13px; font-weight: 650; }}
    #peerTag {{
        background: {t.card3}; color: {t.ink3}; font-size: 9.5px; font-weight: 700;
        border-radius: 8px; padding: 1.5px 6px;
    }}
    #peerMeta {{ color: {t.ink3}; font-size: 10.5px; font-family: "{MONO_FAMILIES[0]}"; }}
    QPushButton#peerBtn {{
        background: {t.accent}; color: {t.on_accent}; border: 0;
        border-radius: 7px; font-size: 11.5px; font-weight: 650; padding: 0 12px;
    }}
    QPushButton#peerBtn:hover {{ background: {t.accent_deep}; }}
    QPushButton#peerBtnGhost {{
        background: {t.card}; color: {t.ink2}; border: 1px solid {t.line};
        border-radius: 7px; font-size: 11.5px; font-weight: 650; padding: 0 12px;
    }}
    QPushButton#peerBtnGhost:hover {{ border-color: {t.accent}; color: {t.accent_deep}; }}
    QLineEdit#pairIp {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 8px;
        color: {t.ink}; font-size: 12px; padding: 0 10px;
    }}
    QLineEdit#pairIp:focus {{ border-color: {t.accent}; }}
    #pinBlock {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 11px;
        color: {t.ink}; font-family: "{MONO_FAMILIES[0]}";
        font-size: 25px; font-weight: 700;
    }}
    QLineEdit#pinInput {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 10px;
        color: {t.ink}; font-family: "{MONO_FAMILIES[0]}";
        font-size: 22px; font-weight: 700;
    }}
    QLineEdit#pinInput:focus {{ border: 2px solid {t.accent}; background: {t.accent_soft}; }}
    QLineEdit#pinInput:disabled {{ background: {t.card3}; color: {t.ink3}; }}
    #pinCount {{ color: {t.warn}; font-size: 10px; font-weight: 600; }}
    #pairError {{ background: {t.danger_bg}; border-radius: 8px; }}
    #pairErrorText {{ color: {t.danger}; font-size: 11px; }}
    #pairRingOk {{ background: {t.ok_bg}; border-radius: 28px; color: {t.ok}; }}
    #pairRingBad {{ background: {t.danger_bg}; border-radius: 28px; color: {t.danger}; }}
    #pairSelected {{
        background: {t.card}; border: 1px solid {t.line}; border-radius: 10px;
    }}
    #pairFacts {{ background: {t.line_soft}; border: 1px solid {t.line_soft};
        border-radius: 9px; }}
    #pairFact {{ background: {t.card}; }}
    #pairFactK {{ color: {t.ink3}; font-size: 9.5px; }}
    #pairFactV {{
        color: {t.ink2}; font-size: 11px;
        font-family: "{MONO_FAMILIES[0]}";
    }}
    #pairNote {{ background: {t.card3}; border-radius: 8px; }}
    #pairNoteText {{ color: {t.ink3}; font-size: 10.5px; }}
    #pairCenterTitle {{ color: {t.ink}; font-size: 14px; font-weight: 680; }}
    #pairCenterSub {{ color: {t.ink3}; font-size: 11.5px; }}
    """
