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
from .ddc.base import DDCError, DisplayBackend, probe_active_monitors

log = logging_setup.get("app")

Notifier = Callable[[str, str], None]  # (标题, 正文)

# 回读读到目标值后，隔多久复读一次以排除「切了又被抢回」。
# Q2790PC 实测：Set 18 后 1 秒内回读是 18，3 秒后画面仍在 HDMI。
REBOUND_CHECK_DELAY_S = 1.2


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
        # 回读代数：每启动一个回读线程 +1，线程发现自己是旧代就提前收工。
        # 没有它，连点 N 次「本机发送」就会堆 N 个回读线程同时打显示器——
        # 200ms 一发的 Get 风暴会把下一次 Set 搅掉（2026-10-24 实测「要点好几次」）。
        self._readback_gen = 0
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
        self.detect_interval_s = 2.0   # 轮询间隔（仅用于无直读后端的枚举推断）
        # DDC 直读**不做后台轮询**（0 = 关闭）。这台 AOC 的 DDC 承受不了持续
        # I2C 流量：2s 轮询把「本机发送」的 Set 搅掉，30s 也仍是纯开销——
        # 归属只有一个展示行在用，切换动作全靠显式通道 + 发号前即时直读。
        # 按需读的时机：启动 note_scan、_execute 发号前、回读确认、
        # runner 在窗口回前台 / 切到切换页时调 refresh_owner_now()。
        # 代价（接受）：OSD 手动切输入后，展示行要等下一次交互才刷新。
        self.read_poll_interval_s = 0.0
        self._last_read_poll = float("-inf")
        # 自己刚切完的静默期，等显示器真正切走/切回。
        # ⚠️ 必须大于**显示器完成切换的时间**：Q2790PC 实测一次切换要 2–3 秒，
        # 静默期太短会在「切换进行中」去枚举，于是把「还在切换」误判成
        # 「没切成功」。曾经改成 3 秒就是这个坑（刚好卡在切换完成边缘）。
        # 留 6 秒 = 2~3 秒切换 + 两轮确认的余量。
        self.detect_settle_s = 6.0
        # 两台都打不到这块屏时（拔线 / 关电 / 对端关机）作废「见过」证据，
        # 否则会一直把「枚举不到」解释成「画面在对面」——见 _note_no_monitor()
        self._monitor_absent = False
        # 上一次远端代发失败的原因（空串 = 上次成功或还没试过）。
        # 失败时拼进 result.error，界面 / 日志才说得清「到底卡在哪一环」。
        self._last_remote_error = ""

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

    def host_of_value(self, value: int) -> str:
        """VCP 0x60 的取值反查主机名；配置里没有的取值返回空串。"""
        for host, val in self.cfg.sources.items():
            try:
                if int(val) == int(value):
                    return host
            except (TypeError, ValueError):
                continue
        return ""

    # ------------------------------------------------------------------ 显式通道
    def set_input_local(self, value: int, source: str = "ui") -> SwitchResult:
        """显式通道①：**只让本机**发一条 DDC Set（界面「本机发送 17/18」）。

        与 `switch_now()` 的区别：不看归属、不算目标、不自动请对端。
        理由见 docs/SCREEN-OWNERSHIP.md §3.2 —— 本机的 `set_input` 会「返回 0 但没生效」
        （假成功），把它和代发混成一条自动链路时，「到底哪条起了作用」就查不出来了。
        """
        return self._do_local(int(value), source)

    def switch_remote(self, value: int, source: str = "ui") -> SwitchResult:
        """显式通道②：**只请对端**发一条 DDC Set（界面「请对端发送 17/18」）。"""
        return self._do_remote(int(value), source)

    # ------------------------------------------------------------------ 外部状态检测
    def _monitors(self) -> list:
        """枚举「画面在本机」的判据来源：优先活动信号，后端不支持时退回普通枚举。

        macOS 的 `m1ddc display list` 在屏切走之后照样列得出这块屏（DCP 代理还在），
        用它判断归属会让本机永远「看到」屏，见 `ddc.macos.active_monitors`。
        """
        return probe_active_monitors(self.backend)

    def _picture_here(self) -> bool | None:
        """这块屏此刻是不是在本机输出画面。None = 后端分不出「挂着」和「在用」。"""
        probe = getattr(self.backend, "active_monitors", None)
        if not callable(probe):
            return None
        try:
            active = probe()
        except Exception as exc:  # noqa: BLE001 - 探测失败只影响优化路径
            log.debug("活动显示器探测失败：%s", exc)
            return None
        if active is None:
            return None
        needle = (self.cfg.monitor_match or "").strip()
        return any(m.matches(needle) for m in active) if needle else bool(active)

    def _read_host(self) -> str | None:
        """显示器亲口报当前输入（DDC 直读 VCP 0x60），反查成主机名。

        2026-10-24 破案后这是**最硬**的证据：m1ddc 读失败是它自己的校验和
        bug，绕开之后 Q2790R3 在画面切到对端时照样能读到 17/18（DP AUX
        链路不随画面切换断）。之前的 phantom 屏推断链全是为「读不了」绕的弯，
        直读可用时应优先于一切枚举推断。

        返回 None = 后端不支持 / 读失败 / 值不认识（比如屏被 OSD 切到了
        第三个输入），调用方回落枚举推断。
        """
        if not getattr(self.backend, "supports_ddc_detect", False):
            return None
        try:
            value = int(self.backend.get_input())
        except Exception as exc:  # noqa: BLE001 - 读失败只是少一条证据
            log.debug("DDC 直读当前输入失败：%s", exc)
            return None
        return self.host_of_value(value) or None

    def _commit_read_host(self, host: str) -> None:
        """把直读结果落进状态。直读是即时真值，不需要两轮确认。"""
        with self._lock:
            self._source_checked = True
            self._monitor_absent = False      # 答得上话，显示器肯定活着
            self._ever_seen_monitor = True
            changed = host != self._display_source
            self._display_source = host
            self._current_source = host
        if changed:
            log.info("显示器直报当前输入 → 画面在 %s（DDC 直读 0x60）", host)

    def refresh_owner_now(self, min_gap: float = 1.0) -> None:
        """按需直读一次归属（界面回前台 / 切到切换页时由 runner 调，放后台线程）。

        min_gap 防手抖连点页面造成读风暴——这台显示器的 DDC 经不起打扰。
        """
        with self._lock:
            now = time.monotonic()
            if now - self._last_read_poll < min_gap:
                return
            self._last_read_poll = now
        host = self._read_host()
        if host is not None:
            self._commit_read_host(host)

    def detect_source(self) -> str | None:
        """推断画面现在属于哪台机器：**先直读，读不了再走枚举推断**。

        直读（`_read_host`）可靠时一步到位；读不了才退回老三样——

        显示器只会在它当前选中的输入端被枚举到：
          · 能枚举到这台屏 → 画面在本机
          · 枚举不到，但这次运行里见过它 → 画面在另一台
          · 枚举不到，而且从来没在这台机器上见过它 → `SOURCE_UNKNOWN`

        第三条是 ISSUE-001：以前只有前两条，于是「没接显示器」被硬说成
        「画面在 Windows」。返回值 None 仍然表示这一轮判断不了（枚举异常）。
        """
        host = self._read_host()
        if host:
            return host
        try:
            monitors = self._monitors()
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

        直读可用时显示器一句话就说清归属，`monitors` 只作读失败时的兜底。
        """
        detected = self._read_host()
        if not detected:
            detected = self.classify(monitors)
        else:
            with self._lock:
                self._source_checked = True
                self._monitor_absent = False
                self._ever_seen_monitor = True
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
        # 直读后端：**不做后台轮询**（read_poll_interval_s=0 即关闭；留这个旋钮
        # 只为将来换一台 DDC 结实的显示器时可选开）。也绝不跑枚举推断——
        # phantom 屏会让枚举恒判「画面在本机」（2026-10-24 实测 8 秒后就误报过）。
        # 归属的真值全部来自按需读：note_scan / _execute 发号前 / 回读 / 界面回前台。
        if getattr(self.backend, "supports_ddc_detect", False):
            if self.read_poll_interval_s > 0:
                now = time.monotonic()
                if now - self._last_read_poll >= self.read_poll_interval_s:
                    self._last_read_poll = now
                    host = self._read_host()
                    if host is not None:
                        self._commit_read_host(host)
            return
        # 无直读能力的后端（mock / Windows）才走枚举推断
        try:
            monitors = self._monitors()
        except Exception as exc:  # 枚举失败不代表画面变了，直接放弃这一轮
            log.debug("检测画面归属失败：%s", exc)
            self._last_detected = None
            self._confirm = 0
            return
        detected = self.classify(monitors)
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
        """算出这次该把画面切到哪台。

        **画面在哪台，就切到另一台** —— 界面按钮写的「切换到 Mac / Windows」、
        托盘菜单、热键说的都是这个意思。以前这里在 `switch_mode=other` 时
        无条件返回 `other_host()`，于是 Mac 端永远只会把画面「送出去」、
        永远叫不回来：按钮写着「切换到 Mac」，实际发的是 Windows 的 17，
        屏一点动静都没有（ISSUE-003 的根因之一）。

        `switch_mode` 只在**判不出画面归属**时起作用：
        `other` = 送到另一台；`toggle` = 按上一次的认知翻。
        """
        if forced in ("windows", "mac"):
            return forced
        host = self.cfg.effective_host()
        if forced == "other":
            return self.cfg.other_host()
        state = self._display_source
        if state in ("windows", "mac"):
            return self.cfg.other_host() if state == host else host
        if forced == "toggle" or self.cfg.switch_mode == "toggle":
            return self.cfg.other_host() if self._current_source == host else host
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
        # 发号前即时直读一次归属（显示器亲口报）——热键「切到另一台」的方向
        # 就靠它，不依赖 30s 低频轮询的新鲜度。显式指定目标时不必读。
        if (
            not self.cfg.dry_run
            and forced_target is None
            and getattr(self.backend, "supports_ddc_detect", False)
        ):
            host = self._read_host()
            if host is not None:
                self._commit_read_host(host)
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

        # 屏此刻在不在本机输出画面（None = 后端分不出「挂着」和「在用」）。
        # 这一个判据决定下面两条路，见 _picture_here() 上的注释。
        picture_here = None if policy_force else self._picture_here()

        # 屏不在本机、而目标正是「把画面叫回来」⇒ 请对端代发兜底。
        # **修正（2026-10-24 实测，SCREEN-OWNERSHIP §1.4）**：本机的 DDC 写通道
        # 始终可达——画面在对面时「本机发送」照样一步切回来（DDC/CI 是随线走的
        # 旁路，显示器每个输入口都监听）。旧注释「m1ddc 打在没监听的输入口上
        # 返回 0（假成功）」已被证伪。这里保留代发只是兜底（本机 DDC 栈坏掉时），
        # 不再是「画面在对面时的必经路」。
        wants_here = target == self.cfg.effective_host()
        if picture_here is False and wants_here:
            if self._try_remote(result, target, value):
                return result
            # 屏确实不在本机：本地这条 DDC 事务打不到它，如实报错。
            result.ok = False
            result.set_call_ms = (self._clock() - t_send) * 1000.0
            result.error = f"画面不在本机，请对端代发失败：{self._last_remote_error or '未知原因'}"
            log.error("切换失败（目标 %s / 值 %d）：%s", target, value, result.error)
            self._record(result)
            if self.cfg.notify:
                self._notify("切换失败", f"未能把显示器切到 {target}。\n{result.error}")
            return result

        # 判不出屏在不在本机（后端没有活动信号，例如 Windows / 缺 pyobjc），
        # 而目标又是「把画面叫回来」⇒ **先请对端试一次**，失败再走本地。
        # （2026-10-24 实测：本机 DDC 写通道始终可达，本地这条路本身就够；
        #  先请对端只是省一次本地调用的偏好，不再是「本机发不动」的补救。）
        if picture_here is None and wants_here and not policy_force:
            if self._try_remote(result, target, value):
                return result
            log.info("请对端代发未成功（%s），改回本机发送", self._last_remote_error or "-")

        # 从没枚举到过外接屏：本地肯定打不到这块屏，先看对端能不能代发。
        # 对端也没有（没连上 / 版本太旧）才说清楚失败原因 ——
        # m1ddc 的原文 "Could not find a suitable external display" 用户看不懂。
        # 显式指定目标（托盘「强制切到本机」/ CLI --to）是用户的明确指令，不拦。
        if (
            forced_target is None
            and self._source_checked
            and not self._ever_seen_monitor
            and not policy_force
        ):
            if self._try_remote(result, target, value):
                return result
            result.error = "未检测到外接显示器"
            if self._last_remote_error:
                result.error += f"；请对端代发也失败：{self._last_remote_error}"
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
            if self._last_remote_error:
                # 把代发那一环的失败原因也带上：只报本地错会让人以为「屏没接」，
                # 而真正的原因往往是「对端根本没连上」。
                result.error = f"{exc}；请对端代发也失败：{self._last_remote_error}"
            log.error("切换失败（目标 %s / 值 %d）：%s", target, value, result.error)
            self._record(result)
            if self.cfg.notify:
                self._notify("切换失败", f"未能把显示器切到 {target}。\n{result.error}")
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

    # ------------------------------------------------------------------ 显式通道实现
    def _do_local(self, value: int, source: str) -> SwitchResult:
        """本机发一条 Set（docs/SCREEN-OWNERSHIP.md §3.2 通道①）。

        「返回 0」只写进日志，不当成功证据 —— 生效与否由 `_readback` 判断（§3.3）。
        不碰 `_display_source`：那是「画面在哪」的判断，不是「我刚命令它去哪」。
        """
        t0 = self._clock()
        host = self.host_of_value(value)
        result = SwitchResult(
            ok=False,
            target=host or f"VCP 0x{self.cfg.vcp_code:02X}={value}",
            value=value,
            source=source,
            send_latency_ms=0.0,
        )

        if self.cfg.dry_run:
            result.ok = True
            result.dry_run = True
            result.readback = "skipped"
            log.info("[dry-run] 本应本机发送 Set VCP 0x%02X = %d", self.cfg.vcp_code, value)
            self._record(result)
            return result

        self._run_before_switch(source, host or str(value))
        try:
            self.backend.set_input(value)
            result.ok = True
            result.set_call_ms = (self._clock() - t0) * 1000.0
        except DDCError as exc:
            result.set_call_ms = (self._clock() - t0) * 1000.0
            result.error = str(exc)
            log.error("本机发送失败（VCP 0x%02X=%d）：%s", self.cfg.vcp_code, value, exc)
            self._record(result)
            if self.cfg.notify:
                self._notify("切换失败", f"本机发送 {value} 失败。\n{exc}")
            return result
        except Exception as exc:  # noqa: BLE001 - 驱动层怪异常
            result.set_call_ms = (self._clock() - t0) * 1000.0
            result.error = f"{type(exc).__name__}: {exc}"
            log.exception("本机发送时出现意外异常")
            self._record(result)
            return result

        self._last_set_at = time.monotonic()
        with self._lock:
            if host:
                self._current_source = host      # 给热键的 toggle 用，不代表「画面已到」
            self._source_checked = True
            self._ever_seen_monitor = True
            self._monitor_absent = False

        log.info("%s", result.summary())
        self._record(result)
        if self.cfg.readback.enabled:
            threading.Thread(
                target=self._readback, args=(result,), name="readback", daemon=True
            ).start()
        else:
            result.readback = "skipped"
        return result

    def _do_remote(self, value: int, source: str) -> SwitchResult:
        """只请对端发一条 Set（通道②）。本机一次 DDC 都不发。"""
        started = self._clock()
        host = self.host_of_value(value)
        result = SwitchResult(
            ok=False,
            target=host or f"VCP 0x{self.cfg.vcp_code:02X}={value}",
            value=value,
            source=source,
            via="remote",
        )

        if self.cfg.dry_run:
            result.ok = True
            result.dry_run = True
            result.readback = "skipped"
            log.info("[dry-run] 本应请对端发送 Set VCP 0x%02X = %d", self.cfg.vcp_code, value)
            self._record(result)
            return result

        fn = self.remote_switch
        reason = ""
        if fn is None:
            reason = "本机没有挂载同步引擎（剪贴板同步未启用或启动失败）"
        elif self.remote_ready is not None:
            try:
                ready = bool(self.remote_ready())
            except Exception as exc:  # noqa: BLE001 - 状态查询不该影响切换
                ready = False
                log.warning("远端可用性查询失败：%s", exc)
            if not ready:
                reason = "对端离线或未配对（链路没连上）"

        if not reason:
            try:
                ok, detail = fn(value)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"代发调用异常：{exc}"
            if not ok:
                reason = detail or "对端拒绝"
            else:
                result.ok = True
                result.readback = "skipped"     # 屏不在本机，没有可回读的对象
                result.readback_detail = "由对端执行"
                result.set_call_ms = (self._clock() - started) * 1000.0
                self._last_remote_error = ""
                self._last_set_at = time.monotonic()
                with self._lock:
                    if host:
                        self._current_source = host
                    self._source_checked = True
                    self._ever_seen_monitor = True
                    self._monitor_absent = False
                log.info("%s", result.summary())
                self._record(result)
                return result

        result.set_call_ms = (self._clock() - started) * 1000.0
        result.error = reason
        self._last_remote_error = reason
        log.warning("请对端代发失败（VCP 0x%02X=%d）：%s", self.cfg.vcp_code, value, reason)
        self._record(result)
        if self.cfg.notify:
            self._notify("切换失败", f"请对端发送 {value} 失败。\n{reason}")
        return result

    def _try_remote(
        self, result: SwitchResult, target: str, value: int, local_error: str = ""
    ) -> bool:
        """请对端代发一次 Set。成功则把 result 补全并记录，返回 True。

        失败**一定**写清楚原因（`self._last_remote_error` + warning 日志）。
        以前 `remote_ready()` 为假时静默 `return False`，用户点了切换只看到本地
        那句「未发现物理显示器」，完全不知道真正原因是「对端根本没连上」——
        这是「对端代发不好用」最主要的信息缺口。
        """
        fn = self.remote_switch
        if fn is None:
            self._last_remote_error = "本机没有挂载同步引擎（剪贴板同步未启用或启动失败）"
            log.warning("跳过远端代发：%s", self._last_remote_error)
            return False
        if self.remote_ready is not None:
            try:
                ready = bool(self.remote_ready())
            except Exception as exc:  # noqa: BLE001 - 状态查询不该影响切换
                ready = False
                log.warning("远端可用性查询失败：%s", exc)
            if not ready:
                self._last_remote_error = "对端离线或未配对（链路没连上）"
                log.warning("跳过远端代发：%s", self._last_remote_error)
                return False
        started = self._clock()
        try:
            ok, detail = fn(value)
        except Exception as exc:  # noqa: BLE001
            self._last_remote_error = f"代发调用异常：{exc}"
            log.warning("远端代发切屏异常：%s", exc)
            return False
        if not ok:
            self._last_remote_error = detail or "对端拒绝"
            log.warning(
                "远端代发切屏失败：%s（本地原因：%s）",
                self._last_remote_error,
                local_error or "-",
            )
            return False

        self._last_remote_error = ""
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
        self._readback_gen += 1
        gen = self._readback_gen               # 之后有新切换进来，本线程就是旧代
        deadline = time.monotonic() + rb.timeout_ms / 1000.0
        time.sleep(rb.delay_ms / 1000.0)
        last_detail = "无读数"
        attempts = 0
        while time.monotonic() < deadline:
            if gen != self._readback_gen:
                # 用户又点了一次新的切换：旧回读继续跑只会给显示器添 I2C 噪声，
                # 把新 Set 搅掉。立即收工，确认责任交给最新那一代。
                log.debug("回读（第 %d 代）被更新的切换取代，提前收工", gen)
                return
            attempts += 1
            try:
                current = self.backend.get_input()
                if current == result.value:
                    # 读到目标值先别急着确认：Q2790PC 实测会「先接受、再被自动
                    # 信号源抢回」。只读一次会把假成功报成 confirmed —— 日志里
                    # 21:29:38「回读确认 18」+ 21:29:41「画面仍在 windows」就是这情况。
                    result.readback_detail = (
                        f"VCP 0x{self.cfg.vcp_code:02X} = {current}（{attempts} 次尝试）"
                    )
                    if self._confirm_not_bounced(result, gen):
                        result.readback = "confirmed"
                        log.info("回读确认：%s", result.readback_detail)
                    else:
                        result.readback = "unconfirmed"
                        log.warning("回读回弹（切了又被抢回）：%s", result.readback_detail)
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

    def _confirm_not_bounced(self, result: SwitchResult, gen: int) -> bool:
        """读到目标值后隔一拍复读，确认不是「切了又被显示器抢回」。

        返回 False = 检测到回弹（`readback_detail` 已写明回弹后的读数）。
        复读时读不到屏是**成功**信号（屏真的切走了，本机枚举不到它），
        不能当成失败 —— 否则正常切换会被全部误报成 unconfirmed。
        """
        time.sleep(REBOUND_CHECK_DELAY_S)
        if gen != self._readback_gen:
            return True                     # 已被新切换取代，别再打了，让位
        try:
            again = self.backend.get_input()
        except DDCError:
            result.readback_detail += "；复读时屏已离开本机"
            return True
        except Exception:  # pragma: no cover - 驱动层怪异常按成功处理
            return True
        if again == result.value:
            return True
        result.readback_detail += (
            f"；{REBOUND_CHECK_DELAY_S:.1f}s 后回弹到 {again}（显示器自动信号源？）"
        )
        return False

    def _record(self, result: SwitchResult) -> None:
        with self._lock:
            self.history.append(result)
            if len(self.history) > 50:
                del self.history[:-50]

    @property
    def current_source(self) -> str:
        with self._lock:
            return self._current_source
