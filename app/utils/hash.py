"""哈希工具：用于缓存键、resume_id 等"""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    """对字节流计算 SHA-256 十六进制摘要。"""
    return hashlib.sha256(data).hexdigest()


def sha256_str(text: str) -> str:
    """对字符串计算 SHA-256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(data: bytes | str, length: int = 12) -> str:
    """生成短哈希，用于 resume_id 等用户可见的 ID。

    长度 12 个十六进制字符 ≈ 48 bit 熵，碰撞概率在 24h 内的简历量级下可忽略。
    """
    if isinstance(data, str):
        full = sha256_str(data)
    else:
        full = sha256_bytes(data)
    return full[:length]
