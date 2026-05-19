"""LLM 提示词集中管理

D 阶段只放通用工具与系统级提示；
E 阶段会按段（basic / job / background）/ JD（keywords）/ 评分（match）填具体业务 prompt。

设计原则：
- 所有 prompt 写成纯字符串常量或纯函数，便于做 prompt diff 与 A/B
- 业务 prompt 必须显式声明输出 JSON schema，并要求模型严格遵守
- 给 1-2 个 few-shot 让模型对齐字段命名
"""

from __future__ import annotations

# 通用 JSON 输出指令（可拼接到任意业务 system prompt 末尾）
JSON_OUTPUT_INSTRUCTION = (
    "你必须严格输出 JSON，不要添加 markdown 代码块、不要写解释文字。"
    "字段缺失时输出 null（不要写空字符串、不要写'无'、不要省略字段）。"
)


def with_json_instruction(system_prompt: str) -> str:
    """为系统提示拼接 JSON 输出约束。"""
    return system_prompt.rstrip() + "\n\n" + JSON_OUTPUT_INSTRUCTION
