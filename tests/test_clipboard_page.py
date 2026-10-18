"""剪贴板页离屏测试：不需要显示器，也不需要真实剪贴板。

覆盖三件事：
1. 页面本身（列表 / 选中 / 空态 / 删除 / 重发 / 复制）
2. 主窗口里的接线（列表入口、翻页、高度、返回）
3. chrome 契约的两个新字段（两端都要有）
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过界面测试")

from PySide6.QtWidgets import QApplication  # noqa: E402

from dualswitch.clipboard import make_image, make_text  # noqa: E402
from dualswitch.clipboard.mock import MockBackend  # noqa: E402
from dualswitch.config import ClipboardConfig, Config  # noqa: E402
from dualswitch.sync.engine import SyncEngine  # noqa: E402
from dualswitch.ui import theme  # noqa: E402
from dualswitch.ui.clipboard_page import ClipboardPage  # noqa: E402
from dualswitch.ui.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def engine():
    cfg = ClipboardConfig()
    cfg.psk = "ab" * 32                     # 已配对
    return SyncEngine(cfg, backend=MockBackend())


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
