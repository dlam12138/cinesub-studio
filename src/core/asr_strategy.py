from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


EXPERIMENT_MODES = {"off", "dry_run", "apply"}


@dataclass(frozen=True)
class AsrDecodeOptions:
    condition_on_previous_text: bool = True
    repetition_penalty: float = 1.0
    no_repeat_ngram_size: int = 0
    vad_threshold: float = 0.5
    vad_min_silence_duration_ms: int = 2000
    vad_speech_pad_ms: int = 400
    clip_timestamps: str = "0"

    def validate(self) -> "AsrDecodeOptions":
        if not 0 <= self.vad_threshold <= 1:
            raise ValueError("vad_threshold must be between 0 and 1")
        if self.vad_min_silence_duration_ms < 0 or self.vad_speech_pad_ms < 0:
            raise ValueError("VAD durations must be non-negative")
        if self.repetition_penalty < 1:
            raise ValueError("repetition_penalty must be at least 1")
        if self.no_repeat_ngram_size < 0:
            raise ValueError("no_repeat_ngram_size must be non-negative")
        return self

    def transcribe_kwargs(self, vad_filter: bool) -> dict[str, Any]:
        values: dict[str, Any] = {
            "condition_on_previous_text": self.condition_on_previous_text,
            "repetition_penalty": self.repetition_penalty,
            "no_repeat_ngram_size": self.no_repeat_ngram_size,
            "clip_timestamps": self.clip_timestamps,
        }
        if vad_filter:
            values["vad_parameters"] = {
                "threshold": self.vad_threshold,
                "min_silence_duration_ms": self.vad_min_silence_duration_ms,
                "speech_pad_ms": self.vad_speech_pad_ms,
            }
        return values


@dataclass(frozen=True)
class TranscriptionCue:
    start: float
    end: float
    text: str
    avg_logprob: float | None = None
    compression_ratio: float | None = None
    no_speech_prob: float | None = None


@dataclass(frozen=True)
class TranscriptionArtifact:
    cues: tuple[TranscriptionCue, ...]
    language: str = ""
    language_probability: float | None = None
    duration_seconds: float | None = None

    def safe_summary(self) -> dict[str, Any]:
        return {
            "cue_count": len(self.cues),
            "language": self.language,
            "language_probability": self.language_probability,
            "duration_seconds": self.duration_seconds,
            "speech_start": self.cues[0].start if self.cues else None,
            "speech_end": self.cues[-1].end if self.cues else None,
            "duplicate_cue_rate": duplicate_cue_rate(self),
            "suspicious_cue_count": len(suspicious_cue_indexes(self)),
        }


@dataclass(frozen=True)
class AsrCandidateDefinition:
    candidate_id: str
    version: int
    decode_options: AsrDecodeOptions
    applicable_models: tuple[str, ...] = ("small", "large-v3")
    allowed_modes: tuple[str, ...] = ("off", "dry_run")
    strategy: str = "decode"
    target_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AsrCandidateReport:
    schema_version: int
    candidate_id: str
    candidate_version: int
    mode: str
    status: str
    selected: str
    output_affected: bool
    baseline_sha256: str
    candidate_sha256: str = ""
    baseline_summary: dict[str, Any] = field(default_factory=dict)
    candidate_summary: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str = ""
    retried_window_count: int = 0
    accepted_window_count: int = 0
    rejected_window_count: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    quality_deltas: list[dict[str, Any]] = field(default_factory=list)
    model_reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BASELINE_DECODE_OPTIONS = AsrDecodeOptions()

