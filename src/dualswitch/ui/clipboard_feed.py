"""剪贴板记录源：**没有同步引擎时的本机实现**。

正常情况下 `ui/clipboard_page.py` 的数据来自 `sync/engine.py` 的 `SyncEngine`。
但引擎可能起不来（配置里关了同步、本机没有可用的剪贴板后端、配对还没做），
那时页面不该变成一个死入口 —— 这里用 `clipboard` 后端把「本机复制过什么」
记成环形缓冲，让剪贴板页照样有真数据可看。

接口刻意与 `SyncEngine` 完全一致（`records / status / copy_to_local / repush /
drop_record / clear_records`），`ClipRecord` 也直接用引擎那个 dataclass，
所以 runner 里换实现不用改页面、也不用改测试。

差别只有一处：没有对端，所以每条记录的状态都是 `local`（仅本机）、
`status()["authenticated"]` 恒为 `"no"`、标题显示「未连接」—— 这是实话，不是占位。

三条规则：
  · 只在内存里留最近 `capacity` 条，退出即清（不落库：落盘就要处理加密和清理）
  · 敏感内容不记录 —— 由后端按 `SKIP_MARKERS_WINDOWS` 拦掉，这一层不用管
  · 写回本机（`copy_to_local`）走同一个后端，`write()` 的自写回弹由后端自己吞
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

from ..clipboard import ClipItem, ClipboardError, create_backend
from ..sync.engine import ClipRecord

log = logging.getLogger("dualswitch.ui.clipboard")

# 没有配置对象时给页面的兜底：`psk` 为空 → 页面按「还没配对」显示空态文案
_NO_CFG = SimpleNamespace(psk="", record_limit=10)


class LocalClipFeed:
    """把本机剪贴板变成一串 `ClipRecord`（最新的在前）。"""

    def __init__(self, cfg=None, backend=None, capacity: int | None = None):
        self.cfg = getattr(cfg, "clipboard", cfg) if cfg is not None else _NO_CFG
        if capacity is None:
            capacity = int(getattr(self.cfg, "record_limit", 10) or 10)
        self.capacity = max(1, capacity)
        if backend is None:
            backend = create_backend("auto")
        self.backend = backend
        self._records: list[ClipRecord] = []
        self._items: dict[str, ClipItem] = {}

    # ------------------------------------------------------------------ 采集
    @property
    def available(self) -> bool:
        return getattr(self.backend, "name", "none") != "none"

    def poll(self) -> ClipRecord | None:
        """读一次剪贴板。由 runner 的定时器驱动（页面关着也要记）。"""
        if not self.available:
            return None
        try:
            item = self.backend.poll()
        except Exception:  # noqa: BLE001 - 剪贴板坏了不能连累界面
            log.warning("读取剪贴板失败", exc_info=True)
            return None
        if item is None:
            return None
        return self._add(item)

    def _add(self, item: ClipItem) -> ClipRecord:
        record = self._to_record(item)
        self._records.insert(0, record)
        self._items[record.id] = item
        # 环形缓冲：超出容量的旧记录（连同它的原图）一起丢掉，别让内存慢慢涨
        for dropped in self._records[self.capacity:]:
            self._items.pop(dropped.id, None)
        del self._records[self.capacity:]
        return record

    def _to_record(self, item: ClipItem) -> ClipRecord:
        image = item.image
        kind = "image" if image is not None else "text"
        return ClipRecord(
            id=item.digest,
            kind=kind,
            text=item.text,
            png=image.png if image is not None else None,
            width=image.width if image is not None else 0,
            height=image.height if image is not None else 0,
            source="local",
            peer_name="",
            ts=item.ts or time.time(),
            state="local",              # 没有对端：只在本机
            progress=0.0,
            size=item.wire_size(),
        )

    # ------------------------------------------------------------------ 查询
    def records(self) -> list[ClipRecord]:
        return list(self._records)

    def record(self, record_id: str) -> ClipRecord | None:
        for rec in self._records:
            if rec.id == record_id:
                return rec
        return None

    def item(self, record_id: str) -> ClipItem | None:
        return self._items.get(record_id)

    def status(self) -> dict[str, str]:
        """字段与 `SyncEngine.status()` 对齐，页面只读其中几个。"""
        return {
            "enabled": "yes" if self.available else "no",
            "engine": "local",          # 没有同步层，只有本机
            "backend": getattr(self.backend, "name", "none"),
            "device": "-",
            "psk": "yes" if getattr(self.cfg, "psk", "") else "no",
            "peer": "-",
            "peer_device": "-",
            "authenticated": "no",      # 没有对端，永远连不上
            "records": str(len(self._records)),
        }

    # ------------------------------------------------------------------ 动作
    def copy_to_local(self, record_id: str) -> bool:
        """把一条记录写回本机剪贴板。"""
        item = self._items.get(record_id)
        if item is None:
            return False
        try:
            self.backend.write(item)
        except ClipboardError as exc:
            log.warning("复制到本机失败：%s", exc)
            return False
        except Exception:  # noqa: BLE001
            log.warning("复制到本机失败", exc_info=True)
            return False
        return True

    def repush(self, record_id: str) -> None:
        """重发。没有对端时等同「复制到本机」—— 至少让用户能立刻粘贴。"""
        self.copy_to_local(record_id)

    def drop_record(self, record_id: str) -> None:
        self._records = [r for r in self._records if r.id != record_id]
        self._items.pop(record_id, None)

    def clear_records(self) -> None:
        self._records.clear()
        self._items.clear()

    def close(self) -> None:
        closer = getattr(self.backend, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
