"""通用数据模型：API 响应信封 + Meta

所有接口返回统一形状：
    { "code": "OK", "message": "...", "data": {...}, "meta": {...} }

错误响应额外可带 `suggestion` 字段。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Meta(BaseModel):
    """响应元信息。

    每次响应都会带这些字段，便于前端排查问题、监控成本。
    """

    model_config = ConfigDict(extra="allow")  # 各服务可附加自定义字段（如 cache_hit_detail）

    elapsed_ms: int = Field(default=0, description="服务端处理耗时（毫秒）")
    tokens_used: int = Field(default=0, description="本次请求消耗的 LLM token 总数")
    cache_hit: bool = Field(default=False, description="是否命中缓存")
    request_id: str = Field(description="请求唯一 ID，便于日志追踪")


class APIResponse(BaseModel, Generic[T]):
    """统一 API 响应信封。

    成功响应：
        { "code": "OK", "message": "success", "data": <payload>, "meta": {...} }

    错误响应（由全局 handler 自动构造）：
        { "code": "<ERROR_CODE>", "message": "...", "data": null, "meta": {...},
          "suggestion": "..." }
    """

    model_config = ConfigDict(extra="allow")  # 允许 suggestion 等可选字段

    code: str = Field(default="OK", description="状态码：OK 或具体错误码")
    message: str = Field(default="success", description="人类可读的描述信息")
    data: T | None = Field(default=None, description="业务数据，错误时为 null")
    meta: Meta = Field(description="响应元信息")

    @classmethod
    def ok(cls, data: Any = None, *, meta: Meta, message: str = "success") -> dict:
        """构造成功响应（dict 形式，避免 Generic 在 FastAPI 序列化时的边界）。"""
        return {
            "code": "OK",
            "message": message,
            "data": data,
            "meta": meta.model_dump(),
        }

    @classmethod
    def error(
        cls,
        code: str,
        message: str,
        *,
        meta: Meta,
        suggestion: str | None = None,
    ) -> dict:
        """构造错误响应。"""
        body: dict = {
            "code": code,
            "message": message,
            "data": None,
            "meta": meta.model_dump(),
        }
        if suggestion:
            body["suggestion"] = suggestion
        return body
