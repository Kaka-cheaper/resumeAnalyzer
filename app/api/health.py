"""健康检查路由"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.response import get_request_context, make_meta
from app.models.common import APIResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="健康检查",
    description="返回服务存活状态与版本号，用于探活、冒烟测试、负载均衡探针。",
    responses={
        200: {
            "description": "服务存活",
            "content": {
                "application/json": {
                    "example": {
                        "code": "OK",
                        "message": "success",
                        "data": {
                            "status": "alive",
                            "version": "0.1.0",
                            "environment": "development",
                            "llm_configured": False,
                        },
                        "meta": {
                            "elapsed_ms": 1,
                            "tokens_used": 0,
                            "cache_hit": False,
                            "request_id": "req-d7ab70d3c003",
                        },
                    }
                }
            },
        }
    },
)
async def health(request: Request) -> dict:
    """返回服务存活状态。"""
    ctx = get_request_context(request)
    settings = get_settings()
    data = {
        "status": "alive",
        "version": settings.app_version,
        "environment": settings.environment,
        "llm_configured": settings.is_llm_configured,
    }
    return APIResponse.ok(data, meta=make_meta(ctx))
