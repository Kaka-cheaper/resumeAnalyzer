"""简历相关路由：上传、查询"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Path, Request, UploadFile

from app.core.config import get_settings
from app.core.exceptions import (
    FileTooLargeError,
    InvalidFileTypeError,
    PDFParseError,
    ResumeNotFoundError,
)
from app.core.response import get_request_context, make_meta
from app.models.common import APIResponse
from app.models.resume import ResumeAggregate, UploadResponse
from app.services import resume_store
from app.services.extract_service import (
    extract_background,
    extract_basic,
    extract_job_intent,
)
from app.services.pdf_service import parse_pdf
from app.utils.hash import sha256_bytes, short_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/resume", tags=["resume"])

# 允许的 PDF MIME 类型（不同浏览器/客户端可能略有差异）
_ALLOWED_MIME = {"application/pdf", "application/x-pdf", "application/octet-stream"}

# PDF magic bytes
_PDF_MAGIC = b"%PDF"


def _validate_extension(filename: str | None) -> None:
    """校验文件扩展名。"""
    if not filename:
        raise InvalidFileTypeError(message="缺少文件名")
    if not filename.lower().endswith(".pdf"):
        raise InvalidFileTypeError(message=f"文件类型不支持：{filename}")


def _validate_mime(content_type: str | None) -> None:
    """校验 MIME 类型（容忍 octet-stream 等模糊类型）。"""
    if content_type and content_type.lower() not in _ALLOWED_MIME:
        raise InvalidFileTypeError(message=f"MIME 类型不支持：{content_type}")


def _validate_magic(content: bytes) -> None:
    """校验 PDF magic bytes，防止改扩展名绕过。"""
    if not content.lstrip()[:4] == _PDF_MAGIC:
        raise InvalidFileTypeError(
            message="文件头不是有效的 PDF 标识",
            suggestion="请确认上传的是真正的 PDF 文件",
        )


def _validate_size(content: bytes) -> None:
    """校验文件大小。"""
    settings = get_settings()
    size = len(content)
    if size == 0:
        raise InvalidFileTypeError(message="文件为空")
    if size > settings.max_upload_size_bytes:
        raise FileTooLargeError(
            message=f"文件大小 {size / 1024 / 1024:.1f} MB 超过限制 {settings.max_upload_size_mb} MB"
        )


@router.post(
    "/upload",
    summary="上传简历 PDF",
    description=(
        "上传单个 PDF 简历，服务端解析为文本并返回 `resume_id`。\n\n"
        "**多层校验**：扩展名 + MIME + magic bytes + 大小（≤10MB）。\n"
        "**幂等**：同一份 PDF 重复上传会复用上次的解析结果（同一 resume_id），缓存命中标记 `meta.cache_hit=true`。"
    ),
    responses={
        200: {
            "description": "上传与解析成功",
            "content": {
                "application/json": {
                    "example": {
                        "code": "OK",
                        "message": "success",
                        "data": {
                            "resume_id": "rsm_a1b2c3d4e5f6",
                            "pages": 2,
                            "char_count": 3120,
                            "is_scanned_suspect": False,
                            "text_preview": "张三 / 男 / 1995年生 / ...",
                        },
                        "meta": {
                            "elapsed_ms": 850,
                            "tokens_used": 0,
                            "cache_hit": False,
                            "request_id": "req-x",
                        },
                    }
                }
            },
        },
        400: {"description": "INVALID_FILE_TYPE / FILE_TOO_LARGE"},
        422: {"description": "PDF_PARSE_FAILED：损坏 / 加密 / 0 页 / 页数过多"},
    },
)
async def upload_resume(request: Request, file: UploadFile = File(..., description="PDF 文件")):
    """上传简历并解析。"""
    ctx = get_request_context(request)

    # 第一层：扩展名 + MIME
    _validate_extension(file.filename)
    _validate_mime(file.content_type)

    # 读全部字节（multipart 已由 FastAPI 处理）
    content = await file.read()

    # 第二层：大小 + magic bytes
    _validate_size(content)
    _validate_magic(content)

    # 计算 resume_id（同一份 PDF 永远得到同一个 ID）
    file_hash = sha256_bytes(content)
    resume_id = f"rsm_{short_hash(content, 12)}"

    # 命中已存简历？
    cached = await resume_store.get(resume_id)
    if cached is not None:
        ctx.mark_cache_hit()
        logger.info(
            "upload cache hit",
            extra={"scope": "upload", "resume_id": resume_id, "request_id": ctx.request_id},
        )
        data = UploadResponse(
            resume_id=resume_id,
            pages=cached.pages,
            char_count=cached.char_count,
            is_scanned_suspect=cached.is_scanned_suspect,
            text_preview=cached.text[:200],
        )
        return APIResponse.ok(data.model_dump(), meta=make_meta(ctx))

    # 解析
    try:
        result = parse_pdf(content)
    except PDFParseError as e:
        # 让全局 handler 包装为统一信封
        e.details["file_hash"] = file_hash
        raise

    # 落临时存储
    await resume_store.save(resume_id, result)

    logger.info(
        "upload parsed",
        extra={
            "scope": "upload",
            "resume_id": resume_id,
            "pages": result.pages,
            "char_count": result.char_count,
            "is_scanned_suspect": result.is_scanned_suspect,
            "request_id": ctx.request_id,
        },
    )

    data = UploadResponse(
        resume_id=resume_id,
        pages=result.pages,
        char_count=result.char_count,
        is_scanned_suspect=result.is_scanned_suspect,
        text_preview=result.text[:200],
    )
    return APIResponse.ok(data.model_dump(), meta=make_meta(ctx))


@router.get(
    "/{resume_id}",
    summary="查询简历结构化信息",
    description=(
        "根据 `resume_id` 返回该简历的结构化抽取结果（基本/求职/背景三段）。\n\n"
        "**懒抽取**：首次请求会触发 LLM 调用（三段并发，约 2-4 秒）；后续请求命中缓存（<10ms）。\n"
        "**容错**：任一段抽取失败不影响其他段；失败的段返回空对象，并在 `extract_errors` 中记录段名。\n"
        "**降级**：基本信息段在 LLM 失败时走正则兜底（至少抽出邮箱/手机号）。"
    ),
    responses={
        200: {
            "description": "成功",
            "content": {
                "application/json": {
                    "example": {
                        "code": "OK",
                        "message": "success",
                        "data": {
                            "resume_id": "rsm_a1b2c3d4e5f6",
                            "basic": {
                                "name": "张三",
                                "phone": "13800000000",
                                "email": "zhangsan@example.com",
                                "address": "北京市海淀区",
                            },
                            "job_intent": {
                                "target_role": "后端工程师",
                                "expected_salary": "25-35k",
                            },
                            "background": {
                                "years_of_experience": 5.0,
                                "education": [
                                    {
                                        "school": "北京大学",
                                        "degree": "本科",
                                        "major": "计算机",
                                        "period": "2014-2018",
                                    }
                                ],
                                "experience": [],
                                "projects": [],
                            },
                            "extract_errors": [],
                        },
                        "meta": {
                            "elapsed_ms": 3200,
                            "tokens_used": 1820,
                            "cache_hit": False,
                            "request_id": "req-x",
                        },
                    }
                }
            },
        },
        404: {"description": "RESUME_NOT_FOUND：简历不存在或已过期"},
    },
)
async def get_resume(
    request: Request,
    resume_id: str = Path(
        ..., description="上传接口返回的 resume_id", pattern="^rsm_[a-f0-9]{12}$"
    ),
):
    """三段并发抽取后聚合返回。"""
    ctx = get_request_context(request)

    parse_result = await resume_store.get(resume_id)
    if parse_result is None:
        raise ResumeNotFoundError(message=f"未找到 resume_id={resume_id}")

    # 三段并发抽取；任一失败不影响其他
    basic_task = extract_basic(parse_result.text)
    job_task = extract_job_intent(parse_result.text)
    bg_task = extract_background(parse_result.text)
    results = await asyncio.gather(basic_task, job_task, bg_task, return_exceptions=True)

    extract_errors: list[str] = []

    def _unpack(idx: int, name: str, default):
        r = results[idx]
        if isinstance(r, Exception):
            logger.warning(
                "extract section unexpected failure",
                extra={"scope": "extract", "section": name, "err": str(r)},
            )
            extract_errors.append(name)
            return default, None, False
        return r  # (parsed, usage, cache_hit)

    from app.models.resume import ResumeBackground, ResumeBasic, ResumeJobIntent

    basic, basic_usage, basic_hit = _unpack(0, "basic", ResumeBasic())
    job, job_usage, job_hit = _unpack(1, "job_intent", ResumeJobIntent())
    bg, bg_usage, bg_hit = _unpack(2, "background", ResumeBackground())

    # 聚合 token 用量与缓存命中
    for usage in (basic_usage, job_usage, bg_usage):
        if usage is not None:
            ctx.add_tokens(usage.total_tokens)
    if basic_hit or job_hit or bg_hit:
        ctx.mark_cache_hit()

    aggregate = ResumeAggregate(
        resume_id=resume_id,
        basic=basic,
        job_intent=job,
        background=bg,
        text_preview=parse_result.text[:200],
        pages=parse_result.pages,
        char_count=parse_result.char_count,
        is_scanned_suspect=parse_result.is_scanned_suspect,
        extract_errors=extract_errors,
    )

    logger.info(
        "resume aggregate ok",
        extra={
            "scope": "extract",
            "resume_id": resume_id,
            "extract_errors": extract_errors,
            "request_id": ctx.request_id,
        },
    )

    return APIResponse.ok(aggregate.model_dump(), meta=make_meta(ctx))
