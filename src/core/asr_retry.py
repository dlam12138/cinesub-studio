from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from asr_runtime import (
    ASR_RETRY_RECIPE_VERSION,
    TranscriptionArtifact,
    TranscriptionCue,
    _merge_intervals,
    suspicious_cue_indexes,
)


MAX_RETRY_WINDOWS = 20
MAX_WINDOW_SECONDS = 30.0
MAX_RETRY_DURATION_RATIO = 0.10
WINDOW_PAD_SECONDS = 1.25
MIN_SCORE_DELTA = 0.20


@dataclass(frozen=True)
class WindowQuality:
    cue_count: int
    coverage_seconds: float
    average_logprob: float | None
    max_compression_ratio: float | None
    no_speech_conflict_count: int
    duplicate_count: int
    suspicious_count: int
    text_length: int
    start: float | None
    end: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RetryWindowReport:
    window: tuple[float, float]
    cue_indexes: tuple[int, ...]
    accepted: bool = False
    skipped: bool = False
    reasons: tuple[str, ...] = ()
    baseline_metrics: dict = field(default_factory=dict)
    candidate_metrics: dict = field(default_factory=dict)
    metric_deltas: dict = field(default_factory=dict)
    baseline_text_hash: str = ""
    candidate_text_hash: str = ""
    baseline_length: int = 0
    candidate_length: int = 0

    def to_dict(self) -> dict:
        return {
            "window": [round(self.window[0], 3), round(self.window[1], 3)],
            "cue_indexes": list(self.cue_indexes),
            "accepted": self.accepted,
            "skipped": self.skipped,
            "reasons": list(self.reasons),
            "baseline_metrics": self.baseline_metrics,
            "candidate_metrics": self.candidate_metrics,
            "metric_deltas": self.metric_deltas,
            "baseline_text_hash": self.baseline_text_hash,
            "candidate_text_hash": self.candidate_text_hash,
            "baseline_length": self.baseline_length,
            "candidate_length": self.candidate_length,
        }


def empty_retry_report(mode: str = "off") -> dict:
    return {
        "mode": mode,
        "recipe_version": ASR_RETRY_RECIPE_VERSION,
        "planned_window_count": 0,
        "executed_window_count": 0,
        "accepted_window_count": 0,
        "rejected_window_count": 0,
        "budget_skipped_window_count": 0,
        "windows": [],
    }


def validate_artifact(artifact: TranscriptionArtifact) -> list[str]:
    errors: list[str] = []
    previous_end = 0.0
    for index, cue in enumerate(artifact.cues, 1):
        if not math.isfinite(cue.start) or not math.isfinite(cue.end):
            errors.append(f"cue {index} timestamp is not finite")
        if cue.start < 0 or cue.end <= cue.start:
            errors.append(f"cue {index} has invalid timestamps")
        if index > 1 and cue.start < previous_end:
            errors.append(f"cue {index} overlaps previous cue")
        if _has_control_text(cue.text):
            errors.append(f"cue {index} contains control characters")
        previous_end = cue.end
    return errors


def plan_retry_windows(
    artifact: TranscriptionArtifact,
    *,
    pad_seconds: float = WINDOW_PAD_SECONDS,
    max_window_seconds: float = MAX_WINDOW_SECONDS,
    max_windows: int = MAX_RETRY_WINDOWS,
    max_duration_ratio: float = MAX_RETRY_DURATION_RATIO,
) -> tuple[list[tuple[float, float]], list[RetryWindowReport]]:
    suspicious = suspicious_cue_indexes(artifact.cues)
    raw_windows: list[tuple[float, float]] = []
    for index in suspicious:
        cue = artifact.cues[index]
        raw_windows.append((max(0.0, cue.start - pad_seconds), cue.end + pad_seconds))
    merged = _merge_intervals(raw_windows, max_gap=0.25)
    clipped: list[tuple[float, float]] = []
    skipped: list[RetryWindowReport] = []
    for window in merged:
        start, end = window
        if end - start > max_window_seconds:
            center = (start + end) / 2
            half = max_window_seconds / 2
            window = (max(0.0, center - half), center + half)
        clipped.append(window)

    duration = artifact.duration_seconds or (artifact.cues[-1].end if artifact.cues else 0.0)
    budget = max(0.0, duration * max_duration_ratio)
    ordered = sorted(
        clipped,
        key=lambda item: (
            -window_quality(artifact, item).suspicious_count,
            item[0],
        ),
    )
    accepted: list[tuple[float, float]] = []
    total = 0.0
    for index, window in enumerate(ordered):
        if len(accepted) >= max_windows or (budget and total + (window[1] - window[0]) > budget):
            skipped.append(RetryWindowReport(
                window=window,
                cue_indexes=tuple(_window_indexes(artifact, window)),
                skipped=True,
                reasons=("retry_budget_exceeded",),
            ))
            continue
        accepted.append(window)
        total += window[1] - window[0]
    return sorted(accepted), skipped


