"""简历-岗位匹配评分服务

两层评分：
1. 启发式（规则 + 关键词命中 + 经验/学历加权），快、稳、可解释
2. LLM 精准评分（语义级），加分项；可通过 use_llm_score=False 关闭

融合：final = heuristic_weight * heuristic + llm_weight * llm_score（权重在 settings）

为什么是「启发式 + LLM 融合」而不是单选其一？
- 启发式：可解释（HR 必须能告诉候选人为啥 75 分）+ 稳定（同输入同输出）+ 零成本（无 LLM 调用）
- LLM：语义级（捕获改写后启发式抓不到的"暗信号"）+ 灵活（理解上下文）
- 融合：取两者所长，权重可配；LLM 不可用时自动退化为纯启发式
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
#
# 设计：用整数等级表达学历高低，便于做差值比较
# 数字越大 = 学历越高
# 同等级别名（如 "学士" = "本科" = 3）让归一更鲁棒
# ============================================================

_EDU_LEVEL = {
    "高中": 1,
    "中专": 1,  # 中专与高中同级
    "大专": 2,
    "专科": 2,  # 专科 = 大专（不同地区叫法）
    "本科": 3,
    "学士": 3,  # 学士 = 本科（学位 vs 学历叫法）
    "硕士": 4,
    "研究生": 4,  # 国内招聘场景"研究生"通常指硕士
    "博士": 5,
    "phd": 5,  # 兼容英文（小写后匹配）
}


def _normalize_edu(s: str | None) -> str | None:
    """学历字符串归一为枚举键。

    策略：小写后查表中是否包含某个枚举键作为子串
    例：'Master of Science' → 'master' (不在表里) → 原值返回
        '研究生学历' → 包含'研究生' → 'research生'... 等等

    注意：这里实际是 substring 匹配，'本科生' 也会被识别为 '本科'
    """
    if not s:
        return None
    s = s.strip().lower()
    # 遍历表里的键，看是否作为 s 的子串出现
    # 这样 '本科生' / '本科毕业' 都能命中 '本科'
    for key in _EDU_LEVEL:
        if key in s:
            return key
    # 不命中表内任何键时返回小写原值（用于后续比较时识别 unknown）
    return s


def _edu_level(s: str | None) -> int | None:
    """学历等级数值（越高越好）；未知返回 None。"""
    if not s:
        return None
    norm = _normalize_edu(s)
    return _EDU_LEVEL.get(norm) if norm else None


def _highest_edu(educations: list) -> str | None:
    """从教育经历列表中找最高学历。

    简历里常有「本科 + 硕士」两段教育，取最高那段做 JD 比对。
    """
    best: tuple[int, str] | None = None
    for edu in educations:
        # getattr 防止字段缺失（pydantic 模型理论上不会，但兜底）
        deg = getattr(edu, "degree", None)
        lvl = _edu_level(deg)
        if lvl is None:
            continue  # 这段教育没解析出学历级别，跳过
        # 维护一个最大值
        if best is None or lvl > best[0]:
            best = (lvl, deg)
    return best[1] if best else None


# ============================================================
# 技能匹配（轻量归一化）
#
# 业务问题：JD 写 "Kubernetes" 候选简历写 "K8s"——纯字符串相等会漏匹
# 解决：同义词归一表 + 子串兜底（处理多词技能）
# ============================================================

# 单向归一表：alias → canonical
# 扩展只需改这里一处；归一只查一次表
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
    """技能名归一：小写、去空格、查同义词表。

    例：
        'Python3'   → 'python'
        ' K8s '     → 'kubernetes'
        'Node.JS'   → 'node.js'
    """
    if not s:
        return ""
    # 小写 + 去掉所有空白字符（'machine learning' 会被合并）
    norm = re.sub(r"\s+", "", s.strip().lower())
    # 查同义词表，没命中就返回归一后的原值
    return _SKILL_ALIAS.get(norm, norm)


def _build_resume_skill_set(resume: ResumeAggregate) -> set[str]:
    """从简历中收集所有可能的技能词，用于命中匹配。

    设计：不接 NER 模型——简历里技能名基本是英文（Python/FastAPI/MySQL/C++/C#），
    用正则抓所有英文词足够，零依赖、零模型成本。
    中文部分靠子串匹配兜底。

    数据源：项目摘要 + 工作摘要 + 教育专业 + 求职意向 + 文本预览
    （越全面命中率越高，但要避免冗余拖慢匹配）
    """
    pool: list[str] = []
    # 项目经历的摘要 + 名称
    for proj in resume.background.projects:
        if proj.summary:
            pool.append(proj.summary)
        if proj.name:
            pool.append(proj.name)
    # 工作经历的摘要 + 职位名
    for exp in resume.background.experience:
        if exp.summary:
            pool.append(exp.summary)
        if exp.role:
            pool.append(exp.role)
    # 教育的专业（如 "Computer Science" 包含 'computer'）
    for edu in resume.background.education:
        if edu.major:
            pool.append(edu.major)
    # 求职意向（"Python 后端" 含 python）
    if resume.job_intent.target_role:
        pool.append(resume.job_intent.target_role)
    # 文本预览兜底（多 200 字符无害）
    pool.append(resume.text_preview)

    # 整段文本归一：取所有英文词（含 .+- 等技能里常见字符如 "C++" "Node.js"）
    blob = " ".join(pool).lower()
    return {
        _normalize_skill(w)
        # [a-zA-Z][a-zA-Z0-9.+\-#]+ 匹配以字母开头、含字母/数字/常见符号的词
        # 这样能保留 "C++" "C#" "Node.js" "F#" 等完整技能名
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9.+\-#]+|\u4e00-\u9fa5+", blob)
        if w
    }


def _is_skill_hit(skill: str, resume_skills: set[str], blob: str) -> bool:
    """判断单个 JD 技能是否在简历中。

    两条路径：
    1. 归一后与 resume_skills 集合相交（精确匹配）
    2. 在简历全文（blob）做 substring 匹配（兜底）

    第二层兜底处理多词技能：
    - "machine learning" 会被分词为 ["machine", "learning"] 两个词存进集合
    - 但 JD 里是连起来的 "machine learning"，归一后还是 "machinelearning"
    - 集合相交命中不到，但 blob 里能找到 "machine learning" 子串
    """
    norm = _normalize_skill(skill)
    if not norm:
        return False
    # 第一层：归一集合相交
    if norm in resume_skills:
        return True
    # 第二层：原 skill 小写后在简历全文做 substring
    return skill.strip().lower() in blob


# ============================================================
# 启发式评分：三个维度分项 + 加权综合
# ============================================================


def _score_skills(jd: JDKeywords, resume: ResumeAggregate) -> SkillBreakdown:
    """技能命中评分。

    评分公式：
        must_have 命中率 * 80% + nice_to_have 命中率 * 20%

    为什么必备 80% / 加分项 20%？
    - 必备不命中是大问题，加分项不命中只少几分
    - 这个权重在拼写错误的简历上特别重要——必备 1/4 vs 4/4 不能给一样的分

    三档退化：
    - JD 有必备 → 走 80/20 公式
    - JD 只有加分项 → 100% × nice_rate（公平评分）
    - JD 啥都没 → 中性 50（不公平打 0 分）
    """
    # 简历全文 blob：用于 substring 兜底匹配（处理多词技能）
    # 字段顺序无所谓，反正全部 lower() 后拼接
    blob = " ".join(
        [
            resume.text_preview or "",
            *[p.summary or "" for p in resume.background.projects],
            *[p.name or "" for p in resume.background.projects],
            *[e.summary or "" for e in resume.background.experience],
            *[e.role or "" for e in resume.background.experience],
        ]
    ).lower()
    # 简历的归一技能集合（用于精确匹配）
    resume_skills = _build_resume_skill_set(resume)

    must = jd.requirements.must_have or []
    nice = jd.requirements.nice_to_have or []

    # 列表推导式：分别计算命中和未命中（must_miss 用于 UI 展示"缺什么"）
    must_hit = [s for s in must if _is_skill_hit(s, resume_skills, blob)]
    must_miss = [s for s in must if s not in must_hit]
    nice_hit = [s for s in nice if _is_skill_hit(s, resume_skills, blob)]

    # 三档评分逻辑
    if not must and not nice:
        # JD 没给任何关键词 → 中性分 50
        # 不公平打 0 分（不是候选人的问题），也不打 100（不公平加分）
        score = 50
    elif not must:
        # 只有 nice → 100% × 命中率
        score = round(len(nice_hit) / len(nice) * 100) if nice else 50
    else:
        # 标准公式：必备 80% + 加分项 20%
        must_rate = len(must_hit) / len(must)
        nice_rate = (len(nice_hit) / len(nice)) if nice else 0
        score = round(must_rate * 80 + nice_rate * 20)

    return SkillBreakdown(
        # max(0, min(100, ...)) 防越界（理论上不会但兜底）
        score=max(0, min(100, score)),
        must_total=len(must),
        must_hit=must_hit,
        must_miss=must_miss,
        nice_total=len(nice),
        nice_hit=nice_hit,
    )


def _score_experience(jd: JDKeywords, resume: ResumeAggregate) -> ExperienceBreakdown:
    """经验年限评分：每差 1 年扣 10 分；满足或超出 100 分。

    公式：
        diff = candidate_years - required_years
        diff >= 0 → 100 分
        diff < 0  → max(0, 100 + diff * 10)

    例：
        候选 5 年 vs 要求 3 年 = 100（满足即满分，不奖励超出）
        候选 2 年 vs 要求 5 年 = 70（差 3 年还能进面试池）
        候选 0 年 vs 要求 10 年 = 0（差太多）

    为什么不奖励超出？
        避免引导候选人虚报年限。10 年和 5 年都能干 3 年的活，没必要差异化
    """
    candidate = resume.background.years_of_experience
    required = jd.requirements.min_years

    # JD 没给年限要求 → 中性偏上分（70）
    # 给 70 不给 100：HR 既然没要求就别奖励，但也别惩罚
    if required is None:
        return ExperienceBreakdown(score=70, candidate_years=candidate, required_years=None)
    # 简历没年限信息 → 偏低分（40）
    # 信息缺失带来不确定性，谨慎给分
    if candidate is None:
        return ExperienceBreakdown(score=40, candidate_years=None, required_years=required)

    diff = candidate - required
    if diff >= 0:
        score = 100  # 满足或超出
    else:
        # 每差 1 年扣 10 分；max(0, ...) 让差 10+ 年都是 0
        score = max(0, round(100 + diff * 10))
    return ExperienceBreakdown(score=score, candidate_years=candidate, required_years=required)


def _score_education(jd: JDKeywords, resume: ResumeAggregate) -> EducationBreakdown:
    """学历评分：阶梯式扣分。

    评分表：
        候选 ≥ 要求       = 100
        低 1 级（如本→专） = 70
        低 2 级（如本→高） = 40
        低 3+ 级           = 20

    用阶梯而不是线性：学历是离散等级，"本科 vs 硕士"差距和"本科 vs 大专"
    差距感知不一样。线性公式无法体现这种非线性。
    """
    required = jd.requirements.education
    candidate = _highest_edu(resume.background.education)

    # 同 _score_experience 的兜底逻辑
    if required is None:
        return EducationBreakdown(score=70, candidate=candidate, required=None)
    if candidate is None:
        return EducationBreakdown(score=40, candidate=None, required=required)

    cand_lvl = _edu_level(candidate)
    req_lvl = _edu_level(required)
    # 任一级别解析失败 → 中性 70（不可比较时谨慎给分）
    if cand_lvl is None or req_lvl is None:
        return EducationBreakdown(score=70, candidate=candidate, required=required)

    diff = cand_lvl - req_lvl
    if diff >= 0:
        score = 100  # 等于或高于
    elif diff == -1:
        score = 70  # 低 1 级（如要求本科候选大专）
    elif diff == -2:
        score = 40  # 低 2 级
    else:
        score = 20  # 低 3+ 级（差距过大）
    return EducationBreakdown(score=score, candidate=candidate, required=required)


def heuristic_score(jd: JDKeywords, resume: ResumeAggregate) -> tuple[ScoreBreakdown, int]:
    """启发式综合评分。

    公式：
        total = (skill * w_skill + exp * w_exp + edu * w_edu) / (w_skill + w_exp + w_edu)

    默认权重 0.5 / 0.3 / 0.2 反映招聘常识：
        - 技能 50%：HR 看简历最先扫的就是技能关键词
        - 经验 30%：硬约束但允许差几年
        - 学历 20%：软约束，互联网行业逐年降权重

    权重从 settings 读，跑生产时改环境变量就能调，不用重新部署。
    权重归一防止用户配置和不为 1（如设了 0.6/0.4/0.3 = 1.3）

    Returns:
        (breakdown, total) breakdown 含三段明细，total 是综合分
    """
    settings = get_settings()
    skills = _score_skills(jd, resume)
    exp = _score_experience(jd, resume)
    edu = _score_education(jd, resume)

    # 权重归一：不要求和必须为 1，自动按比例分配
    w_skill = settings.skill_weight
    w_exp = settings.experience_weight
    w_edu = settings.education_weight
    total_w = w_skill + w_exp + w_edu
    # 防御：用户设全 0 的极端情况，用 1.0 兜底（避免除 0）
    if total_w <= 0:
        total_w = 1.0
    weighted = (skills.score * w_skill + exp.score * w_exp + edu.score * w_edu) / total_w
    # 防越界（理论上不会，但 round 后可能出现 101 / -1 这种）
    total = max(0, min(100, round(weighted)))

    breakdown = ScoreBreakdown(
        skill_match=skills,
        experience=exp,
        education=edu,
        heuristic_total=total,
        # llm_score 留空，后续 match_resume 填
        llm_score=None,
        # 把权重快照存进响应——便于前端展示"为什么这样算"
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
    """LLM 输出 schema：让 LLM 严格输出这 4 个字段。"""

    score: int = Field(ge=0, le=100)
    summary: str = Field(default="")
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


def _resume_summary_for_llm(resume: ResumeAggregate) -> str:
    """构造给 LLM 的简历摘要（去冗余、保关键）。

    为什么不直接把整份 ResumeAggregate JSON 喂给 LLM？
    - 浪费 token：很多字段（pages / char_count / extract_errors）评分不需要
    - 不利于模型聚焦：太多噪音字段会让评分偏离重点

    输出格式：自然语言短句，每行一个维度，对 LLM 友好
    """
    parts: list[str] = []
    b = resume.basic
    parts.append(f"姓名：{b.name or '未填'}；地区：{b.address or '未填'}")
    if resume.job_intent.target_role:
        parts.append(f"求职意向：{resume.job_intent.target_role}")
    if resume.background.years_of_experience is not None:
        parts.append(f"工作年限：{resume.background.years_of_experience} 年")
    if resume.background.education:
        # 拼成 "学校 学位 专业" 单行，最多 3 段
        parts.append(
            "教育："
            + " / ".join(
                f"{e.school or ''} {e.degree or ''} {e.major or ''}".strip()
                for e in resume.background.education[:3]
            )
        )
    if resume.background.experience:
        # 工作经历：公司 职位（起止）格式，最多 5 段
        parts.append(
            "工作经历："
            + "；".join(
                f"{e.company or ''} {e.role or ''}（{e.start or '?'}-{e.end or '?'}）"
                for e in resume.background.experience[:5]
            )
        )
    if resume.background.projects:
        # 项目只取名字（详细描述太占 token）
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
    """LLM 精准评分；失败返回 None（融合时跳过）。

    设计：失败不抛异常，让上游 match_resume 自动降级到纯启发式。
    这是「LLM 不可用是失败但不是错误」的工程哲学。
    """
    cli = client or get_llm_client()
    # 未配置 API key → 直接跳过 LLM 评分（不算失败，也不抛错）
    if not cli.is_configured:
        return None
    summary = _resume_summary_for_llm(resume)
    try:
        # jd_text[:4000] 截断防止超长 JD 把 token 跑飞
        parsed, usage = await cli.chat_json(
            system=LLM_SCORE_SYSTEM,
            user=llm_score_user(summary, jd_text[:4000]),
            schema=_LLMScoreSchema,
            max_tokens=1024,
            # temperature 0.2：评分要相对稳定，但允许一点变化（让 strengths/gaps 表述自然）
            temperature=0.2,
        )
        return parsed, usage
    except LLMError as e:
        # LLM 失败（超时/限流/JSON 解析失败）→ 返回 None 让上游降级
        # 注意：不重新抛错，因为评分模块不是必选——LLM 挂了启发式照样能跑
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

    score_strategy 三档：
    - fusion       : 启发式 0.6 + LLM 0.4 加权（默认；LLM 失败自动降级到 heuristic_only）
    - heuristic_only: 跳过 LLM 调用——节省 token、降低延迟
    - llm_only     : 纯 LLM 评分——为 A/B 实验准备

    Args:
        resume: 候选人结构化简历（来自 extract_service 的输出）
        jd_keywords: JD 关键词（来自 jd_service 的输出）
        jd_text: JD 原文（LLM 评分需要原始上下文，启发式不需要）
        use_llm_score: 是否启用 LLM（前端可关，省钱）
        score_strategy: 见上面三档
        client: LLM 客户端（可注入 mock 用于测试）

    Returns:
        (match_result, usage_or_None, cache_hit)
        cache 由调用方控制（接口层），此函数纯计算
    """
    settings = get_settings()
    # 第一步：启发式评分（无 LLM 调用，恒定快）
    breakdown, h_total = heuristic_score(jd_keywords, resume)

    # 第二步：可选 LLM 评分（默认开启）
    llm_usage: TokenUsage | None = None
    llm_summary = ""
    llm_strengths: list[str] = []
    llm_gaps: list[str] = []

    if use_llm_score and score_strategy != "heuristic_only":
        llm_result = await llm_score(resume, jd_text, client=client)
        # llm_result 为 None 表示 LLM 失败/未配置，不影响后续
        if llm_result is not None:
            llm_parsed, llm_usage = llm_result
            # 把 LLM 分填进 breakdown，方便前端展示"启发式 X，AI Y"对比
            breakdown.llm_score = llm_parsed.score
            llm_summary = llm_parsed.summary
            llm_strengths = llm_parsed.strengths
            llm_gaps = llm_parsed.gaps

    # 第三步：根据策略计算最终分
    if score_strategy == "heuristic_only" or breakdown.llm_score is None:
        # heuristic_only 路径 / LLM 失败降级路径：纯启发式
        final = h_total
    elif score_strategy == "llm_only":
        # 纯 LLM 路径
        final = breakdown.llm_score
    else:
        # fusion 路径：启发式 × wh + LLM × wl
        wh = settings.heuristic_weight
        wl = settings.llm_weight
        # 防御：用户配 0/0 的极端情况
        total_w = wh + wl if (wh + wl) > 0 else 1.0
        final = round((h_total * wh + breakdown.llm_score * wl) / total_w)

    # 防越界
    final = max(0, min(100, final))

    # 第四步：生成 summary / strengths / gaps（LLM 不可用时启发式兜底）
    skills = breakdown.skill_match
    if not llm_summary:
        # LLM 没给 summary → 启发式生成简短 summary
        # （如 "高度匹配（必备命中 3/4）"）
        llm_summary = _heuristic_summary(final, skills, breakdown.experience, breakdown.education)
    if not llm_strengths:
        # 优势：从命中的必备技能里提取
        llm_strengths = [f"命中必备技能：{', '.join(skills.must_hit)}"] if skills.must_hit else []
    if not llm_gaps:
        # 差距：从未命中的必备 + 不达标的经验/学历里提取
        misses = []
        if skills.must_miss:
            misses.append(f"未命中必备：{', '.join(skills.must_miss)}")
        if breakdown.experience.score < 70:
            misses.append(
                f"经验年限不足（候选 {breakdown.experience.candidate_years} / "
                f"要求 {breakdown.experience.required_years} 年）"
            )
        if breakdown.education.score < 70:
            misses.append(
                f"学历低于要求（候选 {breakdown.education.candidate} / "
                f"要求 {breakdown.education.required}）"
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
        # cache_hit 由接口层控制；这里始终返 False（纯计算无缓存）
        False,
    )


