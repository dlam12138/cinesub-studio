#!/usr/bin/env python3
"""
subtitle_analyzer.py — 人工字幕翻译风格分析器

用途：
    分析从 OpenSubtitles 下载的人工翻译字幕，提取专业翻译风格特征，
    用于指导 LLM 翻译提示词优化。

分析维度：
    1. 节奏控制：CPS（每秒字符数）、字幕长度分布、换行策略
    2. 翻译策略：直译 vs 意译比例、省略策略、增译策略
    3. 语言特征：口语化程度、敬语/俗语使用、标点风格
    4. 文化适配：专有名词处理、双关/幽默翻译、文化注释
    5. 一致性：术语统一度、角色语气一致性

用法：
    python subtitle_analyzer.py data/reference_subtitles/french/
    python subtitle_analyzer.py data/reference_subtitles/ --recursive --output analysis_report.json

输出：
    JSON 风格报告，可直接用于 style_prompt_generator.py 生成基准提示词
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

# ── SRT 解析器（轻量独立，不依赖项目其他模块）──────────────────────────────


class SRTEntry:
    """单条字幕。"""
    def __init__(self, index: int, start: float, end: float, text: str) -> None:
        self.index = index
        self.start = start  # 秒
        self.end = end      # 秒
        self.text = text.strip()
        self.duration = end - start

    def __repr__(self) -> str:
        return f"SRTEntry({self.index}, {self.start:.2f}-{self.end:.2f}, {self.text[:30]!r})"


def _parse_time(t: str) -> float:
    """将 SRT 时间戳 'HH:MM:SS,mmm' 转为秒。"""
    t = t.strip().replace(".", ",")
    if "," not in t:
        t += ",000"
    h, m, s = t.split(":")
    s, ms = s.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _read_text_with_fallback(path: Path) -> str:
    """读取文件文本，自动检测编码（UTF-8 / GBK / latin-1）。"""
    raw = path.read_bytes()
    # 尝试 UTF-8
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # 尝试 UTF-8 with BOM
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # 尝试 GBK / GB2312 / CP936（中文 Windows 常见编码）
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        pass
    # 最后尝试 latin-1（不会丢字节，但可能显示乱码）
    return raw.decode("latin-1")


def parse_srt(path: Path) -> list[SRTEntry]:
    """解析 SRT 文件，返回 SRTEntry 列表。自动检测编码。"""
    text = _read_text_with_fallback(path)
    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\n+", text)
    entries: list[SRTEntry] = []
    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        # 第一行是序号，后面是时间轴，再后面是文本
        if not lines[0].isdigit():
            continue
        if len(lines) < 2:
            continue
        time_line = lines[1]
        m = re.match(r"(.+?)\s+-->\s+(.+)", time_line)
        if not m:
            continue
        start = _parse_time(m.group(1))
        end = _parse_time(m.group(2))
        text_lines = lines[2:]
        text = "\n".join(text_lines)
        entries.append(SRTEntry(int(lines[0]), start, end, text))
    return entries


# ── 语言检测辅助 ───────────────────────────────────────────────────────────


def _detect_language(text: str) -> str:
    """粗略检测语言。返回 'zh', 'ja', 'ko', 'latin', 'mixed'。"""
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ja_chars = len(re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", text))
    ko_chars = len(re.findall(r"[\uac00-\ud7af]", text))
    total = max(len(text), 1)
    if zh_chars / total > 0.3:
        return "zh"
    if ja_chars / total > 0.3:
        return "ja"
    if ko_chars / total > 0.3:
        return "ko"
    if zh_chars + ja_chars + ko_chars > 0:
        return "mixed"
    return "latin"


# ── 分析器核心 ───────────────────────────────────────────────────────────


class SubtitleAnalyzer:
    """分析单语或双语字幕的风格特征。"""

    def __init__(self, entries: list[SRTEntry]) -> None:
        self.entries = entries
        self.lang = self._detect_dominant_lang()

    def _detect_dominant_lang(self) -> str:
        """检测字幕主要语言。"""
        all_text = " ".join(e.text for e in self.entries)
        return _detect_language(all_text)

    def analyze(self) -> dict[str, Any]:
        """返回完整风格分析报告。"""
        return {
            "basic_stats": self._basic_stats(),
            "rhythm": self._rhythm_analysis(),
            "translation_style": self._translation_style(),
            "language_features": self._language_features(),
            "text_patterns": self._text_patterns(),
        }

    def _basic_stats(self) -> dict[str, Any]:
        """基础统计：条数、总时长、平均长度等。"""
        texts = [e.text for e in self.entries]
        lengths = [len(t) for t in texts]
        durations = [e.duration for e in self.entries]
        return {
            "entry_count": len(self.entries),
            "total_duration_sec": round(sum(durations), 2),
            "avg_chars_per_entry": round(sum(lengths) / max(len(lengths), 1), 1),
            "max_chars_per_entry": max(lengths) if lengths else 0,
            "min_chars_per_entry": min(lengths) if lengths else 0,
            "avg_duration_sec": round(sum(durations) / max(len(durations), 1), 2),
            "detected_lang": self.lang,
        }

    def _rhythm_analysis(self) -> dict[str, Any]:
        """节奏分析：CPS、长度分布、换行策略。"""
        cps_list = []
        line_counts = []
        char_per_line = []
        for e in self.entries:
            if e.duration > 0:
                cps = len(e.text) / e.duration
                cps_list.append(cps)
            lines = e.text.split("\n")
            line_counts.append(len(lines))
            for line in lines:
                char_per_line.append(len(line))

        cps_list = cps_list or [0]
        line_counts = line_counts or [0]
        char_per_line = char_per_line or [0]

        return {
            "cps": {
                "mean": round(sum(cps_list) / len(cps_list), 2),
                "max": round(max(cps_list), 2),
                "p95": round(self._percentile(cps_list, 0.95), 2),
            },
            "lines_per_entry": {
                "mean": round(sum(line_counts) / len(line_counts), 2),
                "single_line_pct": round(sum(1 for c in line_counts if c == 1) / len(line_counts) * 100, 1),
                "two_line_pct": round(sum(1 for c in line_counts if c == 2) / len(line_counts) * 100, 1),
            },
            "chars_per_line": {
                "mean": round(sum(char_per_line) / len(char_per_line), 1),
                "max": max(char_per_line),
                "p95": round(self._percentile(char_per_line, 0.95), 1),
            },
        }

    def _translation_style(self) -> dict[str, Any]:
        """翻译风格：增减译、直译/意译迹象。"""
        all_text = " ".join(e.text for e in self.entries).lower()
        # 省略标记：常见省略模式
        omission_markers = ["…", "...", "—", "~", "..."]
        omission_count = sum(all_text.count(m) for m in omission_markers)
        # 注释标记：方括号注释
        annotation_count = len(re.findall(r"[\[\(].*?[\]\)]", all_text))
        # 保留原文：未翻译的专有名词（大写单词）
        untranslated = len(re.findall(r"\b[A-Z][A-Z\s]+\b", " ".join(e.text for e in self.entries)))

        return {
            "omission_markers_count": omission_count,
            "annotation_count": annotation_count,
            "untranslated_proper_nouns": untranslated,
            "style_tags": self._infer_style_tags(all_text),
        }

    def _infer_style_tags(self, text: str) -> list[str]:
        """根据文本特征推断风格标签。"""
        tags = []
        # 口语化检测
        casual_words = ["嗯", "啊", "呢", "吧", "啦", "嘛", "哈", "哦", "哎"]
        casual_count = sum(text.count(w) for w in casual_words)
        if casual_count > len(self.entries) * 0.05:
            tags.append("口语化")
        # 书面语检测
        formal_words = ["因此", "然而", "鉴于", " accordingly", " nevertheless", " furthermore"]
        formal_count = sum(text.count(w) for w in formal_words)
        if formal_count > len(self.entries) * 0.02:
            tags.append("书面化")
        # 简洁风格
        avg_len = sum(len(e.text) for e in self.entries) / max(len(self.entries), 1)
        if avg_len < 25:
            tags.append("极简风格")
        elif avg_len > 45:
            tags.append("详尽风格")
        else:
            tags.append("平衡风格")
        return tags

    def _language_features(self) -> dict[str, Any]:
        """语言特征：敬语、方言、标点风格。"""
        all_text = " ".join(e.text for e in self.entries)
        # 敬语检测（日语/韩语）
        honorifics_ja = len(re.findall(r"です|ます|ございます|さん|様", all_text))
        honorifics_ko = len(re.findall(r"습니다|ㅂ니다|요\b|님|께서", all_text))
        # 脏话/粗俗检测（通用）
        profanity = len(re.findall(r"\b(shit|fuck|damn|hell|bitch|bastard)\b", all_text, re.I))
        # 感叹号使用频率
        exclamation_count = all_text.count("!") + all_text.count("！")
        question_count = all_text.count("?") + all_text.count("？")

        return {
            "honorifics_ja": honorifics_ja,
            "honorifics_ko": honorifics_ko,
            "profanity_markers": profanity,
            "exclamation_per_entry": round(exclamation_count / max(len(self.entries), 1), 2),
            "question_per_entry": round(question_count / max(len(self.entries), 1), 2),
            "punctuation_style": self._punctuation_style(all_text),
        }

    def _punctuation_style(self, text: str) -> str:
        """判断标点风格。"""
        cn_punct = text.count("，") + text.count("。") + text.count("！") + text.count("？")
        en_punct = text.count(",") + text.count(".") + text.count("!") + text.count("?")
        if cn_punct > en_punct:
            return "中文标点"
        elif en_punct > cn_punct:
            return "英文标点"
        return "混合标点"

    def _text_patterns(self) -> dict[str, Any]:
        """文本模式：常见句式、重复短语。"""
        all_text = " ".join(e.text for e in self.entries)
        # 常见开头
        starts = Counter()
        for e in self.entries:
            first_line = e.text.split("\n")[0]
            if len(first_line) >= 3:
                starts[first_line[:3]] += 1
        common_starts = [item for item, count in starts.most_common(5) if count > 1]
        # 平均句长（按句号/逗号分）
        sentences = re.split(r"[.!?。！？]+", all_text)
        sentence_lengths = [len(s.strip()) for s in sentences if s.strip()]
        return {
            "avg_sentence_len": round(sum(sentence_lengths) / max(len(sentence_lengths), 1), 1),
            "common_starts": common_starts,
            "repetition_score": self._calc_repetition(all_text),
        }

    def _calc_repetition(self, text: str) -> float:
        """计算文本重复度（术语一致性指标）。"""
        words = re.findall(r"\b\w{3,}\b", text.lower())
        if not words:
            return 0.0
        unique = len(set(words))
        total = len(words)
        return round(unique / total, 3)

    @staticmethod
    def _percentile(data: list[float], p: float) -> float:
        """计算百分位数。"""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


# ── 双语对齐分析（核心：需要原文和译文）──────────────────────────────────────


class BilingualAnalyzer:
    """分析原文和译文的对照关系，提取翻译策略。"""

    def __init__(self, source_entries: list[SRTEntry], target_entries: list[SRTEntry]) -> None:
        self.source = source_entries
        self.target = target_entries
        self.pairs = self._align_by_time()

    def _align_by_time(self) -> list[tuple[SRTEntry, SRTEntry]]:
        """基于时间戳对齐原文和译文。"""
        pairs = []
        for s in self.source:
            # 找到时间重叠最多的目标字幕
            best_match = None
            best_overlap = 0.0
            for t in self.target:
                overlap_start = max(s.start, t.start)
                overlap_end = min(s.end, t.end)
                overlap = max(0, overlap_end - overlap_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match = t
            if best_match and best_overlap > 0.3:  # 至少 300ms 重叠
                pairs.append((s, best_match))
        return pairs

    def analyze(self) -> dict[str, Any]:
        """返回双语对照分析报告。"""
        if not self.pairs:
            return {"error": "无法对齐原文和译文（时间戳不匹配）"}

        length_ratios = []
        compression_cases = []
        expansion_cases = []
        omission_cases = []
        literal_cases = []

        for s, t in self.pairs:
            s_len = len(s.text)
            t_len = len(t.text)
            ratio = t_len / max(s_len, 1)
            length_ratios.append(ratio)

            # 压缩检测：译文明显短于原文
            if ratio < 0.6 and s_len > 15:
                compression_cases.append({
                    "source": s.text[:80],
                    "target": t.text[:80],
                    "ratio": round(ratio, 2),
                })
            # 扩增检测：译文明显长于原文
            elif ratio > 1.5 and s_len > 10:
                expansion_cases.append({
                    "source": s.text[:80],
                    "target": t.text[:80],
                    "ratio": round(ratio, 2),
                })
            # 省略检测：原文有内容但译文极短
            if s_len > 20 and t_len < 5:
                omission_cases.append({
                    "source": s.text[:80],
                    "target": t.text[:80],
                })
            # 直译检测：长度接近，词汇对应
            elif 0.9 <= ratio <= 1.1 and s_len > 10:
                literal_cases.append({
                    "source": s.text[:80],
                    "target": t.text[:80],
                })

        ratios = length_ratios or [1.0]
        return {
            "aligned_pairs": len(self.pairs),
            "length_ratio": {
                "mean": round(sum(ratios) / len(ratios), 2),
                "median": round(self._median(ratios), 2),
                "min": round(min(ratios), 2),
                "max": round(max(ratios), 2),
            },
            "translation_strategy": {
                "compression_ratio": round(len(compression_cases) / len(self.pairs), 3),
                "expansion_ratio": round(len(expansion_cases) / len(self.pairs), 3),
                "omission_ratio": round(len(omission_cases) / len(self.pairs), 3),
                "literal_ratio": round(len(literal_cases) / len(self.pairs), 3),
            },
            "examples": {
                "compression": compression_cases[:5],
                "expansion": expansion_cases[:5],
                "omission": omission_cases[:5],
                "literal": literal_cases[:5],
            },
        }

    @staticmethod
    def _median(data: list[float]) -> float:
        if not data:
            return 0.0
        s = sorted(data)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2


# ── 混合双语字幕分析（单文件：原文+译文在同一文件内）──────────────────


class MixedBilingualAnalyzer:
    """分析单文件内的双语字幕（原文和译文在同一文件中，如射手网双语字幕）。

    假设每个字幕条目有2行：第一行是原文，第二行是译文。
    检测逻辑：如果每个条目恰好2行，且第一行和第二行语言明显不同，则视为双语字幕。
    """

    def __init__(self, entries: list[SRTEntry]) -> None:
        self.entries = entries
        self.pairs = self._extract_pairs()

    def _extract_pairs(self) -> list[tuple[str, str]]:
        """从每个条目中提取原文-译文对。"""
        pairs = []
        for e in self.entries:
            lines = e.text.split("\n")
            if len(lines) >= 2:
                # 第一行作为原文，第二行作为译文
                # 后续行（如有）也加入译文
                source = lines[0].strip()
                target = "\n".join(lines[1:]).strip()
                if source and target:
                    pairs.append((source, target))
        return pairs

    def _detect_source_lang(self, text: str) -> str:
        """粗略检测原文语言。"""
        text = text.strip()
        if not text:
            return "unknown"
        # 检测是否为中文（译文）
        cn_ratio = len(re.findall(r"[\u4e00-\u9fff]", text)) / max(len(text), 1)
        # 检测是否为日语
        ja_ratio = len(re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", text)) / max(len(text), 1)
        # 检测是否为韩文
        ko_ratio = len(re.findall(r"[\uac00-\ud7af]", text)) / max(len(text), 1)

        if cn_ratio > 0.3:
            return "zh"
        if ja_ratio > 0.3:
            return "ja"
        if ko_ratio > 0.3:
            return "ko"
        # 默认认为是英文/拉丁语系原文
        return "en"

    def analyze(self) -> dict[str, Any]:
        """返回混合双语字幕的对照分析报告。"""
        if not self.pairs:
            return {"error": "无法提取双语对照（条目不足2行）", "pairs": 0}

        # 判断源语言（通过统计第一行语言）
        source_langs = [self._detect_source_lang(s) for s, t in self.pairs]
        source_lang = max(set(source_langs), key=source_langs.count) if source_langs else "en"
        target_lang = "zh" if source_lang == "en" else "en"

        length_ratios = []
        compression_cases = []
        expansion_cases = []
        omission_cases = []
        literal_cases = []

        for source_text, target_text in self.pairs:
            s_len = len(source_text)
            t_len = len(target_text)
            ratio = t_len / max(s_len, 1)
            length_ratios.append(ratio)

            # 压缩检测
            if ratio < 0.6 and s_len > 15:
                compression_cases.append({
                    "source": source_text[:80],
                    "target": target_text[:80],
                    "ratio": round(ratio, 2),
                })
            # 扩增检测
            elif ratio > 1.5 and s_len > 10:
                expansion_cases.append({
                    "source": source_text[:80],
                    "target": target_text[:80],
                    "ratio": round(ratio, 2),
                })
            # 省略检测
            if s_len > 20 and t_len < 5:
                omission_cases.append({
                    "source": source_text[:80],
                    "target": target_text[:80],
                })
            # 直译检测
            elif 0.9 <= ratio <= 1.1 and s_len > 10:
                literal_cases.append({
                    "source": source_text[:80],
                    "target": target_text[:80],
                })

        ratios = length_ratios or [1.0]

        # 计算译文风格指标
        target_texts = [t for s, t in self.pairs]
        target_entries = [SRTEntry(i, 0, 0, t) for i, t in enumerate(target_texts)]
        target_analyzer = SubtitleAnalyzer(target_entries)

        return {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "aligned_pairs": len(self.pairs),
            "length_ratio": {
                "mean": round(sum(ratios) / len(ratios), 2),
                "median": round(self._median(ratios), 2),
                "min": round(min(ratios), 2),
                "max": round(max(ratios), 2),
            },
            "translation_strategy": {
                "compression_ratio": round(len(compression_cases) / len(self.pairs), 3),
                "expansion_ratio": round(len(expansion_cases) / len(self.pairs), 3),
                "omission_ratio": round(len(omission_cases) / len(self.pairs), 3),
                "literal_ratio": round(len(literal_cases) / len(self.pairs), 3),
            },
            "examples": {
                "compression": compression_cases[:5],
                "expansion": expansion_cases[:5],
                "omission": omission_cases[:5],
                "literal": literal_cases[:5],
            },
            "target_style": target_analyzer.analyze(),
        }

    @staticmethod
    def _median(data: list[float]) -> float:
        if not data:
            return 0.0
        s = sorted(data)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2


def is_mixed_bilingual(entries: list[SRTEntry]) -> bool:
    """检测字幕是否为混合双语格式（单文件内原文+译文）。

    判断标准：
    1. 至少70%的条目有2行或更多行
    2. 第一行和第二行语言明显不同（一行拉丁语，一行中文/日文/韩文）
    """
    two_line_count = 0
    mixed_count = 0
    for e in entries:
        lines = e.text.split("\n")
        if len(lines) >= 2:
            two_line_count += 1
            first = lines[0]
            second = lines[1]
            first_cn = len(re.findall(r"[\u4e00-\u9fff]", first))
            second_cn = len(re.findall(r"[\u4e00-\u9fff]", second))
            first_latin = len(re.findall(r"[a-zA-Z]", first))
            second_latin = len(re.findall(r"[a-zA-Z]", second))
            # 判断第一行和第二行语言是否不同
            if (first_cn > 0 and second_latin > 0) or (first_latin > 0 and second_cn > 0):
                mixed_count += 1

    if len(entries) == 0:
        return False
    two_line_ratio = two_line_count / len(entries)
    mixed_ratio = mixed_count / len(entries) if two_line_count > 0 else 0
    return two_line_ratio >= 0.6 and mixed_ratio >= 0.3


# ── 批量处理 ─────────────────────────────────────────────────────────────────


def analyze_directory(dir_path: Path, recursive: bool = False) -> dict[str, Any]:
    """分析目录下的所有字幕文件。"""
    # 先解压 ZIP 文件中的 .srt
    _extract_zip_files(dir_path, recursive)

    srt_files = []
    if recursive:
        srt_files = list(dir_path.rglob("*.srt"))
    else:
        srt_files = list(dir_path.glob("*.srt"))

    if not srt_files:
        print(f"WARNING: 在 {dir_path} 中未找到 .srt 文件。")
        return {}

    # 尝试配对：同名文件 + .en.srt / .eng.srt / .source.srt
    bilingual_pairs: list[tuple[Path, Path]] = []
    single_files: list[Path] = []

    source_markers = [".eng.srt", ".en.srt", ".english.srt", ".source.srt"]
    target_markers = [".fre.srt", ".fr.srt", ".french.srt", ".spa.srt", ".es.srt",
                      ".jpn.srt", ".ja.srt", ".japanese.srt", ".ger.srt", ".de.srt",
                      ".ita.srt", ".it.srt", ".kor.srt", ".ko.srt", ".chi.srt",
                      ".zh.srt", ".rus.srt", ".ru.srt"]

    # 简单配对逻辑：找 source + target
    for f in srt_files:
        name_lower = f.name.lower()
        if any(name_lower.endswith(m) for m in source_markers):
            base = f.name
            for m in source_markers:
                base = base.replace(m, "")
            # 找对应的 target
            for t in srt_files:
                if t == f:
                    continue
                t_name = t.name.lower()
                if any(t_name.endswith(m) for m in target_markers):
                    t_base = t.name
                    for m in target_markers:
                        t_base = t_base.replace(m, "")
                    if t_base == base or t_base.replace(".", "") == base.replace(".", ""):
                        bilingual_pairs.append((f, t))
                        break
        else:
            single_files.append(f)

    # 去重
    used = set()
    for s, t in bilingual_pairs:
        used.add(s)
        used.add(t)
    single_files = [f for f in single_files if f not in used]

    results = {
        "source_dir": str(dir_path),
        "total_files": len(srt_files),
        "bilingual_pairs": [],
        "single_files": [],
    }

    # 分析双语对
    for s_path, t_path in bilingual_pairs:
        print(f"分析双语对: {s_path.name} <-> {t_path.name}")
        source_entries = parse_srt(s_path)
        target_entries = parse_srt(t_path)
        bi = BilingualAnalyzer(source_entries, target_entries)
        bi_result = bi.analyze()

        # 额外分析 target 语言特征
        target_analyzer = SubtitleAnalyzer(target_entries)
        bi_result["target_style"] = target_analyzer.analyze()

        results["bilingual_pairs"].append({
            "source": s_path.name,
            "target": t_path.name,
            "analysis": bi_result,
        })

    # 分析单语文件（也检查是否为混合双语字幕）
    for f in single_files:
        print(f"分析单语文件: {f.name}")
        entries = parse_srt(f)

        # 检测是否为混合双语字幕（射手网格式：原文+译文在同一文件）
        if is_mixed_bilingual(entries):
            print(f"  检测到混合双语字幕: {f.name}")
            bi = MixedBilingualAnalyzer(entries)
            bi_result = bi.analyze()
            results["bilingual_pairs"].append({
                "source": f.name,
                "target": f.name + " (mixed bilingual)",
                "analysis": bi_result,
            })
        else:
            analyzer = SubtitleAnalyzer(entries)
            results["single_files"].append({
                "file": f.name,
                "analysis": analyzer.analyze(),
            })

    return results


def _extract_zip_files(dir_path: Path, recursive: bool) -> int:
    """解压目录中的 .zip 文件，提取 .srt 字幕。

    返回提取的 .srt 文件数量。
    """
    import zipfile

    def _decode_zip_name(name: str) -> str:
        """尝试用多种编码解码 ZIP 文件名（中文 ZIP 常见 GBK 编码）。"""
        # zipfile 默认用 cp437 解码，如果原始编码是 gbk 就会乱码
        # 策略：先尝试 cp437->gbk，再尝试 cp437->utf-8
        for target_enc in ("gbk", "utf-8", "gb2312"):
            try:
                return name.encode("cp437").decode(target_enc)
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        return name

    zip_files = list(dir_path.rglob("*.zip")) if recursive else list(dir_path.glob("*.zip"))
    extracted_count = 0

    for zip_path in zip_files:
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # 尝试多种编码解析文件名
                srt_members = []
                for info in zf.infolist():
                    decoded_name = _decode_zip_name(info.filename)
                    if decoded_name.lower().endswith('.srt'):
                        srt_members.append((info, decoded_name))

                if srt_members:
                    print(f"[解压] {zip_path.name} -> 提取 {len(srt_members)} 个 .srt")
                    for info, decoded_name in srt_members:
                        dest_name = decoded_name.split('/')[-1]  # 取文件名，去掉路径
                        dest = dir_path / dest_name
                        if dest.exists():
                            print(f"  跳过（已存在）: {dest.name}")
                            continue
                        dest.write_bytes(zf.read(info.filename))
                        extracted_count += 1
                        print(f"  [OK] 提取: {dest.name}")
        except zipfile.BadZipFile:
            print(f"[警告] 无法解压（非标准 ZIP）: {zip_path.name}")
        except Exception as exc:
            print(f"[错误] 解压失败: {zip_path.name} - {exc}")

    return extracted_count


def main() -> int:
    parser = argparse.ArgumentParser(description="分析字幕翻译风格")
    parser.add_argument("path", help="字幕文件或目录路径")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归分析子目录")
    parser.add_argument("-o", "--output", type=str, default="", help="输出 JSON 报告路径")
    args = parser.parse_args()

    target_path = Path(args.path).resolve()
    if not target_path.exists():
        print(f"ERROR: 路径不存在: {target_path}")
        return 1

    print("=" * 60)
    print("CineSub Studio — 字幕风格分析器")
    print("=" * 60)

    if target_path.is_dir():
        results = analyze_directory(target_path, recursive=args.recursive)
    else:
        entries = parse_srt(target_path)
        # 检测是否为混合双语字幕
        if is_mixed_bilingual(entries):
            print(f"检测到混合双语字幕: {target_path.name}")
            bi = MixedBilingualAnalyzer(entries)
            results = {
                "file": target_path.name,
                "bilingual_analysis": bi.analyze(),
            }
        else:
            analyzer = SubtitleAnalyzer(entries)
            results = {
                "file": target_path.name,
                "analysis": analyzer.analyze(),
            }

    # 输出
    json_output = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_output, encoding="utf-8")
        print(f"\n报告已保存: {out_path}")
    else:
        print("\n" + "=" * 60)
        print("分析报告")
        print("=" * 60)
        print(json_output)

    print("\n提示：接下来运行 style_prompt_generator.py 生成基准提示词。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
