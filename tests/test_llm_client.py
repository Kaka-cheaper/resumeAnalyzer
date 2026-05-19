"""MiMoClient 单元测试

不调真实 LLM，全部用 monkey-patch 替换底层 client。
覆盖：
- chat() 普通调用 + 用量记录
- chat_json() 三层 JSON 解析兜底
- schema 校验失败抛 LLMError
- 超时映射 LLMTimeoutError
- 限流映射 LLMRateLimitedError
- 鉴权失败映射 LLMError
- 重试逻辑（retryable 异常 → 成功）
- 未配置 API key → 抛 LLMError
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import BaseModel

from app.core.exceptions import LLMError, LLMRateLimitedError, LLMTimeoutError
from app.llm.client import MiMoClient


def _make_completion(content: str, *, prompt_tokens: int = 10, completion_tokens: int = 5):
    """构造一个 OpenAI ChatCompletion 风格的 mock 对象。"""
    msg = MagicMock(content=content)
    choice = MagicMock(message=msg)
    usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return MagicMock(choices=[choice], usage=usage)


@pytest.fixture
def configured_client():
    """带 API key 的客户端。"""
    return MiMoClient(api_key="test-key", base_url="http://fake", model="test-model", max_retries=2)


@pytest.fixture
def unconfigured_client():
    """无 API key 的客户端。"""
    return MiMoClient(api_key="", base_url="http://fake", model="test-model")


def _patch_create(client: MiMoClient, side_effect):
    """替换 client._client.chat.completions.create。"""
    mock_create = AsyncMock(side_effect=side_effect)
    client._client.chat.completions.create = mock_create
    return mock_create


# ======================================================================
# 配置与基础调用
# ======================================================================


def test_is_configured_with_key(configured_client: MiMoClient):
    assert configured_client.is_configured is True


def test_is_configured_without_key(unconfigured_client: MiMoClient):
    assert unconfigured_client.is_configured is False


async def test_chat_without_key_raises(unconfigured_client: MiMoClient):
    with pytest.raises(LLMError):
        await unconfigured_client.chat(system="s", user="u")


async def test_chat_basic(configured_client: MiMoClient):
    mock = _patch_create(configured_client, [_make_completion("hello")])
    text, usage = await configured_client.chat(system="s", user="u")
    assert text == "hello"
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 5
    assert usage.total_tokens == 15
    assert usage.model == "test-model"
    assert usage.retried == 0
    assert usage.latency_ms >= 0
    mock.assert_awaited_once()


async def test_chat_passes_temperature_and_tokens(configured_client: MiMoClient):
    mock = _patch_create(configured_client, [_make_completion("ok")])
    await configured_client.chat(system="s", user="u", max_tokens=512, temperature=0.7)
    kwargs = mock.await_args.kwargs
    assert kwargs["max_tokens"] == 512
    assert kwargs["temperature"] == 0.7
    assert "response_format" not in kwargs  # chat 不开 JSON 模式


# ======================================================================
# JSON 模式
# ======================================================================


async def test_chat_json_clean(configured_client: MiMoClient):
    """模型直接返回合法 JSON。"""
    _patch_create(configured_client, [_make_completion('{"name":"张三","age":30}')])
    parsed, usage = await configured_client.chat_json(system="s", user="u")
    assert parsed == {"name": "张三", "age": 30}
    assert usage.total_tokens == 15


async def test_chat_json_with_markdown_fence(configured_client: MiMoClient):
    """模型返回带 ```json``` 代码块的内容应被剥壳。"""
    raw = 'Sure! Here is the result:\n```json\n{"name":"李四"}\n```\nDone.'
    _patch_create(configured_client, [_make_completion(raw)])
    parsed, _ = await configured_client.chat_json(system="s", user="u")
    assert parsed == {"name": "李四"}


async def test_chat_json_loose_fallback(configured_client: MiMoClient):
    """模型在自由文本里夹带 JSON 也应能抓出。"""
    raw = '抱歉，输出如下：{"name":"王五","skills":["Python"]} 希望有用。'
    _patch_create(configured_client, [_make_completion(raw)])
    parsed, _ = await configured_client.chat_json(system="s", user="u")
    assert parsed == {"name": "王五", "skills": ["Python"]}


async def test_chat_json_unparseable_raises(configured_client: MiMoClient):
    _patch_create(configured_client, [_make_completion("nothing useful here")])
    with pytest.raises(LLMError):
        await configured_client.chat_json(system="s", user="u")


async def test_chat_json_empty_raises(configured_client: MiMoClient):
    _patch_create(configured_client, [_make_completion("")])
    with pytest.raises(LLMError):
        await configured_client.chat_json(system="s", user="u")


async def test_chat_json_passes_response_format(configured_client: MiMoClient):
    mock = _patch_create(configured_client, [_make_completion('{"x":1}')])
    await configured_client.chat_json(system="s", user="u")
    assert mock.await_args.kwargs["response_format"] == {"type": "json_object"}


# ======================================================================
# Schema 校验
# ======================================================================


class _DemoSchema(BaseModel):
    name: str
    age: int


async def test_chat_json_with_schema_ok(configured_client: MiMoClient):
    _patch_create(configured_client, [_make_completion('{"name":"赵六","age":25}')])
    parsed, _ = await configured_client.chat_json(system="s", user="u", schema=_DemoSchema)
    assert isinstance(parsed, _DemoSchema)
    assert parsed.name == "赵六"
    assert parsed.age == 25


async def test_chat_json_with_schema_validation_fails(configured_client: MiMoClient):
    _patch_create(configured_client, [_make_completion('{"name":"x","age":"not-int"}')])
    with pytest.raises(LLMError):
        await configured_client.chat_json(system="s", user="u", schema=_DemoSchema)


# ======================================================================
# 异常映射
# ======================================================================


async def test_timeout_maps_to_llm_timeout(configured_client: MiMoClient):
    err = APITimeoutError(request=httpx.Request("POST", "http://x"))
    _patch_create(configured_client, err)
    with pytest.raises(LLMTimeoutError):
        await configured_client.chat(system="s", user="u")


async def test_rate_limit_maps_to_llm_rate_limited(configured_client: MiMoClient):
    err = RateLimitError(message="rate", response=MagicMock(status_code=429, headers={}), body=None)
    _patch_create(configured_client, err)
    with pytest.raises(LLMRateLimitedError):
        await configured_client.chat(system="s", user="u")


async def test_auth_error_maps_to_llm_error(configured_client: MiMoClient):
    err = AuthenticationError(
        message="bad key", response=MagicMock(status_code=401, headers={}), body=None
    )
    _patch_create(configured_client, err)
    with pytest.raises(LLMError):
        await configured_client.chat(system="s", user="u")


# ======================================================================
# 重试逻辑
# ======================================================================


async def test_retries_on_connection_error_then_succeeds(configured_client: MiMoClient):
    err = APIConnectionError(request=httpx.Request("POST", "http://x"))
    mock = _patch_create(
        configured_client,
        [err, _make_completion("ok-after-retry")],
    )
    text, usage = await configured_client.chat(system="s", user="u")
    assert text == "ok-after-retry"
    assert usage.retried == 1
    assert mock.await_count == 2


async def test_retries_exhausted(configured_client: MiMoClient):
    """连续失败超过重试次数 → 抛对应异常。"""
    err = APIConnectionError(request=httpx.Request("POST", "http://x"))
    _patch_create(configured_client, [err, err])  # max_retries=2，全失败
    with pytest.raises(LLMError):
        await configured_client.chat(system="s", user="u")


async def test_no_retry_on_bad_request(configured_client: MiMoClient):
    """400 BadRequest 不应重试。"""
    from openai import BadRequestError

    err = BadRequestError(message="bad", response=MagicMock(status_code=400, headers={}), body=None)
    mock = _patch_create(configured_client, err)
    with pytest.raises(LLMError):
        await configured_client.chat(system="s", user="u")
    assert mock.await_count == 1  # 只调一次


# ======================================================================
# 边界
# ======================================================================


async def test_empty_response_raises(configured_client: MiMoClient):
    """choices[0].message.content 为 None 时抛 LLMError。"""
    msg = MagicMock(content=None)
    choice = MagicMock(message=msg)
    usage = MagicMock(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    _patch_create(configured_client, [MagicMock(choices=[choice], usage=usage)])
    with pytest.raises(LLMError):
        await configured_client.chat(system="s", user="u")
