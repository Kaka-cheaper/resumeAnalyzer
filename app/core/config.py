"""应用配置

所有配置统一从环境变量读取，遵循 12-factor 原则。
本地开发可放 `.env` 文件；FC 部署在控制台/s.yaml 注入。

设计要点：
- API key 等敏感信息不进代码、不进日志
- 数值型配置带边界校验，避免运行时错误
- 配置只读：实例化后修改抛 ValidationError
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。

    通过 `get_settings()` 获取单例，避免重复加载 .env。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # .env 里多余的键忽略，便于运维加临时变量
    )

    # ===== 应用元数据 =====
    app_name: str = Field(default="resume-analyzer")
    app_version: str = Field(default="0.1.0")
    environment: Literal["development", "staging", "production"] = Field(default="development")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    # ===== LLM（小米 MiMo via Novita） =====
    mimo_api_key: str = Field(default="", description="Novita API Key；未配置时 LLM 路径会降级")
    mimo_base_url: str = Field(default="https://api.novita.ai/v3/openai")
    mimo_model: str = Field(default="xiaomimimo/mimo-v2-flash")
    mimo_timeout: int = Field(default=30, ge=5, le=120, description="单次调用超时秒数")
    mimo_max_retries: int = Field(default=3, ge=0, le=5)

    # ===== 缓存 =====
    cache_backend: Literal["memory", "redis"] = Field(default="memory")
    cache_default_ttl: int = Field(default=86400, ge=60, description="默认 TTL（秒）")
    redis_url: str = Field(default="")

    # ===== 上传限制 =====
    max_upload_size_mb: int = Field(default=10, ge=1, le=100)

    # ===== 评分权重 =====
    heuristic_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    llm_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    skill_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    experience_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    education_weight: float = Field(default=0.2, ge=0.0, le=1.0)

    # ===== CORS =====
    cors_origins: str = Field(default="*", description="逗号分隔；'*' 表示放开")

    # ===== 派生属性 =====
    @property
    def max_upload_size_bytes(self) -> int:
        """上传上限的字节数。"""
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def cors_origins_list(self) -> list[str]:
        """解析 CORS 来源为列表。"""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_llm_configured(self) -> bool:
        """LLM 是否已配置可用 API key。"""
        return bool(self.mimo_api_key.strip())

    # ===== 校验：评分权重内部一致性 =====
    @field_validator("llm_weight")
    @classmethod
    def _check_fusion_weight(cls, v: float, info) -> float:
        """LLM 权重 + 启发式权重应当近似为 1（允许 ±0.01 浮点误差）。"""
        heuristic = info.data.get("heuristic_weight", 0.6)
        if abs((heuristic + v) - 1.0) > 0.01:
            raise ValueError(f"heuristic_weight + llm_weight 应为 1.0，当前为 {heuristic + v:.2f}")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取配置单例。

    用 lru_cache 避免重复读取 .env；测试中可调 `get_settings.cache_clear()`。
    """
    return Settings()
