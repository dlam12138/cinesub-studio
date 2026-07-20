"""WenYi-derived prompt composition, specialized for immutable SRT cues.

The ordering mirrors WenYi v0.3.2's translator pipeline: style/profile,
global synopsis, local digest, relevant glossary, rolling target context,
then the current numbered source. Copyright (c) 2025 BigDawnGhost, MIT.
"""

from __future__ import annotations

import json
from typing import Any


def _block(label: str, value: Any) -> str:
    rendered = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    )
    return f"【{label}】\n{rendered or '（无）'}"


def analysis_prompt(target_language: str) -> str:
    return f"""你是视频字幕翻译项目的前期分析师。目标语言是 {target_language}。
通读带固定 ID 的原始字幕，输出全片概览、分场摘要、内容口吻和初始术语。
discourse_style 只能是 interview、explanation、speech、dialogue、mixed、unknown。
术语只收人物、地点、机构、作品名和技术术语；普通词、一次性修辞、语气词不得进入。
每条术语必须给出 type、source、target、confidence 与 evidence_ids。
ASR 只报告可疑项，不修改原文；category 只能是 low_confidence_name、
grammatically_impossible、context_conflict。
仅返回 JSON：
{{"video_summary":"...","scene_summaries":[{{"scene_index":1,"summary":"..."}}],
"discourse_style":"unknown","typed_glossary":[{{"source":"...","target":"...",
"type":"person|place|organization|work|technical","confidence":"high|medium|low",
"evidence_ids":[1]}}],"asr_warnings":[{{"id":1,"category":"context_conflict",
"detail":"...","confidence":"high|medium|low"}}]}}"""


def translation_prompt(
    *,
    target_language: str,
    profile: str,
    video_summary: str,
    scene_summary: str,
    discourse_style: str,
    glossary: list[dict],
    recent: list[dict],
    context_before: list[dict],
    context_after: list[dict],
) -> str:
    return "\n\n".join([
        f"""你是资深影视字幕译者，把原文忠实翻译为 {target_language}。
严格保持每个 ID、条数和顺序；不得合并、拆分或跨 ID 搬移信息。
只翻译 items；前后文只读。优先自然、简洁的现代汉语，按内容口吻处理，
避免机械使用“进行、对于、之所以”等套话，但不得添加情绪、解释或推断。
相关术语只在其 source 确实出现时使用；人工术语优先。
返回严格 JSON：{{"items":[{{"id":1,"translation":"..."}}]}}。""",
        _block("Language Profile", profile),
        _block("全片概览", video_summary),
        _block("本场摘要", scene_summary),
        _block("内容口吻", discourse_style),
        _block("相关术语", glossary),
        _block("最近译文", recent),
        _block("只读前文", context_before),
        _block("只读后文", context_after),
    ])


def reviewer_prompt(*, cross_line: bool = False) -> str:
    scope = (
        "输入包含同一翻译批次内的多个确定性跨行窗口。逐个检查 windows，"
        "重点检查跨 cue 指代、否定、比较、因果和未完句；不要因为窗口被筛中就推定有错。"
        if cross_line else
        "逐 ID 比较原文和译文，并结合只读上下文检查连续性。"
    )
    return f"""你是严格的字幕审校员。{scope}
只报告高置信、实质性的漏译、增译、误译、术语、人称或连续性问题。
合理语序调整和自然意译不算问题；拿不准就不报。
问题必须落到一个现有 ID，绝不重写整个窗口。
返回严格 JSON：
{{"issues":[{{"id":1,"type":"missing|added|mistranslation|terminology|pronoun|continuity",
"detail":"...","confidence":"high|medium|low"}}],
"terms":[{{"source":"...","target":"...","type":"person|place|organization|work|technical",
"confidence":"high|medium|low","evidence_ids":[1]}}]}}"""


def repair_prompt() -> str:
    return """只修复给定 ID 的高置信实质问题。完整保留当前译文中正确的事实、
否定、数字、实体、指代和逻辑关系；不润色全句，不增加原文没有的信息。
上下文只读。返回严格 JSON：{"items":[{"id":1,"translation":"..."}]}。"""


def judge_prompt(*, shortening: bool = False, strict: bool = False) -> str:
    if shortening:
        extra = (
            ',"issue_resolved":true,"no_new_error":true'
            if strict else ""
        )
        return f"""匿名比较长版与短版。只有短版满足 max_target_chars 且事实、否定、
数字、实体、指代、逻辑关系全部完整保留，才能选择短版。返回严格 JSON：
{{"id":1,"choice":"A|B|TIE","confidence":"high|medium|low",
"facts":true,"negation":true,"numbers":true,"entities":true,
"references":true,"logic":true{extra},"reason":"..."}}。"""
    if strict:
        return """匿名比较当前字幕与挑战候选，只按原文、问题证据和只读上下文判断。
只有挑战候选确实解决所报问题、没有新增错误，并完整保留事实、否定、数字、实体、
指代和逻辑关系时才能选择挑战候选；不奖励单纯润色，不能证明时选择 TIE。
返回严格 JSON：
{"id":1,"choice":"A|B|TIE","confidence":"high|medium|low",
"facts":true,"negation":true,"numbers":true,"entities":true,
"references":true,"logic":true,"issue_resolved":true,"no_new_error":true,
"reason":"..."}。"""
    return """匿名比较两个字幕版本，只判断忠实、完整、术语、人称和上下文连续性。
不奖励单纯润色；除非一个版本明确更忠实，否则选择 TIE。返回严格 JSON：
{"id":1,"choice":"A|B|TIE","confidence":"high|medium|low","reason":"..."}。"""


def shortening_prompt() -> str:
    return """在不改变 ID 和字幕边界的前提下，为当前长版提供不超过
max_target_chars 的短版。必须完整保留事实、否定、数字、人物/专名、指代和逻辑。
不能证明可缩短时返回原长版。返回严格 JSON：
{"items":[{"id":1,"translation":"..."}]}。"""


def consistency_prompt() -> str:
    return """只读审计全片最终字幕，按同一人物或术语聚合译名变体。
只处理人物、地点、机构、作品名和技术术语，普通词不得当作专名。
区分 definite_error 与 acceptable_variant，不做自动替换。
返回严格 JSON：{"groups":[{"source":"...","term_type":"person|place|organization|work|technical",
"canonical":"...","variants":[{"text":"...","ids":[1]}],
"classification":"definite_error|acceptable_variant","detail":"..."}]}。"""
