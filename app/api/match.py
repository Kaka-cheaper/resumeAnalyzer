"""简历-岗位匹配评分路由"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.core.exceptions import MissingParameterError, ResumeNotFoundError
from app.core.response import get_request_context, make_meta
from app.models.common import APIResponse
from app.models.match import MatchRequest
from app.models.resume import ResumeAggregate
from app.services import resume_store
from app.services.extract_service import (
    extract_background,
    extract_basic,
    extract_job_intent,
)
from app.services.jd_service import extract_jd_keywords, get_jd_keywords_by_hash
from app.services.match_service import (
    get_cached_match,
    match_resume,
    save_match_cache,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["match"])


@router.post(
    "/match",
    summary="简历-岗位匹配评分",
    description=(
        "根据 `resume_id` 和岗位描述（或已缓存的 jd_hash），"
        "计算简历与岗位的匹配度。\n\n"
        "**两层评分**：启发式（技能/经验/学历加权）+ LLM 精准评分（语义级），"
        "按权重 `heuristic_weight:llm_weight = 0.6:0.4` 融合。\n"
        "**关闭 LLM**：传 `use_llm_score=false` 走纯启发式，更快、零 LLM 成本。\n"
        "**幂等**：同一 (resume_id, jd_hash, flags) 命中缓存。"
    ),
    responses={
        200: {
            "description": "评分成功",
            "content": {
                "application/json": {
                    "example": {
                        "code": "OK",
                        "message": "success",
                        "data": {
                            "final_score": 82,
                            "breakdown": {
                                "skill_match": {
                                    "score": 85,
                                    "must_hit": ["Python", "MySQL"],
                                    "must_miss": ["Kubernetes"],
                                    "nice_hit": ["Redis"],
                                },
                                "experience": {
                                    "score": 100,
                                    "candidate_years": 5.0,
                                    "required_years": 3,
                                },
                                "education": {
                                    "score": 100,
                                    "candidate": "本科",
                                    "required": "本科",
                                },
                                "heuristic_total": 88,
                                "llm_score": 78,
                                "weights": {
                                    "skill": 0.5,
                                    "experience": 0.3,
                                    "education": 0.2,
                                    "heuristic": 0.6,
                                    "llm": 0.4,
                                },
                            },
                            "summary": "高度匹配（必备命中 2/3）",
                            "strengths": ["命中必备技能：Python, MySQL"],
                            "gaps": ["未命中必备：Kubernetes"],
                        },
                        "meta": {
                            "elapsed_ms": 4200,
                            "tokens_used": 1500,
                            "cache_hit": False,
                            "request_id": "req-x",
                        },
                    }
                }
            },
        },
        400: {"description": "MISSING_PARAMETER：jd_text 与 jd_hash 至少传一个"},
        404: {"description": "RESUME_NOT_FOUND / JD_NOT_FOUND"},
    },
)
async def post_match(req: MatchRequest, request: Request):
    ctx = get_request_context(request)

    # ===== 1. 解析 JD =====
    if not req.jd_text and not req.jd_hash:
        raise MissingParameterError(message="jd_text 与 jd_hash 至少传一个")

    jd_text_for_llm = req.jd_text or ""
    if req.jd_hash:
        jd = await get_jd_keywords_by_hash(req.jd_hash)
        if jd is None and not req.jd_text:
            raise ResumeNotFoundError(
                message=f"未找到 jd_hash={req.jd_hash}",
                suggestion="请先调用 POST /api/jd/keywords 提取关键词或直接传 jd_text",
            )
        if jd is None:
            jd, jd_usage, _ = await extract_jd_keywords(req.jd_text or "")
            if jd_usage is not None:
                ctx.add_tokens(jd_usage.total_tokens)
        else:
            ctx.mark_cache_hit()
    else:
        jd, jd_usage, jd_hit = await extract_jd_keywords(req.jd_text or "")
        if jd_usage is not None:
            ctx.add_tokens(jd_usage.total_tokens)
        if jd_hit:
            ctx.mark_cache_hit()

    # 评分接口需要 LLM 时也需要 jd_text 原文（启发式只需关键词）
    if not jd_text_for_llm and req.use_llm_score and req.score_strategy != "heuristic_only":
        # 没传 jd_text 又要 LLM 评分 → 只能跑启发式
        logger.info(
            "no jd_text provided, fallback to heuristic_only",
            extra={"scope": "match", "request_id": ctx.request_id},
        )
        req.use_llm_score = False

    # ===== 2. 命中评分缓存？=====
    cached = await get_cached_match(
        req.resume_id, jd.jd_hash, req.use_llm_score, req.score_strategy
    )
    if cached is not None:
        ctx.mark_cache_hit()
        return APIResponse.ok(cached.model_dump(), meta=make_meta(ctx))

    # ===== 3. 取/算简历结构化信息 =====
    parse_result = await resume_store.get(req.resume_id)
    if parse_result is None:
        raise ResumeNotFoundError(message=f"未找到 resume_id={req.resume_id}")

    # 三段抽取（命中各段缓存即不调 LLM）
    import asyncio as _asyncio

    basic_t = extract_basic(parse_result.text)
    job_t = extract_job_intent(parse_result.text)
    bg_t = extract_background(parse_result.text)
    results = await _asyncio.gather(basic_t, job_t, bg_t, return_exceptions=True)

    from app.models.resume import ResumeBackground, ResumeBasic, ResumeJobIntent

    def _unpack(idx, default):
        r = results[idx]
        if isinstance(r, Exception):
            return default, None, False
        return r

    basic, b_u, b_h = _unpack(0, ResumeBasic())
    job, j_u, j_h = _unpack(1, ResumeJobIntent())
    bg, bg_u, bg_h = _unpack(2, ResumeBackground())

    for u in (b_u, j_u, bg_u):
        if u is not None:
            ctx.add_tokens(u.total_tokens)
    if b_h or j_h or bg_h:
        ctx.mark_cache_hit()

    resume = ResumeAggregate(
        resume_id=req.resume_id,
        basic=basic,
        job_intent=job,
        background=bg,
        text_preview=parse_result.text[:200],
        pages=parse_result.pages,
        char_count=parse_result.char_count,
        is_scanned_suspect=parse_result.is_scanned_suspect,
    )

    # ===== 4. 评分 =====
    result, llm_usage, _ = await match_resume(
        resume=resume,
        jd_keywords=jd,
        jd_text=jd_text_for_llm,
        use_llm_score=req.use_llm_score,
        score_strategy=req.score_strategy,
    )
    if llm_usage is not None:
        ctx.add_tokens(llm_usage.total_tokens)

    # ===== 5. 缓存 =====
    await save_match_cache(result, req.resume_id, jd.jd_hash, req.use_llm_score, req.score_strategy)

    return APIResponse.ok(result.model_dump(), meta=make_meta(ctx))
