"""JD 与匹配评分相关模型"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ============================================================
# JD 关键词
# ============================================================


class JDRequirements(BaseModel):
    """JD 提取出的硬性要求。"""

    min_years: int | None = Field(default=None, description="最低工作年限要求")
    education: str | None = Field(
        default=None, description="最低学历要求（博士/硕士/本科/大专/高中/其他）"
    )
    must_have: list[str] = Field(default_factory=list, description="必备技能")
    nice_to_have: list[str] = Field(default_factory=list, description="加分项技能")


class JDKeywords(BaseModel):
    """POST /api/jd/keywords 的响应 data。"""

    jd_hash: str = Field(description="岗位描述文本的哈希，便于复用")
    skills: list[str] = Field(default_factory=list, description="提到的所有技能/工具")
    responsibilities: list[str] = Field(default_factory=list, description="主要职责")
    requirements: JDRequirements = Field(default_factory=JDRequirements)


# ============================================================
# 评分
# ============================================================


class SkillBreakdown(BaseModel):
    """技能匹配明细。"""

    score: int = Field(ge=0, le=100, description="技能得分 0-100")
    must_total: int = Field(default=0, description="必备技能总数")
    must_hit: list[str] = Field(default_factory=list, description="命中的必备")
    must_miss: list[str] = Field(default_factory=list, description="未命中的必备")
    nice_total: int = Field(default=0, description="加分项总数")
    nice_hit: list[str] = Field(default_factory=list, description="命中的加分项")


class ExperienceBreakdown(BaseModel):
    """经验匹配明细。"""

    score: int = Field(ge=0, le=100)
    candidate_years: float | None = Field(default=None)
    required_years: int | None = Field(default=None)


class EducationBreakdown(BaseModel):
    """学历匹配明细。"""

    score: int = Field(ge=0, le=100)
    candidate: str | None = Field(default=None)
    required: str | None = Field(default=None)


class ScoreBreakdown(BaseModel):
    """评分明细。"""

    skill_match: SkillBreakdown
    experience: ExperienceBreakdown
    education: EducationBreakdown
    heuristic_total: int = Field(ge=0, le=100, description="启发式综合分")
    llm_score: int | None = Field(default=None, description="LLM 评分（可选）")
    weights: dict = Field(default_factory=dict, description="融合权重快照")


class MatchResult(BaseModel):
    """POST /api/match 的响应 data。"""

    final_score: int = Field(ge=0, le=100, description="最终匹配分 0-100")
    breakdown: ScoreBreakdown
    summary: str = Field(default="", description="一句话总结")
    strengths: list[str] = Field(default_factory=list, description="候选人优势")
    gaps: list[str] = Field(default_factory=list, description="不匹配项")


# ============================================================
# 接口请求体
# ============================================================


class JDKeywordsRequest(BaseModel):
    """POST /api/jd/keywords 请求体。"""

    model_config = ConfigDict(extra="forbid")

    jd_text: str = Field(min_length=10, max_length=20000, description="岗位描述文本")


class MatchRequest(BaseModel):
    """POST /api/match 请求体。

    `jd_text` 与 `jd_hash` 至少传一个；都传时优先用 jd_hash（已缓存）。
    """

    model_config = ConfigDict(extra="forbid")

    resume_id: str = Field(pattern=r"^rsm_[a-f0-9]{12}$")
    jd_text: str | None = Field(default=None, max_length=20000)
    jd_hash: str | None = Field(default=None, max_length=64)
    use_llm_score: bool = Field(default=True, description="是否启用 LLM 精准评分")
    score_strategy: Literal["fusion", "heuristic_only", "llm_only"] = Field(
        default="fusion", description="评分策略；fusion 走融合，*_only 跳过另一侧"
    )
