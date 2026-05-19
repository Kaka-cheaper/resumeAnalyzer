"""简历存储回归测试

确保 C1 重构后 resume_store 行为不变。
"""

from __future__ import annotations

import pytest

from app.models.resume import ParseResult
from app.services import resume_store
from app.services.cache_service import reset_cache


@pytest.fixture(autouse=True)
async def isolated_cache():
    """每个测试用独立的内存缓存实例。"""
    reset_cache()
    yield
    await resume_store.clear()
    reset_cache()


def _make_result(text: str = "hello") -> ParseResult:
    return ParseResult(
        pages=1,
        text=text,
        raw_text=text,
        char_count=len(text),
        is_scanned_suspect=False,
    )


async def test_save_then_get():
    result = _make_result("张三 / 北京 / 5 年经验")
    await resume_store.save("rsm_test1", result)

    got = await resume_store.get("rsm_test1")
    assert got is not None
    assert got.text == result.text
    assert got.pages == 1


async def test_get_missing_returns_none():
    assert await resume_store.get("rsm_notexist") is None


async def test_exists():
    assert await resume_store.exists("rsm_x") is False
    await resume_store.save("rsm_x", _make_result())
    assert await resume_store.exists("rsm_x") is True


async def test_overwrite():
    await resume_store.save("rsm_x", _make_result("v1"))
    await resume_store.save("rsm_x", _make_result("v2"))
    got = await resume_store.get("rsm_x")
    assert got.text == "v2"
