"""内存剪贴板后端：两端自测 / 单元测试用，不需要真的碰系统剪贴板。

它是 `ClipboardBackend` 契约的**参考实现**：`poll()` 的去重与「自写吞掉」语义看这里。
`tests/test_clipboard_contract.py` 会用同一套用例跑 MockBackend 和真实后端，
两边的行为必须一致 —— 这是"少人工调试"的第一道闸。
"""

from __future__ import annotations

import collections
from typing import Iterable

from .base import ClipItem


class MockBackend:
    """把剪贴板当成一个队列：`feed()` 放内容，`poll()` 取内容。"""

    name = "mock"

    def __init__(self) -> None:
        self._queue: collections.deque[ClipItem] = collections.deque()
        self._last_digest: str = ""
        self.written: list[ClipItem] = []      # 供测试断言"对端写进来了什么"
        self.fail_next_write: bool = False     # 模拟写入失败

    # ---- 测试驱动 -------------------------------------------------------- #
    def feed(self, *items: ClipItem) -> None:
        """模拟"用户复制了这些内容"（相当于外部改了系统剪贴板）。"""
        self._queue.extend(items)

    def feed_many(self, items: Iterable[ClipItem]) -> None:
        self._queue.extend(items)

    def reset(self) -> None:
        self._queue.clear()
        self._last_digest = ""
        self.written.clear()
        self.fail_next_write = False

    # ---- 契约实现 -------------------------------------------------------- #
    def poll(self) -> ClipItem | None:
        if not self._queue:
            return None
        item = self._queue.popleft()
        if item.digest == self._last_digest:   # 去重：内容没变就不上报
            return None
        self._last_digest = item.digest
        return item

    def write(self, item: ClipItem) -> None:
        if self.fail_next_write:
            self.fail_next_write = False
            from .base import ClipboardError

            raise ClipboardError("mock: 模拟写入失败")
        self.written.append(item)
        # 关键：记下指纹，让紧接着的 poll() 把这次自写吞掉
        self._last_digest = item.digest
        self._queue.clear()

    def probe(self) -> dict[str, str]:
        return {
            "backend": "mock",
            "pending": str(len(self._queue)),
            "last_digest": self._last_digest[:12],
            "written": str(len(self.written)),
        }

    def close(self) -> None:
        pass
