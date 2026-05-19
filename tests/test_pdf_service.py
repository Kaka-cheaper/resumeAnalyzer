"""PDF 解析服务单元测试

按 tasks.md B1 验收清单，覆盖：
- 正常多页 PDF
- 极简 PDF
- 损坏 PDF（非 PDF 内容假冒 .pdf）
- 空字节
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import PDFParseError
from app.services.pdf_service import parse_pdf

SAMPLES_DIR = Path(__file__).parent.parent / "samples"


def _read(name: str) -> bytes:
    p = SAMPLES_DIR / name
    if not p.exists():
        pytest.skip(f"sample missing: {p}")
    return p.read_bytes()


def test_parse_normal_resume():
    """多页中英混排简历应正常解析。"""
    result = parse_pdf(_read("sample_resume.pdf"))
    assert result.pages >= 2
    assert result.char_count > 100
    assert "zhangsan@example.com" in result.text or "Zhang San" in result.text
    assert result.is_scanned_suspect is False


def test_parse_minimal_pdf():
    """极简 PDF 应能解析，但可能被标记为扫描件。"""
    result = parse_pdf(_read("sample_minimal.pdf"))
    assert result.pages == 1
    # "hi" 只有 2 个字符 < 50，会被标扫描件
    assert result.is_scanned_suspect is True


def test_parse_corrupt_raises():
    """伪装成 PDF 的文本文件应抛 PDFParseError。"""
    with pytest.raises(PDFParseError):
        parse_pdf(_read("sample_corrupt.pdf"))


def test_parse_empty_bytes():
    """空字节流应抛 PDFParseError。"""
    with pytest.raises(PDFParseError):
        parse_pdf(b"")
