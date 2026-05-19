"""文本清洗工具

设计原则：
- 清洗规则单一职责，便于测试与组合
- 中文友好：保留中英混排空格规范、不暴力删非 ASCII
- 幂等：清洗已经干净的文本不应改变结果
"""

from __future__ import annotations

import re
import unicodedata

# 控制字符（保留 \n \t），其余 < 0x20 全去
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 多个连续空白（不含换行）合并为单空格
_INLINE_WHITESPACE_RE = re.compile(r"[ \t]+")

# 三个及以上换行合并为两个（保段落感）
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

# 行首/行尾空白
_LINE_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_LINE_LEADING_WS_RE = re.compile(r"^[ \t]+", re.MULTILINE)

# 中文与英文字母/数字之间补一个空格（提升可读性，可选）
_CN_EN_RE = re.compile(r"([\u4e00-\u9fa5])([A-Za-z0-9])")
_EN_CN_RE = re.compile(r"([A-Za-z0-9])([\u4e00-\u9fa5])")


def clean_text(raw: str, *, normalize_unicode: bool = True) -> str:
    """清洗 PDF 抽取出的原始文本。

    步骤：
    1. Unicode NFKC 归一化（全角→半角、兼容字符规范化）
    2. 去除控制字符（保留 \\n \\t）
    3. 行内多空白合并为单空格
    4. 行首/行尾空白去除
    5. 多换行合并为段落分隔（最多 2 个）
    6. 截断头尾空行

    Args:
        raw: 原始文本
        normalize_unicode: 是否走 NFKC 归一化（默认开）
    """
    if not raw:
        return ""

    text = raw
    if normalize_unicode:
        text = unicodedata.normalize("NFKC", text)

    text = _CONTROL_CHARS_RE.sub("", text)
    # Windows 风格换行 → \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INLINE_WHITESPACE_RE.sub(" ", text)
    text = _LINE_TRAILING_WS_RE.sub("", text)
    text = _LINE_LEADING_WS_RE.sub("", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def normalize_cn_en_spacing(text: str) -> str:
    """中文与英文/数字之间补单空格。

    可选项：默认 clean_text 不做这一步，避免改动专业术语形态；
    需要时由调用方显式启用。
    """
    text = _CN_EN_RE.sub(r"\1 \2", text)
    text = _EN_CN_RE.sub(r"\1 \2", text)
    return text


def truncate(text: str, max_len: int) -> str:
    """截断文本到指定字符数。

    超长时保留头尾部各一半，中间用 [...] 标记，避免简历开头/结尾信息被切掉。
    """
    if len(text) <= max_len:
        return text
    # 留一个标记的位置
    marker = "\n...[已截断]...\n"
    keep = max_len - len(marker)
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + text[-tail:]


def char_count(text: str) -> int:
    """字符数（去除空白后），用于检测扫描件。"""
    return len(re.sub(r"\s+", "", text))
