"""界面主题：设计 token 与 QSS。

颜色 / 尺寸与 `design/windows/index.html` 一一对应，改这里就等于改设计稿。
"""

from __future__ import annotations

import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# token
# --------------------------------------------------------------------------- #
INK = "#11151C"
INK2 = "#4B5566"
INK3 = "#818B9C"
LINE = "#E5E9F1"
LINE_SOFT = "#EEF1F6"

ACCENT = "#2F6BFF"
ACCENT_DEEP = "#1E4FD8"
ACCENT_SOFT = "#EDF3FF"

GREEN = "#0E9F5B"          # 亮灯状态文字
LED_CORE = "#34C759"
LED_DEEP = "#0F8A3D"

DANGER = "#D33B3B"
WARN = "#B4690E"
OK = "#0F9D58"

WIN_W, WIN_H = 440, 600
SETTINGS_H = 484          # 设置页内容比主页面少，矮一截避免中部大片空白
RADIUS_WIN = 14
RADIUS_CARD = 13
RADIUS_CTRL = 10

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"]


def asset(name: str) -> Path:
    """资源路径：包内 assets/ → 仓库 design/icon/ → 打包后的 _MEIPASS。"""
    here = Path(__file__).resolve().parent
    candidates = [here / "assets" / name]
    # 开发态：从包目录向上找 design/icon（src/dualswitch/ui → 仓库根）
    for parent in here.parents[:5]:
        candidates.append(parent / "design" / "icon" / name)
    candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "assets" / name)
    candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "dualswitch" / "ui" / "assets" / name)
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError(f"找不到资源 {name}")


