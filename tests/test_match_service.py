"""匹配评分服务单元测试

不调真实 LLM；mock client 与启发式纯函数验证：
- 学历归一化与等级
- 技能匹配（同义词、子串、命中率）
- 经验/学历/技能各自评分边界
- 启发式综合分
- 融合策略 fusion / heuristic_only / llm_only
- LLM 失败降级到纯启发式
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import LLMError
from app.models.match import (
    JDKeywords,
    JDRequirements,
)
from app.models.resume import (
    Education,
    Project,
    ResumeAggregate,
    ResumeBackground,
    ResumeBasic,
    ResumeJobIntent,
    WorkExperience,
)
from app.services import cache_service
from app.services.match_service import (
    _edu_level,
    _highest_edu,
    _normalize_edu,
    _normalize_skill,
    _score_education,
    _score_experience,
    _score_skills,
    heuristic_score,
    match_resume,
)


@pytest.fixture(autouse=True)
async def isolated_cache():
    cache_service.reset_cache()
    yield
    cache_service.reset_cache()


def _make_resume(
    *,
    yoe: float | None = 5.0,
    edu_degree: str | None = "本科",
    edu_school: str = "Peking University",
    skills_in_text: str = "",
    target_role: str | None = None,
) -> ResumeAggregate:
    return ResumeAggregate(
        resume_id="rsm_test12345678",
        basic=ResumeBasic(name="张三"),
        job_intent=ResumeJobIntent(target_role=target_role),
        background=ResumeBackground(
            years_of_experience=yoe,
            education=[Education(school=edu_school, degree=edu_degree, major="CS")]
            if edu_degree
            else [],
            experience=[
                WorkExperience(company="ACME", role="Backend Engineer", summary=skills_in_text)
            ],
            projects=[Project(name="P1", summary=skills_in_text)],
        ),
        text_preview=skills_in_text,
    )


# ============================================================
# 学历归一
# ============================================================


def test_normalize_edu_variants():
    assert _normalize_edu("本科") == "本科"
    assert _normalize_edu("学士") == "学士"
    assert _normalize_edu("Bachelor of CS") == "bachelor of cs"
    assert _normalize_edu("研究生") == "研究生"
    assert _normalize_edu(None) is None


def test_edu_level_ordering():
    assert _edu_level("博士") > _edu_level("硕士")
    assert _edu_level("硕士") > _edu_level("本科")
    assert _edu_level("本科") > _edu_level("大专")
    assert _edu_level("大专") > _edu_level("高中")
    assert _edu_level("研究生") == _edu_level("硕士")
    assert _edu_level("学士") == _edu_level("本科")


def test_highest_edu_picks_max():
    edus = [
        Education(school="A", degree="本科"),
        Education(school="B", degree="硕士"),
    ]
    assert _highest_edu(edus) == "硕士"


def test_highest_edu_empty():
    assert _highest_edu([]) is None


# ============================================================
# 技能归一
# ============================================================


def test_normalize_skill_aliases():
    assert _normalize_skill("Python3") == "python"
    assert _normalize_skill("K8s") == "kubernetes"
    assert _normalize_skill("Postgres") == "postgresql"
    assert _normalize_skill("Node.JS") == "node.js"
    assert _normalize_skill(" Python ") == "python"


# ============================================================
# 技能评分
# ============================================================


def test_skill_score_full_match():
    jd = JDKeywords(
        jd_hash="x",
        skills=["Python", "MySQL"],
        responsibilities=[],
        requirements=JDRequirements(must_have=["Python", "MySQL"], nice_to_have=[]),
    )
    resume = _make_resume(skills_in_text="后端开发，使用 Python FastAPI MySQL")
    score = _score_skills(jd, resume)
    assert score.score == 80  # must 100% * 0.8 = 80（无 nice）
    assert "Python" in score.must_hit
    assert "MySQL" in score.must_hit


def test_skill_score_partial():
    jd = JDKeywords(
        jd_hash="x",
        skills=["Python", "MySQL", "Redis"],
        responsibilities=[],
        requirements=JDRequirements(
            must_have=["Python", "MySQL", "Kubernetes"],
            nice_to_have=["Redis"],
        ),
    )
    resume = _make_resume(skills_in_text="Python, MySQL, Redis used")
    score = _score_skills(jd, resume)
    # must 命中 2/3 = 66.67% * 80 = 53.33；nice 命中 1/1 = 100% * 20 = 20；总 73.33 → 73
    assert 70 <= score.score <= 76
    assert "Kubernetes" in score.must_miss


def test_skill_score_no_keywords_returns_neutral():
    jd = JDKeywords(jd_hash="x", skills=[], responsibilities=[], requirements=JDRequirements())
    resume = _make_resume()
    score = _score_skills(jd, resume)
    assert score.score == 50


def test_skill_synonym_hit():
    jd = JDKeywords(
        jd_hash="x",
        skills=["Kubernetes"],
        responsibilities=[],
        requirements=JDRequirements(must_have=["Kubernetes"]),
    )
    # 简历用 K8s 写
    resume = _make_resume(skills_in_text="项目使用 K8s 部署")
    score = _score_skills(jd, resume)
    assert "Kubernetes" in score.must_hit


# ============================================================
# 经验评分
# ============================================================


def test_experience_meets_requirement():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(min_years=3))
    resume = _make_resume(yoe=5.0)
    score = _score_experience(jd, resume)
    assert score.score == 100


def test_experience_short_by_one_year_score_90():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(min_years=5))
    resume = _make_resume(yoe=4.0)
    score = _score_experience(jd, resume)
    assert score.score == 90


def test_experience_short_by_three_year_score_70():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(min_years=5))
    resume = _make_resume(yoe=2.0)
    score = _score_experience(jd, resume)
    assert score.score == 70


def test_experience_no_required_returns_70():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements())
    resume = _make_resume(yoe=5.0)
    assert _score_experience(jd, resume).score == 70


def test_experience_no_candidate_returns_40():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(min_years=3))
    resume = _make_resume(yoe=None)
    assert _score_experience(jd, resume).score == 40


# ============================================================
# 学历评分
# ============================================================


def test_education_meets():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(education="本科"))
    resume = _make_resume(edu_degree="本科")
    assert _score_education(jd, resume).score == 100


def test_education_higher_than_required():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(education="本科"))
    resume = _make_resume(edu_degree="博士")
    assert _score_education(jd, resume).score == 100


def test_education_one_step_lower():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(education="本科"))
    resume = _make_resume(edu_degree="大专")
    assert _score_education(jd, resume).score == 70


def test_education_two_steps_lower():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(education="本科"))
    resume = _make_resume(edu_degree="高中")
    assert _score_education(jd, resume).score == 40


def test_education_unknown_returns_40():
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(education="本科"))
    resume = _make_resume(edu_degree=None)  # 无学历
    assert _score_education(jd, resume).score == 40


# ============================================================
# 启发式综合
# ============================================================


def test_heuristic_total_within_bounds():
    jd = JDKeywords(
        jd_hash="x",
        skills=["Python"],
        requirements=JDRequirements(must_have=["Python"], min_years=3, education="本科"),
    )
    resume = _make_resume(skills_in_text="Python", yoe=5.0, edu_degree="本科")
    breakdown, total = heuristic_score(jd, resume)
    assert 0 <= total <= 100
    assert breakdown.skill_match.score >= 0
    assert breakdown.heuristic_total == total


def test_heuristic_total_perfect():
    jd = JDKeywords(
        jd_hash="x",
        requirements=JDRequirements(must_have=[], min_years=None, education=None),
    )
    resume = _make_resume(yoe=5.0, edu_degree="本科")
    _, total = heuristic_score(jd, resume)
    # 三段都给中性/满分附近
    assert total >= 50


# ============================================================
# 融合策略
# ============================================================


@pytest.fixture
def mock_llm():
    cli = MagicMock()
    cli.is_configured = True
    cli.chat_json = AsyncMock()
    return cli


async def test_fusion_combines_heuristic_and_llm(mock_llm):
    """融合分应在两个分之间。"""
    from app.services.match_service import _LLMScoreSchema

    mock_llm.chat_json.return_value = (
        _LLMScoreSchema(score=60, summary="一般", strengths=[], gaps=[]),
        MagicMock(total_tokens=200),
    )
    jd = JDKeywords(
        jd_hash="x",
        requirements=JDRequirements(must_have=["Python"], min_years=3, education="本科"),
    )
    resume = _make_resume(skills_in_text="Python", yoe=5.0, edu_degree="本科")

    result, _, _ = await match_resume(
        resume=resume,
        jd_keywords=jd,
        jd_text="some jd",
        use_llm_score=True,
        score_strategy="fusion",
        client=mock_llm,
    )
    assert result.breakdown.llm_score == 60
    # heuristic 至少 60+，llm=60 → 融合在 60-100 之间
    assert 55 <= result.final_score <= 100


async def test_heuristic_only_skips_llm(mock_llm):
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(must_have=["Python"]))
    resume = _make_resume(skills_in_text="Python")
    result, usage, _ = await match_resume(
        resume=resume,
        jd_keywords=jd,
        jd_text="x",
        use_llm_score=True,
        score_strategy="heuristic_only",
        client=mock_llm,
    )
    assert result.breakdown.llm_score is None
    assert usage is None
    mock_llm.chat_json.assert_not_awaited()


async def test_llm_failure_falls_back_to_heuristic(mock_llm):
    """LLM 失败时不影响最终分（仍走启发式）。"""
    mock_llm.chat_json.side_effect = LLMError("boom")
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(must_have=["Python"]))
    resume = _make_resume(skills_in_text="Python")
    result, usage, _ = await match_resume(
        resume=resume,
        jd_keywords=jd,
        jd_text="x",
        use_llm_score=True,
        score_strategy="fusion",
        client=mock_llm,
    )
    assert result.breakdown.llm_score is None
    assert result.final_score == result.breakdown.heuristic_total  # 没 LLM 时融合 = 启发式
    assert usage is None  # LLM 失败不计 token


async def test_use_llm_score_false_skips_llm(mock_llm):
    jd = JDKeywords(jd_hash="x", requirements=JDRequirements(must_have=["Python"]))
    resume = _make_resume(skills_in_text="Python")
    await match_resume(
        resume=resume,
        jd_keywords=jd,
        jd_text="x",
        use_llm_score=False,
        client=mock_llm,
    )
    mock_llm.chat_json.assert_not_awaited()
