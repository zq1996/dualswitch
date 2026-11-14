"""外接屏省电（`display_power.AwayDisplayPower`）的编排逻辑测试。

⚠️ 这里**一律注入假平台层**（`disable` / `enable` / `available_check` / `state_path`），
绝不让测试真的去停用户的显示器。
"""

from __future__ import annotations

import time

import pytest

from dualswitch.display_power import AwayDisplayPower


class FakePlatform:
    def __init__(self, host: str | None = "windows", ids: list[int] | None = None) -> None:
        self.host = host
        self.ids = [3] if ids is None else ids
        self.disable_calls = 0
        self.enable_calls: list[list[int]] = []
        self.off = False

    # ---- 注入给 guard 的四件套 ----
    def read_host(self) -> str | None:
        return self.host

    def list_ids(self) -> list[int]:
        """CGS 列表里的 id：停用后仍在（1×1 桩），拔线才消失。"""
        return list(self.ids)

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
        list_ids=fake.list_ids,
        blind_restore_s=0,          # 安全阀默认关，单独用例再打开
        # ⚠️ 必须指到 tmp：默认会去读用户真实的 config.json，用例之间互相串味
        watch_config=tmp_path / "config.json",
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


def test_keeps_off_when_read_fails_but_display_still_listed(tmp_path):
    """读不到 ≠ 屏没了。

    2026-11-13 21:58 实测：真停用之后 DDC 读恒失败，旧逻辑「连续 5 次读不到就恢复」
    让省电刚生效 7.5 秒就自我撤销。现在只要屏还在 CGS 列表里（停用后的 1×1 桩），
    就必须保持停用。
    """
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, probe_interval_ms=100)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)

        fake.host = None                       # 读通道哑了
        time.sleep(1.2)                        # 远超旧逻辑的 5 次探测
        assert fake.enable_calls == [], "屏还在列表里，不能因为读不到就恢复"
        assert guard.mode == AwayDisplayPower.OFF
    finally:
        guard.stop()


def test_restores_when_disabled_display_vanished(tmp_path):
    """被停用的屏从 CGS 列表消失（拔线 / 断电）→ 必须恢复，别留个死 id。"""
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, probe_interval_ms=100)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)

        fake.host = None
        fake.ids = []                          # 拔线
        assert wait_until(lambda: bool(fake.enable_calls), timeout=2.0)
    finally:
        guard.stop()


def test_safety_valve_restores_after_long_blind(tmp_path):
    """读通道彻底哑掉时的安全阀：到点恢复一次，别把屏永久留在黑屏。"""
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, probe_interval_ms=100, blind_restore_s=0.4)
    guard.start()
    try:
        guard.on_source("windows")
        assert wait_until(lambda: fake.disable_calls == 1)

        fake.host = None
        assert wait_until(lambda: bool(fake.enable_calls), timeout=3.0)
    finally:
        guard.stop()


def test_trusted_arm_disables_even_when_read_is_dead(tmp_path):
    """本机刚发出一条成功切到对面的 Set ⇒ 到点照停，不再被「读不出来」否决。

    这正是 2026-11-14 的线上 bug：切到 Windows 后 2.5s 的二次确认读回 None，
    整条省电路径被静默抛弃，屏一直 active。
    """
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, off_delay_ms=50)
    guard.start()
    try:
        fake.host = None                       # 读通道已经哑了
        guard.on_source("windows", trusted=True)
        assert wait_until(lambda: fake.disable_calls == 1)
        assert guard.mode == AwayDisplayPower.OFF
    finally:
        guard.stop()


def test_untrusted_arm_aborts_when_read_is_dead(tmp_path):
    """没有硬依据（只是「听说」画面在对面）又读不出来 ⇒ 宁可不省电。"""
    fake = FakePlatform("windows")
    guard = make_guard(tmp_path, fake, off_delay_ms=50)
    guard.start()
    try:
        fake.host = None
        guard.on_source("windows")
        time.sleep(0.5)
        assert fake.disable_calls == 0
        assert guard.mode == AwayDisplayPower.IDLE
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
        list_ids=fake.list_ids,
        blind_restore_s=0,
        watch_config=tmp_path / "config.json",
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
        list_ids=fake.list_ids,
        blind_restore_s=0,
        watch_config=tmp_path / "config.json",
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