CANDIDATES: dict[str, AsrCandidateDefinition] = {
    "vad-balanced-v1": AsrCandidateDefinition(
        "vad-balanced-v1", 1,
        AsrDecodeOptions(vad_threshold=0.5, vad_min_silence_duration_ms=1000, vad_speech_pad_ms=400),
        target_tags=("distant", "low_volume", "dialogue"),
    ),
    "vad-sensitive-v1": AsrCandidateDefinition(
        "vad-sensitive-v1", 1,
        AsrDecodeOptions(vad_threshold=0.4, vad_min_silence_duration_ms=500, vad_speech_pad_ms=400),
        target_tags=("distant", "low_volume"),
    ),
    "decode-repeat-guard-v1": AsrCandidateDefinition(
        "decode-repeat-guard-v1", 1,
        AsrDecodeOptions(repetition_penalty=1.05, no_repeat_ngram_size=3),
        target_tags=("overlapping_speech",),
    ),
    "previous-text-off-v1": AsrCandidateDefinition(
        "previous-text-off-v1", 1, AsrDecodeOptions(condition_on_previous_text=False),
        allowed_modes=("off", "dry_run"), target_tags=("overlapping_speech",),
    ),
    "local-retry-v1": AsrCandidateDefinition(
        "local-retry-v1", 1,
        AsrDecodeOptions(
            condition_on_previous_text=False, repetition_penalty=1.05, no_repeat_ngram_size=3,
            vad_threshold=0.4, vad_min_silence_duration_ms=500,
        ),
        allowed_modes=("off", "dry_run"), strategy="local_retry",
        target_tags=("distant", "low_volume", "overlapping_speech"),
    ),
    "local-retry-selective-v2": AsrCandidateDefinition(
        "local-retry-selective-v2", 2,
        AsrDecodeOptions(
            condition_on_previous_text=False, repetition_penalty=1.05, no_repeat_ngram_size=3,
            vad_threshold=0.4, vad_min_silence_duration_ms=500,
        ),
        allowed_modes=("off", "dry_run"), strategy="local_retry_selective",
        target_tags=("distant", "low_volume", "overlapping_speech", "noise"),
    ),
    "mixed-route-v1": AsrCandidateDefinition(
        "mixed-route-v1", 1, BASELINE_DECODE_OPTIONS,
        allowed_modes=("off", "dry_run"), strategy="mixed_route", target_tags=("code_switching",),
    ),
}


def get_candidate(candidate_id: str, mode: str = "dry_run", model: str = "large-v3") -> AsrCandidateDefinition:
    candidate = CANDIDATES.get(str(candidate_id or "").strip())
    if candidate is None:
        raise ValueError(f"Unknown ASR candidate: {candidate_id}")
    if mode not in EXPERIMENT_MODES:
        raise ValueError(f"Invalid ASR experiment mode: {mode}")
    if mode not in candidate.allowed_modes:
        raise ValueError(f"ASR candidate {candidate.candidate_id} does not allow mode {mode}")
    if model not in candidate.applicable_models:
        raise ValueError(f"ASR candidate {candidate.candidate_id} does not support model {model}")
    candidate.decode_options.validate()
    return candidate


def validate_strategy_config(value: object, *, model: str = "large-v3") -> dict[str, str]:
    data = value if isinstance(value, dict) else {}
    unknown = set(data) - {"mode", "candidate_id"}
    if unknown:
        raise ValueError(f"Unknown asr_strategy fields: {', '.join(sorted(unknown))}")
    mode = str(data.get("mode") or "off").strip()
    candidate_id = str(data.get("candidate_id") or "").strip()
    if mode == "off":
        return {"mode": "off", "candidate_id": candidate_id}
    get_candidate(candidate_id, mode, model)
    return {"mode": mode, "candidate_id": candidate_id}


def validate_artifact(artifact: TranscriptionArtifact, media_duration: float | None = None) -> list[str]:
    errors: list[str] = []
    previous_start = -1.0
    for index, cue in enumerate(artifact.cues, 1):
        if not cue.text.strip():
            errors.append(f"cue {index} is empty")
        if cue.start < 0 or cue.end <= cue.start:
            errors.append(f"cue {index} has invalid timestamps")
        if cue.start < previous_start:
            errors.append(f"cue {index} is not monotonic")
        if media_duration is not None and cue.end > media_duration + 0.5:
            errors.append(f"cue {index} exceeds media duration")
        previous_start = cue.start
    if not artifact.cues:
        errors.append("artifact has no cues")
    return errors


