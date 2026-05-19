"""简历临时存储

⚠️ 临时实现：本期 B 阶段用 module-level dict 兜住跨请求的简历查询；
   C1 阶段会替换为 Cache 抽象（memory/redis 双实现）。

设计意图：
- 上传接口写入解析结果；查询/抽取/匹配接口读取
- 不持久化，进程重启即丢；这是 MVP 选择
- 接口设计成 async，便于 C1 阶段无缝切到异步 cache
"""

from __future__ import annotations

import asyncio
import time

from app.models.resume import ParseResult

_DEFAULT_TTL_SECONDS = 24 * 3600  # 24 小时

_store: dict[str, tuple[ParseResult, float]] = {}
_lock = asyncio.Lock()


async def save(resume_id: str, parse_result: ParseResult, ttl: int = _DEFAULT_TTL_SECONDS) -> None:
    """保存解析结果。"""
    async with _lock:
        _store[resume_id] = (parse_result, time.time() + ttl)


async def get(resume_id: str) -> ParseResult | None:
    """读取解析结果；过期则返回 None 并清理。"""
    async with _lock:
        item = _store.get(resume_id)
        if item is None:
            return None
        result, expire_at = item
        if time.time() > expire_at:
            _store.pop(resume_id, None)
            return None
        return result


async def exists(resume_id: str) -> bool:
    """判断 resume_id 是否存在且未过期。"""
    return (await get(resume_id)) is not None


async def clear() -> None:
    """清空所有数据；测试用。"""
    async with _lock:
        _store.clear()
