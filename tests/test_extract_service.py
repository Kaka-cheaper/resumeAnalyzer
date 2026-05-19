"""信息抽取服务单元测试

不调真实 LLM；mock client 验证：
- 三段抽取的成功路径 + 缓存路径 + 失败降级路径
- 工作年限计算（calc_years_of_experience）
- 正则兜底（_regex_fallback_basic）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cache.base import CacheKeys
from app.core.exceptions import LLMError
from app.models.resume import (
    ResumeBackground,
    ResumeBasic,
    ResumeJobIntent,
    WorkExperience,
)
from app.services import cache_service
from app.services.extract_service import (
    _parse_month,
    _regex_fallback_basic,
    calc_years_of_experience,
    extract_background,
    extract_basic,
    extract_job_intent,
)
from app.utils.hash import sha256_str


@pytest.fixture(autouse=True)
async def isolated_cache():
    cache_service.reset_cache()
    yield
    cache_service.reset_cache()


@pytest.fixture
def mock_llm():
    client = MagicMock()
    client.chat_json = AsyncMock()
    return client


# ============================================================
# 正则兜底
# ============================================================


def test_regex_fallback_basic_phone_email():
    text = "张三 / 后端工程师\n联系：zhangsan@example.com / 138-0000-0000"
    result = _regex_fallback_basic(text)
    assert result.email == "zhangsan@example.com"
    assert "138" in result.phone
    assert result.address is None  # 正则不抽地址


def test_regex_fallback_basic_with_name_label():
    text = "姓名：李四\nemail: li@x.com"
    result = _regex_fallback_basic(text)
    assert result.name == "李四"
    assert result.email == "li@x.com"


def test_regex_fallback_basic_empty():
    result = _regex_fallback_basic("纯随便文本")
    assert result.name is None
    assert result.email is None
    assert result.phone is None


# ============================================================
# 工作年限计算
# ============================================================


def test_parse_month_yyyy_mm():
    assert _parse_month("2020-03") == (2020, 3)
    assert _parse_month("2020.03") == (2020, 3)
    assert _parse_month("2020/3") == (2020, 3)


def test_parse_month_yyyy_only_defaults_mid_year():
    assert _parse_month("2020") == (2020, 6)


def test_parse_month_present():
    res = _parse_month("present")
    assert res is not None and res[0] >= 2025  # 当前年份
    assert _parse_month("至今") is not None
    assert _parse_month("Now") is not None


def test_parse_month_invalid_returns_none():
    assert _parse_month(None) is None
    assert _parse_month("") is None
    assert _parse_month("乱码") is None
    assert _parse_month("2020-13") is None  # 月份非法


def test_calc_years_basic():
    exps = [
        WorkExperience(start="2020-01", end="2025-01"),
    ]
    assert calc_years_of_experience(exps) == 5.0


def test_calc_years_multi_segments():
    exps = [
        WorkExperience(start="2018-06", end="2020-06"),  # 2 年
        WorkExperience(start="2021-01", end="2024-01"),  # 3 年
    ]
    assert calc_years_of_experience(exps) == 5.0


def test_calc_years_present():
    exps = [WorkExperience(start="2024-01", end="present")]
    res = calc_years_of_experience(exps)
    assert res is not None and 1.0 <= res <= 3.0  # 2024 到现在大约 1-2 年


def test_calc_years_no_valid_dates():
    exps = [WorkExperience(start=None, end=None, summary="x")]
    assert calc_years_of_experience(exps) is None


def test_calc_years_empty_list():
    assert calc_years_of_experience([]) is None


def test_calc_years_invalid_period_skipped():
    exps = [
        WorkExperience(start="2020", end="2018"),  # end < start，跳过
        WorkExperience(start="2020", end="2022"),  # 2 年
    ]
    assert calc_years_of_experience(exps) == 2.0


# ============================================================
# extract_basic
# ============================================================


async def test_extract_basic_llm_success(mock_llm):
    expected = ResumeBasic(name="张三", phone="138-0000-0000", email="z@x.com", address="北京")
    mock_llm.chat_json.return_value = (expected, MagicMock(total_tokens=100, latency_ms=500))

    parsed, usage, cache_hit = await extract_basic("一段简历文本", client=mock_llm)
    assert parsed.name == "张三"
    assert usage is not None and usage.total_tokens == 100
    assert cache_hit is False


async def test_extract_basic_cache_hit(mock_llm):
    """第二次调用应命中缓存，不再调 LLM。"""
    expected = ResumeBasic(name="张三", phone=None, email="z@x.com", address=None)
    mock_llm.chat_json.return_value = (expected, MagicMock(total_tokens=100, latency_ms=500))

    text = "简历"
    await extract_basic(text, client=mock_llm)
    parsed, usage, cache_hit = await extract_basic(text, client=mock_llm)

    assert parsed.name == "张三"
    assert cache_hit is True
    assert usage is None  # 命中缓存不算 token
    assert mock_llm.chat_json.await_count == 1


async def test_extract_basic_llm_fail_falls_back_to_regex(mock_llm):
    """LLM 抛 LLMError → 走正则兜底。"""
    mock_llm.chat_json.side_effect = LLMError(message="模拟失败")

    text = "联系：fallback@example.com / 13900000000"
    parsed, usage, cache_hit = await extract_basic(text, client=mock_llm)

    assert parsed.email == "fallback@example.com"
    assert "139" in (parsed.phone or "")
    assert usage is None
    assert cache_hit is False


# ============================================================
# extract_job_intent
# ============================================================


async def test_extract_job_intent_llm_success(mock_llm):
    expected = ResumeJobIntent(target_role="后端工程师", expected_salary="25-35k")
    mock_llm.chat_json.return_value = (expected, MagicMock(total_tokens=80, latency_ms=400))
    parsed, usage, _ = await extract_job_intent("简历", client=mock_llm)
    assert parsed.target_role == "后端工程师"
    assert usage.total_tokens == 80


async def test_extract_job_intent_llm_fail_returns_empty(mock_llm):
    mock_llm.chat_json.side_effect = LLMError("fail")
    parsed, usage, _ = await extract_job_intent("简历", client=mock_llm)
    assert parsed.target_role is None
    assert parsed.expected_salary is None
    assert usage is None


# ============================================================
# extract_background
# ============================================================


async def test_extract_background_llm_success_with_year_calc(mock_llm):
    """LLM 给的 years_of_experience 应被计算结果覆盖。"""
    expected = ResumeBackground(
        years_of_experience=99,  # LLM 错算
        education=[],
        experience=[
            WorkExperience(start="2020-01", end="2024-01"),  # 实际 4 年
        ],
        projects=[],
    )
    mock_llm.chat_json.return_value = (expected, MagicMock(total_tokens=300, latency_ms=800))
    parsed, _, _ = await extract_background("简历", client=mock_llm)
    assert parsed.years_of_experience == 4.0  # 计算覆盖


async def test_extract_background_llm_success_no_dates_keeps_llm_value(mock_llm):
    """无可解析日期时不覆盖（保留 LLM 给的值）。"""
    expected = ResumeBackground(
        years_of_experience=5.0,
        experience=[WorkExperience(start=None, end=None)],
    )
    mock_llm.chat_json.return_value = (expected, MagicMock(total_tokens=200, latency_ms=600))
    parsed, _, _ = await extract_background("简历", client=mock_llm)
    assert parsed.years_of_experience == 5.0


async def test_extract_background_llm_fail_returns_empty(mock_llm):
    mock_llm.chat_json.side_effect = LLMError("fail")
    parsed, usage, _ = await extract_background("简历", client=mock_llm)
    assert parsed.years_of_experience is None
    assert parsed.education == []
    assert usage is None


# ============================================================
# 缓存 key 命名空间隔离
# ============================================================


async def test_extract_basic_cache_key_namespace(mock_llm):
    """basic / job / background 的缓存 key 互不干扰。"""
    expected_basic = ResumeBasic(name="张三")
    expected_job = ResumeJobIntent(target_role="后端")
    mock_llm.chat_json.side_effect = [
        (expected_basic, MagicMock(total_tokens=50)),
        (expected_job, MagicMock(total_tokens=50)),
    ]
    text = "简历"
    b, _, b_hit = await extract_basic(text, client=mock_llm)
    j, _, j_hit = await extract_job_intent(text, client=mock_llm)
    assert b.name == "张三"
    assert j.target_role == "后端"
    assert b_hit is False and j_hit is False
    # 两段独立调用各 1 次
    assert mock_llm.chat_json.await_count == 2


async def test_cache_key_format_alignment():
    """验证 extract_service 用的 key 与 CacheKeys 工厂一致。"""
    text = "x"
    h = sha256_str(text)
    assert CacheKeys.extract(h, "basic") == f"extract:basic:{h}"
    assert CacheKeys.extract(h, "job") == f"extract:job:{h}"
    assert CacheKeys.extract(h, "background") == f"extract:background:{h}"