def window_quality(artifact: TranscriptionArtifact, window: tuple[float, float]) -> WindowQuality:
    cues = _window_cues(artifact, window)
    logprobs = [float(cue.avg_logprob) for cue in cues if cue.avg_logprob is not None]
    compressions = [float(cue.compression_ratio) for cue in cues if cue.compression_ratio is not None]
    duplicate_count = sum(
        1 for previous, current in zip(cues, cues[1:])
        if _normalized(previous.text) == _normalized(current.text)
    )
    no_speech_conflicts = sum(
        1 for cue in cues
        if cue.no_speech_prob is not None and cue.no_speech_prob > 0.6 and cue.text.strip()
    )
    local = TranscriptionArtifact(cues=tuple(cues), duration_seconds=artifact.duration_seconds)
    start, end = window
    coverage = sum(max(0.0, min(cue.end, end) - max(cue.start, start)) for cue in cues)
    text_length = len(_normalized(" ".join(cue.text for cue in cues)))
    return WindowQuality(
        cue_count=len(cues),
        coverage_seconds=round(coverage, 6),
        average_logprob=round(sum(logprobs) / len(logprobs), 6) if logprobs else None,
        max_compression_ratio=round(max(compressions), 6) if compressions else None,
        no_speech_conflict_count=no_speech_conflicts,
        duplicate_count=duplicate_count,
        suspicious_count=len(suspicious_cue_indexes(local.cues)),
        text_length=text_length,
        start=min((cue.start for cue in cues), default=None),
        end=max((cue.end for cue in cues), default=None),
    )


def select_retry_window(
    baseline: TranscriptionArtifact,
    candidate: TranscriptionArtifact,
    window: tuple[float, float],
) -> RetryWindowReport:
    baseline_cues = _window_cues(baseline, window)
    candidate_cues = _window_cues(candidate, window)
    baseline_quality = window_quality(baseline, window)
    candidate_quality = window_quality(candidate, window)
    reasons = _hard_rejections(baseline_quality, candidate_quality, baseline, candidate, window)
    deltas = _quality_deltas(baseline_quality, candidate_quality)
    accepted = False
    if not reasons:
        repaired = (
            candidate_quality.suspicious_count < baseline_quality.suspicious_count
            or candidate_quality.duplicate_count < baseline_quality.duplicate_count
            or candidate_quality.no_speech_conflict_count < baseline_quality.no_speech_conflict_count
            or (
                baseline_quality.max_compression_ratio is not None
                and candidate_quality.max_compression_ratio is not None
                and candidate_quality.max_compression_ratio < baseline_quality.max_compression_ratio
            )
        )
        score = _composite_score(baseline_quality, candidate_quality)
        if repaired and score >= MIN_SCORE_DELTA:
            accepted = True
            reasons = ["accepted"]
        else:
            reasons = ["no_clear_improvement"]
    return RetryWindowReport(
        window=window,
        cue_indexes=tuple(index + 1 for index in _window_indexes(baseline, window)),
        accepted=accepted,
        reasons=tuple(reasons),
        baseline_metrics=baseline_quality.to_dict(),
        candidate_metrics=candidate_quality.to_dict(),
        metric_deltas=deltas,
        baseline_text_hash=_text_hash(cue.text for cue in baseline_cues),
        candidate_text_hash=_text_hash(cue.text for cue in candidate_cues),
        baseline_length=sum(len(cue.text) for cue in baseline_cues),
        candidate_length=sum(len(cue.text) for cue in candidate_cues),
    )


def merge_retry_artifact(
    baseline: TranscriptionArtifact,
    candidate: TranscriptionArtifact,
    accepted_windows: Iterable[tuple[float, float]],
) -> TranscriptionArtifact:
    windows = list(accepted_windows)
    if not windows:
        return baseline
    kept = [
        cue for cue in baseline.cues
        if not any(start <= (cue.start + cue.end) / 2 <= end for start, end in windows)
    ]
    replacements = [
        cue for cue in candidate.cues
        if any(cue.start < end and cue.end > start for start, end in windows)
    ]
    merged = tuple(sorted((*kept, *replacements), key=lambda cue: (cue.start, cue.end, cue.text)))
    artifact = TranscriptionArtifact(
        cues=merged,
        language=baseline.language,
        language_probability=baseline.language_probability,
        duration_seconds=baseline.duration_seconds,
        backend_versions=baseline.backend_versions,
        metadata={**baseline.metadata, "asr_retry_recipe_version": ASR_RETRY_RECIPE_VERSION},
    )
    errors = validate_artifact(artifact)
    if errors:
        raise ValueError("ASR retry merge failed: " + "; ".join(errors[:3]))
    return artifact


