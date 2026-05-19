"""缓存服务工厂

职责：
- 按 settings 选择 memory / redis 实现
- 提供模块级单例 `get_cache()`
- 启动时如果 Redis 配置错误，**降级到 memory** 而不是崩溃

调用方都通过 `get_cache()` 拿到 `Cache` 接口对象，看不到底层实现。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.cache.base import Cache
from app.cache.memory import MemoryCache
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _build_redis_cache() -> Cache | None:
    """尝试构建 Redis 缓存；失败返回 None。"""
    settings = get_settings()
    if not settings.redis_url.strip():
        logger.warning(
            "cache_backend=redis but REDIS_URL is empty; falling back to memory",
            extra={"scope": "cache"},
        )
        return None
    try:
        # 延迟导入：未启用 redis 时不引入依赖
        import redis.asyncio as aioredis

        from app.cache.redis_impl import RedisCache
    except ImportError as e:
        logger.warning(
            "redis library not installed; falling back to memory",
            extra={"scope": "cache", "err": str(e)},
        )
        return None

    try:
        client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
    except Exception as e:
        logger.warning(
            "redis client init failed; falling back to memory",
            extra={"scope": "cache", "err": str(e)},
        )
        return None

    logger.info("redis cache initialized", extra={"scope": "cache", "url": settings.redis_url})
    return RedisCache(client)


@lru_cache(maxsize=1)
def get_cache() -> Cache:
    """获取全局缓存单例。

    根据 settings.cache_backend 决定实现：
    - "memory"：默认，零依赖
    - "redis"：跨实例共享；Redis 不可用时自动降级到 memory
    """
    settings = get_settings()
    if settings.cache_backend == "redis":
        redis_cache = _build_redis_cache()
        if redis_cache is not None:
            return redis_cache
    return MemoryCache()


def reset_cache() -> None:
    """清掉单例（测试用）。"""
    get_cache.cache_clear()
