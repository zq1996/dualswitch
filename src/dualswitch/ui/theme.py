"""界面主题：组装 token 与 QSS。

配色 / 字体 / 字号来自当前平台的外观模块（`chrome/win.py` 或 `chrome/mac.py`），
这里只负责把它们拼成 QSS 与常量，**不写任何平台分支**。

改 Windows 的蓝、mac 的系统蓝 → 改各自的 chrome 模块，不要改这个文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .chrome import current as _chrome

_C = _chrome()
_P = _C.PALETTE

# --------------------------------------------------------------------------- #
# token
# --------------------------------------------------------------------------- #
INK = "#11151C"
INK2 = "#4B5566"
INK3 = "#818B9C"
LINE = "#E5E9F1"
LINE_SOFT = "#EEF1F6"

ACCENT = _P.accent
ACCENT_DEEP = _P.accent_deep
ACCENT_SOFT = _P.accent_soft

PRIMARY_TOP = _P.primary_top
PRIMARY_BOTTOM = _P.primary_bottom
PRIMARY_TOP_HOVER = _P.primary_top_hover
PRIMARY_BOTTOM_HOVER = _P.primary_bottom_hover
PRIMARY_TOP_DOWN = _P.primary_top_down
PRIMARY_BOTTOM_DOWN = _P.primary_bottom_down

GREEN = "#0E9F5B"          # 亮灯状态文字
LED_CORE = "#34C759"
LED_DEEP = "#0F8A3D"

DANGER = "#D33B3B"
WARN = "#B4690E"
OK = "#0F9D58"

WIN_W, WIN_H = 440, 600
SETTINGS_H = 484          # 设置页内容比主页面少，矮一截避免中部大片空白
RADIUS_WIN = 14
RADIUS_CARD = 12
RADIUS_CTRL = 10

# 平台外观（窗口外框 / 标题栏 / 字号 / 字体）
NATIVE_CHROME = _C.NATIVE_CHROME
# 文本记录缩略图用的「本机系统图标」：Mac → apple，Windows → windows
SYSTEM_LOGO = "apple" if _C.NAME == "macos" else "windows"
TITLEBAR_H = 0 if NATIVE_CHROME else 44
BODY_TOP = _C.BODY_TOP
FONT_FAMILIES = list(_P.font_families)
BODY_FS = _P.body_font_size
APP_FONT_POINT = _P.app_font_point
CLICK_HINT = _C.CLICK_HINT


def pretty_hotkey(describe: str) -> str:
    """把 `Hotkey.describe()` 的文案变成界面上的写法。

    Windows  `Alt+Space`         → `Alt + Space`
    macOS    `Right Option+Space` → `⌥ Space`（符号表来自 chrome.PALETTE.key_glyphs，
                                     完整文案留给 tooltip / 状态栏）
    """
    if not describe or describe == "未启用":
        return describe
    # 只按 "+" 切分，不要先吃掉空格：`Right Option` / `Right Alt` 这类修饰键本身就带空格
    parts = [p.strip() for p in describe.split("+") if p.strip()]
    if not parts:
        return describe
    glyphs = _P.key_glyphs
    if glyphs:
        out = []
        for part in parts:
            glyph = glyphs.get(part) or glyphs.get(part.replace(" ", ""))
            out.append(glyph or part)
        return " ".join(out)
    return " + ".join(parts)


def asset(name: str) -> Path:
    """资源路径：包内 assets/ → 仓库 design/icon/ → 打包后的 _MEIPASS。"""
    here = Path(__file__).resolve().parent
    candidates = [here / "assets" / name]
    # 开发态：从包目录向上找 design/icon（src/dualswitch/ui → 仓库根）
    for parent in here.parents[:5]:
        candidates.append(parent / "design" / "icon" / name)
        candidates.append(parent / "design" / "icon" / "png" / name)
    candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "assets" / name)
    candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "dualswitch" / "ui" / "assets" / name)
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError(f"找不到资源 {name}")


def app_icon() -> Path:
    """应用图标：候选名由平台提供（macOS 优先 .icns，Windows 用 .ico）。"""
    for name in _C.APP_ICON_NAMES:
        try:
            return asset(name)
        except FileNotFoundError:
            continue
    raise FileNotFoundError("找不到应用图标 screen-switch.icns / .ico")


# --------------------------------------------------------------------------- #
# QSS
# --------------------------------------------------------------------------- #
def qss() -> str:
    return f"""
    QWidget {{
        color: {INK};
        font-size: {BODY_FS}px;
    }}
    #root {{
        {_C.ROOT_QSS}
    }}
    #body {{ background: transparent; }}

    /* ---------- 标题栏（仅 Windows 自绘；macOS 用系统标题栏） ---------- */
    #titlebar {{
        background: #F5F7FB;
        border: 0;
        border-bottom: 1px solid {LINE};
        border-top-left-radius: {RADIUS_WIN - 1}px;
        border-top-right-radius: {RADIUS_WIN - 1}px;
    }}
    #tbTitle {{ color: {INK2}; font-size: {BODY_FS}px; font-weight: 600; }}

    /* ---------- 状态卡 ---------- */
    #hero {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #EDF4FF, stop:0.52 #F7FAFF, stop:1 #EFFAF7);
        border: 1px solid #DCE7FF;
        border-radius: {RADIUS_CARD}px;
    }}
    #eyebrow {{ color: {INK3}; font-size: 11px; font-weight: 600; }}
    #heroTitle {{ color: {INK}; font-size: 31px; font-weight: 600; }}
    #heroSub {{ color: {INK2}; font-size: {BODY_FS}px; }}
    #heroFoot {{ color: {INK2}; font-size: 11.5px; }}
    #heroFootOk {{ color: {GREEN}; font-size: 11.5px; }}
    #heroFootErr {{ color: {DANGER}; font-size: 11.5px; }}
    #logoPlate {{
        background: #FFFFFF; border: 1px solid #DCE7FF; border-radius: 12px;
    }}

    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {PRIMARY_TOP}, stop:1 {PRIMARY_BOTTOM});
        color: #FFFFFF; border: 0; border-radius: {RADIUS_CTRL}px;
        font-size: 14.5px; font-weight: 600; text-align: left; padding-left: 14px;
    }}
    QPushButton#primary:hover {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {PRIMARY_TOP_HOVER}, stop:1 {PRIMARY_BOTTOM_HOVER});
    }}
    QPushButton#primary:pressed {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {PRIMARY_TOP_DOWN}, stop:1 {PRIMARY_BOTTOM_DOWN});
    }}
    QPushButton#primary:disabled {{
        background: #B7C6E8; color: #FFFFFF;
    }}
    /* 键帽：纯半透明底，不加描边 */
    #kbd {{
        background: rgba(255,255,255,0.17);
        border: 0; border-radius: 5px;
        color: #FFFFFF; font-size: 11px; font-weight: 600;
        padding: 3px 8px;
    }}

    /* 状态药丸：浅绿底无描边 */
    #pill {{ background: #E8F7EE; border: 0; border-radius: 11px; }}
    #pillDot {{ background: #1FA85C; border-radius: 3px; }}
    #pillText {{ color: #0B7A44; font-size: 11px; font-weight: 600; background: transparent; }}
    #spinnerText {{ color: #FFFFFF; font-size: 14.5px; font-weight: 600; }}

    /* ---------- 设备卡 ---------- */
    #devLit {{
        background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,
                    stop:0 #F1FBF4, stop:0.58 #FFFFFF, stop:1 #FFFFFF);
        border: 1px solid #C3E7CF;
        border-radius: 12px;
    }}
    #devDim {{
        background: #F7F8FB;
        border: 1px solid {LINE};
        border-radius: 12px;
    }}
    #devName {{ color: {INK}; font-size: 13px; font-weight: 600; }}
    #devNameDim {{ color: #7C8698; font-size: 13px; font-weight: 600; }}
    #devPort {{ color: {INK3}; font-size: 11px; }}
    #devStateOn {{ color: {GREEN}; font-size: 10.5px; font-weight: 600; }}
    #devStateOff {{ color: #A0A9B8; font-size: 10.5px; }}

    /* ---------- 列表 ---------- */
    #listWrap {{ border: 1px solid {LINE}; border-radius: 12px; background: #FFFFFF; }}
    QPushButton#row {{
        background: #FFFFFF; border: 0; border-bottom: 1px solid {LINE_SOFT};
        color: {INK}; font-size: {BODY_FS}px; text-align: left; padding-left: 0;
    }}
    QPushButton#row:hover {{ background: #F7F9FD; }}
    QPushButton#rowLast {{
        background: #FFFFFF; border: 0; color: {INK}; font-size: {BODY_FS}px; text-align: left;
    }}
    QPushButton#rowLast:hover {{ background: #F7F9FD; }}
    #rowVal {{ color: {INK3}; font-size: 11.5px; }}
    #chev {{ color: #B6BFCD; font-size: 12px; }}

    /* ---------- 状态栏 ---------- */
    #statusbar {{
        background: #F7F9FC; border-top: 1px solid {LINE_SOFT};
        {_C.STATUSBAR_QSS}
    }}
    #statusText {{ color: {INK3}; font-size: 11.5px; }}

    /* ---------- 设置页 ---------- */
    #tabs {{ background: #F1F4FA; border-radius: 12px; }}
    QPushButton#tab {{
        background: transparent; border: 0; border-radius: 8px;
        color: {INK2}; font-size: {BODY_FS}px; font-weight: 500;
        padding: 0 6px;
    }}
    QPushButton#tab:hover:!checked {{
        background: rgba(255,255,255,0.55); color: {INK};
    }}
    QPushButton#tab:checked {{
        background: #FFFFFF; color: {INK}; font-weight: 600;
    }}
    #groupTitle {{ color: {INK3}; font-size: 10.5px; font-weight: 700; }}
    #pageHint {{ color: {INK3}; font-size: 11px; padding: 0 2px 2px 2px; }}
    #gTitle {{ color: {INK}; font-size: {BODY_FS}px; font-weight: 600; }}
    #gDesc {{ color: {INK3}; font-size: 11px; }}
    #group {{ background: {LINE_SOFT}; border: 1px solid {LINE}; border-radius: 12px; }}
    #gRow {{ background: #FFFFFF; border-bottom: 1px solid {LINE_SOFT}; }}
    #gRowLast {{ background: #FFFFFF; }}
    QPushButton#ghost {{
        background: #FFFFFF; border: 1px solid #D5DCE8; border-radius: 8px;
        color: {INK2}; font-size: {BODY_FS}px; padding: 0 14px; min-width: 52px;
    }}
    QPushButton#ghost:hover {{ background: #F7F9FD; border-color: #C3CCDB; }}
    QPushButton#ghost:pressed {{ background: #EEF2F8; }}
    QPushButton#solid {{
        background: {ACCENT}; border: 0; border-radius: 8px;
        color: #FFFFFF; font-size: {BODY_FS}px; font-weight: 600;
        padding: 0 22px; min-width: 44px;
    }}
    QPushButton#solid:hover {{ background: {ACCENT_DEEP}; }}
    QPushButton#solid:pressed {{ background: #1A44B8; }}

    /* 底部操作条：和状态栏同色，把按钮收进一条区域里 */
    #footer {{
        background: #F7F9FC;
        border-top: 1px solid {LINE};
    }}

    /* ---------- 滑块 / 下拉（原生样式太糙，统一收成细轨道 + 圆柄） ---------- */
    QSlider::groove:horizontal {{
        height: 4px; border-radius: 2px; background: #E3E8F0;
    }}
    QSlider::sub-page:horizontal {{
        height: 4px; border-radius: 2px; background: {ACCENT};
    }}
    QSlider::handle:horizontal {{
        width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
        background: #FFFFFF; border: 1.5px solid {ACCENT};
    }}
    QSlider::handle:horizontal:hover {{ background: {ACCENT_SOFT}; }}
    QSlider::handle:horizontal:pressed {{ background: {ACCENT}; }}

    /* 下拉框本体由 widgets.Combo 自绘（QSS 画不出三角箭头），这里只管弹层 */
    QComboBox QAbstractItemView {{
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 8px;
        color: {INK}; font-size: {BODY_FS}px; padding: 4px; outline: 0;
        selection-background-color: {ACCENT_SOFT}; selection-color: {INK};
    }}
    QLineEdit#num {{
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 8px;
        color: {INK}; font-size: {BODY_FS}px; font-weight: 600; padding: 4px 6px;
    }}
    QLineEdit#num:focus {{ border-color: {ACCENT}; }}
    QLineEdit#text {{
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 8px;
        color: {INK}; font-size: 12px; padding: 5px 8px;
    }}
    QLineEdit#text:focus {{ border-color: {ACCENT}; }}

    /* ---------- 托盘 / 菜单栏菜单 ---------- */
    QMenu {{
        background: #FFFFFF; border: 1px solid rgba(16,24,40,0.10);
        border-radius: 8px; padding: 5px;
    }}
    QMenu::item {{
        padding: 6px 26px 6px 12px; border-radius: 6px; font-size: {BODY_FS}px; color: {INK};
    }}
    QMenu::item:selected {{ background: #F2F5FA; }}
    QMenu::item:disabled {{ color: #A7B0BE; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 6px; }}

    /* ---------- 提示条 ---------- */
    #bannerWarn {{
        background: #FFF8EC; border: 1px solid #F3E2C4; border-radius: 8px;
        color: #7A4A05; font-size: 11.5px;
    }}
    #bannerErr {{
        background: #FDF1F1; border: 1px solid #F4D6D6; border-radius: 8px;
        color: #8C2222; font-size: 11.5px;
    }}
    /* 横幅里的动作（去授权 / 重试）：白底细边，压在浅色横幅上不抢眼但能点 */
    QPushButton#bannerAction {{
        background: rgba(255,255,255,0.92); border: 1px solid rgba(16,24,40,0.16);
        border-radius: 8px; color: {INK}; font-size: 11.5px; font-weight: 600;
        padding: 3px 10px;
    }}
    QPushButton#bannerAction:hover {{ background: #FFFFFF; border-color: {ACCENT}; color: {ACCENT}; }}
    QPushButton#bannerAction:pressed {{ background: {ACCENT_SOFT}; }}

    /* ---------- 剪贴板页（docs/UI-DESIGN-CLIPBOARD.md） ---------- */
    #clipHead {{ background: #FFFFFF; border-bottom: 1px solid {LINE_SOFT}; }}
    QPushButton#clipBack {{
        background: transparent; border: 0; color: {INK2};
        font-size: 12px; text-align: left; padding: 4px 7px 4px 3px; border-radius: 8px;
    }}
    QPushButton#clipBack:hover {{ background: #F3F6FB; color: {ACCENT_DEEP}; }}
    #clipTitle {{ color: {INK}; font-size: 13.5px; font-weight: 600; }}
    #clipConn {{ color: {INK3}; font-size: 11px; }}

    /* 行：选中靠左侧主色条 + 浅蓝底，不加边框（会和 hover 混） */
    #clipList {{ background: #FFFFFF; }}
    #citem {{
        background: #FFFFFF; border: 0; border-bottom: 1px solid {LINE_SOFT};
        border-left: 2.5px solid transparent;
    }}
    #citem:hover {{ background: #FAFBFE; }}
    #citemSel {{
        background: #F3F7FF; border: 0; border-bottom: 1px solid {LINE_SOFT};
        border-left: 2.5px solid {ACCENT};
    }}
    #ctitle {{ color: {INK}; font-size: 12.5px; }}
    #ctitleMono {{ color: {INK}; font-size: 11.8px; }}
    #cmeta {{ color: {INK3}; font-size: 10.5px; }}
    #cthumb {{ background: #F4F6FA; border: 1px solid {LINE}; border-radius: 8px; }}

    #stOk {{ color: #0E7C4A; background: #EDF8F1; font-size: 10px; font-weight: 600;
             border-radius: 5px; padding: 2px 6px; }}
    #stPend {{ color: #1E4FD8; background: #EEF4FF; font-size: 10px; font-weight: 600;
               border-radius: 5px; padding: 2px 6px; }}
    #stFail {{ color: #A82B2B; background: #FDEEEE; font-size: 10px; font-weight: 600;
               border-radius: 5px; padding: 2px 6px; }}
    #stLocal {{ color: #7C8698; background: #F3F5F9; font-size: 10px; font-weight: 600;
                border-radius: 5px; padding: 2px 6px; }}

    #clipPreview {{ background: #FAFBFD; border-top: 1px solid {LINE}; }}
    #pvHead {{ color: {INK3}; font-size: 10.5px; }}
    #pvKind {{ color: {INK2}; font-size: 11px; font-weight: 600; }}
    #pvHint {{ color: #A0A9B8; font-size: 10.5px; }}
    #pvImg {{ background: #FFFFFF; border: 1px solid #DCE2EC; border-radius: 8px; }}
    #pvText {{
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 8px;
        color: {INK2}; font-size: 11.5px;
    }}
    #pvEmpty {{ color: #A0A9B8; font-size: 11.5px; }}

    #clipBar {{ background: #FFFFFF; border-top: 1px solid {LINE}; }}
    QPushButton#clipPrimary {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {PRIMARY_TOP}, stop:1 {PRIMARY_BOTTOM});
        color: #FFFFFF; border: 0; border-radius: 8px;
        font-size: 12px; font-weight: 600; padding: 0 12px;
    }}
    QPushButton#clipPrimary:hover {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {PRIMARY_TOP_HOVER}, stop:1 {PRIMARY_BOTTOM_HOVER});
    }}
    QPushButton#clipPrimary:disabled {{ background: #B7C6E8; color: #FFFFFF; }}
    QPushButton#clipBtn {{
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 8px;
        color: {INK2}; font-size: 12px; font-weight: 600; padding: 0 11px;
    }}
    QPushButton#clipBtn:hover {{ background: #F7F9FC; border-color: #D6DDE9; }}
    QPushButton#clipBtn:disabled {{ color: #B6BFCD; border-color: {LINE_SOFT}; }}
    QPushButton#clipDanger {{
        background: #FFFFFF; border: 1px solid #F0D6D6; border-radius: 8px;
        color: {DANGER}; font-size: 12px; font-weight: 600; padding: 0 11px;
    }}
    QPushButton#clipDanger:hover {{ background: #FDEEEE; border-color: #E9BFBF; }}
    QPushButton#clipDanger:disabled {{ color: #D9AFAF; border-color: {LINE_SOFT}; }}

    #clipEmptyIcon {{ background: #F4F7FC; border: 1px solid {LINE}; border-radius: 14px; }}
    #clipEmptyTitle {{ color: {INK}; font-size: 13.5px; font-weight: 600; }}
    #clipEmptyText {{ color: {INK3}; font-size: 11.5px; }}
    {_C.QSS_EXTRA}
    """
