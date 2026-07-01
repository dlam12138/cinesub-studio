from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineOutputPlan:
    output_root: Path
    source_dir: Path
    translated_dir: Path
    bilingual_dir: Path
    reports_dir: Path
    source_srt: Path
    translated_srt: Path
    bilingual_srt: Path
    translation_output: Path
    quality_report: Path
    review_needed: Path


def plan_pipeline_outputs(
    output_root: Path,
    stem: str,
    model: str,
    target_language: str,
    translation_mode: str,
) -> PipelineOutputPlan:
    root = Path(output_root)
    source_dir = root / "source"
    translated_dir = root / "zh"
    bilingual_dir = root / "bilingual"
    reports_dir = root / "reports"

    mode_tag = "translated" if translation_mode == "translated" else "bilingual"
    source_srt = source_dir / f"{stem}.{model}.srt"
    translated_srt = translated_dir / f"{stem}.{model}.translated.{target_language}.srt"
    bilingual_srt = bilingual_dir / f"{stem}.{model}.bilingual.{target_language}.srt"
    translation_output = translated_srt if mode_tag == "translated" else bilingual_srt
    quality_report = reports_dir / f"{stem}.{model}.quality_report.json"
    review_needed = reports_dir / f"{stem}.{model}.review_needed.srt"

    return PipelineOutputPlan(
        output_root=root,
        source_dir=source_dir,
        translated_dir=translated_dir,
        bilingual_dir=bilingual_dir,
        reports_dir=reports_dir,
        source_srt=source_srt,
        translated_srt=translated_srt,
        bilingual_srt=bilingual_srt,
        translation_output=translation_output,
        quality_report=quality_report,
        review_needed=review_needed,
    )


def pipeline_output_dirs(output_root: Path) -> list[Path]:
    plan = plan_pipeline_outputs(
        output_root=output_root,
        stem="",
        model="",
        target_language="",
        translation_mode="bilingual",
    )
    return [plan.source_dir, plan.translated_dir, plan.bilingual_dir, plan.reports_dir]
