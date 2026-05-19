"""内存缓存实现

适用：
- 本地开发
- 单实例 FC 部署（MVP）
- 测试

限制：
- 进程重启数据丢失（FC 实例回收即清空）
- 多 FC 实例之间不共享（详见 README §限制）
"""

from __future__ import annotations

import asyncio
import time

from app.cache.base import DEFAULT_TTL_SECONDS


class MemoryCache:
    """协程安全的 TTL 内存缓存。

    用 asyncio.Lock 保护字典；惰性过期（读时检查并清理）。
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[dict, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict | None:
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, expire_at = item
            if time.time() > expire_at:
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: dict, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        async with self._lock:
            self._store[key] = (value, time.time() + ttl)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return (await self.get(key)) is not None

    async def clear(self) -> None:
        """清空全部（测试用，非协议方法）。"""
        async with self._lock:
            self._store.clear()

    async def size(self) -> int:
        """当前条目数（含未清理的过期项），调试用。"""
        async with self._lock:
            return len(self._store)
