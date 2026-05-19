"""全局异常 handler 与请求中间件

注册到 FastAPI app 后，所有异常都会被包装为统一 APIResponse 错误形状。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.core.response import (
    REQUEST_ID_HEADER,
    REQUEST_STATE_KEY,
    get_request_context,
    make_meta,
    new_request_context,
)
from app.models.common import APIResponse

logger = get_logger("app.http")


async def request_context_middleware(request: Request, call_next):
    """为每个请求注入 RequestContext + 记录访问日志。

    - 优先用客户端传的 X-Request-ID（便于跨服务追踪）
    - 响应头回带 X-Request-ID
    """
    incoming_rid = request.headers.get(REQUEST_ID_HEADER)
    ctx = new_request_context(incoming_rid)
    setattr(request.state, REQUEST_STATE_KEY, ctx)

    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        # 即便发生异常，也要记一条访问日志
        elapsed = ctx.elapsed_ms()
        status = response.status_code if response is not None else 500
        logger.info(
            "http request",
            extra={
                "scope": "http",
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "elapsed_ms": elapsed,
                "tokens_used": ctx.tokens_used,
                "cache_hit": ctx.cache_hit,
                "request_id": ctx.request_id,
            },
        )
        if response is not None:
            response.headers[REQUEST_ID_HEADER] = ctx.request_id


def _build_error_response(
    request: Request,
    *,
    code: str,
    message: str,
    http_status: int,
    suggestion: str | None = None,
    log_details: dict | None = None,
) -> JSONResponse:
    """构造统一错误响应。"""
    ctx = get_request_context(request)
    body = APIResponse.error(
        code=code,
        message=message,
        meta=make_meta(ctx),
        suggestion=suggestion,
    )

    log_extra = {
        "scope": "error",
        "code": code,
        "path": request.url.path,
        "method": request.method,
        "request_id": ctx.request_id,
    }
    if log_details:
        log_extra["details"] = log_details

    # 5xx 用 error 级别，4xx 用 warning
    if http_status >= 500:
        logger.error(message, extra=log_extra)
    else:
        logger.warning(message, extra=log_extra)

    return JSONResponse(status_code=http_status, content=body)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """处理业务异常（AppException 及子类）。"""
    return _build_error_response(
        request,
        code=exc.code,
        message=exc.message,
        http_status=exc.http_status,
        suggestion=exc.suggestion,
        log_details=exc.details,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """处理 FastAPI/Starlette 抛出的 HTTPException（如 404）。"""
    # FastAPI 的 404 默认 detail 是 "Not Found"，给个中文友好版本
    code_map = {
        404: ("NOT_FOUND", "请求的资源不存在"),
        405: ("METHOD_NOT_ALLOWED", "请求方法不被允许"),
        413: ("FILE_TOO_LARGE", "请求体过大"),
    }
    # starlette 默认 detail 是英文短语（如 "Not Found"），有 code_map 时优先用中文
    if exc.status_code in code_map:
        code, message = code_map[exc.status_code]
    else:
        code = f"HTTP_{exc.status_code}"
        message = str(exc.detail) if exc.detail else "请求失败"
    return _build_error_response(
        request,
        code=code,
        message=message,
        http_status=exc.status_code,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理 Pydantic 请求体校验失败。"""
    # 把第一个错误的字段路径与原因拼成可读 message
    first_err = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first_err.get("loc", []) if x not in ("body",))
    msg = first_err.get("msg", "参数校验失败")
    full_msg = f"参数 `{loc}` 校验失败：{msg}" if loc else msg

    return _build_error_response(
        request,
        code="VALIDATION_ERROR",
        message=full_msg,
        http_status=422,
        suggestion="请检查请求参数是否符合接口约定",
        log_details={"errors": exc.errors()[:5]},  # 限制日志大小
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：所有未被前面 handler 捕获的异常。"""
    logger.exception("unhandled exception")
    return _build_error_response(
        request,
        code="INTERNAL_ERROR",
        message="服务内部错误",
        http_status=500,
        suggestion="请稍后重试；若持续失败请联系管理员",
    )


def register_handlers(app: FastAPI) -> None:
    """把所有 handler 注册到 FastAPI app。

    顺序：业务异常 > HTTP 异常 > 校验异常 > 兜底
    """
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
