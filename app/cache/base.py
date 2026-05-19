"""缓存抽象层

设计原则：
- 接口面向 dict（JSON 可序列化），让 memory / redis 行为一致
- 调用方用 `model.model_dump()` 序列化、`Model(**cached)` 反序列化
- 所有方法 async：兼容 redis.asyncio；memory 实现内部用 asyncio.Lock 保证安全
- TTL 必填（带默认值），强制开发者思考过期策略
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """缓存协议。

    实现类只要满足这 4 个 async 方法即可被 `cache_service` 注入。
    """

    async def get(self, key: str) -> dict | None:
        """读取；不存在或已过期返回 None。"""
        ...

    async def set(self, key: str, value: dict, ttl: int = 86400) -> None:
        """写入，TTL 单位秒。"""
        ...

    async def delete(self, key: str) -> None:
        """删除；不存在不抛错。"""
        ...

    async def exists(self, key: str) -> bool:
        """判断是否存在且未过期。"""
        ...


# 默认 TTL：与 design.md §6.4 缓存键设计表对齐
DEFAULT_TTL_SECONDS = 86400  # 24 小时


# 缓存键命名约定
class CacheKeys:
    """缓存键工厂，集中管理 key 命名规则。

    避免散落各处的 f-string，改 key 时一处修改全局生效。
    """

    @staticmethod
    def resume(resume_id: str) -> str:
        """简历解析结果。"""
        return f"resume:{resume_id}"

    @staticmethod
    def pdf_parse(file_hash: str) -> str:
        """按文件哈希缓存原始解析结果（与 resume_id 解耦的另一条路径）。"""
        return f"pdf:{file_hash}"

    @staticmethod
    def extract(text_hash: str, section: str) -> str:
        """信息抽取结果按段缓存。"""
        return f"extract:{section}:{text_hash}"

    @staticmethod
    def jd(jd_hash: str) -> str:
        """JD 关键词。"""
        return f"jd:{jd_hash}"

    @staticmethod
    def match(resume_id: str, jd_hash: str, flags_hash: str = "") -> str:
        """匹配评分。"""
        suffix = f":{flags_hash}" if flags_hash else ""
        return f"match:{resume_id}:{jd_hash}{suffix}"


__all__ = ["Cache", "CacheKeys", "DEFAULT_TTL_SECONDS", "Any"]
