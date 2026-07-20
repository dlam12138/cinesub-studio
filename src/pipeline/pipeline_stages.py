from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from encoding_utils import run_text
from ffmpeg_locator import find_ffmpeg
from asr_runtime import AsrDecodeOptions


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    input_path: Path
    work_dir: Path
    output_dir: Path


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: str
    outputs: tuple[Path, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    reused: bool = False
    duration_seconds: float = 0.0


class StageError(RuntimeError):
    def __init__(self, stage: str, message: str, *, returncode: int | None = None):
        super().__init__(message)
        self.stage = stage
        self.returncode = returncode


def _valid(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _terminology_consistency(
    source_srt: Path, translated_srt: Path, glossary: object
) -> dict[str, Any]:
    if not isinstance(glossary, list) or not glossary:
        return {"status": "not_configured", "checked_terms": 0, "missing_terms": []}
    source_text = source_srt.read_text(encoding="utf-8-sig").casefold()
    translated_text = translated_srt.read_text(encoding="utf-8-sig").casefold()
    checked = 0
    matched = 0
    missing: list[dict[str, Any]] = []
    for row in glossary:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "").strip()
        target = str(row.get("target") or "").strip()
        if not source or not target:
            continue
        source_count = source_text.count(source.casefold())
        if not source_count:
            continue
        checked += 1
        target_count = translated_text.count(target.casefold())
        if target_count >= source_count:
            matched += 1
        else:
            missing.append({
                "source": source,
                "expected_target": target,
                "source_occurrences": source_count,
                "target_occurrences": target_count,
            })
    return {
        "status": "pass" if not missing else "warning",
        "checked_terms": checked,
        "matched_terms": matched,
        "consistency_rate": round(matched / checked, 6) if checked else 1.0,
        "missing_terms": missing,
    }


def extract_audio_stage(context: TaskContext, *, project_root: Path, ffmpeg_path: str | None = None) -> StageResult:
    started = time.perf_counter()
    output = context.work_dir / f"{context.input_path.stem}.16k.wav"
    if _valid(output):
        return StageResult("extracting_audio", "completed", (output,), reused=True)
    ffmpeg = ffmpeg_path or find_ffmpeg(project_root)
    if not ffmpeg:
        raise StageError(
            "extracting_audio",
            "ffmpeg was not found. Put ffmpeg.exe in tools/ffmpeg/bin/, run "
            "src/tools/download_ffmpeg.py, or set CINESUB_FFMPEG.",
        )
    result = run_text(
        [
            ffmpeg, "-y", "-i", str(context.input_path), "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", str(output),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise StageError(
            "extracting_audio",
            f"ffmpeg audio extraction failed: {result.stderr[:500]}",
            returncode=result.returncode,
        )
    if not _valid(output):
        raise StageError("extracting_audio", "Audio extraction failed: output file is empty.")
    return StageResult(
        "extracting_audio", "completed", (output,), duration_seconds=time.perf_counter() - started
    )


def transcribe_stage(
    context: TaskContext, *, audio_path: Path, srt_path: Path, config: Any,
    reuse_existing: bool = True,
) -> StageResult:
    started = time.perf_counter()
    if reuse_existing and _valid(srt_path):
        return StageResult("transcribing", "completed", (srt_path,), reused=True)
    from transcribe import transcribe_to_srt

    profile = config.lang_profile_config or {}
    profile_asr = profile.get("asr", {}) if isinstance(profile.get("asr"), dict) else {}
    baseline_options = AsrDecodeOptions(
        condition_on_previous_text=profile_asr.get("condition_on_previous_text", True) is not False
    )
    language_info = transcribe_to_srt(
        audio_path=audio_path,
        srt_path=srt_path,
        model_name=config.model,
        model_dir=config.model_dir,
        device=config.device,
        compute_type=config.compute_type,
        asr_mode=getattr(config, "asr_mode", "auto"),
        language=config.language,
        beam_size=config.beam_size,
        vad_filter=config.vad_filter,
        local_files_only=config.local_files_only,
        language_profile_id=profile.get("profile_id", config.language_profile_id),
        language_profile_name=profile.get("profile_name", config.language_profile_name),
        condition_on_previous_text=baseline_options.condition_on_previous_text,
        decode_options=baseline_options,
    )
    if not _valid(srt_path):
        raise StageError("transcribing", "Transcription completed without a non-empty SRT output.")
    return StageResult(
        "transcribing", "completed", (srt_path,),
        {"language_detection": language_info},
        duration_seconds=time.perf_counter() - started,
    )


def translate_stage(
    context: TaskContext, *, source_srt: Path, output_path: Path, config: Any, effective_prompt: str,
) -> StageResult:
    started = time.perf_counter()
    from subtitle_translate import translate_srt
    profile = getattr(config, "lang_profile_config", {}) or {}
    quality_thresholds = profile.get("quality_thresholds", {}) or {}

    summary = translate_srt(
        input_path=source_srt,
        output_path=output_path,
        api_provider=config.api_provider,
        api_base=config.api_base,
        api_key=config.api_key,
        llm_model=config.llm_model,
        translation_quality_model=getattr(config, "translation_quality_model", ""),
        target_language=config.target_language,
        batch_size=config.translation_batch_size,
        temperature=config.translation_temperature,
        translation_mode=config.translation_mode,
        system_prompt=effective_prompt,
        context_window=config.context_window,
        reliability_mode=getattr(config, "translation_reliability_mode", "off"),
        max_extra_requests=getattr(config, "translation_max_extra_requests", 12),
        translation_strategy_mode=getattr(config, "translation_strategy_mode", "standard"),
        scene_gap_seconds=getattr(config, "translation_scene_gap_seconds", 30.0),
        max_cps_zh=float(quality_thresholds.get("max_cps_zh", 8)),
        max_chars_per_subtitle_zh=int(
            quality_thresholds.get("max_chars_per_subtitle_zh", 36)
        ),
        profile_glossary=profile.get("glossary", []),
    )
    if not _valid(output_path):
        raise StageError("translating", "Translation completed without a non-empty SRT output.")
    safe_summary = (
        summary.safe_summary()
        if hasattr(summary, "safe_summary")
        else {"mode": "off", "strategy_mode": "standard"}
    )
    translation_report = output_path.with_suffix(".translation_report.json")
    from encoding_utils import write_json
    write_json(
        translation_report,
        {
            "schema_version": 1,
            "source_srt": str(source_srt),
            "translated_srt": str(output_path),
            "translation": safe_summary,
            "terminology_consistency": _terminology_consistency(
                source_srt, output_path, profile.get("glossary", [])
            ),
            "degraded": bool(safe_summary.get("quality_model_fallback")),
        },
    )
    semantic_review_report = ""
    strategy_mode = str(getattr(config, "translation_strategy_mode", "standard"))
    if strategy_mode == "semantic_review":
        candidate = output_path.with_name(
            f"{output_path.stem}.semantic_review_report.json"
        )
        semantic_review_report = str(candidate) if _valid(candidate) else ""
    elif strategy_mode == "wenyi_review":
        candidate = output_path.with_name(
            f"{output_path.stem}.wenyi_review_report.json"
        )
        semantic_review_report = str(candidate) if _valid(candidate) else ""
    elif strategy_mode == "semantic_wenyi_review":
        candidate = output_path.with_name(
            f"{output_path.stem}.semantic_wenyi_review_report.json"
        )
        semantic_review_report = str(candidate) if _valid(candidate) else ""
    outputs = [output_path, translation_report]
    if semantic_review_report:
        outputs.append(Path(semantic_review_report))
    return StageResult(
        "translating", "completed", tuple(outputs),
        {
            "translation_reliability": safe_summary,
            "translation_report": str(translation_report),
            "semantic_review_report": semantic_review_report,
        },
        duration_seconds=time.perf_counter() - started,
    )


def quality_check_stage(
    context: TaskContext, *, source_srt: Path, translated_srt: Path, report_path: Path, config: Any,
) -> StageResult:
    started = time.perf_counter()
    from quality_checker import run_quality_check

    profile = config.lang_profile_config or {}
    thresholds = profile.get("quality_thresholds", {}) or {}
    run_quality_check(
        source_srt=source_srt,
        translated_srt=translated_srt,
        target_language=config.target_language,
        output_dir=report_path.parent,
        quality_thresholds=thresholds,
    )
    if not _valid(report_path):
        raise StageError("quality_checking", "Quality check completed without a non-empty report.")
    return StageResult(
        "quality_checking", "completed", (report_path,), duration_seconds=time.perf_counter() - started
    )


def archive_stage(context: TaskContext, *, archive_dir: Path) -> StageResult:
    started = time.perf_counter()
    if not context.input_path.exists():
        return StageResult("archiving", "completed", reused=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / context.input_path.name
    if destination.exists():
        destination = archive_dir / (
            f"{context.input_path.stem}_{int(time.time())}{context.input_path.suffix}"
        )
    shutil.move(str(context.input_path), str(destination))
    if not _valid(destination):
        raise StageError("archiving", "Archive move completed without a valid destination file.")
    return StageResult(
        "archiving", "completed", (destination,), duration_seconds=time.perf_counter() - started
    )
