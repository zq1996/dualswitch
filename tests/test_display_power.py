"""外接屏省电（`display_power.AwayDisplayPower`）的编排逻辑测试。

⚠️ 这里**一律注入假平台层**（`disable` / `enable` / `available_check` / `state_path`），
绝不让测试真的去停用户的显示器。
"""

from __future__ import annotations

import time

import pytest

from dualswitch.display_power import AwayDisplayPower


class FakePlatform:
    def __init__(self, host: str | None = "windows") -> None:
        self.host = host
        self.disable_calls = 0
        self.enable_calls: list[list[int]] = []
        self.off = False

    # ---- 注入给 guard 的四件套 ----
    def read_host(self) -> str | None:
        return self.host

    def disable(self) -> list[int]:
        self.disable_calls += 1
        self.off = True
        return [3]

    def enable(self, ids: list[int]) -> list[int]:
        self.enable_calls.append(list(ids))
        self.off = False
        return list(ids)

    @staticmethod
    def available() -> tuple[bool, str]:
        return True, ""


def make_guard(tmp_path, fake: FakePlatform, **kwargs) -> AwayDisplayPower:
    params = dict(
        enabled=True,
        off_delay_ms=10,
        probe_interval_ms=200,
        read_host=fake.read_host,
        local_host="mac",
        disable=fake.disable,
        enable=fake.enable,
        state_path=tmp_path / "display-power.json",
        available_check=FakePlatform.available,
    )
    params.update(kwargs)
    return AwayDisplayPower(**params)


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_disables_after_delay_when_picture_is_on_the_other_host(tmp_path):
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)
        assert guard.mode == AwayDisplayPower.OFF
        assert guard.off_ids == [3]
    finally:
        guard.stop()


def test_does_not_disable_when_picture_came_back_before_deadline(tmp_path):
    """到点前再读一次：画面已经回来就别停（否则用户会看到黑屏）。"""
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, off_delay_ms=300)
    guard.start()
    try:
        guard.on_source("windows")
        fake.host = "mac"                 # 画面回来了
        time.sleep(0.6)
        assert fake.disable_calls == 0
        assert guard.mode == AwayDisplayPower.IDLE
    finally:
        guard.stop()


def test_restores_when_picture_returns_while_off(tmp_path):
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)

        fake.host = "mac"
        assert wait_until(lambda: bool(fake.enable_calls))
        assert fake.enable_calls[0] == [3]
        assert guard.mode == AwayDisplayPower.IDLE
    finally:
        guard.stop()


def test_restores_on_unknown_host(tmp_path):
    """读不到 / 归属不明时保守恢复 —— 停用是私有 API，宁可不省电也别留黑屏。"""
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)

        fake.host = None
        guard.on_source(None)
        assert wait_until(lambda: bool(fake.enable_calls))
    finally:
        guard.stop()


def test_before_switch_to_local_restores_immediately(tmp_path):
    """切回本机之前必须先恢复：画面要落到一块活着的输出上。"""
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)

        guard.before_switch_to_local()
        assert fake.enable_calls == [[3]]
        assert guard.mode == AwayDisplayPower.IDLE
    finally:
        guard.stop()


def test_stop_always_restores(tmp_path):
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake)
    guard.start()
    guard.on_source("windows")
    assert wait_until(lambda: fake.disable_calls == 1)

    guard.stop()
    assert fake.enable_calls == [[3]]


def test_restores_after_repeated_read_failures(tmp_path):
    """连着读不到就当「屏不在了」恢复：拔线/断电时不能把它留在停用态。"""
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, probe_interval_ms=200)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)

        fake.host = None
        # 5 次探测 × 0.2s，留足余量
        assert wait_until(lambda: bool(fake.enable_calls), timeout=3.0)
    finally:
        guard.stop()


def test_disabled_config_is_a_noop(tmp_path):
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, enabled=False)
    guard.start()
    try:
        guard.on_source("windows")
        time.sleep(0.3)
        assert fake.disable_calls == 0
        assert guard.mode == AwayDisplayPower.IDLE
    finally:
        guard.stop()


def test_writes_and_clears_state_file(tmp_path):
    fake = FakePlatform("windows")
    state = tmp_path / "display-power.json"
    guard = make_guard(tmp_path, fake)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)
        assert wait_until(lambda: state.exists()), "停用后要留痕，崩溃了才有得救"
        assert "3" in state.read_text(encoding="utf-8")

        guard.restore_now("测试")
        assert not state.exists(), "恢复后必须把痕迹清掉，别让下次启动误会"
    finally:
        guard.stop()


