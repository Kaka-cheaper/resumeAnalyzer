"""结构化 JSON 日志

要点：
- 每行一条 JSON，便于 FC 日志服务/SLS 自动索引
- 不打印 prompt 内容（隐私）；LLM 调用只记 hash 和长度
- request_id 透传，便于跨调用链追踪
- 兼容 stdlib logging：业务代码用 `logger.info(...)` 即可，无需关心格式
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from app.core.config import get_settings


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化为单行 JSON。

    通过 `extra={...}` 透传业务字段，例如：
        logger.info("llm call done", extra={"scope": "llm", "tokens": 320})
    """

    # logging.LogRecord 的固定字段，extra 之外的都是这些
    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 业务字段透传（exclude reserved）
        for k, v in record.__dict__.items():
            if k in self._RESERVED or k.startswith("_"):
                continue
            payload[k] = v
        # 异常信息单独序列化为字符串，避免不可序列化对象
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def setup_logging() -> None:
    """配置全局日志。

    幂等：重复调用不会叠加 handler。
    """
    global _configured
    if _configured:
        return

    settings = get_settings()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    # 清掉默认 handler，避免双输出
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # uvicorn 自带的访问日志走自己的格式；我们让它走 JSON
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
        lg.setLevel(settings.log_level)

    _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """获取业务 logger。建议传 `__name__`。"""
    return logging.getLogger(name or "app")