def duplicate_cue_rate(artifact: TranscriptionArtifact) -> float:
    if len(artifact.cues) < 2:
        return 0.0
    duplicate = 0
    for previous, current in zip(artifact.cues, artifact.cues[1:]):
        if " ".join(previous.text.lower().split()) == " ".join(current.text.lower().split()):
            duplicate += 1
    return round(duplicate / len(artifact.cues), 6)


def suspicious_cue_indexes(artifact: TranscriptionArtifact) -> list[int]:
    indexes: list[int] = []
    for index, cue in enumerate(artifact.cues):
        suspicious = (
            cue.avg_logprob is not None and cue.avg_logprob < -1.0
            or cue.compression_ratio is not None and cue.compression_ratio > 2.4
            or cue.no_speech_prob is not None and cue.no_speech_prob > 0.6 and bool(cue.text.strip())
        )
        if index and " ".join(cue.text.lower().split()) == " ".join(artifact.cues[index - 1].text.lower().split()):
            suspicious = True
        if suspicious:
            indexes.append(index)
    return indexes


def retry_windows(artifact: TranscriptionArtifact, pad_seconds: float = 0.5) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    for index in suspicious_cue_indexes(artifact):
        cue = artifact.cues[index]
        start = artifact.cues[index - 1].end if index else cue.start
        end = artifact.cues[index + 1].start if index + 1 < len(artifact.cues) else cue.end
        window = (max(0.0, min(start, cue.start) - pad_seconds), max(end, cue.end) + pad_seconds)
        if windows and window[0] <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], window[1]))
        else:
            windows.append(window)
    return windows


@dataclass(frozen=True)
class WindowQuality:
    cue_count: int
    coverage_seconds: float
    average_logprob: float | None
    max_compression_ratio: float | None
    no_speech_conflict_count: int
    duplicate_count: int
    suspicious_count: int
    start: float | None
    end: float | None

    def safe_summary(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WindowSelection:
    window_start: float
    window_end: float
    accepted: bool
    reason: str
    baseline: WindowQuality
    candidate: WindowQuality

    def safe_summary(self) -> dict[str, Any]:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "accepted": self.accepted,
            "reason": self.reason,
            "baseline": self.baseline.safe_summary(),
            "candidate": self.candidate.safe_summary(),
        }


def _window_cues(artifact: TranscriptionArtifact, window: tuple[float, float]) -> tuple[TranscriptionCue, ...]:
    start, end = window
    return tuple(cue for cue in artifact.cues if cue.start < end and cue.end > start)


def window_quality(artifact: TranscriptionArtifact, window: tuple[float, float]) -> WindowQuality:
    cues = _window_cues(artifact, window)
    logprobs = [float(cue.avg_logprob) for cue in cues if cue.avg_logprob is not None]
    compressions = [float(cue.compression_ratio) for cue in cues if cue.compression_ratio is not None]
    no_speech_conflicts = sum(
        1 for cue in cues if cue.no_speech_prob is not None and cue.no_speech_prob > 0.6 and cue.text.strip()
    )
    duplicates = sum(
        1 for previous, current in zip(cues, cues[1:])
        if " ".join(previous.text.lower().split()) == " ".join(current.text.lower().split())
    )
    local_artifact = TranscriptionArtifact(cues=cues, duration_seconds=artifact.duration_seconds)
    start, end = window
    coverage = sum(max(0.0, min(cue.end, end) - max(cue.start, start)) for cue in cues)
    return WindowQuality(
        cue_count=len(cues), coverage_seconds=round(coverage, 6),
        average_logprob=round(sum(logprobs) / len(logprobs), 6) if logprobs else None,
        max_compression_ratio=round(max(compressions), 6) if compressions else None,
        no_speech_conflict_count=no_speech_conflicts, duplicate_count=duplicates,
        suspicious_count=len(suspicious_cue_indexes(local_artifact)),
        start=min((cue.start for cue in cues), default=None),
        end=max((cue.end for cue in cues), default=None),
    )


