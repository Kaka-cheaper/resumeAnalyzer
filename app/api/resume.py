"""简历相关路由：上传、查询

两个接口：
- POST /api/resume/upload      上传 PDF → 解析 → 返回 resume_id
- GET  /api/resume/{resume_id} 三段并发抽取（基本/求职/背景）→ 聚合返回

设计要点：
- 上传接口的 resume_id 基于文件 SHA-256 → 自然幂等（同份 PDF 永远同 ID）
- 查询接口走 asyncio.gather 三段并发 + return_exceptions=True 隔离失败
- 多层校验（扩展名 + MIME + magic bytes + 大小）防止恶意输入
"""

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

# prefix="/api/resume" 让所有路由自动加前缀；tags 是 OpenAPI 分组用的
router = APIRouter(prefix="/api/resume", tags=["resume"])

# 允许的 PDF MIME 类型（不同浏览器/客户端可能略有差异）
# octet-stream 是浏览器对未知类型的兜底，部分客户端会用这个传 PDF
_ALLOWED_MIME = {"application/pdf", "application/x-pdf", "application/octet-stream"}

# PDF 文件 magic bytes（前 4 字节固定为 %PDF）
# 用 magic bytes 校验比扩展名/MIME 更可靠——后两者都能伪造
_PDF_MAGIC = b"%PDF"


def _validate_extension(filename: str | None) -> None:
    """第一层校验：扩展名必须是 .pdf。

    扩展名校验最弱（用户可改），但成本最低，先过滤明显错误。
    """
    if not filename:
        raise InvalidFileTypeError(message="缺少文件名")
    if not filename.lower().endswith(".pdf"):
        raise InvalidFileTypeError(message=f"文件类型不支持：{filename}")


def _validate_mime(content_type: str | None) -> None:
    """第二层校验：MIME 类型（容忍 octet-stream 等模糊类型）。

    部分客户端不发 Content-Type 或发 octet-stream，直接拒绝会误伤——
    所以这里只在 content_type 存在且不在白名单时才拒绝。
    """
    if content_type and content_type.lower() not in _ALLOWED_MIME:
        raise InvalidFileTypeError(message=f"MIME 类型不支持：{content_type}")


def _validate_magic(content: bytes) -> None:
    """第三层校验：PDF magic bytes，防止改扩展名绕过。

    这是最可靠的检查——文件头 4 字节是 PDF 规范定义的标识，无法伪造。
    例：把 .txt 改名为 .pdf 会被这层拦下。

    `lstrip()` 容忍开头有空白字符（极少见但保险）
    """
    if not content.lstrip()[:4] == _PDF_MAGIC:
        raise InvalidFileTypeError(
            message="文件头不是有效的 PDF 标识",
            suggestion="请确认上传的是真正的 PDF 文件",
        )


def _validate_size(content: bytes) -> None:
    """第四层校验：文件大小（默认 ≤ 10 MB）。

    上限值从 settings 读，不写死——便于运维按业务调整。
    """
    settings = get_settings()
    size = len(content)
    if size == 0:
        raise InvalidFileTypeError(message="文件为空")
    if size > settings.max_upload_size_bytes:
        raise FileTooLargeError(
            message=f"文件大小 {size / 1024 / 1024:.1f} MB "
            f"超过限制 {settings.max_upload_size_mb} MB"
        )


