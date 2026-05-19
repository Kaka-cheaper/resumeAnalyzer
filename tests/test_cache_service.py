"""缓存服务工厂测试

验证：
- 默认 memory 后端
- redis 后端 + 空 URL 自动降级 memory
- redis 后端 + 有效 URL 注入 RedisCache
"""

from __future__ import annotations

import pytest

from app.cache.memory import MemoryCache
from app.cache.redis_impl import RedisCache
from app.core.config import get_settings
from app.services import cache_service


@pytest.fixture(autouse=True)
def reset():
    cache_service.reset_cache()
    get_settings.cache_clear()
    yield
    cache_service.reset_cache()
    get_settings.cache_clear()


def _patch_settings(monkeypatch, **kwargs):
    """覆盖配置：通过 lru_cache 的 single instance 注入。"""
    cache_service.reset_cache()
    get_settings.cache_clear()
    for k, v in kwargs.items():
        monkeypatch.setenv(k.upper(), str(v))


def test_default_is_memory(monkeypatch):
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    cache_service.reset_cache()
    get_settings.cache_clear()
    cache = cache_service.get_cache()
    assert isinstance(cache, MemoryCache)


def test_redis_without_url_falls_back_to_memory(monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "")
    cache_service.reset_cache()
    get_settings.cache_clear()

    cache = cache_service.get_cache()
    assert isinstance(cache, MemoryCache)


def test_redis_with_fake_url_succeeds(monkeypatch):
    """REDIS_URL 配上 redis://localhost:6379 时应实例化 RedisCache。

    注意：这里只验证工厂逻辑（没真连接），实际通信由 fakeredis-redis 测试覆盖。
    """
    monkeypatch.setenv("CACHE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    cache_service.reset_cache()
    get_settings.cache_clear()

    cache = cache_service.get_cache()
    # redis-py from_url 是 lazy，不会立刻报错；类型应是 RedisCache
    assert isinstance(cache, RedisCache)
