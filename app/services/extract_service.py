"""简历信息抽取服务

提供三段抽取：basic / job_intent / background，每段：
1. 优先调 LLM JSON 模式（schema 校验）
2. LLM 失败 → basic 段走正则兜底；其余段返回空对象，记 error
3. 结果按文本 hash 缓存，避免重复调用

并发：上层 API 用 asyncio.gather 并行调三段；本服务方法各自独立。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from app.cache.base import CacheKeys
from app.core.config import get_settings
from app.core.exceptions import LLMError
from app.llm.client import MiMoClient, get_llm_client
from app.llm.prompts import (
    EXTRACT_BACKGROUND_SYSTEM,
    EXTRACT_BASIC_SYSTEM,
    EXTRACT_JOB_INTENT_SYSTEM,
    extract_background_user,
    extract_basic_user,
    extract_job_intent_user,
)
from app.llm.schemas import TokenUsage
from app.models.resume import (
    ResumeBackground,
    ResumeBasic,
    ResumeJobIntent,
    WorkExperience,
)
from app.services.cache_service import get_cache
from app.utils.hash import sha256_str
from app.utils.text import truncate

logger = logging.getLogger(__name__)

# LLM 输入文本上限，避免单次调用 token 失控
_MAX_TEXT_LEN = 6000

# ============================================================
# 正则兜底（仅用于 basic 段）
# ============================================================

# 中国大陆手机号 / 座机 / 国际格式，宽松匹配（容忍连字符与空格）
_PHONE_RE = re.compile(
    r"(?:\+?86[\s\-]?)?1[3-9]\d[\s\-]?\d{4}[\s\-]?\d{4}"  # 11 位手机，可含 - 或空格
    r"|\d{3,4}[\s\-]?\d{7,8}"  # 座机
    r"|\(\d{2,4}\)\s?\d{7,8}"  # (区号) 座机
)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+")

# 中文姓名：2-4 个汉字（启发式，准确率有限）
_CN_NAME_RE = re.compile(r"(?:姓名|Name)[：:\s]+([\u4e00-\u9fa5]{2,4})")


def _regex_fallback_basic(text: str) -> ResumeBasic:
    """正则兜底基本信息（精度有限，仅在 LLM 全失败时调用）。"""
    phone_match = _PHONE_RE.search(text)
    email_match = _EMAIL_RE.search(text)
    name_match = _CN_NAME_RE.search(text)
    return ResumeBasic(
        name=name_match.group(1) if name_match else None,
        phone=phone_match.group(0) if phone_match else None,
        email=email_match.group(0) if email_match else None,
        address=None,  # 地址正则不可靠，留空
    )


# ============================================================
# 工作年限计算
# ============================================================

# 解析 YYYY / YYYY-MM / YYYY.MM / YYYY/MM 等格式
_DATE_RE = re.compile(r"(\d{4})(?:[-./](\d{1,2}))?")


def _parse_month(s: str | None) -> tuple[int, int] | None:
    """解析「年份-月份」字符串为 (year, month) 元组；失败返回 None。"""
    if not s:
        return None
    s = s.strip().lower()
    if s in ("present", "至今", "now", "current"):
        now = datetime.now()
        return now.year, now.month
    m = _DATE_RE.search(s)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 6  # 缺月份默认 6 月，平均化
    if not (1900 <= year <= 2100) or not (1 <= month <= 12):
        return None
    return year, month


def calc_years_of_experience(experiences: list[WorkExperience]) -> float | None:
    """从工作经历列表计算总年限。

    策略：把每段 [start, end] 转为月数，求和后除 12，留 1 位小数。
    经历重叠不去重（招聘场景下重叠经历也是真实工龄）。
    无任何可用日期返回 None。
    """
    if not experiences:
        return None

    total_months = 0
    has_any_valid = False
    for exp in experiences:
        s = _parse_month(exp.start)
        e = _parse_month(exp.end)
        if not s or not e:
            continue
        months = (e[0] - s[0]) * 12 + (e[1] - s[1])
        if months <= 0:
            continue
        total_months += months
        has_any_valid = True

    if not has_any_valid:
        return None
    return round(total_months / 12, 1)


# ============================================================
# 三段抽取
# ============================================================


async def _cached_extract(
    *,
    section: str,
    text: str,
    schema_cls: type,
    system: str,
    user: str,
    client: MiMoClient,
) -> tuple[object, TokenUsage | None, bool]:
    """带缓存的 LLM 抽取通用流程。

    Returns:
        (parsed_instance, token_usage_or_None, cache_hit)
    """
    cache = get_cache()
    settings = get_settings()
    text_hash = sha256_str(text)
    key = CacheKeys.extract(text_hash, section)

    cached = await cache.get(key)
    if cached is not None:
        try:
            return schema_cls.model_validate(cached), None, True
        except Exception as e:
            logger.warning(
                "extract cache validation failed; refetching",
                extra={"scope": "extract", "section": section, "err": str(e)},
            )
            await cache.delete(key)

    parsed, usage = await client.chat_json(
        system=system,
        user=user,
        schema=schema_cls,
        max_tokens=1024 if section != "background" else 2048,
        temperature=0.1,
    )

    # 写缓存
    await cache.set(key, parsed.model_dump(), ttl=settings.cache_default_ttl)
    return parsed, usage, False


async def extract_basic(
    text: str, *, client: MiMoClient | None = None
) -> tuple[ResumeBasic, TokenUsage | None, bool]:
    """抽取基本信息（必选）。

    Returns:
        (basic, usage, cache_hit)。LLM 失败时返回正则兜底结果（usage=None, cache_hit=False）。
    """
    truncated = truncate(text, _MAX_TEXT_LEN)
    cli = client or get_llm_client()
    try:
        parsed, usage, hit = await _cached_extract(
            section="basic",
            text=truncated,
            schema_cls=ResumeBasic,
            system=EXTRACT_BASIC_SYSTEM,
            user=extract_basic_user(truncated),
            client=cli,
        )
        return parsed, usage, hit
    except LLMError as e:
        logger.warning(
            "extract basic LLM failed; falling back to regex",
            extra={"scope": "extract", "section": "basic", "err": str(e)},
        )
        return _regex_fallback_basic(text), None, False


async def extract_job_intent(
    text: str, *, client: MiMoClient | None = None
) -> tuple[ResumeJobIntent, TokenUsage | None, bool]:
    """抽取求职信息（加分项）。LLM 失败返回空对象。"""
    truncated = truncate(text, _MAX_TEXT_LEN)
    cli = client or get_llm_client()
    try:
        parsed, usage, hit = await _cached_extract(
            section="job",
            text=truncated,
            schema_cls=ResumeJobIntent,
            system=EXTRACT_JOB_INTENT_SYSTEM,
            user=extract_job_intent_user(truncated),
            client=cli,
        )
        return parsed, usage, hit
    except LLMError as e:
        logger.warning(
            "extract job_intent LLM failed",
            extra={"scope": "extract", "section": "job", "err": str(e)},
        )
        return ResumeJobIntent(), None, False


async def extract_background(
    text: str, *, client: MiMoClient | None = None
) -> tuple[ResumeBackground, TokenUsage | None, bool]:
    """抽取背景信息（加分项）+ 自动计算工作年限。"""
    truncated = truncate(text, _MAX_TEXT_LEN)
    cli = client or get_llm_client()
    try:
        parsed, usage, hit = await _cached_extract(
            section="background",
            text=truncated,
            schema_cls=ResumeBackground,
            system=EXTRACT_BACKGROUND_SYSTEM,
            user=extract_background_user(truncated),
            client=cli,
        )
        # 用计算结果覆盖 LLM 给的（LLM 算的常常不准）
        calculated = calc_years_of_experience(parsed.experience)
        if calculated is not None:
            parsed.years_of_experience = calculated
        return parsed, usage, hit
    except LLMError as e:
        logger.warning(
            "extract background LLM failed",
            extra={"scope": "extract", "section": "background", "err": str(e)},
        )
        return ResumeBackground(), None, False
