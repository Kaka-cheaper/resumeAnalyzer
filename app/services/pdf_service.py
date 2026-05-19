"""PDF 解析服务

职责：
- 把 PDF 字节流转成清洗后的文本
- 检测扫描件（字符数远低于页数 * 阈值）
- 损坏 / 加密 PDF 抛 PDFParseError

不做：
- 文件落盘（FC 无状态，不持久化）
- 缓存（缓存由调用方 / 上层服务负责）
"""

from __future__ import annotations

import io
import logging

import pdfplumber

from app.core.exceptions import PDFParseError
from app.models.resume import ParseResult
from app.utils.text import char_count as _char_count
from app.utils.text import clean_text

logger = logging.getLogger(__name__)

# 扫描件判定阈值：每页平均字符数低于此值则疑似扫描件
_SCANNED_SUSPECT_PER_PAGE = 50

# 单 PDF 页数上限，避免恶意巨型文件
_MAX_PAGES = 50


def parse_pdf(file_bytes: bytes) -> ParseResult:
    """解析 PDF 字节流。

    Args:
        file_bytes: 原始 PDF 内容

    Returns:
        ParseResult：含清洗后文本、页数、扫描件标记

    Raises:
        PDFParseError: PDF 损坏、加密或无法打开
    """
    if not file_bytes:
        raise PDFParseError(message="PDF 内容为空", suggestion="请重新上传文件")

    # 简单 magic bytes 校验
    if not file_bytes.lstrip().startswith(b"%PDF"):
        raise PDFParseError(
            message="文件不是有效的 PDF 格式",
            suggestion="请确认上传的是 PDF 文件，不是改了扩展名的其他格式",
        )

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = pdf.pages
            page_count = len(pages)

            if page_count == 0:
                raise PDFParseError(
                    message="PDF 内容为空（0 页）", suggestion="请检查 PDF 是否完整"
                )

            if page_count > _MAX_PAGES:
                raise PDFParseError(
                    message=f"PDF 页数过多（{page_count} > {_MAX_PAGES}）",
                    suggestion="简历通常不超过 5 页；请精简后重新上传",
                )

            page_texts: list[str] = []
            for idx, page in enumerate(pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception as e:
                    # 单页异常不致命，继续跑下一页
                    logger.warning(
                        "page extract failed",
                        extra={"scope": "pdf", "page": idx, "error": str(e)},
                    )
                    text = ""
                page_texts.append(text)

            raw_text = "\n\n".join(page_texts)
    except PDFParseError:
        raise
    except Exception as e:
        # pdfplumber/pdfminer 内部异常类型多变，统一包装
        msg = str(e).lower()
        if "encrypted" in msg or "password" in msg:
            raise PDFParseError(
                message="PDF 已加密，无法解析", suggestion="请上传未加密的 PDF"
            ) from e
        raise PDFParseError(
            message=f"PDF 文件可能已损坏：{type(e).__name__}",
            suggestion="请确认 PDF 文件完整后重新上传",
        ) from e

    cleaned = clean_text(raw_text)
    cc = _char_count(cleaned)
    is_scanned = cc < page_count * _SCANNED_SUSPECT_PER_PAGE

    logger.info(
        "pdf parsed",
        extra={
            "scope": "pdf",
            "pages": page_count,
            "char_count": cc,
            "is_scanned_suspect": is_scanned,
            "raw_len": len(raw_text),
        },
    )

    return ParseResult(
        pages=page_count,
        text=cleaned,
        raw_text=raw_text,
        char_count=cc,
        is_scanned_suspect=is_scanned,
    )
