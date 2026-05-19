"""MiMoClient 集成测试（真实 LLM 调用）

默认 skip。手动跑：
    pytest tests/test_llm_integration.py -v -m integration --runintegration

或用环境变量：
    RUN_INTEGRATION=1 pytest tests/test_llm_integration.py -v

需要 .env 里配好 MIMO_API_KEY / MIMO_BASE_URL / MIMO_MODEL。
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.llm.client import MiMoClient


def _should_run() -> bool:
    return os.getenv("RUN_INTEGRATION", "0") == "1"


pytestmark = pytest.mark.skipif(
    not _should_run(),
    reason="集成测试默认跳过；设置 RUN_INTEGRATION=1 启用",
)


@pytest.fixture
def real_client() -> MiMoClient:
    settings = get_settings()
    if not settings.is_llm_configured:
        pytest.skip("MIMO_API_KEY 未配置")
    return MiMoClient()


async def test_real_chat(real_client: MiMoClient):
    text, usage = await real_client.chat(
        system="You are a helpful assistant. Answer briefly.",
        user="用一个汉字回答：好",
        max_tokens=20,
    )
    assert isinstance(text, str)
    assert len(text) > 0
    assert usage.total_tokens > 0
    assert usage.latency_ms > 0


async def test_real_chat_json(real_client: MiMoClient):
    parsed, usage = await real_client.chat_json(
        system="你是一个信息抽取助手，严格按要求输出 JSON。",
        user='抽取下面文本的姓名和电话：\n张三 138-0000-0000\n输出 {"name":"","phone":""}',
        max_tokens=100,
    )
    assert isinstance(parsed, dict)
    assert "name" in parsed
    assert "phone" in parsed
    assert usage.total_tokens > 0


class _ContactSchema(BaseModel):
    name: str = Field(description="姓名")
    phone: str = Field(description="电话")


async def test_real_chat_json_with_schema(real_client: MiMoClient):
    parsed, usage = await real_client.chat_json(
        system=(
            "你是一个信息抽取助手。从文本提取姓名和电话，严格输出 JSON：\n"
            '{"name": "<姓名字符串>", "phone": "<电话字符串>"}\n'
            "字段名必须是英文 name 和 phone，不要用中文 key。"
        ),
        user="文本：李四 13912345678",
        schema=_ContactSchema,
        max_tokens=100,
    )
    assert isinstance(parsed, _ContactSchema)
    assert "李" in parsed.name or "四" in parsed.name
    assert "1391234" in parsed.phone or "13912345678" in parsed.phone
