"""简历信息抽取服务

提供三段抽取：basic / job_intent / background，每段：
1. 优先调 LLM JSON 模式（schema 校验）
2. LLM 失败 → basic 段走正则兜底；其余段返回空对象，记 error
3. 结果按文本 hash 缓存，避免重复调用

并发：上层 API 用 asyncio.gather 并行调三段；本服务方法各自独立。

设计模式：模板方法（Template Method）
- _cached_extract 是通用流程（缓存查 → schema 校验 → LLM 调用 → 写缓存）
- 三个公开接口（extract_basic / extract_job_intent / extract_background）只负责
  传不同的 schema、prompt 和降级策略
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
# 6000 字符约等于 3000-4000 tokens，配合 system prompt 1000 + 输出 1024，
# 总 token < 8000，远低于 mimo-v2-flash 的 256K 上下文上限
_MAX_TEXT_LEN = 6000

# ============================================================
# 正则兜底（仅用于 basic 段）
#
# LLM 全失败时启用——保证至少能抽出邮箱、手机号
# 这是题目「基本信息」必选模块的最低保证
# ============================================================

# 中国大陆手机号 / 座机 / 国际格式，宽松匹配（容忍连字符与空格）
# 例：13800000000 / 138-0000-0000 / 138 0000 0000 / +86 138-0000-0000 / 010-12345678
_PHONE_RE = re.compile(
    r"(?:\+?86[\s\-]?)?1[3-9]\d[\s\-]?\d{4}[\s\-]?\d{4}"  # 11 位手机，可含 - 或空格
    r"|\d{3,4}[\s\-]?\d{7,8}"  # 座机：区号 3-4 位 + 号码 7-8 位
    r"|\(\d{2,4}\)\s?\d{7,8}"  # (区号) 座机
)

# 邮箱正则：符合 RFC 5322 简化版（生产够用）
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+")

# 中文姓名：要求显式标签 "姓名: 张三" 或 "Name: 张三"
# 如果直接匹配 2-4 个汉字会误匹配公司名、学校名
# 限定 2-4 个字符是因为中文姓名通常 2-4 字（含复姓）
_CN_NAME_RE = re.compile(r"(?:姓名|Name)[：:\s]+([\u4e00-\u9fa5]{2,4})")


def _regex_fallback_basic(text: str) -> ResumeBasic:
    """正则兜底基本信息（精度有限，仅在 LLM 全失败时调用）。

    地址正则不可靠（缺乏结构化标记），返回 None 让前端显式标"未抽出"。
    """
    phone_match = _PHONE_RE.search(text)
    email_match = _EMAIL_RE.search(text)
    name_match = _CN_NAME_RE.search(text)
    return ResumeBasic(
        # group(1) 取捕获组，group(0) 取整个匹配
        # 姓名走捕获组（去掉 "姓名:" 前缀）；电话/邮箱整段就是结果
        name=name_match.group(1) if name_match else None,
        phone=phone_match.group(0) if phone_match else None,
        email=email_match.group(0) if email_match else None,
        address=None,  # 地址正则不可靠，留空
    )


# ============================================================
# 工作年限计算
#
# 设计原则：不信 LLM 算的总年限——LLM 经常出错：
#   - 看到 5 段经历就直接写 5
#   - 不会处理 "present / 至今 / Now"
#   - 不会处理重叠经历
#
# 让 LLM 抽 experience: [{start, end, ...}]，自己用代码算。
# ============================================================

# 解析 YYYY / YYYY-MM / YYYY.MM / YYYY/MM 等格式
# 第一个捕获组是年份（4 位数字必填）
# 第二个捕获组是月份（1-2 位数字可选）
_DATE_RE = re.compile(r"(\d{4})(?:[-./](\d{1,2}))?")


def _parse_month(s: str | None) -> tuple[int, int] | None:
    """解析「年份-月份」字符串为 (year, month) 元组；失败返回 None。

    支持：
        2020-03 / 2020.03 / 2020/3 / 2020       → (2020, 3) / (2020, 6)
        present / 至今 / now / current          → 当前年月
    """
    if not s:
        return None
    s = s.strip().lower()
    # 处理"至今"同义词：所有人共识的"还在职"
    if s in ("present", "至今", "now", "current"):
        now = datetime.now()
        return now.year, now.month
    m = _DATE_RE.search(s)
    if not m:
        return None
    year = int(m.group(1))
    # 缺月份默认 6 月，平均化
    # 用 1 月会高估、用 12 月会低估，6 月误差最小（最大 ±6 个月 = ±0.5 年）
    month = int(m.group(2)) if m.group(2) else 6
    # 防御：年份/月份在合理范围内（避免 LLM 给 "0000" 或 "13" 这种）
    if not (1900 <= year <= 2100) or not (1 <= month <= 12):
        return None
    return year, month


def calc_years_of_experience(experiences: list[WorkExperience]) -> float | None:
    """从工作经历列表计算总年限。

    策略：把每段 [start, end] 转为月数，求和后除 12，留 1 位小数。

    重要决策：
    - **重叠不去重**：招聘场景下 "2020-2023 X 公司 + 2022-2024 兼职 Y" 重叠那 1 年
      也是真实工龄，不该减掉
    - **end ≤ start 跳过**：LLM 偶尔会把日期顺序写反，跳过这种脏数据
    - **任一段日期解析失败 → 跳过该段**（不影响其他段）

    Returns:
        计算成功返回 float（保留 1 位小数）；
        所有段都解析失败返回 None（让上游决定是用 LLM 给的还是空）
    """
    if not experiences:
        return None

    total_months = 0
    has_any_valid = False  # 标记是否有任何一段被成功累加
    for exp in experiences:
        s = _parse_month(exp.start)
        e = _parse_month(exp.end)
        if not s or not e:
            continue
        # 年差 × 12 + 月差 = 总月数
        months = (e[0] - s[0]) * 12 + (e[1] - s[1])
        # end < start 跳过（LLM 数据脏）
        if months <= 0:
            continue
        total_months += months
        has_any_valid = True

    # 没任何一段有效日期 → 返 None 让上游保留 LLM 的值（哪怕不准）
    if not has_any_valid:
        return None
    return round(total_months / 12, 1)


# ============================================================
# 三段抽取：模板方法 + 三个公开接口
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
    """带缓存的 LLM 抽取通用流程（模板方法）。

    流程：
        1. 计算文本 hash → 缓存 key
        2. 查缓存 → 命中且 schema 仍兼容 → 直接返回（cache_hit=True）
        3. 缓存 miss → 调 LLM → 写缓存 → 返回（cache_hit=False）

    Args:
        section: 段名（basic / job / background），决定缓存命名空间
        text: 已截断的简历文本（外层负责 truncate）
        schema_cls: Pydantic 模型类（用于 LLM 输出校验和缓存反序列化）
        system / user: prompt 文本
        client: LLM 客户端（可注入 mock 用于测试）

    Returns:
        (parsed_instance, token_usage_or_None, cache_hit)
        - 缓存命中：(实例, None, True)  ← 没调 LLM，无 token 消耗
        - 缓存 miss：(实例, TokenUsage, False)
    """
    cache = get_cache()
    settings = get_settings()
    text_hash = sha256_str(text)
    # 缓存 key 命名空间：extract:{section}:{hash}
    # 同一份文本的三段抽取互不影响（删 basic 不影响 background）
    key = CacheKeys.extract(text_hash, section)

    # 第一步：查缓存
    cached = await cache.get(key)
    if cached is not None:
        try:
            # 用 schema 反序列化——这一步同时做校验：
            # 如果业务版本升级导致 schema 变了（多了字段、改了类型），旧数据会校验失败
            return schema_cls.model_validate(cached), None, True
        except Exception as e:
            # 缓存数据 schema 不兼容（升级场景常见）→ 删掉重抽，不污染上游
            logger.warning(
                "extract cache validation failed; refetching",
                extra={"scope": "extract", "section": section, "err": str(e)},
            )
            await cache.delete(key)

    # 第二步：调 LLM
    # background 段 max_tokens 翻倍——它要返回 3 个数组（教育/工作/项目），
    # 每条记录字段多，1024 token 容易被截断；其它段够用
    parsed, usage = await client.chat_json(
        system=system,
        user=user,
        schema=schema_cls,
        max_tokens=1024 if section != "background" else 2048,
        # temperature 0.1：抽取任务要稳定，温度越低越确定
        temperature=0.1,
    )

    # 第三步：写缓存（用 model_dump 序列化为 dict，redis/memory 都支持）
    await cache.set(key, parsed.model_dump(), ttl=settings.cache_default_ttl)
    return parsed, usage, False


async def extract_basic(
    text: str, *, client: MiMoClient | None = None
) -> tuple[ResumeBasic, TokenUsage | None, bool]:
    """抽取基本信息（必选模块）。

    LLM 失败时**启用正则兜底**——保证至少能抽出邮箱、手机号。
    这是必选模块的最低保证。

    Returns:
        (basic, usage, cache_hit)
        - 正常路径：LLM 返回的 ResumeBasic
        - 降级路径：正则兜底的 ResumeBasic（usage=None）
    """
    # 文本截断：避免长简历把 token 消耗炸掉
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
        # LLM 失败 → 正则兜底
        # 注意：兜底用原文 text 而不是 truncated，因为正则不消耗 token
        logger.warning(
            "extract basic LLM failed; falling back to regex",
            extra={"scope": "extract", "section": "basic", "err": str(e)},
        )
        return _regex_fallback_basic(text), None, False


async def extract_job_intent(
    text: str, *, client: MiMoClient | None = None
) -> tuple[ResumeJobIntent, TokenUsage | None, bool]:
    """抽取求职信息（加分项）。

    LLM 失败返回空对象——这段是加分项，缺失不影响整体可用性。
    """
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
        # 返回空对象（target_role=None / expected_salary=None），保持响应结构稳定
        return ResumeJobIntent(), None, False


async def extract_background(
    text: str, *, client: MiMoClient | None = None
) -> tuple[ResumeBackground, TokenUsage | None, bool]:
    """抽取背景信息（加分项）+ 自动计算工作年限。

    特殊处理：用代码计算的总年限**覆盖** LLM 给的值——LLM 算总年限常常不准。
    """
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
        # 用代码算的年限覆盖 LLM 给的——LLM 算的常常不准
        # 但代码算不出来时（无可解析日期）保留 LLM 给的，避免显示 None
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