# --------------------------------------------------------------------------- #
# QSS
# --------------------------------------------------------------------------- #
def qss() -> str:
    return f"""
    QWidget {{
        color: {INK};
        font-size: 12.5px;
    }}
    #root {{
        background: #FFFFFF;
        border: 1px solid rgba(16,24,40,0.07);
        border-radius: {RADIUS_WIN}px;
    }}
    #body {{ background: transparent; }}

    /* ---------- 标题栏（比内容区浅一档，形成层次） ---------- */
    #titlebar {{
        background: #F5F7FB;
        border: 0;
        border-bottom: 1px solid {LINE};
        border-top-left-radius: {RADIUS_WIN - 1}px;
        border-top-right-radius: {RADIUS_WIN - 1}px;
    }}
    #tbTitle {{ color: {INK2}; font-size: 12.5px; font-weight: 600; }}

    /* ---------- 状态卡 ---------- */
    #hero {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #EDF4FF, stop:0.52 #F7FAFF, stop:1 #EFFAF7);
        border: 1px solid #DCE7FF;
        border-radius: {RADIUS_CARD}px;
    }}
    #eyebrow {{ color: {INK3}; font-size: 11px; font-weight: 600; }}
    #heroTitle {{ color: {INK}; font-size: 31px; font-weight: 600; }}
    #heroSub {{ color: {INK2}; font-size: 12.5px; }}
    #heroFoot {{ color: {INK2}; font-size: 11.5px; }}
    #heroFootOk {{ color: {GREEN}; font-size: 11.5px; }}
    #heroFootErr {{ color: {DANGER}; font-size: 11.5px; }}
    #logoPlate {{
        background: #FFFFFF; border: 1px solid #DCE7FF; border-radius: 12px;
    }}

    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3F7BFF, stop:1 #2456E0);
        color: #FFFFFF; border: 0; border-radius: {RADIUS_CTRL}px;
        font-size: 14.5px; font-weight: 600; text-align: left; padding-left: 14px;
    }}
    QPushButton#primary:hover {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4A83FF, stop:1 #2A5CE6);
    }}
    QPushButton#primary:pressed {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2F6BFF, stop:1 #1B47C4);
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
    #listWrap {{ border: 1px solid {LINE}; border-radius: 11px; background: #FFFFFF; }}
    QPushButton#row {{
        background: #FFFFFF; border: 0; border-bottom: 1px solid {LINE_SOFT};
        color: {INK}; font-size: 12.5px; text-align: left; padding-left: 0;
    }}
    QPushButton#row:hover {{ background: #F7F9FD; }}
    QPushButton#rowLast {{
        background: #FFFFFF; border: 0; color: {INK}; font-size: 12.5px; text-align: left;
    }}
    QPushButton#rowLast:hover {{ background: #F7F9FD; }}
    #rowVal {{ color: {INK3}; font-size: 11.5px; }}
    #chev {{ color: #B6BFCD; font-size: 12px; }}

    /* ---------- 状态栏 ---------- */
    #statusbar {{
        background: #F7F9FC; border-top: 1px solid {LINE_SOFT};
        border-bottom-left-radius: {RADIUS_WIN - 1}px;
        border-bottom-right-radius: {RADIUS_WIN - 1}px;
    }}
    #statusText {{ color: {INK3}; font-size: 11.5px; }}

    /* ---------- 设置页 ---------- */
    #tabs {{ background: #F1F4FA; border-radius: 10px; }}
    QPushButton#tab {{
        background: transparent; border: 0; border-radius: 8px;
        color: {INK2}; font-size: 12.5px; font-weight: 500;
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
    #gTitle {{ color: {INK}; font-size: 12.5px; font-weight: 600; }}
    #gDesc {{ color: {INK3}; font-size: 11px; }}
    #group {{ background: {LINE_SOFT}; border: 1px solid {LINE}; border-radius: 11px; }}
    #gRow {{ background: #FFFFFF; border-bottom: 1px solid {LINE_SOFT}; }}
    #gRowLast {{ background: #FFFFFF; }}
    QPushButton#ghost {{
        background: #FFFFFF; border: 1px solid #D5DCE8; border-radius: 8px;
        color: {INK2}; font-size: 12.5px; padding: 0 14px; min-width: 52px;
    }}
    QPushButton#ghost:hover {{ background: #F7F9FD; border-color: #C3CCDB; }}
    QPushButton#ghost:pressed {{ background: #EEF2F8; }}
    QPushButton#solid {{
        background: {ACCENT}; border: 0; border-radius: 8px;
        color: #FFFFFF; font-size: 12.5px; font-weight: 600;
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
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 7px;
        color: {INK}; font-size: 12.5px; padding: 4px; outline: 0;
        selection-background-color: {ACCENT_SOFT}; selection-color: {INK};
    }}
    QLineEdit#num {{
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 7px;
        color: {INK}; font-size: 12.5px; font-weight: 600; padding: 4px 6px;
    }}
    QLineEdit#num:focus {{ border-color: {ACCENT}; }}
    QLineEdit#text {{
        background: #FFFFFF; border: 1px solid {LINE}; border-radius: 7px;
        color: {INK}; font-size: 12px; padding: 5px 8px;
    }}
    QLineEdit#text:focus {{ border-color: {ACCENT}; }}

    /* ---------- 托盘菜单 ---------- */
    QMenu {{
        background: #FFFFFF; border: 1px solid rgba(16,24,40,0.10);
        border-radius: 8px; padding: 5px;
    }}
    QMenu::item {{
        padding: 6px 26px 6px 12px; border-radius: 6px; font-size: 12.5px; color: {INK};
    }}
    QMenu::item:selected {{ background: #F2F5FA; }}
    QMenu::item:disabled {{ color: #A7B0BE; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 6px; }}

    /* ---------- 提示条 ---------- */
    #bannerWarn {{
        background: #FFF8EC; border: 1px solid #F3E2C4; border-radius: 9px;
        color: #7A4A05; font-size: 11.5px;
    }}
    #bannerErr {{
        background: #FDF1F1; border: 1px solid #F4D6D6; border-radius: 9px;
        color: #8C2222; font-size: 11.5px;
    }}
    """
