"""缓存层单元测试

按 tasks.md C1 验收清单覆盖：
- set → get 命中
- TTL 过期
- delete
- 不存在的 key
- 协议契约
"""

from __future__ import annotations

import asyncio

import pytest

from app.cache.base import Cache, CacheKeys
from app.cache.memory import MemoryCache


@pytest.fixture
async def cache() -> MemoryCache:
    return MemoryCache()


async def test_set_then_get(cache: MemoryCache):
    await cache.set("k1", {"a": 1, "b": "x"})
    got = await cache.get("k1")
    assert got == {"a": 1, "b": "x"}


async def test_get_missing_returns_none(cache: MemoryCache):
    assert await cache.get("nope") is None


async def test_exists(cache: MemoryCache):
    assert await cache.exists("k") is False
    await cache.set("k", {"v": 1})
    assert await cache.exists("k") is True


async def test_delete(cache: MemoryCache):
    await cache.set("k", {"v": 1})
    await cache.delete("k")
    assert await cache.get("k") is None
    # 删不存在的 key 不应抛
    await cache.delete("never-existed")


async def test_ttl_expires(cache: MemoryCache):
    await cache.set("k", {"v": 1}, ttl=1)
    assert await cache.get("k") == {"v": 1}
    await asyncio.sleep(1.05)
    assert await cache.get("k") is None


async def test_set_invalid_ttl_raises(cache: MemoryCache):
    with pytest.raises(ValueError):
        await cache.set("k", {"v": 1}, ttl=0)
    with pytest.raises(ValueError):
        await cache.set("k", {"v": 1}, ttl=-1)


async def test_overwrite(cache: MemoryCache):
    await cache.set("k", {"v": 1})
    await cache.set("k", {"v": 2})
    assert await cache.get("k") == {"v": 2}


async def test_protocol_compliance(cache: MemoryCache):
    """MemoryCache 必须满足 Cache Protocol。"""
    assert isinstance(cache, Cache)


async def test_concurrent_writes_safe():
    """并发写入不应数据损坏（Lock 生效）。"""
    cache = MemoryCache()

    async def writer(i: int):
        for _ in range(100):
            await cache.set(f"k{i}", {"v": i})

    await asyncio.gather(*[writer(i) for i in range(10)])
    for i in range(10):
        assert await cache.get(f"k{i}") == {"v": i}


def test_cache_keys_format():
    """CacheKeys 命名规范快照测试。"""
    assert CacheKeys.resume("rsm_x") == "resume:rsm_x"
    assert CacheKeys.pdf_parse("h") == "pdf:h"
    assert CacheKeys.extract("h", "basic") == "extract:basic:h"
    assert CacheKeys.jd("h") == "jd:h"
    assert CacheKeys.match("rsm_x", "jdh") == "match:rsm_x:jdh"
    assert CacheKeys.match("rsm_x", "jdh", "f1") == "match:rsm_x:jdh:f1"
