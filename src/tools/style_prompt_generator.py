#!/usr/bin/env python3
"""
style_prompt_generator.py — 从字幕分析报告生成基准翻译提示词

用途：
    读取 subtitle_analyzer.py 生成的 JSON 报告，提取人工翻译风格特征，
    生成可直接用于 LLM 翻译的基准提示词（System Prompt）。

生成的提示词维度：
    1. 节奏约束：CPS 上限、单行长度、换行策略
    2. 翻译策略：增减译比例、省略策略、意译倾向
    3. 语言风格：口语化程度、敬语使用、标点风格
    4. 文化适配：专有名词处理、俚语/幽默翻译原则
    5. 一致性：术语统一、角色语气

输出格式：
    - 纯文本提示词（可直接复制到 Language Profile 的 translation_style）
    - JSON 格式（可直接写入 language_profiles.local.json）

用法：
    python style_prompt_generator.py analysis_report.json --lang zh
    python style_prompt_generator.py analysis_report.json --output prompt.json

注意：
    这是独立工具，不修改项目现有配置。生成结果需手动审核后应用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent


# ── 提示词模板引擎 ──────────────────────────────────────────────────────────


class PromptGenerator:
    """根据分析报告生成翻译提示词。"""

    def __init__(self, report: dict[str, Any], target_lang: str = "zh") -> None:
        self.report = report
        self.target_lang = target_lang
        self.rules: list[str] = []
        self.constraints: list[str] = []
        self.examples: list[str] = []

    def generate(self) -> dict[str, str]:
        """生成完整提示词，返回 {system_prompt, style_summary, raw_rules}。"""
        # 检测报告结构类型
        all_pairs: list[dict] = []
        all_singles: list[dict] = []

        if "bilingual_analysis" in self.report:
            # 单文件混合双语分析
            all_pairs = [{"analysis": self.report["bilingual_analysis"]}]
        elif "analysis" in self.report:
            # 单文件单语分析
            all_singles = [self.report]
        else:
            # 目录分析（标准格式）
            all_pairs = self.report.get("bilingual_pairs", [])
            all_singles = self.report.get("single_files", [])

        # 提取关键指标（跨文件平均）
        metrics = self._aggregate_metrics(all_pairs, all_singles)

        # 构建规则
        self._build_rhythm_rules(metrics)
        self._build_translation_rules(metrics)
        self._build_language_rules(metrics)
        self._build_culture_rules(metrics)
        self._build_examples(all_pairs)

        system_prompt = self._assemble_prompt()
        style_summary = self._build_summary(metrics)

        return {
            "system_prompt": system_prompt,
            "style_summary": style_summary,
            "raw_rules": self.rules + self.constraints,
            "metrics": metrics,
        }

    def _aggregate_metrics(self, pairs: list[dict], singles: list[dict]) -> dict[str, Any]:
        """聚合多个文件的分析指标。"""
        cps_values = []
        cps_max_values = []
        chars_per_line = []
        length_ratios = []
        comp_ratios = []
        exp_ratios = []
        om_ratios = []
        lit_ratios = []
        style_tags: list[str] = []
        punct_styles: list[str] = []
        honorifics = 0
        profanity = 0

        # 从双语对提取
        for pair in pairs:
            target = pair.get("analysis", {}).get("target_style", {})
            rhythm = target.get("rhythm", {})
            cps = rhythm.get("cps", {})
            cps_values.append(cps.get("mean", 0))
            cps_max_values.append(cps.get("max", 0))
            cpl = rhythm.get("chars_per_line", {})
            chars_per_line.append(cpl.get("mean", 0))

            bi = pair.get("analysis", {})
            lr = bi.get("length_ratio", {})
            length_ratios.append(lr.get("mean", 1.0))
            ts = bi.get("translation_strategy", {})
            comp_ratios.append(ts.get("compression_ratio", 0))
            exp_ratios.append(ts.get("expansion_ratio", 0))
            om_ratios.append(ts.get("omission_ratio", 0))
            lit_ratios.append(ts.get("literal_ratio", 0))

            lf = target.get("language_features", {})
            honorifics += lf.get("honorifics_ja", 0) + lf.get("honorifics_ko", 0)
            profanity += lf.get("profanity_markers", 0)
            punct_styles.append(lf.get("punctuation_style", ""))
            style_tags.extend(target.get("translation_style", {}).get("style_tags", []))

        # 从单语文件提取
        for sf in singles:
            analysis = sf.get("analysis", {})
            rhythm = analysis.get("rhythm", {})
            cps = rhythm.get("cps", {})
            cps_values.append(cps.get("mean", 0))
            cps_max_values.append(cps.get("max", 0))
            cpl = rhythm.get("chars_per_line", {})
            chars_per_line.append(cpl.get("mean", 0))
            lf = analysis.get("language_features", {})
            honorifics += lf.get("honorifics_ja", 0) + lf.get("honorifics_ko", 0)
            profanity += lf.get("profanity_markers", 0)
            punct_styles.append(lf.get("punctuation_style", ""))
            style_tags.extend(analysis.get("translation_style", {}).get("style_tags", []))

        def avg(vals: list[float]) -> float:
            return sum(vals) / max(len(vals), 1)

        # 风格标签投票
        tag_counter = {}
        for t in style_tags:
            tag_counter[t] = tag_counter.get(t, 0) + 1
        dominant_tags = sorted(tag_counter.items(), key=lambda x: x[1], reverse=True)[:3]
        dominant_punct = max(set(punct_styles), key=punct_styles.count) if punct_styles else ""

        return {
            "cps_mean": round(avg(cps_values), 2),
            "cps_max": round(avg(cps_max_values), 2),
            "chars_per_line_mean": round(avg(chars_per_line), 1),
            "length_ratio_mean": round(avg(length_ratios), 2),
            "compression_ratio": round(avg(comp_ratios), 3),
            "expansion_ratio": round(avg(exp_ratios), 3),
            "omission_ratio": round(avg(om_ratios), 3),
            "literal_ratio": round(avg(lit_ratios), 3),
            "dominant_tags": [t[0] for t in dominant_tags],
            "punctuation_style": dominant_punct,
            "honorifics": honorifics,
            "profanity": profanity,
            "total_files": len(pairs) + len(singles),
        }

    def _build_rhythm_rules(self, m: dict[str, Any]) -> None:
        """构建节奏相关规则。"""
        cps_max = m["cps_max"]
        if cps_max > 0:
            # 建议 CPS 上限为观察到的 P95 值
            suggested_cps = min(round(cps_max * 0.85, 1), 15.0)
            self.constraints.append(
                f"字幕阅读节奏：每秒不超过 {suggested_cps} 个字符（CPS），"
                f"确保观众有足够时间阅读。"
            )

        cpl = m["chars_per_line_mean"]
        if cpl > 0:
            self.constraints.append(
                f"单行长度：每行不超过 {int(cpl * 1.2)} 个字符，"
                f"过长句子需要拆分或精简。"
            )

        self.rules.append("优先使用双行字幕（每行独立完整），避免三行及以上。")

    def _build_translation_rules(self, m: dict[str, Any]) -> None:
        """构建翻译策略规则。"""
        lr = m["length_ratio_mean"]
        comp = m["compression_ratio"]
        exp = m["expansion_ratio"]
        om = m["omission_ratio"]
        lit = m["literal_ratio"]

        # 根据数据判断翻译倾向
        if comp > 0.3 and lit < 0.3:
            self.rules.append(
                "翻译策略：以意译为主，允许适度压缩。"
                "当原文表达冗长时，提取核心含义并精简表达，"
                "不追求逐字对应。"
            )
        elif lit > 0.4 and comp < 0.2:
            self.rules.append(
                "翻译策略：以直译为主，保留原文结构和用词风格。"
                "在不影响理解的前提下，尽量贴近原文。"
            )
        else:
            self.rules.append(
                "翻译策略：直译与意译结合。"
                "描述性和叙述性内容以直译为主，"
                "对话和口语化内容以意译为主，允许适度压缩。"
            )

        if om > 0.1:
            self.rules.append(
                "省略策略：次要信息（如重复称谓、填充词、冗余修饰）可以省略，"
                "但关键信息（主语、谓语、核心情感）必须保留。"
            )
        else:
            self.rules.append(
                "完整翻译：避免省略关键信息，确保译文完整传达原文含义。"
            )

        if exp > 0.15:
            self.rules.append(
                "增译策略：当原文包含文化特定概念或双关时，"
                "可以适当增译以帮助目标观众理解，但保持简洁。"
            )

    def _build_language_rules(self, m: dict[str, Any]) -> None:
        """构建语言风格规则。"""
        tags = m["dominant_tags"]

        if "口语化" in tags:
            self.rules.append(
                "语言风格：口语化表达。"
                "使用日常对话用语，避免过于书面化或学术化的表达。"
                "适当使用语气词增强自然感。"
            )
        elif "书面化" in tags:
            self.rules.append(
                "语言风格：书面化表达。"
                "使用规范、正式的语言，避免过于随意的口语化表达。"
            )
        else:
            self.rules.append(
                "语言风格：根据场景调整。"
                "正式场合（法庭、演讲、新闻）使用书面语；"
                "日常对话使用口语化表达。"
            )

        if m["honorifics"] > 5:
            self.rules.append(
                "敬语体系：保留并使用目标语言的敬语系统。"
                "根据角色关系（上下级、亲疏）调整语体。"
            )

        if m["profanity"] > 3:
            self.rules.append(
                "粗俗语言处理：脏话和粗俗表达根据语境翻译。"
                "轻微粗口使用委婉表达；严重粗口保留力度但适当软化。"
            )
        else:
            self.rules.append(
                "粗俗语言处理：脏话使用目标语言中同等力度的表达，"
                "或根据影片分级适当弱化。"
            )

        punct = m["punctuation_style"]
        if self.target_lang == "zh" and punct == "英文标点":
            self.constraints.append(
                "标点规范：使用中文全角标点（，。！？），不使用英文半角标点。"
            )
        elif self.target_lang == "zh" and punct == "中文标点":
            self.rules.append("标点规范：使用中文全角标点，符合中文排版习惯。")

    def _build_culture_rules(self, m: dict[str, Any]) -> None:
        """构建文化适配规则。"""
        self.rules.append(
            "专有名词：人名、地名使用目标语言通用译法（如法语 'Jean' → '让'）。"
            "无通用译法的保留原文并加注释。"
        )
        self.rules.append(
            "文化典故：涉及目标文化不熟悉的典故时，采用意译或简短注释，"
            "不直接使用原文（除非上下文已解释）。"
        )
        self.rules.append(
            "双关与幽默：优先保留幽默效果，可适当改写以触发目标观众的笑点。"
            "若无法保留双关，使用同类型的幽默替换。"
        )
        self.rules.append(
            "角色语气一致性：每个角色保持稳定的语气风格。"
            "粗鲁的角色始终用粗鲁的语气，文雅的角色始终用文雅的语气。"
        )

    def _build_examples(self, pairs: list[dict]) -> None:
        """从分析报告中提取典型示例。"""
        examples = []
        for pair in pairs:
            bi = pair.get("analysis", {})
            ex = bi.get("examples", {})
            # 压缩示例
            for item in ex.get("compression", [])[:2]:
                examples.append(
                    f"压缩示例——原文：{item['source']}\n"
                    f"        译文：{item['target']}"
                )
            # 省略示例
            for item in ex.get("omission", [])[:1]:
                examples.append(
                    f"省略示例——原文：{item['source']}\n"
                    f"       译文：{item['target']}"
                )
        self.examples = examples[:5]  # 最多 5 个示例

    def _assemble_prompt(self) -> str:
        """组装最终提示词。"""
        lines = [
            "你是一位专业的电影字幕翻译师。请将以下电影字幕翻译成目标语言。",
            "",
            "=== 翻译原则 ===",
        ]
        for i, rule in enumerate(self.rules, 1):
            lines.append(f"{i}. {rule}")
        lines.append("")
        lines.append("=== 硬性约束 ===")
        for i, con in enumerate(self.constraints, 1):
            lines.append(f"{i}. {con}")
        lines.append("")
        lines.append("=== 输出格式 ===")
        lines.append("- 保持原文的时间轴和字幕编号")
        lines.append("- 每行字幕字数限制在约束范围内")
        lines.append("- 使用双行字幕，每行独立语义完整")
        lines.append("- 不添加任何解释性注释，只输出翻译后的文本")
        if self.examples:
            lines.append("")
            lines.append("=== 参考示例 ===")
            for ex in self.examples:
                lines.append(ex)
                lines.append("")
        return "\n".join(lines)

    def _build_summary(self, m: dict[str, Any]) -> str:
        """构建风格摘要（一句话描述）。"""
        tags = m["dominant_tags"]
        tag_str = "、".join(tags) if tags else "平衡风格"
        strategy = "意译为主" if m["compression_ratio"] > 0.3 else "直译为主"
        return f"{tag_str}，{strategy}，CPS 均值 {m['cps_mean']}，长度比 {m['length_ratio_mean']}"


# ── 主流程 ─────────────────────────────────────────────────────────────────


def generate_prompt(report_path: Path, target_lang: str) -> dict[str, str]:
    """读取报告并生成提示词。"""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    generator = PromptGenerator(report, target_lang)
    return generator.generate()


def main() -> int:
    parser = argparse.ArgumentParser(description="从字幕分析报告生成基准翻译提示词")
    parser.add_argument("report", help="subtitle_analyzer.py 生成的 JSON 报告路径")
    parser.add_argument("--lang", default="zh", help="目标语言（用于生成标点规范等）。默认: zh")
    parser.add_argument("--output", type=str, default="", help="输出 JSON 文件路径")
    parser.add_argument("--profile-id", type=str, default="", help="建议绑定的 Language Profile ID")
    args = parser.parse_args()

    report_path = Path(args.report).resolve()
    if not report_path.exists():
        print(f"ERROR: 报告文件不存在: {report_path}")
        return 1

    print("=" * 60)
    print("CineSub Studio — 基准提示词生成器")
    print("=" * 60)
    print(f"报告文件: {report_path}")
    print(f"目标语言: {args.lang}")
    print("-" * 60)

    result = generate_prompt(report_path, args.lang)

    print("\n【风格摘要】")
    print(result["style_summary"])
    print("\n【生成规则】")
    for i, rule in enumerate(result["raw_rules"], 1):
        print(f"  {i}. {rule}")
    print("\n" + "=" * 60)
    print("【完整提示词】")
    print("=" * 60)
    print(result["system_prompt"])
    print("=" * 60)

    # 输出为 JSON
    output_data = {
        "profile_id": args.profile_id or "custom",
        "target_language": args.lang,
        "style_summary": result["style_summary"],
        "system_prompt": result["system_prompt"],
        "raw_rules": result["raw_rules"],
        "metrics": result["metrics"],
        "note": "生成自人工字幕分析，建议手动审核后应用",
    }

    json_output = json.dumps(output_data, ensure_ascii=False, indent=2)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_output, encoding="utf-8")
        print(f"\n提示词已保存: {out_path}")
    else:
        print("\n提示：使用 --output prompt.json 保存为文件，")
        print("      然后手动审核后更新到 Language Profile 的 translation_style 中。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
