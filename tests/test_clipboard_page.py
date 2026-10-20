"""剪贴板页离屏测试：不需要显示器，也不需要真实剪贴板。

覆盖三件事：
1. 页面本身（列表 / 选中 / 空态 / 删除 / 重发 / 复制）—— **两端同一份骨架**
2. 主窗口里的接线（列表入口、翻页、高度、返回）
3. chrome 契约的两个新字段（两端都要有）

> 2026-10-20 起两端界面代码物理分开（`ui/mac/` 与 `ui/win/`），所以这个文件里
> 少数几条用例按平台分叉 —— 它们描述的是**两端本来就不同**的呈现：
>
> | 行为 | mac（`ui/mac/`） | Windows（`ui/win/`） |
> |---|---|---|
> | 双击预览图 | 写临时 PNG 交给系统看图软件 | 应用内全屏窗口 + ←/→ 翻页 |
> | 页头 | 自带一条（返回 / 居中标题 / 状态） | `widgets.page_header`（两侧等宽格，严格居中） |
> | 空态 | 保留底部操作条 | 收起列表/预览/操作条 |
>
> 分叉写法是刻意的：用同一份断言去套两端，只会逼着某一端改回另一端的形态。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from dualswitch.clipboard import make_image, make_text  # noqa: E402
from dualswitch.clipboard.mock import MockBackend  # noqa: E402
from dualswitch.config import ClipboardConfig, Config  # noqa: E402
from dualswitch.sync.engine import SyncEngine  # noqa: E402
from dualswitch.ui import platform_ui, theme  # noqa: E402
from dualswitch.ui.chrome import current as chrome  # noqa: E402

UI = platform_ui()
ClipboardPage = UI.clipboard_page.ClipboardPage
MainWindow = UI.window.MainWindow

IS_WIN = chrome().NAME == "windows"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def engine():
    cfg = ClipboardConfig()
    cfg.psk = "ab" * 32                     # 已配对
    engine = SyncEngine(cfg, backend=MockBackend())
    yield engine
    # 每个用例用完必须停：传输的心跳线程是 daemon 线程，不收尾会一直转，
    # 会话退出时踩已经释放的 socket（实测 access violation 崩在 _beat_loop）
    try:
        engine.stop()
    except Exception:                       # noqa: BLE001 - 收尾失败不该连带挂掉用例
        pass


@pytest.fixture()
def page(app, engine):
    widget = ClipboardPage(engine)
    widget.resize(440, 560)
    widget.show()
    app.processEvents()
    return widget


def _fill(engine) -> None:
    engine._add(make_text("第一行\n第二行", ), source="local", state="ok")
    png = b"\x89PNG\r\n\x1a\n" + bytes(64)
    engine._add(make_image(png, 40, 30), source="remote", state="pend")


# --------------------------------------------------------------------------- #
def test_page_shows_records(page, engine, app):
    _fill(engine)
    page.refresh()
    app.processEvents()
    assert len(page._rows) == 2
    assert page.scroll.isVisible() or page.scroll.isVisibleTo(page)
    assert not page.empty.isVisible()
    # 最新的在最前，且默认选中第一条
    assert page._selected == engine.records()[0].id


def test_empty_state_paired(page, engine, app):
    page.refresh()
    app.processEvents()
    assert page.empty.isVisibleTo(page)
    assert page.empty_title.text() == "还没有内容"
    assert page.pair_btn.isVisibleTo(page) is False


def test_empty_state_unpaired_shows_pair_button(page, app):
    page.engine.cfg.psk = ""
    page.refresh()
    app.processEvents()
    assert page.empty_title.text() == "两台机器连上才能互通"
    assert page.pair_btn.isVisibleTo(page) is True


def test_select_updates_preview(page, engine, app):
    _fill(engine)
    page.refresh()
    ids = [r.id for r in engine.records()]
    page._select(ids[1])
    app.processEvents()
    assert page._selected == ids[1]
    assert "预览" in page.preview.caption.text()


def test_delete_removes_only_from_list(page, engine, app):
    _fill(engine)
    page.refresh()
    target = engine.records()[0].id
    page._drop(target)
    app.processEvents()
    assert target not in [r.id for r in engine.records()]
    assert target not in page._rows


def test_clear_clears_records_and_shows_empty(page, engine, app):
    _fill(engine)
    page.refresh()
    page._clear()
    app.processEvents()
    assert engine.records() == []
    assert page.empty.isVisibleTo(page)


def test_copy_writes_local_clipboard(page, engine, app):
    _fill(engine)
    page.refresh()
    rid = engine.records()[0].id
    page._copy(rid)
    assert engine._backend.written, "复制到本机没有写回后端"


def test_retry_marks_pending(page, engine, app):
    _fill(engine)
    page.refresh()
    rid = engine.records()[0].id
    page._retry(rid)
    # 未启动引擎 → worker 不在跑，这里只验证「已入队 + 界面强制刷新」
    assert rid in engine._manual


def test_back_button_emits(page, app):
    fired = []
    page.backRequested.connect(lambda: fired.append(True))
    page.back.click()
    assert fired == [True]


# --------------------------------------------------------------------------- #
# 版式细节：文本缩略图用本机图标 / 双击看原图 / 空态收起操作条
# --------------------------------------------------------------------------- #
def _png(w: int = 24, h: int = 16) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor("#3366CC"))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def test_text_thumb_uses_platform_logo(page, engine, app):
    """缩略图画**来源端**的图标：对端来的画对端标，本机复制出来的画本机标。"""
    from dualswitch.ui.clipboard_page import Thumb

    engine._add(make_text("本机复制的"), source="local", state="ok")
    engine._add(make_text("对端发来的"), source="remote", state="ok")
    page.refresh()
    app.processEvents()

    local, remote = engine.records()[1], engine.records()[0]
    thumb_local = page._rows[local.id].findChild(Thumb)
    thumb_remote = page._rows[remote.id].findChild(Thumb)
    assert thumb_local is not None and thumb_remote is not None

    if IS_WIN:
        # Windows 版把「画哪个图标」记在 _logo_kind 上，图标自身延迟绘制
        # （文本记录不进 _pix，_pix 只装真图片；`paintEvent` 里才画 _logo）
        assert thumb_local._pix is None and thumb_remote._pix is None
        assert thumb_local._logo_kind == theme.PLATFORM_LOGO == chrome().PLATFORM_LOGO
        assert thumb_remote._logo_kind == theme.PEER_LOGO == chrome().PEER_LOGO
        assert theme.PEER_LOGO != theme.PLATFORM_LOGO
    else:
        # mac 版直接渲染成 pixmap（按 SYSTEM_LOGO 取本机/对端那张），没有 _logo_kind
        assert thumb_local._pix is not None and thumb_remote._pix is not None
        assert theme.SYSTEM_LOGO in ("apple", "windows")
        assert not hasattr(thumb_local, "_logo_kind")
        assert thumb_local._pix.toImage() != thumb_remote._pix.toImage(), (
            "本机与对端的缩略图应画不同系统的图标"
        )


def test_page_head_is_shared_and_centered(page, app):
    """返回 / 页名 / 连接状态同一水平线；Windows 还要求页名严格居中。"""
    if IS_WIN:
        bar = page.header
        assert bar.objectName() == "pageHead"
        title = bar.findChild(QLabel, "pageTitle")
        assert title is not None and title.text() == "剪贴板"

        def center_y(widget: QWidget) -> int:
            return widget.mapTo(bar, QPoint(0, widget.height() // 2)).y()

        back_center = center_y(bar.back_btn)
        title_center = center_y(title)
        assert abs(back_center - title_center) <= 2, "返回键和页名不在同一水平线"

        bar.resize(440, 40)
        app.processEvents()
        # QRect.center() 是 (x + (w-1)/2) 的整数除，直接比会有 1px 抖动，用浮点中心比
        title_center = title.x() + title.width() / 2
        assert abs(title_center - bar.width() / 2) <= 1, "页名没居中"
        return

    # mac 版：页头是页面自己排的一条（返回靠左、标题靠中间、状态靠右）
    assert page.back.text() == "\u2039 返回"
    titles = [lb for lb in page.findChildren(QLabel) if lb.text() == "剪贴板"]
    assert titles, "页头里找不到「剪贴板」标题"
    title = titles[0]
    assert abs(title.mapTo(page, QPoint()).y() - page.back.mapTo(page, QPoint()).y()) <= 10, (
        "返回键和页名不在同一水平线"
    )


def test_image_thumb_decodes_png(page, engine, app):
    from dualswitch.ui.clipboard_page import Thumb

    engine._add(make_image(_png(), 24, 16), source="local", state="ok")
    page.refresh()
    app.processEvents()
    thumb = page.findChild(Thumb)
    assert thumb is not None and thumb._pix is not None


def test_double_click_preview_opens_viewer(page, engine, app):
    """Windows：预览写着「双击全屏预览」，双击就得真全屏（顶层窗口，不是页面内小遮罩）。"""
    if not IS_WIN:
        pytest.skip("mac 版双击预览图走系统看图软件，见下一条用例")

    engine._add(make_image(_png(64, 48), 64, 48), source="local", state="ok")
    page.refresh()
    app.processEvents()
    page._select(engine.records()[0].id)
    assert page.preview.hint.text() == "双击全屏预览"

    page.preview.image.activated.emit()
    app.processEvents()
    assert page.viewer.isVisible()
    assert page.viewer.windowFlags() & Qt.FramelessWindowHint
    screen = app.primaryScreen().availableGeometry()
    assert page.viewer.width() >= screen.width(), "预览应是全屏窗口，不是页面大小的遮罩"
    assert page.viewer.height() >= screen.height()
    assert page.viewer._shots and page.viewer._shots[page.viewer._index][1] == engine.records()[0].id

    page.viewer.close_viewer()
    assert not page.viewer.isVisible()


def test_double_click_preview_opens_system_viewer(page, engine, app, monkeypatch):
    """mac：双击预览图 → 把原图写成临时 PNG，交给系统默认看图软件打开。"""
    if IS_WIN:
        pytest.skip("Windows 版双击预览图走应用内全屏预览，见上一条用例")

    engine._add(make_image(_png(64, 48), 64, 48), source="local", state="ok")
    page.refresh()
    app.processEvents()
    page._select(engine.records()[0].id)
    assert page.preview.hint.text() == "双击查看原图"

    opened: list[str] = []
    monkeypatch.setattr(page, "_open_external", opened.append, raising=False)
    page.preview.imageActivated.emit(engine.records()[0])
    app.processEvents()

    assert len(opened) == 1, "双击没有交给系统看图"
    target = Path(opened[0])
    assert target.suffix == ".png" and target.exists(), "交给系统的应是一张落盘的 PNG"
    assert target.read_bytes().startswith(b"\x89PNG")


def test_empty_state_hides_action_bar(page, app):
    if not IS_WIN:
        pytest.skip("mac 版空态保留底部操作条（旧版行为，未跟进收起）")
    page.refresh()
    app.processEvents()
    assert page.footer.isVisibleTo(page) is False


def test_row_fits_visible_area(page, engine, app):
    """行不能被撑到比可视区更宽，否则状态药丸会被挤出屏幕。

    副行曾经是普通 `QLabel`：它的 `minimumSizeHint` 是整段文字宽度，把行撑到
    500+（可视区只有 404），药丸和删除按钮全在窗口外 —— 界面看着像少了一半内容。
    """
    from PySide6.QtCore import QPoint

    for _ in range(4):
        engine._add(make_text("很长" * 40), source="local", state="ok")
    page.refresh()
    app.processEvents()

    assert page._rows
    for row in page._rows.values():
        assert row.width() <= page.scroll.viewport().width()
        pill_right = row.pill.mapTo(page, QPoint()).x() + row.pill.width()
        assert pill_right <= page.width()


def test_list_gap_is_not_scroll_area_gray(engine, app):
    """列表行之间不能露出滚动容器被强制刷上的灰底 —— 「灰底」回归守卫。

    `QScrollArea.setWidget()` 会**强行**把宿主的 `autoFillBackground` 打开，宿主就用
    `QPalette::Window`（浅灰）把自己刷一遍。而列表行在未选中时不画背景，于是整片
    列表变成灰的 —— 这就是用户报的「列表容器被刷成灰底」。修复是在 `setWidget()`
    之后把宿主的 `autoFillBackground` 关掉（见 `ClipboardPage._list_area`）。
    这条用例同时守住根因（属性被改写）与表现（渲染出来的像素）。
    """
    for text in ("第一条记录", "第二条记录", "第三条记录"):
        engine._add(make_text(text), source="local", state="ok")

    # 页面必须**新建**在主窗口里，并且套上应用 QSS 再渲染：不给 QSS，`#root`
    # 就没有白底（窗口是透明的），采样出来全是透明像素，渲染断言不可靠。
    # 样式表用完还原，免得影响同模块里别的用例。
    prev_qss = app.styleSheet()
    app.setStyleSheet(theme.qss())
    page = ClipboardPage(engine)
    win = MainWindow(Config(), "Alt+Space", "v1.0.0")
    win.attach_clipboard(page)
    win.show()
    win.show_clipboard_page()
    app.processEvents()
    page.refresh()
    app.processEvents()
    try:
        # 根因：宿主不能被强行打开自动填充
        assert page.list_host.autoFillBackground() is False, "滚动宿主又被刷上灰底了"

        image = win.grab().toImage()
        # 采样一条**未选中**行左侧留白处（x=4，缩略图从 x=12 才开始）：
        # 该行不画背景，这里露出的就是滚动宿主的底色。
        target = list(page._rows.values())[1]
        at = page.mapTo(win, target.mapTo(page, QPoint(4, target.height() // 2)))
        color = image.pixelColor(at.x(), at.y())
        gray = app.palette().color(QPalette.Window)
    finally:
        win.deleteLater()
        app.setStyleSheet(prev_qss)

    assert color != gray, f"行间露出了滚动容器灰底：{color.name()}"
    assert color.lightness() > gray.lightness() + 8, (
        f"行间底色应是窗口白底，实测 {color.name()}"
    )


# --------------------------------------------------------------------------- #
# 主窗口接线
# --------------------------------------------------------------------------- #
@pytest.fixture()
def window(app):
    cfg = Config()
    win = MainWindow(cfg, "⌥ Space", "v1.0.0", monitor_name="AOC")
    yield win
    win.deleteLater()


def test_main_window_has_clipboard_row_on_mac(window):
    from dualswitch.ui.chrome import current as chrome

    if chrome().INLINE_CLIPBOARD_ROW:
        assert window.row_clipboard is not None
        assert window.row_clipboard.isEnabled()


def test_clipboard_row_triggers_signal(window):
    if window.row_clipboard is None:
        pytest.skip("该平台剪贴板入口在标题栏")
    fired = []
    window.openClipboardRequested.connect(lambda: fired.append(True))
    window.row_clipboard.click()
    assert fired == [True]


def test_attach_and_switch_pages(app, engine):
    cfg = Config()
    win = MainWindow(cfg, "⌥ Space", "v1.0.0")
    page = ClipboardPage(engine)
    win.attach_clipboard(page)
    win.show_clipboard_page()
    assert win.on_clipboard_page() is True
    assert win.windowTitle() == "屏幕切换 · 剪贴板"
    assert win.height() == win.win_h()
    win.show_main_page()
    assert win.on_clipboard_page() is False
    assert win.windowTitle() == "屏幕切换"


def test_extra_h_matches_chrome(window):
    from dualswitch.ui.chrome import current as chrome

    assert window.height() == theme.WIN_H + chrome().EXTRA_H


# --------------------------------------------------------------------------- #
# chrome 契约
# --------------------------------------------------------------------------- #
def test_chrome_contract_fields_exist():
    from dualswitch.ui.chrome import mac, win  # noqa: F401 - 两端模块都要能 import

    for module in (mac, win):
        assert isinstance(module.INLINE_CLIPBOARD_ROW, bool)
        assert isinstance(module.CLIPBOARD_ROW_HINT, str)
        assert isinstance(module.EXTRA_H, int)
    # mac 多一行列表；Windows 入口在标题栏、高度不变
    assert mac.INLINE_CLIPBOARD_ROW is True
    assert win.INLINE_CLIPBOARD_ROW is False
    assert mac.EXTRA_H > 0
    assert win.EXTRA_H == 0
