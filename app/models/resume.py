"""简历相关的数据模型

E 阶段补全抽取后的结构化模型。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ParseResult(BaseModel):
    """PDF 解析结果（领域对象）。"""

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


# ===== E 阶段：抽取结果 =====


class ResumeBasic(BaseModel):
    """基本信息（必选）。"""

    name: str | None = Field(default=None, description="姓名")
    phone: str | None = Field(default=None, description="电话")
    email: str | None = Field(default=None, description="邮箱")
    address: str | None = Field(default=None, description="地址（城市/区/详细）")


class ResumeJobIntent(BaseModel):
    """求职信息（加分项）。"""

    target_role: str | None = Field(default=None, description="求职意向 / 期望职位")
    expected_salary: str | None = Field(default=None, description="期望薪资，原文格式")


class Education(BaseModel):
    """单条教育经历。"""

    school: str | None = Field(default=None, description="学校")
    degree: str | None = Field(default=None, description="学位（本科/硕士/博士/大专 等）")
    major: str | None = Field(default=None, description="专业")
    period: str | None = Field(default=None, description="起止时间，原文格式")


class WorkExperience(BaseModel):
    """单段工作经历，用于年限计算。"""

    company: str | None = Field(default=None, description="公司")
    role: str | None = Field(default=None, description="职位")
    start: str | None = Field(default=None, description="起始时间（YYYY-MM 或 YYYY）")
    end: str | None = Field(default=None, description="结束时间（YYYY-MM / YYYY / 至今 / present）")
    summary: str | None = Field(default=None, description="工作内容简述")


class Project(BaseModel):
    """单个项目经历。"""

    name: str | None = Field(default=None, description="项目名称")
    role: str | None = Field(default=None, description="承担角色")
    summary: str | None = Field(default=None, description="项目简述")


class ResumeBackground(BaseModel):
    """背景信息（加分项）。"""

    years_of_experience: float | None = Field(
        default=None, description="工作年限（基于经历起止日期计算，留 1 位小数）"
    )
    education: list[Education] = Field(default_factory=list, description="教育经历")
    experience: list[WorkExperience] = Field(default_factory=list, description="工作经历")
    projects: list[Project] = Field(default_factory=list, description="项目经历")


class ResumeAggregate(BaseModel):
    """聚合三段抽取结果，对应 GET /api/resume/{id} 响应 data。"""

    resume_id: str = Field(description="简历唯一 ID")
    basic: ResumeBasic = Field(default_factory=ResumeBasic)
    job_intent: ResumeJobIntent = Field(default_factory=ResumeJobIntent)
    background: ResumeBackground = Field(default_factory=ResumeBackground)
    text_preview: str = Field(default="", description="清洗后文本前 200 字")
    pages: int = Field(default=0)
    char_count: int = Field(default=0)
    is_scanned_suspect: bool = Field(default=False)
    extract_errors: list[str] = Field(
        default_factory=list, description="哪些段抽取失败（仅记段名）"
    )
