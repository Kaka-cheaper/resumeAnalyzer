"""阿里云 FC Python 内置 runtime 入口（WSGI Handler 模式）

FC 内置 python3.10 runtime 用 WSGI 协议调用 handler。
FastAPI 是 ASGI 框架，用 a2wsgi 适配为 WSGI。
handler 配置：bootstrap.handler
"""

from __future__ import annotations

import sys
import traceback

# 让 FC 找得到 ../python（部署时 pre-deploy 把 Linux wheel 装到此处）
sys.path.insert(0, "/code/python")

_import_error: str | None = None
_real_handler = None

try:
    from a2wsgi import ASGIMiddleware

    from app.main import app

    _real_handler = ASGIMiddleware(app)
except Exception:
    _import_error = traceback.format_exc()
    print("=== bootstrap import error ===", file=sys.stderr)
    print(_import_error, file=sys.stderr)


def handler(environ, start_response):
    """FC 入口。

    成功时：把请求转发给 a2wsgi-wrapped FastAPI。
    失败时（import 阶段挂了）：返回 500 + traceback 文本，便于不开 SLS 也能看根因。
    """
    if _real_handler is not None:
        return _real_handler(environ, start_response)

    body = (
        f"=== bootstrap import failed ===\n\n{_import_error}\n\n"
        f"sys.path = {sys.path}\n"
    ).encode("utf-8")
    start_response(
        "500 Internal Server Error",
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]
