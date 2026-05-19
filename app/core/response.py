"""响应封装工具

提供两个能力：
1. `RequestContext`：从 FastAPI Request 提取/生成 request_id 与计时起点
2. `make_meta()`：基于 RequestContext 构造 Meta，统一 elapsed_ms / request_id 等字段

用法（在路由内）：
    @router.get("/foo")
    async def foo(request: Request):
        ctx = get_request_context(request)
        ...
        return APIResponse.ok(data, meta=make_meta(ctx))
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from fastapi import Request

from app.models.common import Meta

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_STATE_KEY = "_app_ctx"


@dataclass
class RequestContext:
    """请求级上下文。

    存放在 `request.state` 上，便于路由、依赖、中间件之间共享。
    """

    request_id: str
    started_at: float  # time.perf_counter()
    tokens_used: int = 0
    cache_hit: bool = False

    def add_tokens(self, n: int) -> None:
        """累加本请求消耗的 token 数（LLM 调用方主动上报）。"""
        self.tokens_used += max(0, n)

    def mark_cache_hit(self) -> None:
        """标记本请求命中过缓存（任意一段命中即设 True）。"""
        self.cache_hit = True

    def elapsed_ms(self) -> int:
        """从请求进入至当前时刻的毫秒数。"""
        return int((time.perf_counter() - self.started_at) * 1000)


def new_request_context(request_id: str | None = None) -> RequestContext:
    """创建新的请求上下文。

    Args:
        request_id: 客户端传入的 request_id；为空则生成。
    """
    rid = request_id or f"req-{uuid.uuid4().hex[:12]}"
    return RequestContext(request_id=rid, started_at=time.perf_counter())


def get_request_context(request: Request) -> RequestContext:
    """从请求中获取上下文；中间件保证每次请求都已注入。

    若中间件未运行（如单元测试直接调路由函数），会兜底创建。
    """
    ctx = getattr(request.state, REQUEST_STATE_KEY, None)
    if ctx is None:
        ctx = new_request_context()
        setattr(request.state, REQUEST_STATE_KEY, ctx)
    return ctx


def make_meta(ctx: RequestContext, **overrides) -> Meta:
    """基于上下文构造 Meta。

    支持额外字段透传（如某接口想加 `cache_hit_detail`）。
    """
    base = {
        "elapsed_ms": ctx.elapsed_ms(),
        "tokens_used": ctx.tokens_used,
        "cache_hit": ctx.cache_hit,
        "request_id": ctx.request_id,
    }
    base.update(overrides)
    return Meta(**base)
