"""MiMo LLM 客户端

封装 OpenAI 兼容协议下的 MiMo 调用，对外暴露语义化方法（chat / chat_json）。

设计要点：
- 重试：tenacity 指数退避（1s/2s/4s 上限），最多 3 次
- 超时：单次 30s（可配）；外层调用方仍可加 asyncio.wait_for 兜底
- JSON 模式：优先 response_format=json_object；失败 → 手动 markdown 剥壳 → 最后抛 LLMError
- 用量记录：每次调用记 prompt/completion/total tokens 与延迟，调用方通过 ctx.add_tokens() 上报
- 隐私：日志只记 prompt 长度与 hash，绝不打印内容
- 异常映射：401/429/超时/连接错误 → 映射到 app.core.exceptions 的对应业务异常

未配置 API_KEY 时仍可实例化（部分降级路径不需要 LLM），但调用会立刻抛 LLMError。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import (
    LLMError,
    LLMRateLimitedError,
    LLMTimeoutError,
)
from app.llm.schemas import TokenUsage
from app.utils.hash import sha256_str

logger = logging.getLogger(__name__)

# 这些异常类型可重试（瞬时网络/限流），其它直接抛
_RETRYABLE_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    httpx.TimeoutException,
    httpx.ConnectError,
)

# Markdown 代码块剥壳：```json ... ```
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
# 兜底：从文本里抓第一个 JSON 对象/数组
_JSON_LOOSE_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


class MiMoClient:
    """MiMo 模型异步客户端（单例，由 get_llm_client() 获取）。

    所有方法都是 coroutine，必须在 async 上下文调用。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()
        # 注意：api_key="" 应被尊重（用于测试无 key 路径），而非 fallback 到 settings
        self.api_key = api_key if api_key is not None else settings.mimo_api_key
        self.base_url = base_url or settings.mimo_base_url
        self.model = model or settings.mimo_model
        self.timeout = timeout or settings.mimo_timeout
        self.max_retries = max_retries if max_retries is not None else settings.mimo_max_retries

        self._configured = bool(self.api_key.strip())
        # 即使 api_key 为空也实例化 client，调用时再判（便于单元测试 mock）
        self._client = AsyncOpenAI(
            api_key=self.api_key or "missing",
            base_url=self.base_url,
            timeout=self.timeout,
        )

    @property
    def is_configured(self) -> bool:
        """LLM 是否可用（API key 已配置）。"""
        return self._configured

    # ===================================================================
    # 公开 API
    # ===================================================================

    async def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> tuple[str, TokenUsage]:
        """发起一次普通对话调用，返回文本与用量。

        Args:
            system: 系统提示词
            user: 用户输入
            max_tokens: 最大输出 token
            temperature: 采样温度（抽取/评分场景建议低，0.0-0.3）

        Returns:
            (assistant_text, token_usage)

        Raises:
            LLMError: 未配置 API key 或不可恢复错误
            LLMTimeoutError: 重试后仍超时
            LLMRateLimitedError: 限流
        """
        return await self._call(
            system=system,
            user=user,
            json_mode=False,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> tuple[Any, TokenUsage]:
        """发起一次 JSON 模式对话，返回解析后的 dict（或 schema 实例）与用量。

        三层兜底：
        1. response_format=json_object（优先）
        2. 模型仍输出 markdown ```json``` 代码块 → 剥壳
        3. 兜底正则抓第一个 {...} / [...]

        Args:
            schema: 可选 Pydantic 模型；提供时返回 schema 实例并做字段校验

        Raises:
            LLMError: JSON 解析失败 / schema 校验失败
        """
        text, usage = await self._call(
            system=system, user=user, json_mode=True, max_tokens=max_tokens, temperature=temperature
        )
        parsed = self._parse_json(text)

        if schema is not None:
            try:
                return schema.model_validate(parsed), usage
            except ValidationError as e:
                logger.warning(
                    "llm json schema validation failed",
                    extra={
                        "scope": "llm",
                        "model": self.model,
                        "schema": schema.__name__,
                        "raw_preview": text[:200],
                        "errors": e.errors()[:3],
                    },
                )
                raise LLMError(message=f"模型输出与 {schema.__name__} 不匹配") from e

        return parsed, usage

    # ===================================================================
    # 内部方法
    # ===================================================================

    async def _call(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, TokenUsage]:
        """统一调用入口，含重试与异常映射。"""
        if not self._configured:
            raise LLMError(message="LLM 未配置（MIMO_API_KEY 为空）")

        prompt_hash = sha256_str(system + "\n||\n" + user)[:12]
        prompt_len = len(system) + len(user)

        params: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            params["response_format"] = {"type": "json_object"}

        retried = 0
        started = time.perf_counter()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max(1, self.max_retries)),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    if attempt.retry_state.attempt_number > 1:
                        retried = attempt.retry_state.attempt_number - 1
                        logger.info(
                            "llm retrying",
                            extra={
                                "scope": "llm",
                                "model": self.model,
                                "attempt": attempt.retry_state.attempt_number,
                                "prompt_hash": prompt_hash,
                            },
                        )
                    resp = await self._client.chat.completions.create(**params)
        except RetryError as e:
            # tenacity reraise=True 时不会包，这里兜底（理论不会进）
            raise LLMTimeoutError(message="LLM 重试后仍失败") from e
        except (APITimeoutError, httpx.TimeoutException) as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("timeout", e, prompt_hash, elapsed, retried)
            raise LLMTimeoutError() from e
        except RateLimitError as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("rate_limited", e, prompt_hash, elapsed, retried)
            raise LLMRateLimitedError() from e
        except AuthenticationError as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("auth", e, prompt_hash, elapsed, retried)
            raise LLMError(message="LLM 鉴权失败，请检查 API Key") from e
        except BadRequestError as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("bad_request", e, prompt_hash, elapsed, retried)
            raise LLMError(message=f"LLM 请求参数错误：{e}") from e
        except APIConnectionError as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("connection", e, prompt_hash, elapsed, retried)
            raise LLMError(message="LLM 网络连接失败") from e
        except Exception as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("unknown", e, prompt_hash, elapsed, retried)
            raise LLMError(message=f"LLM 调用失败：{type(e).__name__}") from e

        elapsed = int((time.perf_counter() - started) * 1000)

        # 解析响应
        choice = resp.choices[0] if resp.choices else None
        if choice is None or choice.message is None or choice.message.content is None:
            raise LLMError(message="LLM 返回空响应")
        content = choice.message.content

        usage = TokenUsage(
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
            total_tokens=resp.usage.total_tokens if resp.usage else 0,
            latency_ms=elapsed,
            model=self.model,
            retried=retried,
        )

        logger.info(
            "llm call done",
            extra={
                "scope": "llm",
                "model": self.model,
                "prompt_hash": prompt_hash,
                "prompt_len": prompt_len,
                "tokens": {
                    "prompt": usage.prompt_tokens,
                    "completion": usage.completion_tokens,
                    "total": usage.total_tokens,
                },
                "latency_ms": elapsed,
                "retried": retried,
                "json_mode": json_mode,
            },
        )

        return content, usage

    def _log_failure(
        self, kind: str, err: Exception, prompt_hash: str, elapsed_ms: int, retried: int
    ) -> None:
        """记录失败，不打印 prompt 内容。"""
        logger.warning(
            "llm call failed",
            extra={
                "scope": "llm",
                "model": self.model,
                "prompt_hash": prompt_hash,
                "kind": kind,
                "err_type": type(err).__name__,
                "err_msg": str(err)[:200],
                "elapsed_ms": elapsed_ms,
                "retried": retried,
            },
        )

    def _parse_json(self, text: str) -> Any:
        """三层 JSON 解析兜底。

        1. 直接 json.loads
        2. ```json ... ``` 代码块剥壳
        3. 正则抓第一个 {...} 或 [...]
        全失败 → 抛 LLMError
        """
        text = text.strip()
        if not text:
            raise LLMError(message="模型返回空 JSON")

        # 1. 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. 剥壳
        m = _JSON_FENCE_RE.search(text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 3. 兜底
        m = _JSON_LOOSE_RE.search(text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        logger.warning(
            "llm json parse failed",
            extra={
                "scope": "llm",
                "model": self.model,
                "raw_preview": text[:200],
            },
        )
        raise LLMError(message="模型输出无法解析为 JSON")


# ===================================================================
# 单例工厂
# ===================================================================

_client_instance: MiMoClient | None = None


def get_llm_client() -> MiMoClient:
    """获取全局 LLM 客户端单例。"""
    global _client_instance
    if _client_instance is None:
        _client_instance = MiMoClient()
    return _client_instance


def reset_llm_client() -> None:
    """重置单例（测试用）。"""
    global _client_instance
    _client_instance = None
