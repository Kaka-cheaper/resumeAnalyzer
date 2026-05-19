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
# 注意：BadRequestError(400) / AuthenticationError(401) 不在此处——
# 它们是确定性错误，重试再多次也是错，浪费 retry 配额
_RETRYABLE_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    httpx.TimeoutException,
    httpx.ConnectError,
)

# Markdown 代码块剥壳：匹配 ```json ... ``` 或 ``` ... ```
# re.DOTALL 让 . 匹配换行符（默认不匹配）
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
# 兜底：从文本里抓第一个 JSON 对象 {...} 或数组 [...]
# 贪婪匹配让它抓最长（最外层）的 JSON 串
_JSON_LOOSE_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


class MiMoClient:
    """MiMo 模型异步客户端（单例，由 get_llm_client() 获取）。

    所有方法都是 coroutine，必须在 async 上下文调用。

    典型用法：
        client = get_llm_client()
        text, usage = await client.chat(system="...", user="...")
        parsed, usage = await client.chat_json(system="...", user="...", schema=MySchema)
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
        """构造客户端。所有参数可选；未传则从 settings 读取。

        api_key="" 是合法值（用于测试无 key 路径），与 None 不同：
        - None: 使用 settings 里的默认值
        - "":   显式无 key，is_configured 返回 False
        """
        settings = get_settings()
        # 注意：api_key="" 应被尊重（用于测试无 key 路径），而非 fallback 到 settings
        # 如果用 `or` 操作符，"" 会被当成 falsy 触发 fallback 取默认值——这就违反了测试隔离
        self.api_key = api_key if api_key is not None else settings.mimo_api_key
        self.base_url = base_url or settings.mimo_base_url
        self.model = model or settings.mimo_model
        self.timeout = timeout or settings.mimo_timeout
        self.max_retries = max_retries if max_retries is not None else settings.mimo_max_retries

        self._configured = bool(self.api_key.strip())
        # 即使 api_key 为空也实例化 client，调用时再判（便于单元测试 mock）
        # OpenAI SDK 不允许 api_key=None，所以传 "missing" 占位
        self._client = AsyncOpenAI(
            api_key=self.api_key or "missing",
            base_url=self.base_url,
            timeout=self.timeout,
        )

    @property
    def is_configured(self) -> bool:
        """LLM 是否可用（API key 已配置且非空白字符串）。"""
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
            system: 系统提示词（角色/约束/输出格式说明）
            user: 用户输入（业务数据）
            max_tokens: 最大输出 token；防止跑飞
            temperature: 采样温度（抽取/评分场景建议低，0.0-0.3 更稳定）

        Returns:
            (assistant_text, token_usage)
            usage 含 prompt/completion/total tokens 与本次调用延迟（毫秒）

        Raises:
            LLMError: 未配置 API key 或不可恢复错误（鉴权失败/参数错/未知错误）
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
        1. response_format=json_object（OpenAI 协议优先，模型应严格输出 JSON）
        2. 模型仍输出 markdown ```json``` 代码块 → 剥壳取出 JSON
        3. 兜底正则抓第一个 {...} / [...]（处理模型在 JSON 前后加解释文字的情况）

        Args:
            schema: 可选 Pydantic 模型；提供时返回 schema 实例并做字段类型校验
                  不提供时返回原始 dict / list

        Returns:
            (parsed, token_usage)
            schema 提供时 parsed 是 schema 实例；否则是 dict / list

        Raises:
            LLMError: JSON 解析失败 / schema 校验失败 / 模型返回空响应
        """
        text, usage = await self._call(
            system=system, user=user, json_mode=True, max_tokens=max_tokens, temperature=temperature
        )
        # 第一步：解析为 JSON（含三层兜底）
        parsed = self._parse_json(text)

        # 第二步：可选的 schema 校验
        # 即使 JSON 解析成功，字段类型 / 必填项不对也要拦下来
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
                        # 只记 200 字预览防止日志污染（生产里 prompt 内容也是隐私）
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
        """统一调用入口，含重试与异常映射。

        架构：
            重试循环（tenacity 指数退避，仅 _RETRYABLE_EXCEPTIONS 重试）
                ↓
            OpenAI SDK 调用 chat.completions.create
                ↓
            异常分流：超时 / 限流 / 鉴权 / 参数 / 连接 / 未知 → 各自业务异常
                ↓
            正常路径：解析 choices + usage → 包装 TokenUsage 返回
        """
        # 防御：未配置 key 直接抛，不浪费一次失败的网络调用
        if not self._configured:
            raise LLMError(message="LLM 未配置（MIMO_API_KEY 为空）")

        # 隐私：日志只记 prompt 的 hash 与长度，永不打印内容
        # sha256 前 12 位（48 bit 熵）足够区分不同 prompt
        prompt_hash = sha256_str(system + "\n||\n" + user)[:12]
        prompt_len = len(system) + len(user)

        # 构造 OpenAI 协议请求体
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
            # OpenAI 协议：要求模型严格输出 JSON。但模型不一定遵守——
            # 这就是为什么 _parse_json 还要做三层兜底
            params["response_format"] = {"type": "json_object"}

        retried = 0  # 重试次数（成功后回传给 TokenUsage）
        started = time.perf_counter()  # 精确计时（含所有重试时间）
        try:
            # AsyncRetrying 是 tenacity 的异步重试上下文
            # 之所以用上下文形式而不是装饰器，是因为这里需要在循环里读 retry_state
            # 拿到当前重试次数（装饰器形式拿不到）
            async for attempt in AsyncRetrying(
                # 最多尝试 max_retries 次（含第一次），最少 1 次
                stop=stop_after_attempt(max(1, self.max_retries)),
                # 指数退避：第 1 次失败等 1s、2 次等 2s、3 次等 4s，封顶 8s
                wait=wait_exponential(multiplier=1, min=1, max=8),
                # 只在元组里的异常类型上重试，其它异常直接抛出
                retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
                # 重试耗尽时直接抛原始异常，而不是包成 RetryError
                # 这样外层 try/except 才能命中具体异常类型做精准映射
                reraise=True,
            ):
                with attempt:
                    if attempt.retry_state.attempt_number > 1:
                        # 第 2 次起记录是第几次重试
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
                    # 实际的 LLM 调用
                    resp = await self._client.chat.completions.create(**params)
        except RetryError as e:
            # 理论上不会进——reraise=True 时 tenacity 直接抛原始异常
            # 但留作兜底防止 tenacity 升级行为变化
            raise LLMTimeoutError(message="LLM 重试后仍失败") from e
        except (APITimeoutError, httpx.TimeoutException) as e:
            # 30s 超时（self.timeout）→ 业务超时异常 → handler 返 504
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("timeout", e, prompt_hash, elapsed, retried)
            raise LLMTimeoutError() from e
        except RateLimitError as e:
            # 限流 → 业务限流异常 → handler 返 429
            # 注意：限流不算我们重试范围内的异常（避免恶化下游限流状态）
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("rate_limited", e, prompt_hash, elapsed, retried)
            raise LLMRateLimitedError() from e
        except AuthenticationError as e:
            # 401 鉴权失败：API key 错或过期，重试解决不了
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("auth", e, prompt_hash, elapsed, retried)
            raise LLMError(message="LLM 鉴权失败，请检查 API Key") from e
        except BadRequestError as e:
            # 400 参数错：messages 格式 / token 超限 / model 名错
            # 重试无意义，立即报错让上游修
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("bad_request", e, prompt_hash, elapsed, retried)
            raise LLMError(message=f"LLM 请求参数错误：{e}") from e
        except APIConnectionError as e:
            # 连接错（DNS/网络层）：理论上 _RETRYABLE 已包含，会重试
            # 走到这里说明重试耗尽
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("connection", e, prompt_hash, elapsed, retried)
            raise LLMError(message="LLM 网络连接失败") from e
        except Exception as e:
            # 兜底：未知异常类型 → 包装成 LLMError 不让原始异常泄露给上游
            elapsed = int((time.perf_counter() - started) * 1000)
            self._log_failure("unknown", e, prompt_hash, elapsed, retried)
            raise LLMError(message=f"LLM 调用失败：{type(e).__name__}") from e

        # ====== 正常响应路径 ======
        elapsed = int((time.perf_counter() - started) * 1000)

        # 解析 OpenAI Chat Completion 响应结构
        # 响应可能是空 choices（极少见但要防）
        choice = resp.choices[0] if resp.choices else None
        if choice is None or choice.message is None or choice.message.content is None:
            raise LLMError(message="LLM 返回空响应")
        content = choice.message.content

        # 包装 token 用量（用于 ctx.add_tokens 累加上报到响应 meta）
        usage = TokenUsage(
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
            total_tokens=resp.usage.total_tokens if resp.usage else 0,
            latency_ms=elapsed,
            model=self.model,
            retried=retried,
        )

        # 成功日志：含 prompt_hash 便于关联同一 prompt 的多次重试
        # 注意：tokens 是 dict 嵌套，便于 SLS 索引时按子字段聚合
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
        """记录失败，不打印 prompt 内容。

        kind: 错误分类（timeout/rate_limited/auth/bad_request/connection/unknown）
        便于 SLS 上按 kind 聚合监控。
        """
        logger.warning(
            "llm call failed",
            extra={
                "scope": "llm",
                "model": self.model,
                "prompt_hash": prompt_hash,
                "kind": kind,
                "err_type": type(err).__name__,
                # err.message 长度限制，避免单条日志爆炸
                "err_msg": str(err)[:200],
                "elapsed_ms": elapsed_ms,
                "retried": retried,
            },
        )

    def _parse_json(self, text: str) -> Any:
        """三层 JSON 解析兜底。

        生产里**真的会触发**——不同 LLM 服务商对 response_format=json_object
        的遵守程度不一样，有些会输出 markdown 代码块，有些会加解释文字。

        三层从严到宽：
        1. 直接 json.loads（最快，模型乖乖输出 JSON 时）
        2. 剥 ```json ... ``` 代码块（Claude/GPT 偶尔会犯）
        3. 正则抓最外层 {...} 或 [...]（极端：JSON 前后有解释文字）

        全失败才抛 LLMError——上游业务可降级到正则兜底（如 basic 抽取）。
        """
        text = text.strip()
        if not text:
            raise LLMError(message="模型返回空 JSON")

        # 1. 直接解析（成功率约 90%+）
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. 剥 ```json ... ``` 代码块
        # 注意正则用 .*? 非贪婪，以防文本里有多个代码块时跨段抓
        m = _JSON_FENCE_RE.search(text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 3. 兜底：抓最外层 {...} 或 [...]
        # 这里用贪婪 .* 是有意的——尽量抓最长（最外层）的 JSON 串
        m = _JSON_LOOSE_RE.search(text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 三层全失败 → 记日志（含 200 字预览供调试）+ 抛业务异常
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

# 模块级单例（懒加载）；多次调用 get_llm_client 共享同一实例
# OpenAI SDK 内部维护 HTTP 连接池，单例可复用连接降低延迟
_client_instance: MiMoClient | None = None


def get_llm_client() -> MiMoClient:
    """获取全局 LLM 客户端单例。

    第一次调用时实例化（从 settings 读配置）；后续直接复用。
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = MiMoClient()
    return _client_instance


def reset_llm_client() -> None:
    """重置单例（仅用于测试）。

    业务代码不应调用——会破坏连接池复用。
    """
    global _client_instance
    _client_instance = None
