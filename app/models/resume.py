"""简历相关的数据模型

包含 PDF 解析结果、上传响应、查询响应等。
信息抽取相关模型（ResumeBasic / ResumeJobIntent / ResumeBackground）会在 E 阶段补。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ParseResult(BaseModel):
    """PDF 解析结果（领域对象，不直接对外暴露完整文本）。"""

    model_config = ConfigDict(frozen=False)

    pages: int = Field(description="PDF 页数")
    text: str = Field(description="清洗后的全文")
    raw_text: str = Field(default="", description="清洗前的原始文本，调试用")
    char_count: int = Field(description="清洗后非空白字符数")
    is_scanned_suspect: bool = Field(default=False, description="疑似扫描件（文本字符数过少）")


class UploadResponse(BaseModel):
    """POST /api/resume/upload 的响应 data。"""

    resume_id: str = Field(description="简历唯一 ID，前缀 rsm_")
    pages: int = Field(description="PDF 页数")
    char_count: int = Field(description="清洗后字符数")
    is_scanned_suspect: bool = Field(description="是否疑似扫描件")
    text_preview: str = Field(description="文本预览（前 200 字）")