@router.post(
    "/upload",
    summary="上传简历 PDF",
    description=(
        "上传单个 PDF 简历，服务端解析为文本并返回 `resume_id`。\n\n"
        "**多层校验**：扩展名 + MIME + magic bytes + 大小（≤10MB）。\n"
        "**幂等**：同一份 PDF 重复上传会复用上次的解析结果（同一 resume_id），"
        "缓存命中标记 `meta.cache_hit=true`。"
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
    """上传简历并解析。

    流程：
        1. 4 层校验（扩展名 / MIME / 大小 / magic bytes）
        2. 算文件 hash → resume_id（自然幂等）
        3. 查缓存：命中 → 直接返回（cache_hit=true）
        4. 缓存 miss → pdfplumber 解析 → 落 store → 返回
    """
    ctx = get_request_context(request)

    # 第一层：扩展名 + MIME（流式校验，不读文件内容）
    _validate_extension(file.filename)
    _validate_mime(file.content_type)

    # 读全部字节（multipart 已由 FastAPI 处理）
    # 注意：UploadFile.read() 是 async（背后是 SpooledTemporaryFile）
    content = await file.read()

    # 第二层：大小 + magic bytes（需要文件内容）
    _validate_size(content)
    _validate_magic(content)

    # 计算 resume_id（基于文件 SHA-256 前 12 位）
    # 同一份 PDF 永远得到同一个 ID = 自然幂等
    # 12 位 hex ≈ 48 bit 熵，碰撞概率在 24h 内简历量级下可忽略
    file_hash = sha256_bytes(content)
    resume_id = f"rsm_{short_hash(content, 12)}"

    # 命中已存简历？（同份 PDF 之前传过）
    cached = await resume_store.get(resume_id)
    if cached is not None:
        # 标记 cache_hit=true，前端会展示缓存徽章
        ctx.mark_cache_hit()
        logger.info(
            "upload cache hit",
            extra={"scope": "upload", "resume_id": resume_id, "request_id": ctx.request_id},
        )
        # 重新构造响应（不直接返回 ParseResult，避免暴露内部字段）
        data = UploadResponse(
            resume_id=resume_id,
            pages=cached.pages,
            char_count=cached.char_count,
            is_scanned_suspect=cached.is_scanned_suspect,
            text_preview=cached.text[:200],
        )
        return APIResponse.ok(data.model_dump(), meta=make_meta(ctx))

    # 解析 PDF（pdfplumber 多页抽取 + 文本清洗）
    try:
        result = parse_pdf(content)
    except PDFParseError as e:
        # 把 file_hash 注入异常 details 字段，写日志时能关联
        # 不重新抛——让全局 handler 包装为统一信封
        e.details["file_hash"] = file_hash
        raise

    # 落临时存储（C1 阶段从 dict 升级到 Cache 抽象，调用方零改动）
    await resume_store.save(resume_id, result)

    # 成功日志：含 is_scanned_suspect 标记，方便统计扫描件占比
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

    # 构造响应：text_preview 取前 200 字给前端展示，不暴露完整文本
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
        "**懒抽取**：首次请求会触发 LLM 调用（三段并发，约 2-4 秒）；"
        "后续请求命中缓存（<10ms）。\n"
        "**容错**：任一段抽取失败不影响其他段；失败的段返回空对象，"
        "并在 `extract_errors` 中记录段名。\n"
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
    # Path 参数 pattern 校验：resume_id 必须是 rsm_ 开头 + 12 位 hex
    # 让 Pydantic 在路由层就拒绝格式错误的请求（返回 422），不进入业务函数
    # 避免恶意输入打到 store / 缓存层
    resume_id: str = Path(
        ..., description="上传接口返回的 resume_id", pattern="^rsm_[a-f0-9]{12}$"
    ),
):
    """三段并发抽取后聚合返回。

    性能：三段串行 ≈ 4.5s，asyncio.gather 并发 ≈ 1.5s（取最长那段）
    """
    ctx = get_request_context(request)

    # 先查 store：resume_id 不存在直接 404，避免无意义的 LLM 调用
    parse_result = await resume_store.get(resume_id)
    if parse_result is None:
        raise ResumeNotFoundError(message=f"未找到 resume_id={resume_id}")

    # ===== 三段并发抽取 =====
    # 创建三个 coroutine（不会立即执行）
    basic_task = extract_basic(parse_result.text)
    job_task = extract_job_intent(parse_result.text)
    bg_task = extract_background(parse_result.text)

    # asyncio.gather 并发等待三段完成
    # return_exceptions=True 是关键：默认任一任务异常会让 gather 整体抛异常，
    # 这里改成 True 后异常作为结果返回，不影响其他段
    # 这就实现了"任一段失败不影响其他段"
    results = await asyncio.gather(basic_task, job_task, bg_task, return_exceptions=True)

    # 收集失败的段名，最后透传给前端展示
    extract_errors: list[str] = []

    def _unpack(idx: int, name: str, default):
        """统一处理「成功 / 异常」两条路径，让外层代码不用关心。

        Returns:
            (parsed, usage, cache_hit) 三元组——和 extract_basic 等返回值结构一致
        """
        r = results[idx]
        if isinstance(r, Exception):
            # 异常被 gather 吞了 → 这里显式记日志（含 stack trace 信息）
            logger.warning(
                "extract section unexpected failure",
                extra={"scope": "extract", "section": name, "err": str(r)},
            )
            extract_errors.append(name)
            # 用默认空对象兜底，保证响应结构稳定
            return default, None, False
        return r  # 正常返回 (parsed, usage, cache_hit)

    # 延迟 import：避免循环依赖（resume.py → models → ...）
    from app.models.resume import ResumeBackground, ResumeBasic, ResumeJobIntent

    basic, basic_usage, basic_hit = _unpack(0, "basic", ResumeBasic())
    job, job_usage, job_hit = _unpack(1, "job_intent", ResumeJobIntent())
    bg, bg_usage, bg_hit = _unpack(2, "background", ResumeBackground())

    # ===== 聚合 token 用量与缓存命中标记 =====
    # 三段独立计算 token，需要在 ctx 上累加上报到响应 meta
    for usage in (basic_usage, job_usage, bg_usage):
        if usage is not None:
            ctx.add_tokens(usage.total_tokens)
    # 任一段命中缓存就标记（前端展示一个聚合的徽章）
    if basic_hit or job_hit or bg_hit:
        ctx.mark_cache_hit()

    # 组装最终响应
    aggregate = ResumeAggregate(
        resume_id=resume_id,
        basic=basic,
        job_intent=job,
        background=bg,
        text_preview=parse_result.text[:200],
        pages=parse_result.pages,
        char_count=parse_result.char_count,
        is_scanned_suspect=parse_result.is_scanned_suspect,
        # 失败段名透传给前端，UI 上能展示哪段抽取失败
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
