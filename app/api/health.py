"""健康检查路由

提供存活探针接口，用于：
- 部署后冒烟验证（curl /health）
- 阿里云 FC 健康探测
- CI/CD 流水线探活
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="健康检查",
    description="返回服务存活状态与版本号，可用于探活/冒烟测试。",
)
async def health() -> dict:
    """返回服务状态信息。

    A1 阶段先用最简 dict 满足验收形状；A2 阶段会替换为统一 APIResponse 信封。
    """
    started = time.perf_counter()
    return {
        "code": "OK",
        "message": "success",
        "data": {
            "status": "alive",
            "version": __version__,
        },
        "meta": {
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "request_id": f"req-{uuid.uuid4().hex[:12]}",
        },
    }
