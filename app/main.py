"""FastAPI 应用入口

A2 阶段：接入配置 / 异常体系 / 结构化日志 / 统一响应封装 / 请求上下文中间件。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health, resume
from app.core.config import get_settings
from app.core.handlers import register_handlers, request_context_middleware
from app.core.logging import setup_logging

# 启动时配置日志（先于其它模块输出第一条日志）
setup_logging()

settings = get_settings()

app = FastAPI(
    title="智能简历分析系统",
    description=(
        "AI 赋能的简历解析与岗位匹配评分服务。\n\n"
        "- 上传 PDF 简历，自动抽取关键信息\n"
        "- 输入岗位描述，启发式 + LLM 双层评分\n"
        "- RESTful + JSON，部署于阿里云函数计算"
    ),
    version=__version__,
)

# CORS：从配置读，默认放开（前端 GH Pages 跨域接入零门槛）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # 公开 API，无需携带 cookie
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# 请求上下文中间件（在路由之前注入 request_id 与计时）
app.middleware("http")(request_context_middleware)

# 全局异常 handler
register_handlers(app)

# 注册路由
app.include_router(health.router)
app.include_router(resume.router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    """根路径返回演示入口（不进 OpenAPI schema）。"""
    return {
        "code": "OK",
        "message": "智能简历分析系统已启动",
        "data": {"docs": "/docs", "health": "/health"},
    }
