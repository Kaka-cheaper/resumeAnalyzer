"""Redis 缓存契约测试

用 fakeredis 在没有真实 Redis 服务的环境下验证 RedisCache 行为正确，
保证后续接阿里云 Redis 时代码不需要再调。

如果未装 fakeredis（生产环境），自动 skip。
"""

from __future__ import annotations

import asyncio

import pytest

fakeredis = pytest.importorskip("fakeredis", reason="fakeredis 未安装，跳过 Redis 契约测试")

from app.cache.base import Cache  # noqa: E402
from app.cache.redis_impl import RedisCache  # noqa: E402


@pytest.fixture
async def redis_cache() -> RedisCache:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = RedisCache(client)
    yield cache
    await client.flushall()
    await client.aclose()


async def test_protocol_compliance(redis_cache: RedisCache):
    assert isinstance(redis_cache, Cache)


async def test_set_then_get(redis_cache: RedisCache):
    await redis_cache.set("k1", {"a": 1, "b": "x", "中文": "ok"})
    got = await redis_cache.get("k1")
    assert got == {"a": 1, "b": "x", "中文": "ok"}


async def test_get_missing_returns_none(redis_cache: RedisCache):
    assert await redis_cache.get("nope") is None


async def test_exists(redis_cache: RedisCache):
    assert await redis_cache.exists("k") is False
    await redis_cache.set("k", {"v": 1})
    assert await redis_cache.exists("k") is True


async def test_delete(redis_cache: RedisCache):
    await redis_cache.set("k", {"v": 1})
    await redis_cache.delete("k")
    assert await redis_cache.get("k") is None
    # 删不存在的 key 不应抛
    await redis_cache.delete("never-existed")


async def test_ttl_expires(redis_cache: RedisCache):
    await redis_cache.set("k", {"v": 1}, ttl=1)
    assert await redis_cache.get("k") == {"v": 1}
    await asyncio.sleep(1.1)
    assert await redis_cache.get("k") is None


async def test_set_invalid_ttl_raises(redis_cache: RedisCache):
    with pytest.raises(ValueError):
        await redis_cache.set("k", {"v": 1}, ttl=0)


async def test_get_with_corrupt_value_returns_none(redis_cache: RedisCache):
    """直接塞非 JSON 字符串到底层 → get 应返回 None 而不是抛。"""
    await redis_cache._client.set("bad", "not a json {")
    assert await redis_cache.get("bad") is None


async def test_failure_safe_on_disconnected_client():
    """连接坏掉时 get/set 应静默降级（warn + None / 静默写失败）。"""

    class BrokenClient:
        async def get(self, *a, **kw):
            raise ConnectionError("boom")

        async def set(self, *a, **kw):
            raise ConnectionError("boom")

        async def delete(self, *a, **kw):
            raise ConnectionError("boom")

        async def exists(self, *a, **kw):
            raise ConnectionError("boom")

    cache = RedisCache(BrokenClient())
    assert await cache.get("any") is None
    # 不应抛，业务降级到未命中路径
    await cache.set("any", {"v": 1})
    await cache.delete("any")
    assert await cache.exists("any") is False
