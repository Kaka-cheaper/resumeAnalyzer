"""简历-岗位匹配评分服务

两层评分：
1. 启发式（规则 + 关键词命中 + 经验/学历加权），快、稳、可解释
2. LLM 精准评分（语义级），加分项；可通过 use_llm_score=False 关闭

融合：final = heuristic_weight * heuristic + llm_weight * llm_score（权重在 settings）
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field

from app.cache.base import CacheKeys
from app.core.config import get_settings
from app.core.exceptions import LLMError
from app.llm.client import MiMoClient, get_llm_client
from app.llm.prompts import LLM_SCORE_SYSTEM, llm_score_user
from app.llm.schemas import TokenUsage
from app.models.match import (
    EducationBreakdown,
    ExperienceBreakdown,
    JDKeywords,
    MatchResult,
    ScoreBreakdown,
    SkillBreakdown,
)
from app.models.resume import ResumeAggregate
from app.services.cache_service import get_cache
from app.utils.hash import sha256_str

logger = logging.getLogger(__name__)


# ============================================================
# 学历枚举映射
# ============================================================

# 学历等级（数字越大越高）
_EDU_LEVEL = {
    "高中": 1,
    "中专": 1,
    "大专": 2,
    "专科": 2,
    "本科": 3,
    "学士": 3,
    "硕士": 4,
    "研究生": 4,
    "博士": 5,
    "phd": 5,
}


def _normalize_edu(s: str | None) -> str | None:
    """学历字符串归一为枚举键。"""
    if not s:
        return None
    s = s.strip().lower()
    for key in _EDU_LEVEL:
        if key in s:
            return key
    return s


def _edu_level(s: str | None) -> int | None:
    """学历等级数值（越高越好）；未知返回 None。"""
    if not s:
        return None
    norm = _normalize_edu(s)
    return _EDU_LEVEL.get(norm) if norm else None


def _highest_edu(educations: list) -> str | None:
    """从教育经历列表中找最高学历。"""
    best: tuple[int, str] | None = None
    for edu in educations:
        deg = getattr(edu, "degree", None)
        lvl = _edu_level(deg)
        if lvl is None:
            continue
        if best is None or lvl > best[0]:
            best = (lvl, deg)
    return best[1] if best else None


# ============================================================
# 技能匹配（轻量归一化）
# ============================================================

# 同义词归一表（单向：alias → canonical）；扩展时只改这里
_SKILL_ALIAS = {
    "k8s": "kubernetes",
    "tf": "tensorflow",
    "pg": "postgresql",
    "postgres": "postgresql",
    "js": "javascript",
    "ts": "typescript",
    "node": "node.js",
    "nodejs": "node.js",
    "py": "python",
    "py3": "python",
    "python3": "python",
}


def _normalize_skill(s: str) -> str:
    """技能名归一：小写、去空格、查同义词表。"""
    if not s:
        return ""
    norm = re.sub(r"\s+", "", s.strip().lower())
    return _SKILL_ALIAS.get(norm, norm)


def _build_resume_skill_set(resume: ResumeAggregate) -> set[str]:
    """从简历中收集所有可能的技能词，用于命中匹配。"""
    pool: list[str] = []
    # 项目摘要 + 工作摘要 + 教育专业
    for proj in resume.background.projects:
        if proj.summary:
            pool.append(proj.summary)
        if proj.name:
            pool.append(proj.name)
    for exp in resume.background.experience:
        if exp.summary:
            pool.append(exp.summary)
        if exp.role:
            pool.append(exp.role)
    for edu in resume.background.education:
        if edu.major:
            pool.append(edu.major)
    if resume.job_intent.target_role:
        pool.append(resume.job_intent.target_role)
    pool.append(resume.text_preview)

    # 整段文本归一：取所有英文词与中文连续片段
    blob = " ".join(pool).lower()
    return {
        _normalize_skill(w)
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9.+\-#]+|\u4e00-\u9fa5+", blob)
        if w
    }


def _is_skill_hit(skill: str, resume_skills: set[str], blob: str) -> bool:
    """判断单个 JD 技能是否在简历中。

    两条路径：
    1. 归一后与 resume_skills 集合相交
    2. 在简历全文（blob）做 substring 匹配（兜底，处理多词技能如 "machine learning"）
    """
    norm = _normalize_skill(skill)
    if not norm:
        return False
    if norm in resume_skills:
        return True
    # 包含原 skill（小写）的子串匹配，处理多词技能
    return skill.strip().lower() in blob


# ============================================================
# 启发式评分
# ============================================================


def _score_skills(jd: JDKeywords, resume: ResumeAggregate) -> SkillBreakdown:
    """技能命中评分。

    must_have 的命中率占 80%，nice_to_have 的命中率占 20%（以 must 为主）；
    无 must_have 时全靠 nice / skills，避免 0 分。
    """
    blob = " ".join(
        [
            resume.text_preview or "",
            *[p.summary or "" for p in resume.background.projects],
            *[p.name or "" for p in resume.background.projects],
            *[e.summary or "" for e in resume.background.experience],
            *[e.role or "" for e in resume.background.experience],
        ]
    ).lower()
    resume_skills = _build_resume_skill_set(resume)

    must = jd.requirements.must_have or []
    nice = jd.requirements.nice_to_have or []

    must_hit = [s for s in must if _is_skill_hit(s, resume_skills, blob)]
    must_miss = [s for s in must if s not in must_hit]
    nice_hit = [s for s in nice if _is_skill_hit(s, resume_skills, blob)]

    if not must and not nice:
        # JD 没给关键词时只能给中性分
        score = 50
    elif not must:
        score = round(len(nice_hit) / len(nice) * 100) if nice else 50
    else:
        must_rate = len(must_hit) / len(must)
        nice_rate = (len(nice_hit) / len(nice)) if nice else 0
        score = round(must_rate * 80 + nice_rate * 20)

    return SkillBreakdown(
        score=max(0, min(100, score)),
        must_total=len(must),
        must_hit=must_hit,
        must_miss=must_miss,
        nice_total=len(nice),
        nice_hit=nice_hit,
    )


def _score_experience(jd: JDKeywords, resume: ResumeAggregate) -> ExperienceBreakdown:
    """经验年限评分：每差 1 年扣 10 分；完全满足或超出 100 分。"""
    candidate = resume.background.years_of_experience
    required = jd.requirements.min_years

    if required is None:
        return ExperienceBreakdown(score=70, candidate_years=candidate, required_years=None)
    if candidate is None:
        return ExperienceBreakdown(score=40, candidate_years=None, required_years=required)

    diff = candidate - required
    if diff >= 0:
        score = 100
    else:
        score = max(0, round(100 + diff * 10))  # diff 是负数，每年扣 10
    return ExperienceBreakdown(score=score, candidate_years=candidate, required_years=required)


def _score_education(jd: JDKeywords, resume: ResumeAggregate) -> EducationBreakdown:
    """学历评分：候选 ≥ 要求 → 100；低 1 级 → 70；低 2 级 → 40；低 3 级及以下 → 20。"""
    required = jd.requirements.education
    candidate = _highest_edu(resume.background.education)

    if required is None:
        return EducationBreakdown(score=70, candidate=candidate, required=None)
    if candidate is None:
        return EducationBreakdown(score=40, candidate=None, required=required)

    cand_lvl = _edu_level(candidate)
    req_lvl = _edu_level(required)
    if cand_lvl is None or req_lvl is None:
        return EducationBreakdown(score=70, candidate=candidate, required=required)

    diff = cand_lvl - req_lvl
    if diff >= 0:
        score = 100
    elif diff == -1:
        score = 70
    elif diff == -2:
        score = 40
    else:
        score = 20
    return EducationBreakdown(score=score, candidate=candidate, required=required)


def heuristic_score(jd: JDKeywords, resume: ResumeAggregate) -> tuple[ScoreBreakdown, int]:
    """启发式综合评分。

    Returns:
        (breakdown, total) breakdown 含三段明细，total 是综合分。
    """
    settings = get_settings()
    skills = _score_skills(jd, resume)
    exp = _score_experience(jd, resume)
    edu = _score_education(jd, resume)

    # 权重归一（按设置的 skill/exp/edu 权重重新归一，避免和不为 1）
    w_skill = settings.skill_weight
    w_exp = settings.experience_weight
    w_edu = settings.education_weight
    total_w = w_skill + w_exp + w_edu
    if total_w <= 0:
        total_w = 1.0
    weighted = (skills.score * w_skill + exp.score * w_exp + edu.score * w_edu) / total_w
    total = max(0, min(100, round(weighted)))

    breakdown = ScoreBreakdown(
        skill_match=skills,
        experience=exp,
        education=edu,
        heuristic_total=total,
        llm_score=None,
        weights={
            "skill": w_skill,
            "experience": w_exp,
            "education": w_edu,
            "heuristic": settings.heuristic_weight,
            "llm": settings.llm_weight,
        },
    )
    return breakdown, total


# ============================================================
# LLM 精准评分（加分项）
# ============================================================


class _LLMScoreSchema(BaseModel):
    """LLM 输出 schema。"""

    score: int = Field(ge=0, le=100)
    summary: str = Field(default="")
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


def _resume_summary_for_llm(resume: ResumeAggregate) -> str:
    """构造给 LLM 的简历摘要（去冗余、保关键）。"""
    parts: list[str] = []
    b = resume.basic
    parts.append(f"姓名：{b.name or '未填'}；地区：{b.address or '未填'}")
    if resume.job_intent.target_role:
        parts.append(f"求职意向：{resume.job_intent.target_role}")
    if resume.background.years_of_experience is not None:
        parts.append(f"工作年限：{resume.background.years_of_experience} 年")
    if resume.background.education:
        parts.append(
            "教育："
            + " / ".join(
                f"{e.school or ''} {e.degree or ''} {e.major or ''}".strip()
                for e in resume.background.education[:3]
            )
        )
    if resume.background.experience:
        parts.append(
            "工作经历："
            + "；".join(
                f"{e.company or ''} {e.role or ''}（{e.start or '?'}-{e.end or '?'}）"
                for e in resume.background.experience[:5]
            )
        )
    if resume.background.projects:
        parts.append(
            "项目：" + "；".join((p.name or "") for p in resume.background.projects[:5] if p.name)
        )
    return "\n".join(parts)


async def llm_score(
    resume: ResumeAggregate,
    jd_text: str,
    *,
    client: MiMoClient | None = None,
) -> tuple[_LLMScoreSchema, TokenUsage] | None:
    """LLM 精准评分；失败返回 None（融合时跳过）。"""
    cli = client or get_llm_client()
    if not cli.is_configured:
        return None
    summary = _resume_summary_for_llm(resume)
    try:
        parsed, usage = await cli.chat_json(
            system=LLM_SCORE_SYSTEM,
            user=llm_score_user(summary, jd_text[:4000]),
            schema=_LLMScoreSchema,
            max_tokens=1024,
            temperature=0.2,
        )
        return parsed, usage
    except LLMError as e:
        logger.warning(
            "llm score failed; skipping",
            extra={"scope": "match", "err": str(e)},
        )
        return None


# ============================================================
# 融合（公开 API）
# ============================================================


async def match_resume(
    *,
    resume: ResumeAggregate,
    jd_keywords: JDKeywords,
    jd_text: str,
    use_llm_score: bool = True,
    score_strategy: str = "fusion",
    client: MiMoClient | None = None,
) -> tuple[MatchResult, TokenUsage | None, bool]:
    """统一入口：启发式 + 可选 LLM → 融合。

    Args:
        score_strategy: fusion / heuristic_only / llm_only

    Returns:
        (match_result, usage_or_None, cache_hit)
        cache 由调用方控制（接口层），此函数纯计算。
    """
    settings = get_settings()
    breakdown, h_total = heuristic_score(jd_keywords, resume)

    llm_usage: TokenUsage | None = None
    llm_summary = ""
    llm_strengths: list[str] = []
    llm_gaps: list[str] = []

    # LLM 评分路径
    if use_llm_score and score_strategy != "heuristic_only":
        llm_result = await llm_score(resume, jd_text, client=client)
        if llm_result is not None:
            llm_parsed, llm_usage = llm_result
            breakdown.llm_score = llm_parsed.score
            llm_summary = llm_parsed.summary
            llm_strengths = llm_parsed.strengths
            llm_gaps = llm_parsed.gaps

    # 计算最终分
    if score_strategy == "heuristic_only" or breakdown.llm_score is None:
        final = h_total
    elif score_strategy == "llm_only":
        final = breakdown.llm_score
    else:
        # fusion
        wh = settings.heuristic_weight
        wl = settings.llm_weight
        total_w = wh + wl if (wh + wl) > 0 else 1.0
        final = round((h_total * wh + breakdown.llm_score * wl) / total_w)

    final = max(0, min(100, final))

    # 构造 MatchResult
    skills = breakdown.skill_match
    if not llm_summary:
        # 启发式生成简短 summary
        llm_summary = _heuristic_summary(final, skills, breakdown.experience, breakdown.education)
    if not llm_strengths:
        llm_strengths = [f"命中必备技能：{', '.join(skills.must_hit)}"] if skills.must_hit else []
    if not llm_gaps:
        misses = []
        if skills.must_miss:
            misses.append(f"未命中必备：{', '.join(skills.must_miss)}")
        if breakdown.experience.score < 70:
            misses.append(
                f"经验年限不足（候选 {breakdown.experience.candidate_years} / 要求 {breakdown.experience.required_years} 年）"
            )
        if breakdown.education.score < 70:
            misses.append(
                f"学历低于要求（候选 {breakdown.education.candidate} / 要求 {breakdown.education.required}）"
            )
        llm_gaps = misses

    return (
        MatchResult(
            final_score=final,
            breakdown=breakdown,
            summary=llm_summary,
            strengths=llm_strengths,
            gaps=llm_gaps,
        ),
        llm_usage,
        False,
    )


def _heuristic_summary(
    final: int,
    skills: SkillBreakdown,
    exp: ExperienceBreakdown,
    edu: EducationBreakdown,
) -> str:
    """LLM 不可用时的 summary 兜底。"""
    if final >= 80:
        tag = "高度匹配"
    elif final >= 70:
        tag = "基本胜任"
    elif final >= 60:
        tag = "边缘可考虑"
    else:
        tag = "差距较大"
    must_rate = f"{len(skills.must_hit)}/{skills.must_total}" if skills.must_total else "N/A"
    return f"{tag}（必备命中 {must_rate}）"


# ============================================================
# 缓存键
# ============================================================


def match_cache_key(resume_id: str, jd_hash: str, use_llm_score: bool, strategy: str) -> str:
    flags = sha256_str(f"{int(use_llm_score)}:{strategy}")[:8]
    return CacheKeys.match(resume_id, jd_hash, flags)


async def get_cached_match(
    resume_id: str, jd_hash: str, use_llm_score: bool, strategy: str
) -> MatchResult | None:
    cache = get_cache()
    cached = await cache.get(match_cache_key(resume_id, jd_hash, use_llm_score, strategy))
    if cached is None:
        return None
    try:
        return MatchResult.model_validate(cached)
    except Exception:
        return None


async def save_match_cache(
    result: MatchResult,
    resume_id: str,
    jd_hash: str,
    use_llm_score: bool,
    strategy: str,
) -> None:
    cache = get_cache()
    settings = get_settings()
    await cache.set(
        match_cache_key(resume_id, jd_hash, use_llm_score, strategy),
        result.model_dump(),
        ttl=min(settings.cache_default_ttl, 86400),  # 评分缓存最多 1 天
    )


# 防止 asyncio 未使用 warning（gather 在路由层用）
_ = asyncio
