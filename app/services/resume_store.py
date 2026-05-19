"""简历存储

C1 阶段重构：从 module-level dict 升级到 Cache 抽象。
对外接口不变（save / get / exists / clear），调用方零改动。

后续 D/E 阶段的抽取结果、F 阶段的匹配评分会走同一个 Cache 实例，
通过不同 key 命名空间区分（详见 app/cache/base.py CacheKeys）。
"""

from __future__ import annotations

from app.cache.base import CacheKeys
from app.core.config import get_settings
from app.models.resume import ParseResult
from app.services.cache_service import get_cache


async def save(resume_id: str, parse_result: ParseResult, ttl: int | None = None) -> None:
    """保存解析结果。"""
    settings = get_settings()
    cache = get_cache()
    await cache.set(
        CacheKeys.resume(resume_id),
        parse_result.model_dump(),
        ttl=ttl or settings.cache_default_ttl,
    )


async def get(resume_id: str) -> ParseResult | None:
    """读取解析结果；不存在或过期返回 None。"""
    cache = get_cache()
    raw = await cache.get(CacheKeys.resume(resume_id))
    if raw is None:
        return None
    return ParseResult(**raw)


async def exists(resume_id: str) -> bool:
    """判断 resume_id 是否存在且未过期。"""
    cache = get_cache()
    return await cache.exists(CacheKeys.resume(resume_id))


async def clear() -> None:
    """清空（仅 memory 后端支持，测试用）。"""
    cache = get_cache()
    if hasattr(cache, "clear"):
        await cache.clear()
