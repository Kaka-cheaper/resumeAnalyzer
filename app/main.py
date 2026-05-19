"""FastAPI 应用入口

A1 阶段：实例化 + CORS + 健康检查。
后续阶段会逐步注入：异常 handler（A2）/ 业务路由（B-F）/ 启动钩子（D）。
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health

# CORS 来源：从环境变量读，默认放开（前端 GH Pages 跨域接入零门槛）
_cors_origins_env = os.getenv("CORS_ORIGINS", "*")
_cors_origins = (
    ["*"] if _cors_origins_env.strip() == "*" else [o.strip() for o in _cors_origins_env.split(",")]
)

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

# CORS：默认放开，便于前端（GH Pages）后续接入
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,  # 公开 API，无需携带 cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    """根路径重定向到 Swagger UI（评审/演示便捷入口）。"""
    return {
        "code": "OK",
        "message": "智能简历分析系统已启动",
        "data": {"docs": "/docs", "health": "/health"},
    }
