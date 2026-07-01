"""
Subtitle Quality Checker — 自动字幕质检模块

检查项：
  - SRT 格式：编号连续性、时间码格式、时间轴重叠、空字幕、字幕过长、时长过短、重复内容
  - 翻译质量：未翻译内容检测、LLM 废话检测、中英文混乱检测
  - 一致性：原文与译文条数是否匹配

输出：
  - quality_report.json  — 结构化质量报告
  - review_needed.srt    — 仅包含需人工复核的异常片段
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from encoding_utils import read_json, read_text as read_utf8_text, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ── 问题类型定义 ─────────────────────────────────────────────────────────

@dataclass
class QualityIssue:
    index: int           # 字幕编号（1-based）
    type: str            # 问题类型代码
    severity: str        # "error" | "warning" | "info"
    text: str            # 问题描述
    snippet: str = ""    # 相关字幕内容片段
    suggestion: str = "" # 修复建议


@dataclass
class QualityReport:
    status: str = "pass"  # "pass" | "warning" | "fail"
    source_srt: str = ""
    translated_srt: str = ""
    total_entries: int = 0
    issues: list[QualityIssue] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "source_srt": self.source_srt,
            "translated_srt": self.translated_srt,
            "total_entries": self.total_entries,
            "issues": [
                {
                    "index": i.index,
                    "type": i.type,
                    "severity": i.severity,
                    "text": i.text,
                    "snippet": i.snippet,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
            "summary": self.summary,
        }


# ── 检测规则常量 ─────────────────────────────────────────────────────────

# 单条字幕最大字符数（超过此值标记为过长）
MAX_CHARS_PER_ENTRY = 80

# 单条字幕最短持续时间（秒），低于此值可能阅读困难
MIN_DURATION_SECONDS = 0.5

# 连续重复字幕超过此次数视为异常
MAX_REPEAT_COUNT = 5

# LLM 废话关键词（正则）
LLM_BOILERPLATE_PATTERNS = [
    r"以下是翻译",
    r"以下是.*字幕",
    r"这是.*翻译",
    r"翻译如下",
    r"好的[，,]",
    r"当然[，,]",
    r"Here is the translation",
    r"Here are the subtitles",
    r"I've translated",
    r"Sure[!,\.]",
    r"Certainly[!,\.]",
    r"```",
]

# 常见未翻译语言的特征字符范围（Unicode 区块）
UNTRANSLATED_INDICATORS: dict[str, str] = {
    "ja": r"[぀-ゟ゠-ヿ]",     # 日文假名（当目标语言是中文时）
    "ko": r"[가-힯]",                    # 韩文
    "ar": r"[؀-ۿ]",                    # 阿拉伯文
    "th": r"[฀-๿]",                    # 泰文
    "ru": r"[Ѐ-ӿ]",                    # 西里尔字母
}


# ── SRT 解析（独立实现，不依赖 subtitle_translate） ──────────────────────

@dataclass
class SrtEntry:
    index: int
    start_time: float   # seconds
    end_time: float     # seconds
    time_line: str      # raw "00:00:01,000 --> 00:00:03,000"
    text: str


def parse_srt(path: Path) -> list[SrtEntry]:
    """解析 SRT 文件，返回带时间戳浮点数的条目列表。"""
    raw = read_utf8_text(path, user_input=True).strip()
    entries: list[SrtEntry] = []
    blocks = re.split(r"\n\s*\n", raw)

    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0])
        except ValueError:
            continue

        time_line = lines[1]
        start, end = _parse_timestamp(time_line)
        text = "\n".join(lines[2:])
        entries.append(SrtEntry(
            index=index,
            start_time=start,
            end_time=end,
            time_line=time_line,
            text=text,
        ))

    return entries


def _parse_timestamp(time_line: str) -> tuple[float, float]:
    """解析 "HH:MM:SS,mmm --> HH:MM:SS,mmm" 为秒数。"""
    match = re.match(
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
        time_line,
    )
    if not match:
        return (0.0, 0.0)
    parts = list(map(int, match.groups()))
    start = parts[0] * 3600 + parts[1] * 60 + parts[2] + parts[3] / 1000
    end = parts[4] * 3600 + parts[5] * 60 + parts[6] + parts[7] / 1000
    return (start, end)


# ── 质检核心函数 ─────────────────────────────────────────────────────────

def check_source_srt(
    srt_path: Path,
    quality_thresholds: dict | None = None,
    lang_json: dict | None = None,
) -> QualityReport:
    """对原始语言 SRT 进行格式检查。

    Args:
        srt_path: SRT 文件路径
        quality_thresholds: 来自 Language Profile 的质检阈值
        lang_json: .lang.json 中的语言检测结果
    """
    if quality_thresholds is None:
        quality_thresholds = {}
    raw_text = read_utf8_text(srt_path, user_input=True)
    entries = parse_srt(srt_path)
    report = QualityReport(
        source_srt=str(srt_path),
        total_entries=len(entries),
    )

    if not entries:
        report.issues.append(QualityIssue(
            index=0, type="empty_file", severity="error",
            text="SRT 文件为空或无有效字幕条目",
        ))
        report.status = "fail"
        _finalize_report(report)
        return report

    # 0. 原始文本级别空字幕检查（解析器会跳过无文本条目）
    _check_empty_raw(raw_text, report)

    # 1. 编号连续性检查
    _check_numbering(entries, report)

    # 2. 时间轴重叠检查
    _check_time_overlap(entries, report)

    # 3. 空字幕检查（解析后条目）
    _check_empty(entries, report)

    # 4. 字幕过长检查
    _check_too_long(entries, report)

    # 5. 时长过短检查
    _check_too_short(entries, report)

    # 6. 重复内容检查
    _check_duplicates(entries, report)

    # 7. 时间码格式检查
    _check_timestamp_format(entries, report)

    # 8. 语言置信度检查（来自 Language Profile）
    if lang_json:
        prob = lang_json.get("language_probability")
        forced = lang_json.get("forced_language")
        if prob is not None:
            warn_threshold = quality_thresholds.get("language_probability_warning", 0.85)
            err_threshold = quality_thresholds.get("language_probability_error", 0.60)
            if prob < err_threshold:
                report.issues.append(QualityIssue(
                    index=0, type="language_uncertain_error", severity="error",
                    text=f"语言识别置信度极低: {prob:.2f} (阈值: {err_threshold})",
                    suggestion="建议人工确认源语言，或切换正确的 Language Profile",
                ))
            elif prob < warn_threshold:
                report.issues.append(QualityIssue(
                    index=0, type="language_uncertain_warning", severity="warning",
                    text=f"语言识别置信度偏低: {prob:.2f} (阈值: {warn_threshold})",
                    suggestion="建议人工抽查确认语言正确性",
                ))
        # 源语言不匹配检查
        if forced and detected:
            detected_lang = lang_json.get("source_language", "")
            if detected_lang and detected_lang != forced:
                report.issues.append(QualityIssue(
                    index=0, type="source_language_mismatch", severity="warning",
                    text=f"检测到语言 '{detected_lang}' 与强制语言 '{forced}' 不一致",
                    suggestion="请确认源语言是否正确，或切换为 auto-detect",
                ))

    # 9. 中文 CPS 和字数检查（使用 profile 阈值）
    profile_max_cps = quality_thresholds.get("max_cps_zh", 8)
    profile_max_line = quality_thresholds.get("max_chars_per_line_zh", 18)
    profile_max_sub = quality_thresholds.get("max_chars_per_subtitle_zh", 36)
    _check_cps_and_length(entries, report, profile_max_cps, profile_max_line, profile_max_sub)

    _finalize_report(report)
    return report


def check_translation_quality(
    source_srt: Path,
    translated_srt: Path,
    target_language: str = "zh-CN",
    quality_thresholds: dict | None = None,
) -> QualityReport:
    """对翻译后字幕进行质量检查。"""
    if quality_thresholds is None:
        quality_thresholds = {}
    source_entries = parse_srt(source_srt)
    translated_entries = parse_srt(translated_srt)
    report = QualityReport(
        source_srt=str(source_srt),
        translated_srt=str(translated_srt),
        total_entries=len(translated_entries),
    )

    # 先做翻译后字幕自身的格式检查
    _check_numbering(translated_entries, report)
    _check_time_overlap(translated_entries, report)
    _check_empty(translated_entries, report)
    _check_too_long(translated_entries, report)
    _check_timestamp_format(translated_entries, report)

    # 条数一致性检查
    if len(source_entries) != len(translated_entries):
        report.issues.append(QualityIssue(
            index=0,
            type="count_mismatch",
            severity="error",
            text=f"原文与译文条数不一致：原文 {len(source_entries)} 条，译文 {len(translated_entries)} 条",
            suggestion="检查翻译过程是否丢失或增加了字幕条目",
        ))

    # LLM 废话检查
    _check_llm_boilerplate(translated_entries, report)

    # 未翻译内容检查（针对目标语言）
    _check_untranslated(translated_entries, report, target_language)

    # 中英文混乱检查（针对中文目标）
    if target_language in ("zh-CN", "zh-TW"):
        _check_mixed_language(translated_entries, report)
        _check_foreign_terms_in_chinese(translated_entries, report)

    # 语言置信度检查（也应用于译文）
    if quality_thresholds:
        warn_threshold = quality_thresholds.get("language_probability_warning", 0.85)
        err_threshold = quality_thresholds.get("language_probability_error", 0.60)
        # 尝试读取 .lang.json
        lang_json_path = source_srt.with_suffix(".lang.json")
        if lang_json_path.exists():
            try:
                lang_data = read_json(lang_json_path)
                prob = lang_data.get("language_probability")
                forced = lang_data.get("forced_language")
                if prob is not None:
                    if prob < err_threshold:
                        report.issues.append(QualityIssue(
                            index=0, type="language_uncertain_error", severity="error",
                            text=f"语言识别置信度极低: {prob:.2f} (阈值: {err_threshold})",
                            suggestion="建议人工确认源语言",
                        ))
                    elif prob < warn_threshold:
                        report.issues.append(QualityIssue(
                            index=0, type="language_uncertain_warning", severity="warning",
                            text=f"语言识别置信度偏低: {prob:.2f} (阈值: {warn_threshold})",
                            suggestion="建议人工抽查确认",
                        ))
                if forced and lang_data.get("source_language") and lang_data["source_language"] != forced:
                    report.issues.append(QualityIssue(
                        index=0, type="source_language_mismatch", severity="warning",
                        text=f"检测到语言 '{lang_data['source_language']}' 与强制语言 '{forced}' 不一致",
                    ))
            except (OSError, json.JSONDecodeError):
                pass

    # CPS 和字数检查（译文字幕）
    profile_max_cps = quality_thresholds.get("max_cps_zh", 8)
    profile_max_line = quality_thresholds.get("max_chars_per_line_zh", 18)
    profile_max_sub = quality_thresholds.get("max_chars_per_subtitle_zh", 36)
    _check_cps_and_length(translated_entries, report, profile_max_cps, profile_max_line, profile_max_sub)

    _finalize_report(report)
    return report


# ── 各项检查实现 ─────────────────────────────────────────────────────────

def _check_empty_raw(raw_text: str, report: QualityReport) -> None:
    """从原始 SRT 文本中检测被解析器跳过的空字幕条目。

    解析器会丢弃 len(lines) < 3 的块（即无文本内容的字幕），
    此函数在原始文本层面检测这些空字幕块。
    """
    blocks = re.split(r"\n\s*\n", raw_text.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines()]
        # 正好两行非空行 = 只有编号和时间码，没有文本
        non_empty = [ln for ln in lines if ln]
        if len(non_empty) == 2:
            try:
                index = int(non_empty[0])
            except ValueError:
                continue
            # 第二行看起来像时间码
            if "-->" in non_empty[1]:
                report.issues.append(QualityIssue(
                    index=index,
                    type="empty_subtitle",
                    severity="warning",
                    text="空字幕（无文本内容）",
                    snippet="(空)",
                    suggestion="删除空字幕或填充内容",
                ))

def _check_numbering(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查编号是否连续。"""
    for i, entry in enumerate(entries, start=1):
        if entry.index != i:
            report.issues.append(QualityIssue(
                index=entry.index,
                type="broken_numbering",
                severity="error",
                text=f"编号不连续：期望 {i}，实际 {entry.index}",
                snippet=entry.text[:80],
            ))