def test_after_screen_switch_arms_with_hard_evidence(tmp_path):
    """接线守卫：本机发出的切换成功 ⇒ 必须带 `trusted=True` 通知省电模块。

    没有这一条，2026-11-14 的 bug 会以另一种形式回来：切到对面后 DDC 读不出来，
    `_arm_fire` 拿不到证据就把停用取消，屏一直 active。
    """
    from dualswitch.app import SwitchController
    from dualswitch.config import from_dict
    from dualswitch.ddc.mock import MockBackend

    cfg = from_dict({
        "this_host": "mac",
        "readback": {"enabled": False},
        "mouse_switch": {"enabled": False},     # 别让用例去动真鼠标
    })
    ctl = SwitchController(cfg, MockBackend(current=18, monitors=[]))

    fake = FakePlatform("windows")
    fake.host = None                            # 读通道已经哑了
    guard = AwayDisplayPower(
        enabled=True,
        off_delay_ms=10,
        probe_interval_ms=200,
        read_host=fake.read_host,
        local_host="mac",
        disable=fake.disable,
        enable=fake.enable,
        state_path=tmp_path / "display-power.json",
        available_check=FakePlatform.available,
        list_ids=fake.list_ids,
        blind_restore_s=0,
        watch_config=tmp_path / "config.json",
    )
    guard.start()
    ctl._display_power = guard
    try:
        ctl._after_screen_switch("windows")
        assert wait_until(lambda: fake.disable_calls == 1)
    finally:
        guard.stop()


# --------------------------------------------------------------- 底层启停通道
class _FakeCG:
    """假的 CoreGraphics：只记录调用序列，永远不碰真显示器。"""

    def __init__(self, complete_rc: int = 0) -> None:
        import ctypes as _ct

        self._ct = _ct
        self.complete_rc = complete_rc
        self.scopes: list[int] = []
        self.cancelled = 0
        self.enable_calls: list[tuple[int, bool]] = []

    def CGSBeginDisplayConfiguration(self, refp):
        self._ct.cast(refp, self._ct.POINTER(self._ct.c_void_p))[0] = 1
        return 0

    def CGSConfigureDisplayEnabled(self, ref, display_id, enabled):
        self.enable_calls.append((display_id, enabled))
        return 0

    def CGSCompleteDisplayConfiguration(self, ref, scope):
        self.scopes.append(scope)
        return self.complete_rc

    def CGSCancelDisplayConfiguration(self, ref):
        self.cancelled += 1
        return 0


class _FakeSLS:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.calls = 0

    def SLSConfigureDisplayEnabled(self, ref, display_id, enabled):
        self.calls += 1
        return self.rc


def test_enable_prefers_skylight(monkeypatch):
    """启用必须优先走 SkyLight：CGS 那条路重新启用会稳定回 1001，屏醒不过来。"""
    from dualswitch import display_power as dp

    cg = _FakeCG(complete_rc=0)
    sls = _FakeSLS(rc=0)
    monkeypatch.setattr(dp, "_cg_lib", lambda: cg)
    monkeypatch.setattr(dp, "_sl_lib", lambda: sls)

    dp.set_enabled(3, True)

    assert sls.calls == 1, "应该先用 SLS"
    assert cg.enable_calls == [], "SLS 成功就不该再用 CGS 的 configure"
    assert cg.scopes == [dp._CONFIGURE_FOR_SESSION]


def test_enable_falls_back_to_permanent_scope(monkeypatch):
    """会话作用域被拒（1001）时，启用允许再试永久作用域 —— 多一条活路。"""

    class _CGTwoPhase(_FakeCG):
        def CGSCompleteDisplayConfiguration(self, ref, scope):
            self.scopes.append(scope)
            return 1001 if len(self.scopes) == 1 else 0

    from dualswitch import display_power as dp

    cg = _CGTwoPhase()
    monkeypatch.setattr(dp, "_cg_lib", lambda: cg)
    monkeypatch.setattr(dp, "_sl_lib", lambda: None)     # 只有 CGS 可用

    dp.set_enabled(3, True)

    assert cg.scopes == [dp._CONFIGURE_FOR_SESSION, dp._CONFIGURE_FOR_PERMANENT]


def test_restore_failure_raises_with_details(monkeypatch):
    """全都失败时要抛错并带上各次尝试的错误码，调用方才知道「以为恢复了其实没有」。"""
    from dualswitch import display_power as dp

    cg = _FakeCG(complete_rc=1001)
    monkeypatch.setattr(dp, "_cg_lib", lambda: cg)
    monkeypatch.setattr(dp, "_sl_lib", lambda: None)

    with pytest.raises(RuntimeError) as info:
        dp.set_enabled(3, True)
    message = str(info.value)
    assert "1001" in message and "SLS" not in message
    assert len(cg.scopes) == 2, "会话作用域 + 永久作用域都要试过"