def select_retry_window(
    baseline: TranscriptionArtifact, candidate: TranscriptionArtifact, window: tuple[float, float],
) -> WindowSelection:
    baseline_quality = window_quality(baseline, window)
    candidate_quality = window_quality(candidate, window)
    reason = "accepted"
    if candidate_quality.cue_count == 0:
        reason = "empty_candidate"
    elif candidate_quality.start is None or candidate_quality.end is None or candidate_quality.end <= candidate_quality.start:
        reason = "invalid_timeline"
    elif baseline_quality.coverage_seconds <= 0:
        reason = "empty_baseline_window"
    else:
        coverage_ratio = candidate_quality.coverage_seconds / baseline_quality.coverage_seconds
        if not 0.8 <= coverage_ratio <= 1.2:
            reason = "coverage_out_of_range"
        elif candidate_quality.duplicate_count > baseline_quality.duplicate_count:
            reason = "duplicate_regressed"
        elif candidate_quality.no_speech_conflict_count > baseline_quality.no_speech_conflict_count:
            reason = "no_speech_regressed"
        elif candidate_quality.suspicious_count > baseline_quality.suspicious_count:
            reason = "suspicion_regressed"
        else:
            improvements = (
                baseline_quality.average_logprob is not None and candidate_quality.average_logprob is not None
                and candidate_quality.average_logprob - baseline_quality.average_logprob >= 0.10,
                baseline_quality.max_compression_ratio is not None and candidate_quality.max_compression_ratio is not None
                and baseline_quality.max_compression_ratio - candidate_quality.max_compression_ratio >= 0.20,
                candidate_quality.no_speech_conflict_count < baseline_quality.no_speech_conflict_count,
                candidate_quality.duplicate_count < baseline_quality.duplicate_count,
            )
            if not any(improvements):
                reason = "no_clear_improvement"
    return WindowSelection(window[0], window[1], reason == "accepted", reason, baseline_quality, candidate_quality)


def selective_merge_retry_artifact(
    baseline: TranscriptionArtifact, retry: TranscriptionArtifact, windows: list[tuple[float, float]],
) -> tuple[TranscriptionArtifact, tuple[WindowSelection, ...]]:
    selections = tuple(select_retry_window(baseline, retry, window) for window in windows)
    accepted = [window for window, selection in zip(windows, selections) if selection.accepted]
    if not accepted:
        return baseline, selections
    merged = merge_retry_artifact(baseline, retry, accepted)
    if validate_artifact(merged, baseline.duration_seconds):
        return baseline, tuple(
            WindowSelection(item.window_start, item.window_end, False, "final_structure_invalid", item.baseline, item.candidate)
            for item in selections
        )
    return merged, selections


def merge_retry_artifact(
    baseline: TranscriptionArtifact,
    retry: TranscriptionArtifact,
    windows: list[tuple[float, float]],
) -> TranscriptionArtifact:
    if not windows:
        return baseline
    kept = [
        cue for cue in baseline.cues
        if not any(start <= (cue.start + cue.end) / 2 <= end for start, end in windows)
    ]
    replacements = [
        cue for cue in retry.cues
        if any(cue.start < end and cue.end > start for start, end in windows)
    ]
    merged = tuple(sorted((*kept, *replacements), key=lambda cue: (cue.start, cue.end)))
    artifact = TranscriptionArtifact(
        cues=merged,
        language=baseline.language,
        language_probability=baseline.language_probability,
        duration_seconds=baseline.duration_seconds,
    )
    errors = validate_artifact(artifact, baseline.duration_seconds)
    if errors:
        raise ValueError("local retry merge failed: " + "; ".join(errors[:3]))
    return artifact


def write_artifact_srt(path: Path, artifact: TranscriptionArtifact) -> None:
    def timestamp(value: float) -> str:
        millis = max(0, round(value * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        seconds, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    temporary = path.with_suffix(path.suffix + ".tmp")
    blocks = [
        f"{index}\n{timestamp(cue.start)} --> {timestamp(cue.end)}\n{cue.text}"
        for index, cue in enumerate(artifact.cues, 1)
    ]
    temporary.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    temporary.replace(path)


def safe_file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_candidate_report(path: Path, report: AsrCandidateReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
