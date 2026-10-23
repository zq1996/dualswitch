"""切换编排：热键触发 → 工作线程盲发 Set → 异步回读。

线程模型（保证 ≤200ms 的指令发出延迟，且热键回调永不阻塞）：
   热键线程  →  trigger()      只做去抖 + 入队（微秒级）
   工作线程  →  set_input()    单线程串行，避免并发抢显示器句柄
   回读线程  →  get_input()    发完就 fork，不阻塞下一次切换
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import logging_setup
from .config import SOURCE_UNKNOWN, Config
from .ddc.base import DDCError, DisplayBackend

log = logging_setup.get("app")

Notifier = Callable[[str, str], None]  # (标题, 正文)


@dataclass
class SwitchResult:
    ok: bool
    target: str
    value: int
    source: str = "hotkey"
    send_latency_ms: float = 0.0   # 触发 → Set 调用发出
    set_call_ms: float = 0.0       # Set 调用本身耗时
    error: str | None = None
    readback: str = "pending"      # pending / confirmed / unconfirmed / skipped
    readback_detail: str = ""
    dry_run: bool = False
    via: str = "local"             # local / remote（remote = 对端代发，本机没有可读的对象）
    at: datetime = field(default_factory=datetime.now)

    def summary(self) -> str:
        if not self.ok:
            return f"切换到 {self.target} 失败：{self.error}"
        if self.dry_run:
            return f"[演练] 本应切换到 {self.target} (VCP 0x60={self.value})，未发送指令"
        if self.via == "remote":
            return (
                f"已请对端代发切换 → {self.target} (VCP 0x60={self.value})，"
                f"往返 {self.set_call_ms:.0f}ms"
            )
        return (
            f"已发送切换指令 → {self.target} (VCP 0x60={self.value})，"
            f"发出延迟 {self.send_latency_ms:.1f}ms，调用耗时 {self.set_call_ms:.1f}ms"
        )


class SwitchController:
    def __init__(
        self,
        cfg: Config,
        backend: DisplayBackend,
        notifier: Notifier | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.cfg = cfg
        self.backend = backend
        self._notify = notifier or (lambda title, msg: None)
        self._clock = clock
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._worker: threading.Thread | None = None
        self._detector: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._lock = threading.Lock()
        self._last_trigger = float("-inf")
        self._last_set_at = float("-inf")
        self._last_detected: str | None = None
        self._confirm = 0
        self._current_source = cfg.effective_host()
        # 界面用的三态（允许 unknown）。与 `_current_source` 分开：
        # 后者永远只放 windows / mac，给切换逻辑算目标用，不能被 unknown 污染。
        # 拆开之后「对端在线但本机看不到屏」也能如实显示成 mac，
        # 而不是被 `_ever_seen_monitor` 一刀切成 unknown。
        self._display_source = SOURCE_UNKNOWN
        # 是否「看过一眼」/「这次运行里到底见过外接屏」——决定能不能把看不见解释成在另一台
        self._source_checked = False
        self._ever_seen_monitor = False
        self.history: list[SwitchResult] = []

        # 切屏前的钩子：剪贴板同步在这里把"本机当前剪贴板"入队（必须极快返回）。
        # 放在 _execute 开头而不是 trigger() 里，是为了让托盘菜单 / CLI 的切换也带上同步。
        self.on_before_switch: list[Callable[..., None]] = []

        # 远端代发切屏（ISSUE-002）：本机打不到这块屏时，请对端替我们发 DDC Set。
        # 由 CLI 在引擎起来后注入；没注入 / 对端没连上时行为与以前完全一致。
        self.remote_switch: Callable[[int], tuple[bool, str]] | None = None
        self.remote_ready: Callable[[], bool] | None = None
        # 对端「我能不能枚举到这块屏」（T_STATE 0x32 的心跳结果）。
        # 返回 True / False / None（None = 问不出来，当没有这条证据）。
        self.peer_sees_monitor: Callable[[], bool | None] | None = None

        # 外部切换（例如在 Mac 上执行 CLI）不会经过本进程，
        # 只能靠「显示器还在不在本机」反推画面归属，见 detect_source()
        self.detect_interval_s = 2.0   # 轮询间隔
        # 自己刚切完的静默期，等显示器真正切走/切回。
        # 原来 8 秒：Set 发出去后界面立刻按目标态显示（乐观更新），而这段时间
        # 完全不检测，于是「切了但屏没动」要等 8 秒才被纠正 —— 用户看到的就是
        # 「明明屏还在本机，界面却说在 Mac」。Q2790PC 实测切换 1~2 秒完成，
        # 留 3 秒足够，配合下面的两轮确认，总延迟从 10 秒降到 5 秒左右。
        self.detect_settle_s = 3.0
        # 两台都打不到这块屏时（拔线 / 关电 / 对端关机）作废「见过」证据，
        # 否则会一直把「枚举不到」解释成「画面在对面」——见 _note_no_monitor()
        self._monitor_absent = False

    def set_notifier(self, notifier: Notifier) -> None:
        """托盘就绪后再挂上通知通道。"""
        self._notify = notifier

    def add_before_switch(self, hook: Callable[[str, str], None]) -> None:
        """挂一个切屏前钩子，签名 `(source, target)`。

        钩子里**绝对不能阻塞**：剪贴板同步的实现只是 set 一个事件就返回。
        钩子抛异常只记日志，绝不影响切屏本身。
        """
        self.on_before_switch.append(hook)

    def _run_before_switch(self, source: str, target: str) -> None:
        for hook in list(self.on_before_switch):
            try:
                hook(source, target)
            except Exception:  # noqa: BLE001 - 同步坏了不能连累切屏
                log.exception("切屏前钩子执行失败")

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._loop, name="switch-worker", daemon=True)
        self._worker.start()
        self._detector = threading.Thread(target=self._detect_loop, name="source-detector", daemon=True)
        self._detector.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker:
            self._worker.join(timeout=2.0)
        if self._detector:
            self._detector.join(timeout=2.0)

    # ------------------------------------------------------------------ 触发
    def trigger(self, source: str = "hotkey") -> bool:
        """热键回调里调用：必须极快返回。返回 False 表示被去抖丢弃。"""
        now = self._clock()
        with self._lock:
            gap = (now - self._last_trigger) * 1000
            if gap < self.cfg.debounce_ms:
                log.debug("去抖丢弃一次触发（间隔 %.0fms < %dms）", gap, self.cfg.debounce_ms)
                return False
            self._last_trigger = now
        try:
            self._queue.put_nowait((now, source))
        except queue.Full:
            log.warning("上一次切换尚未完成，忽略本次触发")
            return False
        return True

    def switch_now(self, target: str | None = None, source: str = "cli") -> SwitchResult:
        """同步执行一次切换（CLI / 托盘菜单用）。"""
        return self._execute(self._clock(), source, forced_target=target)

    # ------------------------------------------------------------------ 外部状态检测
    def detect_source(self) -> str | None:
        """按「显示器还在不在本机」推断画面现在属于哪台机器。

        显示器只会在它当前选中的输入端被枚举到：
          · 能枚举到这台屏 → 画面在本机
          · 枚举不到，但这次运行里见过它 → 画面在另一台
          · 枚举不到，而且从来没在这台机器上见过它 → `SOURCE_UNKNOWN`

        第三条是 ISSUE-001：以前只有前两条，于是「没接显示器」被硬说成
        「画面在 Windows」。返回值 None 仍然表示这一轮判断不了（枚举异常）。
        """
        try:
            monitors = self.backend.list_monitors()
        except Exception as exc:  # 枚举失败不代表画面变了，直接放弃这一轮
            log.debug("检测画面归属失败：%s", exc)
            return None
        return self.classify(monitors)

    def classify(self, monitors: list) -> str:
        """给定一次枚举结果，得出画面归属（纯判定，测试直接打这里）。

        「枚举不到」只说明这块屏**不在本机**，它要么在对端、要么根本没接线。
        区分这两者需要第二证据，原来只有一条（`_ever_seen_monitor`），
        而它在冷启动时天然拿不到：应用启动的瞬间屏若在对端，本机从来没见过它，
        于是整个会话都停在 unknown —— 界面永远显示「未检测到外接显示器」，
        哪怕画面明明就在对面。这就是「归属判断一半对一半错」的来源。

        现在补第二条证据：**对端在线且已配对**。两台机器只共用一块屏，
        对端在线就意味着屏在它那边的概率远高于「没接线」，冷启动也能判。
        对端离线时仍回落到 `_ever_seen_monitor`，保持 ISSUE-001 的保守口径。
        """
        needle = (self.cfg.monitor_match or "").strip()
        present = any(m.matches(needle) for m in monitors) if needle else bool(monitors)
        with self._lock:
            self._source_checked = True
            if present:
                self._ever_seen_monitor = True
                self._monitor_absent = False     # 又看到屏了，之前的「不在任何一台」作废
            ever_seen = self._ever_seen_monitor
            absent = self._monitor_absent
        if present:
            return self.cfg.effective_host()
        peer = self._peer_sees()
        if peer is True:
            # 最硬的证据：对端亲口说它枚举得到这块屏 —— 那画面一定在它那边。
            # 冷启动屏在对端时，这条能直接把 unknown 变成确定答案。
            return self.cfg.other_host()
        if absent:
            # 本机打不到、对端也打不到过：这块屏此刻不在任何一台机器上（拔线/关电）。
            # 不能因为「见过」就继续说是「在对面」——那是个看起来确定的假答案。
            return SOURCE_UNKNOWN
        if peer is False:
            # 两台都枚举不到：屏不在任何一台（拔线 / 关电 / 对端也看不到）。
            return SOURCE_UNKNOWN
        if ever_seen or self._remote_alive():
            return self.cfg.other_host()
        return SOURCE_UNKNOWN

    def _peer_sees(self) -> bool | None:
        """对端「看不看得到这块屏」。没注入 / 老版本 / 链路断 → None。"""
        probe = self.peer_sees_monitor
        if probe is None:
            return None
        try:
            return probe()
        except Exception:  # noqa: BLE001 - 状态查询不该影响判定
            return None

    def _remote_alive(self) -> bool:
        """对端是否在线且已配对（能替本机发 Set 的状态）。

        没注入 `remote_ready` 时（CLI 单机、大部分单测）一律 False，
        判定行为与加这条证据之前完全一致 —— 这是改动向后兼容的前提。
        """
        probe = self.remote_ready
        if probe is None:
            return False
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 - 状态查询不该影响判定
            return False

    def _note_no_monitor(self, detail: str = "") -> None:
        """记下「这台屏此刻不在任何一台机器上」的结论（拔线 / 关电 / 对端关机）。

        只在「本地 DDC 打不到 **且** 对端在线却没接住」时调用：两个方向都试过了，
        才有资格推翻 `_ever_seen_monitor`。下次枚举到屏时 `classify()` 自动清除。
        """
        with self._lock:
            self._monitor_absent = True
        log.warning("本地与对端都打不到这块屏，归属退回 unknown（%s）", detail or "-")

    def note_scan(self, monitors: list) -> None:
        """把一次只读探测的结果喂进来，省掉「先猜一个再纠正」的闪烁。

        可重复调用：启动时先喂一次（此时同步引擎还没挂上，`remote_ready` 为空），
        引擎连上后再喂一次，冷启动「屏在对端」的情形才能立刻判对，而不是等轮询。
        """
        detected = self.classify(monitors)
        with self._lock:
            self._display_source = detected if detected is not None else SOURCE_UNKNOWN
            if detected not in (None, SOURCE_UNKNOWN):
                self._current_source = detected

    @property
    def source_state(self) -> str:
        """界面该显示的状态：`windows` / `mac` / `SOURCE_UNKNOWN`。

        与 `current_source` 的区别：后者永远是某一台机器（切换时用它算目标），
        前者允许「不知道」。判定由 `classify()` 得出后写进 `_display_source`，
        这里只负责读 —— 不再拿 `_ever_seen_monitor` 当门槛，否则冷启动屏在
        对端时会永远显示「未检测到外接显示器」。
        """
        with self._lock:
            return self._display_source

    def _detect_loop(self) -> None:
        """后台轮询：外部（另一台机器上的 CLI / 显示器 OSD）切换后同步状态。"""
        while True:
            self._detect_once()
            if self._stop_event.wait(self.detect_interval_s):
                return

    def _detect_once(self) -> None:
        if self.cfg.dry_run:                       # 演练模式不碰真实状态
            return
        if time.monotonic() - self._last_set_at < self.detect_settle_s:
            return                                 # 自己刚切完，等显示器真正切走/切回
        detected = self.detect_source()
        if detected is None:
            self._last_detected = None
            self._confirm = 0
            return
        if detected == SOURCE_UNKNOWN:
            # 没有可归因的画面：不往 _current_source 里写（那里只放 windows / mac），
            # 界面通过 source_state 自己显示「未检测到外接显示器」
            self._last_detected = SOURCE_UNKNOWN
            self._confirm = 1
            with self._lock:
                self._display_source = SOURCE_UNKNOWN
            return
        # 连续两轮结果一致才认：显示器消失/出现都有滞后，单轮容易误判
        if detected == self._last_detected:
            self._confirm += 1
        else:
            self._last_detected = detected
            self._confirm = 1
        if self._confirm < 2:
            return
        with self._lock:
            # 界面态先落，即使 _current_source 没变（例如初值就是 windows）也要显示出来
            self._display_source = detected
            if detected == self._current_source:
                return
            self._current_source = detected
            # 重新看到屏，说明它确实存在（曾经因为「从未见过」被当成 unknown）
            self._ever_seen_monitor = True
        log.info(
            "检测到画面已在 %s（显示器%s），已同步内部状态",
            detected,
            "接回本机" if detected == self.cfg.effective_host() else "离开本机",
        )

    # ------------------------------------------------------------------ 内部
    def _resolve_target(self, forced: str | None = None) -> str:
        if forced in ("windows", "mac"):
            return forced
        if forced == "toggle":
            return self.cfg.other_host() if self._current_source == self.cfg.effective_host() else self.cfg.effective_host()
        if self.cfg.switch_mode == "toggle":
            return self.cfg.other_host() if self._current_source == self.cfg.effective_host() else self.cfg.effective_host()
        return self.cfg.other_host()

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            t0, source = item
            try:
                self._execute(t0, source)
            except Exception:  # 任何异常都不能让常驻进程挂掉
                log.exception("切换过程中出现未捕获异常")

    def _execute(self, t0: float, source: str, forced_target: str | None = None) -> SwitchResult:
        target = self._resolve_target(forced_target)
        value = self.cfg.target_value(target)
        t_send = self._clock()
        result = SwitchResult(
            ok=False,
            target=target,
            value=value,
            source=source,
            send_latency_ms=(t_send - t0) * 1000.0,
        )

        if self.cfg.dry_run:
            result.ok = True
            result.dry_run = True
            result.readback = "skipped"
            result.set_call_ms = 0.0
            log.info("[dry-run] 本应发送 Set VCP 0x%02X = %d（目标 %s）", self.cfg.vcp_code, value, target)
            self._record(result)
            return result

        # 先读剪贴板再切屏（契约 2）：钩子只入队，实测 <1ms，不影响 200ms 目标
        self._run_before_switch(source, target)

        # 切换模式（设置页「切换模式」）：standard = 本地优先、失败才请对端；
        # force = 跳过远端代发，直接本机发 Set（用户明确要「不绕路」）。
        policy_force = getattr(self.cfg, "switch_policy", "standard") == "force"

        # 从没枚举到过外接屏：本地肯定打不到这块屏，先看对端能不能代发。
        # 对端也没有（没连上 / 版本太旧）才说清楚失败原因 ——
        # m1ddc 的原文 "Could not find a suitable external display" 用户看不懂。
        if (
            forced_target is None
            and self._source_checked
            and not self._ever_seen_monitor
            and not policy_force
        ):
            if self._try_remote(result, target, value):
                return result
            result.error = "未检测到外接显示器（可能没接线，或画面在 Windows 那台）"
            log.warning("跳过切换：本次运行还没有枚举到过外接屏，对端也不可用")
            self._record(result)
            return result

        try:
            self.backend.set_input(value)
            result.ok = True
            result.set_call_ms = (self._clock() - t_send) * 1000.0
        except DDCError as exc:
            # 本地打不到（屏在另一台）→ 请对端代发一次；成功就不再报错
            remote_tried = self._remote_alive()
            if not policy_force and self._try_remote(
                result, target, value, local_error=str(exc)
            ):
                return result
            # 本地打不到 + 对端在线却没接住 ⇒ 这块屏此刻不在任何一台机器上。
            # 作废「见过」证据，让界面退回 unknown，而不是继续显示「在对面」。
            if remote_tried:
                self._note_no_monitor(str(exc))
            result.set_call_ms = (self._clock() - t_send) * 1000.0
            result.error = str(exc)
            log.error("切换失败（目标 %s / 值 %d）：%s", target, value, exc)
            self._record(result)
            if self.cfg.notify:
                self._notify("切换失败", f"未能把显示器切到 {target}。\n{exc}")
            return result
        except Exception as exc:  # 兜底，避免驱动层抛出奇怪异常
            result.set_call_ms = (self._clock() - t_send) * 1000.0
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("切换时出现意外异常")
            self._record(result)
            return result

        self._last_set_at = time.monotonic()
        with self._lock:
            self._current_source = target
            # 指令已经发到这块屏上了：之后「看不到它」才等于「被切走了」，
            # 而不是「压根没接线」
            self._source_checked = True
            self._ever_seen_monitor = True
            self._monitor_absent = False     # 指令确实发出去了，屏还在
            self._display_source = target    # 界面立刻按目标态显示（乐观），轮询负责纠偏

        log.info("%s", result.summary())
        if result.send_latency_ms > 200:
            log.warning("指令发出延迟 %.1fms 超过 200ms 目标", result.send_latency_ms)

        self._record(result)

        if self.cfg.readback.enabled:
            threading.Thread(
                target=self._readback, args=(result,), name="readback", daemon=True
            ).start()
        else:
            result.readback = "skipped"
        return result

    def _try_remote(
        self, result: SwitchResult, target: str, value: int, local_error: str = ""
    ) -> bool:
        """请对端代发一次 Set。成功则把 result 补全并记录，返回 True。

        失败只写日志（不覆盖 result.error），让调用方决定报哪个原因 ——
        本地错和对端错同时存在时，本地错更有信息量（比如"没找到显示器"）。
        """
        fn = self.remote_switch
        if fn is None:
            return False
        if self.remote_ready is not None:
            try:
                if not self.remote_ready():
                    return False
            except Exception:  # noqa: BLE001 - 状态查询不该影响切换
                return False
        started = self._clock()
        try:
            ok, detail = fn(value)
        except Exception as exc:  # noqa: BLE001
            log.warning("远端代发切屏异常：%s", exc)
            return False
        if not ok:
            log.warning(
                "远端代发切屏失败：%s（本地原因：%s）", detail or "对端拒绝", local_error or "-"
            )
            return False

        result.ok = True
        result.via = "remote"
        result.readback = "skipped"        # 屏不在本机，没有可回读的对象
        result.readback_detail = "由对端执行"
        result.set_call_ms = (self._clock() - started) * 1000.0
        result.error = None
        self._last_set_at = time.monotonic()
        with self._lock:
            self._current_source = target
            self._source_checked = True
            self._ever_seen_monitor = True
            self._monitor_absent = False
            self._display_source = target    # 对端替我们切了，界面同样按目标态显示
        log.info("%s", result.summary())
        self._record(result)
        return True

    def _readback(self, result: SwitchResult) -> None:
        rb = self.cfg.readback
        deadline = time.monotonic() + rb.timeout_ms / 1000.0
        time.sleep(rb.delay_ms / 1000.0)
        last_detail = "无读数"
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            try:
                current = self.backend.get_input()
                if current == result.value:
                    result.readback = "confirmed"
                    result.readback_detail = f"VCP 0x{self.cfg.vcp_code:02X} = {current}（{attempts} 次尝试）"
                    log.info("回读确认：%s", result.readback_detail)
                    return
                last_detail = f"读回 {current}，期望 {result.value}"
            except DDCError as exc:
                last_detail = f"读取失败：{exc}"
            except Exception as exc:  # pragma: no cover
                last_detail = f"读取异常：{exc}"
            time.sleep(rb.interval_ms / 1000.0)

        result.readback = "unconfirmed"
        result.readback_detail = last_detail
        # 切走之后显示器已从本机消失，回读失败属于预期；按文档只记录，不打扰
        log.info("回读未确认（%s）；Q2790PC 的 HDMI 通道读数已知不可靠，仅记录", last_detail)
        if rb.notify_on_unconfirmed and self.cfg.notify:
            self._notify("切换指令已发送", "显示器未确认，详见日志。")

    def _record(self, result: SwitchResult) -> None:
        with self._lock:
            self.history.append(result)
            if len(self.history) > 50:
                del self.history[:-50]

    @property
    def current_source(self) -> str:
        with self._lock:
            return self._current_source
