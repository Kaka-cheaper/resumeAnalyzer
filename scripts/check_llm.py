"""LLM 连通性自检脚本

用法：
    python scripts/check_llm.py

读 .env / 环境变量里的 MIMO_* 配置，发一次最小请求验证：
1. base_url 可达
2. api_key 有效
3. model 可用
4. 返回结构正常

所有响应内容**截断显示**，不打印 key。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _load_dotenv() -> None:
    """简易 .env 加载（不引依赖）。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v


def _mask(s: str, keep: int = 4) -> str:
    if not s:
        return "<empty>"
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}...{s[-keep:]} (len={len(s)})"


def main() -> int:
    _load_dotenv()

    api_key = os.getenv("MIMO_API_KEY", "")
    base_url = os.getenv("MIMO_BASE_URL", "https://api.novita.ai/v3/openai")
    model = os.getenv("MIMO_MODEL", "xiaomimimo/mimo-v2-flash")
    timeout = int(os.getenv("MIMO_TIMEOUT", "30"))

    print("=" * 60)
    print("LLM 连通性自检")
    print("=" * 60)
    print(f"base_url : {base_url}")
    print(f"model    : {model}")
    print(f"api_key  : {_mask(api_key)}")
    print(f"timeout  : {timeout}s")
    print("-" * 60)

    if not api_key:
        print("✗ MIMO_API_KEY 为空。请检查 .env 或环境变量。")
        return 2

    try:
        from openai import OpenAI
    except ImportError:
        print("✗ openai 库未安装。请 pip install openai==1.51.2")
        return 3

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    # ===== 测试 1：基础 chat.completions =====
    print("\n[1/3] 基础对话调用 ... ", end="", flush=True)
    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Reply briefly."},
                {"role": "user", "content": "用中文回复一个字：好"},
            ],
            max_tokens=20,
        )
        elapsed = (time.perf_counter() - started) * 1000
        content = resp.choices[0].message.content or ""
        usage = resp.usage
        print(f"OK ({elapsed:.0f}ms)")
        print(f"  reply  : {content!r}")
        print(
            f"  tokens : prompt={usage.prompt_tokens} completion={usage.completion_tokens} total={usage.total_tokens}"
        )
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000
        print(f"FAIL ({elapsed:.0f}ms)")
        print(f"  error type : {type(e).__name__}")
        print(f"  error msg  : {e}")
        return 1

    # ===== 测试 2：JSON 模式 =====
    print("\n[2/3] JSON 输出模式 ... ", end="", flush=True)
    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "你是 JSON 输出助手。严格按用户要求的字段输出 JSON，不加任何额外文本。",
                },
                {
                    "role": "user",
                    "content": '从下面文本提取 name 和 phone，输出 JSON {"name":"","phone":""}。\n文本：张三 138-0000-0000',
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=100,
        )
        elapsed = (time.perf_counter() - started) * 1000
        content = resp.choices[0].message.content or ""
        print(f"OK ({elapsed:.0f}ms)")
        print(f"  raw    : {content[:200]}")
        try:
            parsed = json.loads(content)
            print(f"  parsed : {parsed}")
        except json.JSONDecodeError as e:
            print(f"  WARN: 模型返回非合法 JSON：{e}")
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000
        print(f"FAIL ({elapsed:.0f}ms)")
        print(f"  error type : {type(e).__name__}")
        print(f"  error msg  : {e}")
        # 不致命：部分模型/服务商不支持 response_format
        print("  -> 提示：D 阶段会做手动 JSON 解析兜底")

    # ===== 测试 3：异步客户端可用 =====
    print("\n[3/3] 异步客户端连通 ... ", end="", flush=True)
    started = time.perf_counter()
    try:
        import asyncio

        from openai import AsyncOpenAI

        async def _ping():
            ac = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
            r = await ac.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=10,
            )
            return r.choices[0].message.content

        out = asyncio.run(_ping())
        elapsed = (time.perf_counter() - started) * 1000
        print(f"OK ({elapsed:.0f}ms)")
        print(f"  reply  : {out!r}")
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000
        print(f"FAIL ({elapsed:.0f}ms)")
        print(f"  error type : {type(e).__name__}")
        print(f"  error msg  : {e}")
        return 1

    print("\n" + "=" * 60)
    print("✓ 全部连通性测试通过，可以进入 D 阶段")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
