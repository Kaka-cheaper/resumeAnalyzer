"""应用异常体系

设计原则：
- 所有业务错误统一继承 `AppException`，由全局 handler 捕获并包装为 `APIResponse`
- 错误码（code）是机器可读的稳定字符串，不随 message 改动
- HTTP 状态码与错误码绑定，便于前端用 status 做粗分类、用 code 做细分类
- message 面向用户友好；suggestion 给出修复建议（可选）

错误码全集见 `docs/spec/design.md` §4.6。
"""

from __future__ import annotations


class AppException(Exception):
    """应用异常基类。

    所有业务异常都应继承本类，便于 handler 统一处理。
    """

    code: str = "INTERNAL_ERROR"
    message: str = "服务内部错误"
    http_status: int = 500
    suggestion: str | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        suggestion: str | None = None,
        details: dict | None = None,
    ) -> None:
        """实例化异常。

        Args:
            message: 覆盖类级别 message（用户可见）
            suggestion: 修复建议（用户可见）
            details: 调试用的额外信息（仅写日志，不返回给客户端）
        """
        self.message = message or self.message
        self.suggestion = suggestion or self.suggestion
        self.details = details or {}
        super().__init__(self.message)


# ===== 输入校验类 =====


class InvalidFileTypeError(AppException):
    code = "INVALID_FILE_TYPE"
    message = "文件类型不支持，仅支持 PDF"
    http_status = 400
    suggestion = "请确认上传的是 .pdf 文件"


class FileTooLargeError(AppException):
    code = "FILE_TOO_LARGE"
    message = "文件大小超出限制"
    http_status = 400
    suggestion = "请压缩 PDF 后重新上传"


class MissingParameterError(AppException):
    code = "MISSING_PARAMETER"
    message = "缺少必填参数"
    http_status = 400


# ===== 业务处理类 =====


class PDFParseError(AppException):
    code = "PDF_PARSE_FAILED"
    message = "PDF 解析失败"
    http_status = 422
    suggestion = "PDF 可能已加密、损坏或为扫描件，请尝试上传文字版 PDF"


class ResumeNotFoundError(AppException):
    code = "RESUME_NOT_FOUND"
    message = "简历不存在或已过期"
    http_status = 404
    suggestion = "请重新上传简历后再试"


# ===== LLM 类 =====


class LLMTimeoutError(AppException):
    code = "LLM_TIMEOUT"
    message = "AI 模型调用超时"
    http_status = 504
    suggestion = "请稍后重试；高峰时段可能耗时较长"


class LLMRateLimitedError(AppException):
    code = "LLM_RATE_LIMITED"
    message = "AI 模型调用被限流"
    http_status = 429
    suggestion = "请稍后再试"


class LLMError(AppException):
    """LLM 通用错误（响应异常、JSON 解析失败等）。"""

    code = "LLM_ERROR"
    message = "AI 模型调用失败"
    http_status = 502
