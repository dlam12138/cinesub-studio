from __future__ import annotations

import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from encoding_utils import run_text
from ffmpeg_locator import find_ffmpeg
from asr_strategy import (
    AsrCandidateReport,
    AsrDecodeOptions,
    TranscriptionArtifact,
    duplicate_cue_rate,
    get_candidate,
    merge_retry_artifact,
    retry_windows,
    selective_merge_retry_artifact,
    safe_file_hash,
    validate_artifact,
    write_artifact_srt,
    write_candidate_report,
)


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
    mode = str(getattr(config, "asr_experiment_mode", "off") or "off")
    candidate_id = str(getattr(config, "asr_candidate_id", "") or "")
    candidate_definition = (
        get_candidate(candidate_id, mode, config.model)
        if mode != "off" and candidate_id and candidate_id != "mixed-route-v1" else None
    )
    shared_session = None
    if candidate_definition and candidate_definition.strategy == "local_retry_selective":
        from transcribe import create_asr_session
        shared_session = create_asr_session(
            model_name=config.model, model_dir=config.model_dir, device=config.device,
            compute_type=config.compute_type, local_files_only=config.local_files_only,
        )
    baseline_artifacts: list[TranscriptionArtifact] = []
    language_info = transcribe_to_srt(
        audio_path=audio_path,
        srt_path=srt_path,
        model_name=config.model,
        model_dir=config.model_dir,
        device=config.device,
        compute_type=config.compute_type,
        language=config.language,
        beam_size=config.beam_size,
        vad_filter=config.vad_filter,
        local_files_only=config.local_files_only,
        language_profile_id=profile.get("profile_id", config.language_profile_id),
        language_profile_name=profile.get("profile_name", config.language_profile_name),
        condition_on_previous_text=baseline_options.condition_on_previous_text,
        decode_options=baseline_options,
        artifact_out=baseline_artifacts,
        session=shared_session,
    )
    if not _valid(srt_path):
        raise StageError("transcribing", "Transcription completed without a non-empty SRT output.")
    report_path: Path | None = None
    if mode != "off" and candidate_id and candidate_id != "mixed-route-v1":
        baseline_sha256 = safe_file_hash(srt_path)
        candidate = candidate_definition or get_candidate(candidate_id, mode, config.model)
        experiment_dir = context.work_dir / "asr-experiments" / context.input_path.stem
        experiment_dir.mkdir(parents=True, exist_ok=True)
        candidate_srt = experiment_dir / f"{candidate.candidate_id}.srt"
        candidate_artifacts: list[TranscriptionArtifact] = []
        fallback_reason = ""
        selections = ()
        try:
            decode_options = candidate.decode_options
            if candidate.strategy in {"local_retry", "local_retry_selective"} and baseline_artifacts:
                windows = retry_windows(baseline_artifacts[0])
                if not windows and candidate.strategy == "local_retry":
                    raise ValueError("baseline has no suspicious windows")
            if candidate.strategy == "local_retry_selective":
                retry_cues = []
                for index, (window_start, window_end) in enumerate(windows):
                    window_artifacts: list[TranscriptionArtifact] = []
                    window_options = AsrDecodeOptions(**{
                        **asdict(candidate.decode_options),
                        "clip_timestamps": f"{window_start:.3f},{window_end:.3f}",
                    })
                    try:
                        transcribe_to_srt(
                            audio_path=audio_path, srt_path=candidate_srt.with_name(f"{candidate.candidate_id}.{index}.srt"),
                            model_name=config.model, model_dir=config.model_dir, device=config.device,
                            compute_type=config.compute_type, language=config.language,
                            beam_size=config.beam_size, vad_filter=config.vad_filter,
                            local_files_only=config.local_files_only,
                            language_profile_id=profile.get("profile_id", config.language_profile_id),
                            language_profile_name=profile.get("profile_name", config.language_profile_name),
                            condition_on_previous_text=window_options.condition_on_previous_text,
                            decode_options=window_options, artifact_out=window_artifacts, session=shared_session,
                        )
                    except Exception:
                        window_artifacts = []
                    if window_artifacts:
                        retry_cues.extend(window_artifacts[0].cues)
                retry_artifact = TranscriptionArtifact(
                    cues=tuple(sorted(retry_cues, key=lambda cue: (cue.start, cue.end))),
                    language=baseline_artifacts[0].language,
                    language_probability=baseline_artifacts[0].language_probability,
                    duration_seconds=baseline_artifacts[0].duration_seconds,
                )
                merged, selections = selective_merge_retry_artifact(baseline_artifacts[0], retry_artifact, windows)
                candidate_artifacts.append(merged)
                write_artifact_srt(candidate_srt, merged)
            else:
                if candidate.strategy == "local_retry":
                    clips = ",".join(f"{start:.3f},{end:.3f}" for start, end in windows)
                    decode_options = AsrDecodeOptions(**{**asdict(candidate.decode_options), "clip_timestamps": clips})
                transcribe_to_srt(
                    audio_path=audio_path, srt_path=candidate_srt, model_name=config.model,
                    model_dir=config.model_dir, device=config.device, compute_type=config.compute_type,
                    language=config.language, beam_size=config.beam_size, vad_filter=config.vad_filter,
                    local_files_only=config.local_files_only,
                    language_profile_id=profile.get("profile_id", config.language_profile_id),
                    language_profile_name=profile.get("profile_name", config.language_profile_name),
                    condition_on_previous_text=decode_options.condition_on_previous_text,
                    decode_options=decode_options, artifact_out=candidate_artifacts,
                )
            if candidate.strategy == "local_retry":
                merged = merge_retry_artifact(baseline_artifacts[0], candidate_artifacts[0], windows)
                candidate_artifacts[0] = merged
                write_artifact_srt(candidate_srt, merged)
            errors = validate_artifact(candidate_artifacts[0], baseline_artifacts[0].duration_seconds)
            if errors:
                raise ValueError("; ".join(errors[:3]))
            if duplicate_cue_rate(candidate_artifacts[0]) > duplicate_cue_rate(baseline_artifacts[0]):
                raise ValueError("candidate duplicate cue rate regressed")
            if len(candidate_artifacts[0].cues) < max(1, int(len(baseline_artifacts[0].cues) * 0.8)):
                raise ValueError("candidate cue coverage dropped by more than 20%")
            if mode == "apply":
                shutil.copy2(candidate_srt, srt_path.with_suffix(".srt.tmp"))
                srt_path.with_suffix(".srt.tmp").replace(srt_path)
            status = "applied" if mode == "apply" else "evaluated"
            selected = "candidate" if mode == "apply" else "baseline"
            affected = mode == "apply"
        except Exception as exc:
            status, selected, affected = "fallback", "baseline", False
            fallback_reason = str(exc)[:300]
        report_path = context.output_dir / "reports" / "asr_candidates" / f"{context.input_path.stem}.{candidate.candidate_id}.json"
        report = AsrCandidateReport(
            schema_version=1, candidate_id=candidate.candidate_id,
            candidate_version=candidate.version, mode=mode, status=status, selected=selected,
            output_affected=affected, baseline_sha256=baseline_sha256,
            candidate_sha256=safe_file_hash(candidate_srt),
            baseline_summary=baseline_artifacts[0].safe_summary() if baseline_artifacts else {},
            candidate_summary=candidate_artifacts[0].safe_summary() if candidate_artifacts else {},
            fallback_reason=fallback_reason,
            retried_window_count=len(selections),
            accepted_window_count=sum(1 for item in selections if item.accepted),
            rejected_window_count=sum(1 for item in selections if not item.accepted),
            rejection_reasons={
                reason: sum(1 for item in selections if item.reason == reason)
                for reason in sorted({item.reason for item in selections if not item.accepted})
            },
            quality_deltas=[item.safe_summary() for item in selections],
            model_reused=shared_session is not None,
        )
        write_candidate_report(report_path, report)
    outputs = (srt_path, report_path) if report_path else (srt_path,)
    return StageResult(
        "transcribing", "completed", tuple(path for path in outputs if path),
        {"language_detection": language_info, "asr_candidate_report": str(report_path or "")},
        duration_seconds=time.perf_counter() - started,
    )


def translate_stage(
    context: TaskContext, *, source_srt: Path, output_path: Path, config: Any, effective_prompt: str,
) -> StageResult:
    started = time.perf_counter()
    from subtitle_translate import translate_srt

    summary = translate_srt(
        input_path=source_srt,
        output_path=output_path,
        api_provider=config.api_provider,
        api_base=config.api_base,
        api_key=config.api_key,
        llm_model=config.llm_model,
        translation_quality_model=(
            getattr(config, "translation_quality_model", "") or config.llm_model
        ),
        target_language=config.target_language,
        batch_size=config.translation_batch_size,
        temperature=config.translation_temperature,
        translation_mode=config.translation_mode,
        system_prompt=effective_prompt,
        context_window=config.context_window,
        reliability_mode=getattr(config, "translation_reliability_mode", "off"),
        max_extra_requests=getattr(config, "translation_max_extra_requests", 12),
    )
    if not _valid(output_path):
        raise StageError("translating", "Translation completed without a non-empty SRT output.")
    return StageResult(
        "translating", "completed", (output_path,),
        {
            "translation_reliability": summary.safe_summary()
            if hasattr(summary, "safe_summary") else {"mode": "off"}
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
