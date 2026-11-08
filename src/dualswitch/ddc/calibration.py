"""标定状态机（纯逻辑，不碰硬件）。

一次标定的流程（`docs/V3-PLAN.md` §6.6）：

1. 开始前读一次 `VCP 0x60`，得到**起始口**的值（如 USB-C=100）；
2. 用户按提示把显示器切到下一个口；后台轮询读值，**连续两次相同**才算抓到；
3. 抓到后立刻把画面切回起始口（否则用户看不见引导界面）；
4. 等切换完成，进入下一个口；
5. 起始口本身不需要标定，最后跳过。

这块屏的读通道有两个已知问题，状态机必须替界面兜住：

* 读成功率低（macOS ~50%）—— 读失败只算一次 `no-read`，不乱猜；
* 两个口可能同码（AOC U27N3G6R4B 的 HDMI1/HDMI2 都是 17）——
  值不变时无法自动判断，必须允许用户点「已切到（读数不变）」手动确认。

2026-11-08 补的两条（真机标定卡死之后才看出来的）：

* **起始口不能只靠码表反查**。旧表把这块屏的 USB-C 记成 100（实际 18），
  起始读数在码表里找不到 → `start_port` 为空 → 既不能自动切回，又把
  「等于起始值」的读数全判成 `same-as-start`，于是每个口都永远抓不到。
  现在多一层兜底：码表对不上就用**接线**（本机接的那个口）认起始口。
* **同码保护要管到「上一个口」**。HDMI1/HDMI2 都是 17 时，刚抓完 HDMI1
  就会立刻把 HDMI2 也判成 17（用户可能还没切过去）—— 必须让用户确认。

界面只负责画/发号；这里保证「什么时候算抓到、什么时候该切回」只有一个答案。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profiles import MonitorProfile, normalize_port

#: 连续几次相同才算稳定（读成功率 ~50%，一次不算数）
STABLE_READS = 2
#: 两次稳定读之间至少隔多久（显示器切换需要时间）
STABLE_GAP_S = 0.4


@dataclass
class Step:
    port: str
    value: int | None = None
    status: str = "pending"   # pending / active / caught / done / skipped / failed
    note: str = ""


@dataclass
class CalibrationPlan:
    """标定流程的全部状态。调用方按事件推进，不直接改字段。"""

    profile: MonitorProfile
    selected_ports: list[str]
    start_port: str = ""
    start_value: int | None = None
    #: 起始口是「按接线推出来的」而不是「码表反查出来的」—— 说明码表本身可能过时
    start_inferred: bool = False
    steps: list[Step] = field(default_factory=list)
    index: int = 0
    state: str = "ready"       # ready / waiting / caught / switching_back / done
    message: str = ""
    _last_value: int | None = None
    _last_at: float = 0.0
    _stable: int = 0

    # ------------------------------------------------------------------ 构建
    @classmethod
    def create(
        cls,
        profile: MonitorProfile,
        selected_ports: list[str],
        current_value: int | None = None,
        fallback_port: str = "",
    ) -> "CalibrationPlan":
        """`fallback_port` = 本机接的那个口（`wiring.local`）。码表对不上时兜底用。"""
        ports = []
        for raw in selected_ports:
            port = normalize_port(raw)
            if port and port in profile.ports and port not in ports:
                ports.append(port)
        plan = cls(profile=profile, selected_ports=ports)
        plan._apply_start(current_value, fallback_port)
        return plan

    def _apply_start(self, current_value: int | None, fallback_port: str = "") -> None:
        self.start_value = None if current_value is None else int(current_value)
        self.start_port = ""
        self.start_inferred = False
        if self.start_value is not None:
            # 同码的两个口无法区分，取排在前面的那个；界面会提示「起始口可能不唯一」
            for port in self.selected_ports:
                if self.profile.value(port) == self.start_value:
                    self.start_port = port
                    break
            if not self.start_port:
                # 码表里没有这个值：说明码表过时了（换过固件/以前标错）。
                # 这时候**绝不能**放弃起始口 —— 否则 cannot switch back，
                # 而且读到的每个 18 都会被当成 `same-as-start` 永远抓不到。
                # 用接线（本机接的口）认，读到的值就是它的真码。
                fallback = normalize_port(fallback_port)
                if fallback and fallback in self.selected_ports:
                    self.start_port = fallback
                    self.start_inferred = True
        self.steps = [
            Step(port=port)
            for port in self.selected_ports
            if port != self.start_port
        ]
        self.index = 0
        self.state = "ready" if self.steps else "done"
        self.message = ""
        self._reset_stability()
        if self.steps:
            self.steps[0].status = "active"

    def _reset_stability(self) -> None:
        self._last_value = None
        self._last_at = 0.0
        self._stable = 0

    # ------------------------------------------------------------------ 查询
    @property
    def current_step(self) -> Step | None:
        if 0 <= self.index < len(self.steps):
            return self.steps[self.index]
        return None

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def finished(self) -> bool:
        return self.state == "done"

    def can_auto_switch_back(self) -> bool:
        return self.start_value is not None

    def result(self) -> dict[str, int]:
        """已抓到的端口码表（包含起始口）。"""
        ports: dict[str, int] = {}
        if self.start_port and self.start_value is not None:
            ports[self.start_port] = int(self.start_value)
        for step in self.steps:
            if step.value is not None:
                ports[step.port] = int(step.value)
        return ports

    # ------------------------------------------------------------------ 事件
    def begin(self) -> None:
        """开始等待第一个口（或直接完成）。"""
        if self.state == "ready":
            self.state = "waiting" if self.steps else "done"
            if self.current_step is not None:
                self.current_step.status = "active"

    def feed(self, value: int | None, now: float) -> str:
        """喂一次读数。返回事件名，界面据此更新提示。

        `same-as-start` / `same-as-known` 是专门给「两个口同码」的：读数等于
        起始口、或等于**上一个已经抓到的口**，都说明靠读数分不出用户到底有没有
        切过去，状态机不能自作主张，必须让用户手动确认。
        """
        if self.state != "waiting" or self.current_step is None:
            return "ignored"
        if value is None:
            self._stable = 0
            # 读失败会把「连续两次相同」打断，这里把上次值也清掉：
            # 否则 18/失败/18/失败 这种抖动会永远停在「读到了，等待稳定」。
            self._last_value = None
            return "no-read"
        value = int(value)
        if self.start_value is not None and value == self.start_value:
            # 还停在起始口（或目标口与起始口同码）：不自动抓
            self._last_value = value
            self._last_at = now
            self._stable = 0
            return "same-as-start"
        if self._same_as_other_step(value):
            # 上一个口也是这个码（HDMI1/HDMI2 同码）：读数不变不等于已切过去
            self._last_value = value
            self._last_at = now
            self._stable = 0
            return "same-as-known"
        if value == self._last_value:
            self._stable += 1
        else:
            self._last_value = value
            self._last_at = now
            self._stable = 1
        if self._stable < STABLE_READS:
            return "changed"
        if now - self._last_at < STABLE_GAP_S:
            return "changed"
        self._catch(value)
        return "caught"

    def _same_as_other_step(self, value: int) -> bool:
        """这个读数是不是已经属于别的口（同码）。"""
        for step in self.steps:
            if step is self.current_step:
                continue
            if step.value is not None and int(step.value) == int(value):
                return True
        return False

    def _catch(self, value: int) -> None:
        step = self.current_step
        if step is None:
            return
        step.value = int(value)
        step.status = "caught"
        self.state = "caught"
        self.message = f"{step.port} = {value}"

    def manual_catch(self, value: int | None = None) -> bool:
        """用户点「已切到（读数不变）」：用上次读数 / 码表旧值确认当前口。

        注意**不能**退到 `start_value`：那是起始口的码，用它确认当前口就是把
        上一口的码记到这一口上（HDMI2 被写成 18 就是这么来的）。真拿不到值就
        返回 False，让界面提示「手工填码」。
        """
        if self.current_step is None or self.state not in ("waiting", "caught"):
            return False
        if value is None:
            value = self._last_value
        if value is None:
            value = self.profile.value(self.current_step.port)
        if value is None:
            return False
        self._catch(int(value))
        return True

    def mark_switched_back(self, now: float = 0.0) -> None:
        """自动切回起始口之后调用，进入下一个口。"""
        step = self.current_step
        if step is not None:
            step.status = "done"
        self.index += 1
        self._reset_stability()
        if self.index >= len(self.steps):
            self.state = "done"
            self.message = "标定完成"
            return
        self.steps[self.index].status = "active"
        self.state = "waiting"
        self.message = ""

    def skip(self, note: str = "用户跳过") -> None:
        """这个口没信号 / 用户不想标，保留原值（若有）但不写新值。"""
        step = self.current_step
        if step is not None:
            step.status = "skipped"
            step.note = note
        self.index += 1
        self._reset_stability()
        if self.index >= len(self.steps):
            self.state = "done"
            self.message = "标定完成（有端口被跳过）"
            return
        self.steps[self.index].status = "active"
        self.state = "waiting"

    def retry(self) -> None:
        """重试当前口。"""
        if self.current_step is None:
            return
        self.current_step.status = "active"
        self.state = "waiting"
        self.message = ""
        self._reset_stability()

    def fail_current(self, note: str = "读不到这个口") -> None:
        if self.current_step is not None:
            self.current_step.status = "failed"
            self.current_step.note = note
        self.state = "waiting"
