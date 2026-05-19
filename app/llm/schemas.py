"""LLM 输入/输出 schema

D 阶段先放最小集合（用于客户端契约测试）；
E 阶段再补 ResumeBasicSchema / ResumeJobSchema / ResumeBackgroundSchema 等业务 schema。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """LLM 调用的 token 用量与延迟。"""

    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    latency_ms: int = Field(default=0)
    model: str = Field(default="")
    retried: int = Field(default=0)


class PingResponse(BaseModel):
    """连通性测试用：要求模型严格返回 {"ok": true}。"""

    ok: bool
