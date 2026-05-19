"""Redis 缓存实现（加分项）

适用：
- 多实例 FC 部署（跨实例共享）
- 阿里云 Redis / Tair / 自建 Redis

通过 settings.cache_backend = "redis" + REDIS_URL 启用。

设计要点：
- 用 redis.asyncio 异步客户端，与 FastAPI 协程模型一致
- value 存 JSON 字符串（dict 不能直接存 Redis）
- 连接复用：单例 client，避免每次请求建连
- 失败降级：连接断开等异常返回 None / 静默写入失败，不影响业务
"""

from __future__ import annotations

import json
import logging

from app.cache.base import DEFAULT_TTL_SECONDS

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis 异步缓存。

    构造时传入连接好的 redis.asyncio.Redis 实例（由 cache_service 工厂创建）。
    """

    def __init__(self, redis_client) -> None:
        # redis.asyncio.Redis 类型；不在签名里写避免 import 时立刻引入 redis 依赖
        self._client = redis_client

    async def get(self, key: str) -> dict | None:
        try:
            raw = await self._client.get(key)
        except Exception as e:
            logger.warning("redis get failed", extra={"scope": "cache", "key": key, "err": str(e)})
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as e:
            logger.warning(
                "redis value decode failed",
                extra={"scope": "cache", "key": key, "err": str(e)},
            )
            return None

    async def set(self, key: str, value: dict, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        try:
            payload = json.dumps(value, ensure_ascii=False, default=str)
            await self._client.set(key, payload, ex=ttl)
        except Exception as e:
            # 写失败不影响业务流程：下次调用方走未命中路径即可
            logger.warning("redis set failed", extra={"scope": "cache", "key": key, "err": str(e)})

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(key)
        except Exception as e:
            logger.warning(
                "redis delete failed", extra={"scope": "cache", "key": key, "err": str(e)}
            )

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._client.exists(key))
        except Exception as e:
            logger.warning(
                "redis exists failed", extra={"scope": "cache", "key": key, "err": str(e)}
            )
            return False

    async def close(self) -> None:
        """关闭底层连接，进程退出时调用。"""
        try:
            await self._client.aclose()
        except Exception:  # noqa: S110
            pass
