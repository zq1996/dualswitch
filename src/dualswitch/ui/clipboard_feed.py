"""剪贴板记录源：同步引擎落地之前的**本机实现**。

`docs/CLIPBOARD-SYNC-CONTRACT.md` 里 M2（`sync/engine.py`）归 macOS 端，还没写。
但页面不能等它：这里用 `clipboard` 后端把「本机复制过什么」记成环形缓冲，
让 `ui/clipboard_page.py` 现在就有真数据可显示。

接口刻意与将来的 `SyncEngine` 对齐（`records / status / copy_to_local / resend /
remove / clear`），所以 runner 那边只需要把 `LocalClipFeed` 换成 engine，
页面一行都不用改。差别只有一处：没有对端，所以每条记录的状态都是 `local`（仅本机），
标题栏也显示「未连接」—— 这是实话，不是占位。

三条规则：
  · 只在内存里留最近 `capacity` 条，退出即清（不落库：落盘就要处理加密和清理）
  · 敏感内容不记录 —— 由后端按 `SKIP_MARKERS_WINDOWS` 拦掉，这一层不用管
  · 写回本机（`copy_to_local`）走同一个后端，`write()` 的自写回弹由后端自己吞
"""

from __future__ import annotations

import logging
import time

from ..clipboard import ClipItem, ClipboardError, create_backend
from .clipboard_page import ClipRecord

log = logging.getLogger("dualswitch.ui.clipboard")


class LocalClipFeed:
    """把本机剪贴板变成一串 `ClipRecord`（最新的在前）。"""

    def __init__(self, backend=None, capacity: int = 10):
        self.capacity = max(1, int(capacity))
        if backend is None:
            backend = create_backend("auto")
        self.backend = backend
        self._records: list[ClipRecord] = []
        self._items: dict[str, ClipItem] = {}
        self._peer = ""

    # ------------------------------------------------------------------ 采集
    @property
    def available(self) -> bool:
        return getattr(self.backend, "name", "none") != "none"

    def poll(self) -> ClipRecord | None:
        """读一次剪贴板。由 runner 的 500ms 定时器驱动（页面关着也要记）。"""
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

    def status(self) -> dict[str, str | bool]:
        return {
            "peer": self._peer,
            "connected": False,         # 同步层没落地，永远连不上
            "enabled": self.available,
            "backend": getattr(self.backend, "name", "none"),
        }

    # ------------------------------------------------------------------ 动作
    def copy_to_local(self, rid: str) -> bool:
        """把一条记录写回本机剪贴板。"""
        item = self._items.get(rid)
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

    def resend(self, rid: str) -> None:
        """重发。没有对端时等同「复制到本机」—— 至少让用户能立刻粘贴。"""
        self.copy_to_local(rid)

    def remove(self, rid: str) -> None:
        self._records = [r for r in self._records if r.id != rid]
        self._items.pop(rid, None)

    def clear(self) -> None:
        self._records.clear()
        self._items.clear()

    def close(self) -> None:
        closer = getattr(self.backend, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