def test_disable_only_uses_session_scope(monkeypatch):
    """停用绝不能写永久作用域：否则下次登录用户还是少一块屏。"""
    from dualswitch import display_power as dp

    cg = _FakeCG(complete_rc=1001)
    monkeypatch.setattr(dp, "_cg_lib", lambda: cg)
    monkeypatch.setattr(dp, "_sl_lib", lambda: _FakeSLS(rc=0))

    with pytest.raises(RuntimeError):
        dp.set_enabled(3, False)
    assert set(cg.scopes) == {dp._CONFIGURE_FOR_SESSION}


def test_start_heals_leftover_state_even_when_feature_is_off(tmp_path):
    """省电关着也要把上个进程留下的停用态救回来 —— 否则用户开机就少一块屏。"""
    fake = FakePlatform("windows")
    state = tmp_path / "display-power.json"
    state.write_text('{"ids": [3], "at": 0}', encoding="utf-8")

    guard = make_guard(tmp_path, fake, enabled=False)
    guard.start()
    try:
        assert fake.enable_calls == [[3]]
        assert not state.exists()
    finally:
        guard.stop()


def test_start_heals_leftover_state_even_when_feature_is_off(tmp_path):
    """省电关着也要把上个进程留下的停用态救回来 —— 否则用户开机就少一块屏。"""
    fake = FakePlatform("windows")
    state = tmp_path / "display-power.json"
    state.write_text('{"ids": [3], "at": 0}', encoding="utf-8")

    guard = make_guard(tmp_path, fake, enabled=False)
    guard.start()
    try:
        assert fake.enable_calls == [[3]]
        assert not state.exists()
    finally:
        guard.stop()


def test_runtime_toggle_follows_config_without_restart(tmp_path):
    """改 config 里的 `display_power.enabled` 必须立刻生效，不用重启程序。

    2026-11-14 用户被这一点整崩过：配置改成 true 之后，正在跑的实例（启动时读到的
    还是 false）纹丝不动，看上去就像「代码改错了」。
    """
    import os

    fake = FakePlatform("windows")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"display_power": {"enabled": false}}', encoding="utf-8")

    guard = make_guard(tmp_path, fake, off_delay_ms=10, enabled=False)
    guard.start()
    try:
        time.sleep(0.4)
        assert fake.disable_calls == 0, "开关关着就不许动屏"

        cfg_path.write_text('{"display_power": {"enabled": true}}', encoding="utf-8")
        os.utime(cfg_path, (time.time() + 5, time.time() + 5))
        assert wait_until(lambda: fake.disable_calls == 1, timeout=3.0)
        assert guard.mode == AwayDisplayPower.OFF

        cfg_path.write_text('{"display_power": {"enabled": false}}', encoding="utf-8")
        os.utime(cfg_path, (time.time() + 10, time.time() + 10))
        assert wait_until(lambda: bool(fake.enable_calls), timeout=3.0)
    finally:
        guard.stop()


def test_runtime_toggle_follows_config_without_restart(tmp_path):
    """改 config 里的 `display_power.enabled` 必须立刻生效，不用重启程序。

    2026-11-14 用户被这一点整崩过：配置改成 true 之后，正在跑的实例（启动时读到的
    还是 false）纹丝不动，看上去就像「代码改错了」。
    """
    import os

    fake = FakePlatform("windows")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"display_power": {"enabled": false}}', encoding="utf-8")

    guard = make_guard(tmp_path, fake, off_delay_ms=10, enabled=False)
    guard.start()
    try:
        time.sleep(0.4)
        assert fake.disable_calls == 0, "开关关着就不许动屏"

        cfg_path.write_text('{"display_power": {"enabled": true}}', encoding="utf-8")
        os.utime(cfg_path, (time.time() + 5, time.time() + 5))
        assert wait_until(lambda: fake.disable_calls == 1, timeout=3.0)
        assert guard.mode == AwayDisplayPower.OFF

        cfg_path.write_text('{"display_power": {"enabled": false}}', encoding="utf-8")
        os.utime(cfg_path, (time.time() + 10, time.time() + 10))
        assert wait_until(lambda: bool(fake.enable_calls), timeout=3.0)
    finally:
        guard.stop()