def build_retry_report(mode: str, windows: list[RetryWindowReport]) -> dict:
    return {
        "mode": mode,
        "recipe_version": ASR_RETRY_RECIPE_VERSION,
        "planned_window_count": len(windows),
        "executed_window_count": sum(1 for item in windows if not item.skipped),
        "accepted_window_count": sum(1 for item in windows if item.accepted),
        "rejected_window_count": sum(1 for item in windows if not item.accepted and not item.skipped),
        "budget_skipped_window_count": sum(1 for item in windows if item.skipped),
        "windows": [item.to_dict() for item in windows],
    }


def _hard_rejections(
    baseline: WindowQuality,
    candidate: WindowQuality,
    baseline_artifact: TranscriptionArtifact,
    candidate_artifact: TranscriptionArtifact,
    window: tuple[float, float],
) -> list[str]:
    reasons: list[str] = []
    if candidate.cue_count == 0 and baseline.cue_count > 0:
        reasons.append("empty_candidate")
    if candidate.start is None or candidate.end is None or candidate.end <= candidate.start:
        reasons.append("invalid_candidate_timeline")
    if candidate.max_compression_ratio is not None and candidate.max_compression_ratio > 2.8:
        reasons.append("compression_hard_limit")
    if candidate.no_speech_conflict_count > baseline.no_speech_conflict_count:
        reasons.append("no_speech_regressed")
    if candidate.duplicate_count > baseline.duplicate_count:
        reasons.append("duplicate_regressed")
    if baseline.coverage_seconds > 0:
        coverage_ratio = candidate.coverage_seconds / baseline.coverage_seconds
        if not 0.75 <= coverage_ratio <= 1.25:
            reasons.append("coverage_out_of_range")
    if baseline.text_length > 0:
        length_ratio = candidate.text_length / baseline.text_length
        if not 0.50 <= length_ratio <= 1.80:
            reasons.append("text_length_out_of_range")
    if validate_artifact(candidate_artifact):
        reasons.append("candidate_artifact_invalid")
    if _adjacent_duplicate_after_replace(baseline_artifact, candidate_artifact, window):
        reasons.append("adjacent_duplicate_regressed")
    if any(_has_control_text(cue.text) for cue in _window_cues(candidate_artifact, window)):
        reasons.append("control_text_detected")
    return reasons


def _quality_deltas(left: WindowQuality, right: WindowQuality) -> dict:
    def delta(a, b):
        return round(b - a, 6) if a is not None and b is not None else None

    return {
        "average_logprob": delta(left.average_logprob, right.average_logprob),
        "max_compression_ratio": delta(left.max_compression_ratio, right.max_compression_ratio),
        "no_speech_conflict_count": right.no_speech_conflict_count - left.no_speech_conflict_count,
        "duplicate_count": right.duplicate_count - left.duplicate_count,
        "suspicious_count": right.suspicious_count - left.suspicious_count,
        "coverage_seconds": delta(left.coverage_seconds, right.coverage_seconds),
    }


def _composite_score(left: WindowQuality, right: WindowQuality) -> float:
    score = 0.0
    if left.average_logprob is not None and right.average_logprob is not None:
        score += max(0.0, right.average_logprob - left.average_logprob)
    if left.max_compression_ratio is not None and right.max_compression_ratio is not None:
        score += max(0.0, left.max_compression_ratio - right.max_compression_ratio) * 0.5
    score += max(0, left.duplicate_count - right.duplicate_count) * 0.5
    score += max(0, left.no_speech_conflict_count - right.no_speech_conflict_count) * 0.5
    score += max(0, left.suspicious_count - right.suspicious_count) * 0.25
    return round(score, 6)


def _window_cues(artifact: TranscriptionArtifact, window: tuple[float, float]) -> list[TranscriptionCue]:
    start, end = window
    return [cue for cue in artifact.cues if cue.start < end and cue.end > start]


def _window_indexes(artifact: TranscriptionArtifact, window: tuple[float, float]) -> list[int]:
    start, end = window
    return [index for index, cue in enumerate(artifact.cues) if cue.start < end and cue.end > start]


def _adjacent_duplicate_after_replace(
    baseline: TranscriptionArtifact,
    candidate: TranscriptionArtifact,
    window: tuple[float, float],
) -> bool:
    try:
        merged = merge_retry_artifact(baseline, candidate, [window])
    except ValueError:
        return True
    rows = list(merged.cues)
    for previous, current in zip(rows, rows[1:]):
        if previous.end + 0.25 < current.start:
            continue
        if _normalized(previous.text) and _normalized(previous.text) == _normalized(current.text):
            return True
    return False


def _normalized(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _has_control_text(value: str) -> bool:
    return any((ord(char) < 32 and char not in "\r\n\t") for char in value)


def _text_hash(values: Iterable[str]) -> str:
    text = "\n".join(values)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
