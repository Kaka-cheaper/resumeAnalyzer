"""岗位描述（JD）关键词提取服务"""

from __future__ import annotations

import logging

from app.cache.base import CacheKeys
from app.core.config import get_settings
from app.core.exceptions import LLMError
from app.llm.client import MiMoClient, get_llm_client
from app.llm.prompts import (
    EXTRACT_JD_KEYWORDS_SYSTEM,
    extract_jd_keywords_user,
)
from app.llm.schemas import TokenUsage
from app.models.match import JDKeywords, JDRequirements
from app.services.cache_service import get_cache
from app.utils.hash import sha256_str
from app.utils.text import truncate

logger = logging.getLogger(__name__)

_MAX_JD_LEN = 8000


class _JDKeywordsLLMSchema(JDKeywords):
    """LLM 输出 schema：jd_hash 由代码后填，LLM 不需要给。"""

    # 让 LLM 不传 jd_hash 也能通过校验
    jd_hash: str = ""


async def extract_jd_keywords(
    jd_text: str, *, client: MiMoClient | None = None
) -> tuple[JDKeywords, TokenUsage | None, bool]:
    """提取 JD 关键词。

    Returns:
        (jd_keywords, token_usage, cache_hit)
        LLM 失败时返回最小可用的关键词对象（skills 空 + requirements 空），但仍带 jd_hash 便于上层缓存匹配评分。
    """
    truncated = truncate(jd_text, _MAX_JD_LEN)
    jd_hash = sha256_str(truncated)
    short_hash = jd_hash[:16]

    cache = get_cache()
    settings = get_settings()
    cache_key = CacheKeys.jd(short_hash)

    cached = await cache.get(cache_key)
    if cached is not None:
        try:
            return JDKeywords.model_validate(cached), None, True
        except Exception as e:
            logger.warning(
                "jd cache invalid; refetching",
                extra={"scope": "jd", "err": str(e)},
            )
            await cache.delete(cache_key)

    cli = client or get_llm_client()
    try:
        parsed, usage = await cli.chat_json(
            system=EXTRACT_JD_KEYWORDS_SYSTEM,
            user=extract_jd_keywords_user(truncated),
            schema=_JDKeywordsLLMSchema,
            max_tokens=1024,
            temperature=0.1,
        )
    except LLMError as e:
        logger.warning(
            "jd llm failed; returning empty keywords",
            extra={"scope": "jd", "err": str(e)},
        )
        return (
            JDKeywords(
                jd_hash=short_hash,
                skills=[],
                responsibilities=[],
                requirements=JDRequirements(),
            ),
            None,
            False,
        )

    parsed.jd_hash = short_hash
    final = JDKeywords.model_validate(parsed.model_dump())

    await cache.set(cache_key, final.model_dump(), ttl=settings.cache_default_ttl)
    return final, usage, False


async def get_jd_keywords_by_hash(jd_hash: str) -> JDKeywords | None:
    """直接按 hash 查缓存（match 接口走 jd_hash 复用路径用）。"""
    cache = get_cache()
    cached = await cache.get(CacheKeys.jd(jd_hash))
    if cached is None:
        return None
    try:
        return JDKeywords.model_validate(cached)
    except Exception:
        return None