def test_restore_pending_is_idempotent_on_empty_state(tmp_path):
    """没有残留状态时启动不能乱动显示器。"""
    from dualswitch import display_power as dp

    state = tmp_path / "display-power.json"
    assert dp.restore_pending(state) == []
    state.write_text('{"ids": []}', encoding="utf-8")
    assert dp.restore_pending(state) == []


@pytest.mark.parametrize("local, other", [("mac", "windows"), ("windows", "mac")])
def test_other_host_follows_local(tmp_path, local, other):
    """Windows 端将来复用同一套编排：方向要跟着本机身份走。"""
    fake = FakePlatform(other)
    guard = AwayDisplayPower(
        enabled=True,
        off_delay_ms=10,
        probe_interval_ms=200,
        read_host=fake.read_host,
        local_host=local,
        disable=fake.disable,
        enable=fake.enable,
        state_path=tmp_path / "display-power.json",
        available_check=FakePlatform.available,
    )
    guard.start()
    try:
        guard.on_source(other)
        assert wait_until(lambda: fake.disable_calls == 1)
        guard.on_source(local)
        assert wait_until(lambda: bool(fake.enable_calls))
    finally:
        guard.stop()


def test_skips_when_external_is_the_only_output(monkeypatch):
    """Mac mini + 单屏：停掉它等于把本机屏幕拔了，必须放弃省电。"""
    from dualswitch import display_power as dp

    only = dp.DisplayInfo(id=3, active=True, online=True, builtin=False, main=False, width=2560, height=1440)
    monkeypatch.setattr(dp, "list_displays", lambda: [only])
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(dp, "set_enabled", lambda i, e: calls.append((i, e)))
    monkeypatch.setattr(dp, "_write_state", lambda *a, **k: None)

    assert dp.disable_external_displays() == []
    assert calls == []


def test_disables_external_but_keeps_builtin(monkeypatch):
    from dualswitch import display_power as dp

    builtin = dp.DisplayInfo(id=1, active=True, online=True, builtin=True, main=True, width=1470, height=956)
    external = dp.DisplayInfo(id=3, active=True, online=True, builtin=False, main=False, width=2560, height=1440)
    monkeypatch.setattr(dp, "list_displays", lambda: [builtin, external])
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(dp, "set_enabled", lambda i, e: calls.append((i, e)))
    written: list[list[int]] = []
    monkeypatch.setattr(dp, "_write_state", lambda ids, *a, **k: written.append(list(ids)))

    assert dp.disable_external_displays() == [3]
    assert calls == [(3, False)]
    assert written == [[3]]


def test_enable_skips_displays_already_in_use(monkeypatch):
    """已经在用的屏再 enable 一次会返回 1001，还会让「恢复成功」判不出来。"""
    from dualswitch import display_power as dp

    external = dp.DisplayInfo(id=3, active=True, online=True, builtin=False, main=False, width=2560, height=1440)
    monkeypatch.setattr(dp, "list_displays", lambda: [external])
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(dp, "set_enabled", lambda i, e: calls.append((i, e)))

    assert dp.enable_displays([3]) == [3]
    assert calls == []


def test_controller_wires_ownership_to_display_power(tmp_path):
    """接线守卫：`_commit_read_host` 是唯一确认归属的地方，必须喂给省电模块。"""
    from dualswitch.app import SwitchController
    from dualswitch.config import from_dict
    from dualswitch.ddc.mock import MockBackend

    backend = MockBackend(current=17, monitors=[])
    cfg = from_dict({"this_host": "windows", "readback": {"enabled": False}})
    ctl = SwitchController(cfg, backend)

    fake = FakePlatform("mac")
    guard = AwayDisplayPower(
        enabled=True,
        off_delay_ms=10,
        probe_interval_ms=200,
        read_host=fake.read_host,
        local_host="windows",
        disable=fake.disable,
        enable=fake.enable,
        state_path=tmp_path / "display-power.json",
        available_check=FakePlatform.available,
    )
    guard.start()
    ctl._display_power = guard
    try:
        ctl._commit_read_host("mac")          # 画面去了对面
        assert wait_until(lambda: fake.disable_calls == 1)

        ctl._commit_read_host("windows")      # 画面回来
        assert wait_until(lambda: bool(fake.enable_calls))
    finally:
        guard.stop()
