"""岗位描述（JD）相关路由"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.core.response import get_request_context, make_meta
from app.models.common import APIResponse
from app.models.match import JDKeywordsRequest
from app.services.jd_service import extract_jd_keywords

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jd", tags=["jd"])


@router.post(
    "/keywords",
    summary="提取岗位关键词",
    description=(
        "从岗位描述（JD）中提取技能、职责、必备/加分项与硬性要求。\n\n"
        "**幂等**：同一份 JD 文本（按内容 hash）会复用上次的结果，"
        "`meta.cache_hit=true`。返回的 `jd_hash` 可在后续 `/api/match` 中直接传入，避免重复抽取。"
    ),
    responses={
        200: {
            "description": "提取成功",
            "content": {
                "application/json": {
                    "example": {
                        "code": "OK",
                        "message": "success",
                        "data": {
                            "jd_hash": "ab12cd34...",
                            "skills": ["Python", "FastAPI", "MySQL", "Redis"],
                            "responsibilities": ["接口开发", "性能优化"],
                            "requirements": {
                                "min_years": 3,
                                "education": "本科",
                                "must_have": ["Python", "MySQL"],
                                "nice_to_have": ["Kubernetes"],
                            },
                        },
                        "meta": {
                            "elapsed_ms": 1100,
                            "tokens_used": 380,
                            "cache_hit": False,
                            "request_id": "req-x",
                        },
                    }
                }
            },
        }
    },
)
async def post_jd_keywords(req: JDKeywordsRequest, request: Request):
    ctx = get_request_context(request)
    keywords, usage, hit = await extract_jd_keywords(req.jd_text)
    if hit:
        ctx.mark_cache_hit()
    if usage is not None:
        ctx.add_tokens(usage.total_tokens)
    return APIResponse.ok(keywords.model_dump(), meta=make_meta(ctx))