def _check_time_overlap(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查相邻字幕时间轴是否重叠。"""
    for i in range(len(entries) - 1):
        current = entries[i]
        next_entry = entries[i + 1]
        if current.end_time > next_entry.start_time:
            report.issues.append(QualityIssue(
                index=current.index,
                type="time_overlap",
                severity="warning",
                text=f"时间轴重叠：结束 {current.end_time:.3f}s > 下一条开始 {next_entry.start_time:.3f}s",
                snippet=current.text[:80],
                suggestion="调整时间轴使相邻字幕不重叠",
            ))


def _check_empty(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查是否有空字幕。"""
    for entry in entries:
        if not entry.text.strip():
            report.issues.append(QualityIssue(
                index=entry.index,
                type="empty_subtitle",
                severity="warning",
                text="空字幕（无文本内容）",
                snippet="(空)",
                suggestion="删除空字幕或填充内容",
            ))


def _check_too_long(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查字幕是否过长。"""
    for entry in entries:
        # 按行检查最长的行
        lines = entry.text.split("\n")
        max_line_len = max((len(ln) for ln in lines), default=0)
        total_len = len(entry.text)

        if max_line_len > MAX_CHARS_PER_ENTRY:
            report.issues.append(QualityIssue(
                index=entry.index,
                type="too_long",
                severity="warning",
                text=f"字幕过长：最长行 {max_line_len} 字符",
                snippet=entry.text[:100],
                suggestion=f"拆分为多条字幕，保持每行 ≤{MAX_CHARS_PER_ENTRY} 字符",
            ))
        elif total_len > MAX_CHARS_PER_ENTRY * 2:
            # 多行加起来也很长
            report.issues.append(QualityIssue(
                index=entry.index,
                type="too_long",
                severity="info",
                text=f"字幕偏长：总计 {total_len} 字符",
                snippet=entry.text[:100],
            ))


def _check_too_short(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查字幕显示时长是否过短。"""
    for entry in entries:
        duration = entry.end_time - entry.start_time
        if duration < MIN_DURATION_SECONDS and entry.text.strip():
            report.issues.append(QualityIssue(
                index=entry.index,
                type="too_short_duration",
                severity="info",
                text=f"显示时长过短：{duration:.2f}s",
                snippet=entry.text[:80],
                suggestion="适当延长该字幕的显示时间",
            ))


def _check_duplicates(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查是否有大量连续重复字幕。"""
    repeat_count = 1
    for i in range(1, len(entries)):
        if entries[i].text.strip() == entries[i - 1].text.strip() and entries[i].text.strip():
            repeat_count += 1
        else:
            if repeat_count >= MAX_REPEAT_COUNT:
                report.issues.append(QualityIssue(
                    index=entries[i - repeat_count].index,
                    type="duplicate_content",
                    severity="warning",
                    text=f"连续重复 {repeat_count} 次",
                    snippet=entries[i - 1].text[:80],
                    suggestion="检查是否为识别错误，考虑删除重复条目",
                ))
            repeat_count = 1

    # 检查末尾
    if repeat_count >= MAX_REPEAT_COUNT:
        report.issues.append(QualityIssue(
            index=entries[-repeat_count].index,
            type="duplicate_content",
            severity="warning",
            text=f"连续重复 {repeat_count} 次",
            snippet=entries[-1].text[:80],
        ))


def _check_timestamp_format(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查时间码格式是否有异常。"""
    pattern = re.compile(
        r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}$"
    )
    for entry in entries:
        if not pattern.match(entry.time_line):
            report.issues.append(QualityIssue(
                index=entry.index,
                type="broken_timestamp",
                severity="error",
                text=f"时间码格式异常：{entry.time_line}",
                suggestion="修正为标准 SRT 时间码格式 HH:MM:SS,mmm --> HH:MM:SS,mmm",
            ))
        if entry.start_time >= entry.end_time:
            report.issues.append(QualityIssue(
                index=entry.index,
                type="broken_timestamp",
                severity="error",
                text=f"开始时间 >= 结束时间：{entry.time_line}",
            ))


def _check_llm_boilerplate(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查翻译结果中是否混入了 LLM 废话。"""
    for entry in entries:
        for pattern in LLM_BOILERPLATE_PATTERNS:
            if re.search(pattern, entry.text, re.IGNORECASE):
                report.issues.append(QualityIssue(
                    index=entry.index,
                    type="llm_boilerplate",
                    severity="error",
                    text=f"翻译结果中包含 LLM 废话：匹配 '{pattern}'",
                    snippet=entry.text[:100],
                    suggestion="重新翻译该片段，调整提示词以避免模型输出元文本",
                ))
                break


def _check_untranslated(
    entries: list[SrtEntry],
    report: QualityReport,
    target_language: str,
) -> None:
    """检查是否有明显未翻译的内容。

    对于中文目标语言：检查是否仍包含大量日文假名、韩文、阿拉伯文等非中文内容。
    """
    # 如果目标语言是中文，检查是否残留其他语言文字
    if target_language in ("zh-CN", "zh-TW"):
        checks = {
            "ja": UNTRANSLATED_INDICATORS["ja"],
            "ko": UNTRANSLATED_INDICATORS["ko"],
            "ar": UNTRANSLATED_INDICATORS["ar"],
            "th": UNTRANSLATED_INDICATORS["th"],
            "ru": UNTRANSLATED_INDICATORS["ru"],
        }
        for entry in entries:
            for lang_code, pattern in checks.items():
                matches = re.findall(pattern, entry.text)
                if len(matches) >= 3:  # 3个以上非中文字符视为异常
                    report.issues.append(QualityIssue(
                        index=entry.index,
                        type="possibly_untranslated",
                        severity="warning",
                        text=f"可能未翻译（残留{lang_code}文字）：{''.join(matches[:10])}",
                        snippet=entry.text[:100],
                        suggestion=f"检查该条目是否已正确翻译为目标语言",
                    ))
                    break


def _check_cps_and_length(
    entries: list[SrtEntry],
    report: QualityReport,
    max_cps: int,
    max_line: int,
    max_sub: int,
) -> None:
    """使用 Language Profile 阈值检查 CPS 和字幕长度。"""
    for entry in entries:
        text = entry.text
        # 只检查包含中文的字幕
        cn_chars = len(re.findall(r"[一-鿿]", text))
        if cn_chars == 0:
            continue
        duration = entry.end_time - entry.start_time
        # CPS 检查
        if duration > 0:
            cps = cn_chars / duration
            if cps > max_cps:
                report.issues.append(QualityIssue(
                    index=entry.index,
                    type="zh_cps_too_high",
                    severity="warning",
                    text=f"中文信息密度过高: {cps:.1f} 字/秒, 超过上限 {max_cps} 字/秒",
                    snippet=text[:80],
                    suggestion="考虑拆分或精简字幕内容",
                ))
        # 单行长度
        lines = text.split("\n")
        for line in lines:
            line_cn = len(re.findall(r"[一-鿿]", line))
            if line_cn > max_line:
                report.issues.append(QualityIssue(
                    index=entry.index,
                    type="zh_line_too_long",
                    severity="warning",
                    text=f"中文单行过长: {line_cn} 字 (阈值: {max_line})",
                    snippet=line[:80],
                    suggestion="考虑将长行拆分为两行",
                ))
                break
        # 单条总字数
        if cn_chars > max_sub:
            report.issues.append(QualityIssue(
                index=entry.index,
                type="zh_subtitle_too_long",
                severity="warning",
                text=f"单条中文字幕总字数过多: {cn_chars} 字 (阈值: {max_sub})",
                snippet=text[:80],
                suggestion="考虑拆分此字幕为多条",
            ))


def _check_mixed_language(entries: list[SrtEntry], report: QualityReport) -> None:
    """检查中英文是否混乱（大量英文字母出现在中文翻译中）。

    合理的英文（专有名词、缩写）不应被误报，所以设置较高阈值。
    对于双语字幕（原文+译文），只检查译文行（第二行），避免原文被误报。
    """
    for entry in entries:
        text = entry.text

        # 检测是否为双语字幕：按行拆分，如果有换行且第二行有明显中文
        lines = text.split("\n")
        if len(lines) >= 2:
            first_line = lines[0]
            second_line = lines[1]
            first_cn = len(re.findall(r"[一-鿿]", first_line))
            second_cn = len(re.findall(r"[一-鿿]", second_line))
            # 如果第二行中文比第一行多，认为是双语字幕，只检查第二行（译文）
            if second_cn > first_cn:
                text = second_line

        # 统计英文字母和汉字的比例
        en_chars = len(re.findall(r"[a-zA-Z]", text))
        cn_chars = len(re.findall(r"[一-鿿]", text))
        total = en_chars + cn_chars

        # 如果英文占比超过 50% 且文本较长，可能有问题
        if total > 10 and en_chars > cn_chars and en_chars > 20:
            report.issues.append(QualityIssue(
                index=entry.index,
                type="mixed_language",
                severity="warning",
                text=f"中英文混乱：英文 {en_chars} 字符，中文 {cn_chars} 字符",
                snippet=text[:100],
                suggestion="检查该条目翻译是否完整",
            ))


def _check_foreign_terms_in_chinese(entries: list[SrtEntry], report: QualityReport) -> None:
    """Flag suspicious long Latin terms left in Chinese subtitles.

    Proper nouns can be valid in subtitles, so this is intentionally a warning
    and ignores short all-caps abbreviations like AI, G7, P5, and UNESCO.
    """
    allowed_terms = {
        "AI", "API", "GDP", "G7", "G20", "NATO", "ONU", "P5", "UN", "UNESCO",
    }
    term_pattern = re.compile(r"\b[A-Za-zÀ-ÖØ-öø-ÿ]{4,}(?:[-'][A-Za-zÀ-ÖØ-öø-ÿ]{2,})*\b")

    for entry in entries:
        text = _target_text_for_chinese(entry.text)
        if not re.search(r"[一-鿿]", text):
            continue

        suspicious: list[str] = []
        for match in term_pattern.findall(text):
            normalized = match.strip(".,;:!?，。；：！？()（）[]【】")
            if not normalized:
                continue
            if normalized.upper() in allowed_terms:
                continue
            if len(normalized) < 6 and "-" not in normalized and "'" not in normalized:
                continue
            suspicious.append(normalized)

        if suspicious:
            report.issues.append(QualityIssue(
                index=entry.index,
                type="foreign_term_in_chinese",
                severity="warning",
                text=f"中文字幕疑似残留外文专名：{', '.join(suspicious[:3])}",
                snippet=text[:100],
                suggestion="检查该专名是否应译为中文通用译名，或加入术语白名单",
            ))


def _target_text_for_chinese(text: str) -> str:
    """Return likely target-language lines from translated/bilingual subtitles."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) >= 2:
        target_lines = [line for line in lines if re.search(r"[一-鿿]", line)]
        if target_lines:
            return "\n".join(target_lines)
    return text


def _finalize_report(report: QualityReport) -> None:
    """汇总问题统计，确定最终状态。"""
    errors = sum(1 for i in report.issues if i.severity == "error")
    warnings = sum(1 for i in report.issues if i.severity == "warning")
    infos = sum(1 for i in report.issues if i.severity == "info")

    report.summary = {
        "total_issues": len(report.issues),
        "errors": errors,
        "warnings": warnings,
        "info": infos,
        "issue_types": {},
    }

    for issue in report.issues:
        report.summary["issue_types"][issue.type] = \
            report.summary["issue_types"].get(issue.type, 0) + 1

    if errors > 0:
        report.status = "fail"
    elif warnings > 0:
        report.status = "warning"
    else:
        report.status = "pass"


# ── 报告输出 ─────────────────────────────────────────────────────────────

def save_quality_report(report: QualityReport, output_path: Path) -> None:
    """保存质量报告为 JSON 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, report.to_dict())


def generate_review_srt(
    report: QualityReport,
    entries: list[SrtEntry],
    output_path: Path,
) -> None:
    """根据质量报告生成仅包含异常片段的 review_needed.srt。

    只输出被标记为 error 或 warning 的字幕条目，并在每条后附加问题说明。
    """
    problem_indices: dict[int, list[str]] = {}
    for issue in report.issues:
        if issue.index == 0:
            continue  # 跳过全局问题
        if issue.index not in problem_indices:
            problem_indices[issue.index] = []
        problem_indices[issue.index].append(f"[{issue.type}] {issue.text}")

    if not problem_indices:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    entry_map = {e.index: e for e in entries}

    with output_path.open("w", encoding="utf-8") as f:
        review_index = 1
        for idx in sorted(problem_indices):
            entry = entry_map.get(idx)
            if entry is None:
                continue
            problems = problem_indices[idx]
            f.write(f"{review_index}\n")
            f.write(f"{entry.time_line}\n")
            f.write(f"{entry.text}\n")
            for p in problems:
                f.write(f"# {p}\n")
            f.write("\n")
            review_index += 1


def print_report_summary(report: QualityReport) -> None:
    """在终端打印质量报告摘要。兼容 Windows GBK 控制台。"""
    status_icon = {"pass": "✓", "warning": "⚠", "fail": "✗"}.get(report.status, "?")
    severity_icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}

    def _safe_print(text: str) -> None:
        try:
            print(text)
        except UnicodeEncodeError:
            # 降级为 ASCII 近似字符
            safe = text.replace("✓", "[OK]").replace("⚠", "[!]").replace("✗", "[X]").replace("ℹ", "[i]")
            try:
                print(safe)
            except UnicodeEncodeError:
                pass  # 忽略无法打印的输出

    _safe_print(f"\n{'='*60}")
    _safe_print(f"  质量检查报告  {status_icon} {report.status.upper()}")
    _safe_print(f"{'='*60}")
    _safe_print(f"  总条目数: {report.total_entries}")
    _safe_print(f"  问题总数: {report.summary.get('total_issues', 0)}")
    _safe_print(f"  错误: {report.summary.get('errors', 0)}  "
          f"警告: {report.summary.get('warnings', 0)}  "
          f"提示: {report.summary.get('info', 0)}")

    issue_types = report.summary.get("issue_types", {})
    if issue_types:
        _safe_print(f"\n  问题分布:")
        for itype, count in sorted(issue_types.items(), key=lambda x: -x[1]):
            _safe_print(f"    - {itype}: {count}")

    if report.issues:
        _safe_print(f"\n  详细问题列表:")
        for issue in report.issues:
            icon = severity_icon.get(issue.severity, "?")
            idx_str = f"#{issue.index}" if issue.index > 0 else "全局"
            _safe_print(f"    {icon} {idx_str} [{issue.type}] {issue.text}")
            if issue.suggestion:
                _safe_print(f"      建议: {issue.suggestion}")

    _safe_print(f"{'='*60}\n")


# ── 主入口（独立使用） ───────────────────────────────────────────────────

def run_quality_check(
    source_srt: Path,
    translated_srt: Path | None = None,
    target_language: str = "zh-CN",
    output_dir: Path | None = None,
    quality_thresholds: dict | None = None,
) -> QualityReport:
    """运行完整的质量检查流程。

    Args:
        source_srt: 原始语言 SRT 文件路径
        translated_srt: 翻译后 SRT 文件路径（可选）
        target_language: 翻译目标语言
        output_dir: 报告输出目录（默认与 source_srt 同目录）
        quality_thresholds: Language Profile 中的质检阈值

    Returns:
        QualityReport 对象
    """
    if output_dir is None:
        output_dir = source_srt.parent
    if quality_thresholds is None:
        quality_thresholds = {}

    # 尝试读取 .lang.json 获取语言检测信息
    lang_json: dict | None = None
    lang_json_path = source_srt.with_suffix(".lang.json")
    if lang_json_path.exists():
        try:
            lang_json = read_json(lang_json_path)
        except (OSError, json.JSONDecodeError):
            pass

    # 原文格式检查
    print(f"检查原文 SRT: {source_srt}")
    report = check_source_srt(source_srt, quality_thresholds, lang_json)

    # 译文质量检查
    if translated_srt and translated_srt.exists():
        print(f"检查译文 SRT: {translated_srt}")
        report = check_translation_quality(source_srt, translated_srt, target_language, quality_thresholds)

    # 保存报告
    stem = source_srt.stem
    report_path = output_dir / f"{stem}.quality_report.json"
    save_quality_report(report, report_path)
    print(f"质量报告已保存: {report_path}")

    # 生成异常片段字幕
    all_entries = parse_srt(translated_srt if translated_srt and translated_srt.exists() else source_srt)
    review_path = output_dir / f"{stem}.review_needed.srt"
    generate_review_srt(report, all_entries, review_path)
    if report.issues:
        print(f"复核字幕已生成: {review_path}")

    print_report_summary(report)
    return report


# ── CLI ─────────────────────────────────────────────────────────────────

def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="字幕质量检查工具 — 自动检测 SRT 格式和翻译质量问题"
    )
    parser.add_argument("source", nargs="?", help="原始语言 SRT 文件")
    parser.add_argument("--translated", default=None, help="翻译后 SRT 文件（可选）")
    parser.add_argument("--target-language", default="zh-CN", help="目标语言代码")
    parser.add_argument("--output-dir", default=None, help="报告输出目录")
    parser.add_argument("--self-test", action="store_true", help="运行自测")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    if not args.source:
        parser.error("source argument is required (unless --self-test)")
        return 1

    source = Path(args.source)
    if not source.exists():
        print(f"错误: 文件不存在 — {source}")
        return 1

    translated = Path(args.translated) if args.translated else None
    output_dir = Path(args.output_dir) if args.output_dir else None

    run_quality_check(
        source_srt=source,
        translated_srt=translated,
        target_language=args.target_language,
        output_dir=output_dir,
    )
    return 0


def _self_test() -> int:
    """自测：用已知有问题的 SRT 数据验证检测规则。"""
    errors: list[str] = []
    temp_dir = PROJECT_ROOT / ".tmp" / "quality_checker_self_test"
    if temp_dir.exists():
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 构造包含多种问题的测试 SRT
        test_srt = temp_dir / "test.srt"
        test_content = """\
1
00:00:01,000 --> 00:00:03,000
Hello world.

3
00:00:03,500 --> 00:00:06,000
Jumped numbering.

4
00:00:05,000 --> 00:00:07,000
Time overlap with previous.

5
00:00:07,000 --> 00:00:07,200
Too short duration.

6
00:00:07,500 --> 00:00:10,000
This is a very long subtitle line that exceeds the maximum recommended character count for comfortable screen reading.

7
00:00:10,500 --> 00:00:13,000

8
00:00:13,500 --> 00:00:16,000
Repeat content.

9
00:00:16,500 --> 00:00:19,000
Repeat content.

10
00:00:19,500 --> 00:00:22,000
Repeat content.

11
00:00:22,500 --> 00:00:25,000
Repeat content.

12
00:00:25,500 --> 00:00:28,000
Repeat content.
"""
        test_srt.write_text(test_content, encoding="utf-8")
        report = check_source_srt(test_srt)

        # 验证检测到的问题
        issue_types = {i.type for i in report.issues}

        if "broken_numbering" not in issue_types:
            errors.append("未检测到编号跳跃问题")
        if "time_overlap" not in issue_types:
            errors.append("未检测到时间轴重叠问题")
        if "too_long" not in issue_types:
            errors.append("未检测到字幕过长问题")
        if "too_short_duration" not in issue_types:
            errors.append("未检测到时长短问题")
        if "empty_subtitle" not in issue_types:
            errors.append("未检测到空字幕问题")
        if "duplicate_content" not in issue_types:
            errors.append("未检测到重复字幕问题")

        if report.status != "fail":
            errors.append(f"预期状态为 fail，实际为 {report.status}")

        # 测试 LLM 废话检测
        boilerplate_srt = temp_dir / "boilerplate.srt"
        bp_content = """\
1
00:00:01,000 --> 00:00:03,000
以下是翻译结果，请查收。

2
00:00:03,500 --> 00:00:06,000
这是一条正常的翻译。
"""
        boilerplate_srt.write_text(bp_content, encoding="utf-8")
        bp_report = check_source_srt(boilerplate_srt)
        # 需要重新做 LLM 废话检测
        bp_entries = parse_srt(boilerplate_srt)
        bp_report2 = QualityReport(source_srt=str(boilerplate_srt), total_entries=len(bp_entries))
        _check_llm_boilerplate(bp_entries, bp_report2)
        _finalize_report(bp_report2)

        bp_types = {i.type for i in bp_report2.issues}
        if "llm_boilerplate" not in bp_types:
            errors.append("未检测到 LLM 废话")

        # 测试未翻译内容检测
        untranslated_srt = temp_dir / "untranslated.srt"
        ut_content = """\
1
00:00:01,000 --> 00:00:03,000
これは日本語のテキストです。

2
00:00:03,500 --> 00:00:06,000
这是正常的中文翻译。
"""
        untranslated_srt.write_text(ut_content, encoding="utf-8")
        ut_report = check_translation_quality(
            untranslated_srt, untranslated_srt, target_language="zh-CN"
        )
        ut_types = {i.type for i in ut_report.issues}
        if "possibly_untranslated" not in ut_types:
            errors.append("未检测到未翻译的日文内容")

        # 测试中文字幕外文专名残留检测
        foreign_term_srt = temp_dir / "foreign_term.srt"
        ft_content = """\
1
00:00:01,000 --> 00:00:03,000
d'avoir les trésors du Sainte-Saint-Doué
拥有 Sainte-Saint-Doué 的珍宝

2
00:00:03,500 --> 00:00:06,000
UNESCO
在联合国教科文组织列入名录之前
"""
        foreign_term_srt.write_text(ft_content, encoding="utf-8")
        ft_report = check_translation_quality(
            foreign_term_srt, foreign_term_srt, target_language="zh-CN"
        )
        ft_types = {i.type for i in ft_report.issues}
        if "foreign_term_in_chinese" not in ft_types:
            errors.append("未检测到中文字幕外文专名残留")

        # 测试 JSON 报告输出
        report_path = temp_dir / "quality_report.json"
        save_quality_report(report, report_path)
        if not report_path.exists():
            errors.append("质量报告 JSON 未成功保存")

        loaded = read_json(report_path)
        if loaded.get("status") != "fail":
            errors.append(f"JSON 报告状态不正确: {loaded.get('status')}")

    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    print("quality_checker self-test: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