def _heuristic_summary(
    final: int,
    skills: SkillBreakdown,
    exp: ExperienceBreakdown,
    edu: EducationBreakdown,
) -> str:
    """LLM 不可用时的 summary 兜底。

    把分数映射到中文档位标签 + 必备命中信息。
    例：'高度匹配（必备命中 3/4）'
    """
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
# 评分缓存
#
# 缓存键：(resume_id, jd_hash, flags_hash)
# - resume_id: 简历唯一 ID
# - jd_hash:  JD 内容哈希
# - flags_hash: use_llm_score + strategy 的哈希（不同策略不共享缓存）
# ============================================================


def match_cache_key(resume_id: str, jd_hash: str, use_llm_score: bool, strategy: str) -> str:
    """构造评分缓存键。

    flags 用 hash 而不是直接拼接：未来加新参数（如 weights 配置）改 flags 计算
    一处即可，不破坏 key 命名约定。
    """
    flags = sha256_str(f"{int(use_llm_score)}:{strategy}")[:8]
    return CacheKeys.match(resume_id, jd_hash, flags)


async def get_cached_match(
    resume_id: str, jd_hash: str, use_llm_score: bool, strategy: str
) -> MatchResult | None:
    """查评分缓存；schema 不兼容时返回 None 而非抛错。"""
    cache = get_cache()
    cached = await cache.get(match_cache_key(resume_id, jd_hash, use_llm_score, strategy))
    if cached is None:
        return None
    try:
        return MatchResult.model_validate(cached)
    except Exception:
        # 旧缓存 schema 不兼容（升级常见）→ 返 None 让上游重算
        return None


async def save_match_cache(
    result: MatchResult,
    resume_id: str,
    jd_hash: str,
    use_llm_score: bool,
    strategy: str,
) -> None:
    """写评分缓存。

    评分缓存最长 1 天——评分逻辑可能升级（权重调整、prompt 修改），
    长期缓存反而妨碍迭代。
    """
    cache = get_cache()
    settings = get_settings()
    await cache.set(
        match_cache_key(resume_id, jd_hash, use_llm_score, strategy),
        result.model_dump(),
        # min(default, 1天) 双保险
        ttl=min(settings.cache_default_ttl, 86400),
    )


# 防止 asyncio 未使用 warning（gather 在路由层用）
_ = asyncio
