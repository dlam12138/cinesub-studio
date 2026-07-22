from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


ASR_MODES = {"auto", "fixed", "multilingual"}
QUALITY_PRESETS = {"speed", "balanced", "quality"}
ASR_RETRY_MODES = {"off", "dry_run", "apply"}
ASR_RETRY_RECIPE_VERSION = "local-retry-selective-v2"
SAMPLE_RATE = 16000
MULTILINGUAL_TARGET_SECONDS = 45.0
MULTILINGUAL_MAX_SECONDS = 60.0
MULTILINGUAL_LONG_SILENCE_SECONDS = 8.0
MULTILINGUAL_OVERLAP_SECONDS = 0.8
BOUNDARY_SIMILARITY_THRESHOLD = 0.90


def normalize_asr_request(
    mode: object = None,
    language: object = None,
    *,
    reject_conflict: bool = True,
) -> tuple[str, str | None]:
    """Normalize the public ASR mode/language pair.

    Legacy callers that only provide ``language`` are treated as fixed-language
    requests. Explicit automatic and multilingual requests never carry a forced
    language.
    """
    normalized_mode = str(mode or "").strip().lower()
    normalized_language = str(language or "").strip().lower()
    if normalized_language == "auto":
        normalized_language = ""
    if not normalized_mode:
        normalized_mode = "fixed" if normalized_language else "auto"
    if normalized_mode not in ASR_MODES:
        raise ValueError(
            "asr_mode must be one of: auto, fixed, multilingual"
        )
    if normalized_mode == "fixed":
        if not normalized_language:
            raise ValueError("fixed ASR mode requires a source language")
        return normalized_mode, normalized_language
    if normalized_language and reject_conflict:
        raise ValueError(
            f"{normalized_mode} ASR mode does not accept a fixed source language"
        )
    return normalized_mode, None


@dataclass(frozen=True)
class AsrDecodeOptions:
    condition_on_previous_text: bool = True
    repetition_penalty: float = 1.0
    no_repeat_ngram_size: int = 0
    vad_threshold: float = 0.5
    vad_min_silence_duration_ms: int = 2000
    vad_speech_pad_ms: int = 400

    def validate(self) -> "AsrDecodeOptions":
        if not 0 <= self.vad_threshold <= 1:
            raise ValueError("vad_threshold must be between 0 and 1")
        if self.vad_min_silence_duration_ms < 0 or self.vad_speech_pad_ms < 0:
            raise ValueError("VAD durations must be non-negative")
        return self

    def transcribe_kwargs(self, vad_filter: bool) -> dict[str, Any]:
        values: dict[str, Any] = {
            "condition_on_previous_text": self.condition_on_previous_text,
            "repetition_penalty": self.repetition_penalty,
            "no_repeat_ngram_size": self.no_repeat_ngram_size,
        }
        if vad_filter:
            values["vad_parameters"] = {
                "threshold": self.vad_threshold,
                "min_silence_duration_ms": self.vad_min_silence_duration_ms,
                "speech_pad_ms": self.vad_speech_pad_ms,
            }
        return values


@dataclass(frozen=True)
class TranscriptionWord:
    start: float | None
    end: float | None
    text: str
    probability: float | None = None


@dataclass(frozen=True)
class TranscriptionCue:
    start: float
    end: float
    text: str
    words: tuple[TranscriptionWord, ...] = ()
    avg_logprob: float | None = None
    compression_ratio: float | None = None
    no_speech_prob: float | None = None


@dataclass(frozen=True)
class TranscriptionArtifact:
    cues: tuple[TranscriptionCue, ...]
    language: str = ""
    language_probability: float | None = None
    duration_seconds: float | None = None
    backend_versions: tuple[tuple[str, str], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "cue_count": len(self.cues),
            "language": self.language,
            "language_probability": self.language_probability,
            "duration_seconds": self.duration_seconds,
            "backend_versions": dict(self.backend_versions),
            "word_timing_count": sum(len(cue.words) for cue in self.cues),
            "suspicious_cue_count": len(suspicious_cue_indexes(self.cues)),
        }


@dataclass(frozen=True)
class AudioBlock:
    start_sample: int
    end_sample: int
    speech_seconds: float

    @property
    def start(self) -> float:
        return self.start_sample / SAMPLE_RATE

    @property
    def end(self) -> float:
        return self.end_sample / SAMPLE_RATE


def plan_vad_blocks(
    speech_spans: Iterable[dict[str, int]],
    *,
    audio_samples: int,
    sampling_rate: int = SAMPLE_RATE,
    target_seconds: float = MULTILINGUAL_TARGET_SECONDS,
    max_seconds: float = MULTILINGUAL_MAX_SECONDS,
    long_silence_seconds: float = MULTILINGUAL_LONG_SILENCE_SECONDS,
    overlap_seconds: float = MULTILINGUAL_OVERLAP_SECONDS,
) -> list[AudioBlock]:
    spans = [
        (max(0, int(row["start"])), min(audio_samples, int(row["end"])))
        for row in speech_spans
        if int(row.get("end", 0)) > int(row.get("start", 0))
    ]
    if not spans:
        return []
    target_samples = int(target_seconds * sampling_rate)
    max_samples = int(max_seconds * sampling_rate)
    long_silence_samples = int(long_silence_seconds * sampling_rate)
    overlap_samples = int(overlap_seconds * sampling_rate)
    raw_blocks: list[tuple[int, int, int]] = []
    block_start, block_end = spans[0]
    speech_samples = block_end - block_start
    for start, end in spans[1:]:
        gap = start - block_end
        projected_span = end - block_start
        projected_speech = speech_samples + end - start
        should_close = (
            gap >= long_silence_samples
            or projected_span > max_samples
            or speech_samples >= target_samples
            or projected_speech > max_samples
        )
        if should_close:
            raw_blocks.append((block_start, block_end, speech_samples))
            block_start, block_end = start, end
            speech_samples = end - start
        else:
            block_end = end
            speech_samples = projected_speech
    raw_blocks.append((block_start, block_end, speech_samples))

    blocks: list[AudioBlock] = []
    for start, end, speech in raw_blocks:
        padded_start = max(0, start - overlap_samples)
        padded_end = min(audio_samples, end + overlap_samples)
        if padded_end - padded_start > max_samples:
            excess = padded_end - padded_start - max_samples
            trim_left = min(excess // 2, start - padded_start)
            padded_start += trim_left
            padded_end -= excess - trim_left
        blocks.append(AudioBlock(padded_start, padded_end, speech / sampling_rate))
    return blocks


def suspicious_cue_indexes(cues: Iterable[TranscriptionCue]) -> list[int]:
    rows = list(cues)
    indexes: list[int] = []
    for index, cue in enumerate(rows):
        suspicious = (
            cue.avg_logprob is not None and cue.avg_logprob < -1.0
            or cue.compression_ratio is not None and cue.compression_ratio > 2.4
            or cue.no_speech_prob is not None
            and cue.no_speech_prob > 0.6
            and bool(cue.text.strip())
        )
        if index and _normalized_text(cue.text) == _normalized_text(rows[index - 1].text):
            suspicious = True
        if suspicious:
            indexes.append(index)
    return indexes


def normalize_quality_preset(value: object = None) -> str:
    preset = str(value or "").strip().lower()
    if not preset:
        return ""
    if preset not in QUALITY_PRESETS:
        raise ValueError("quality_preset must be one of: speed, balanced, quality")
    return preset


def normalize_asr_retry_mode(value: object = None) -> str:
    mode = str(value or "off").strip().lower()
    if mode not in ASR_RETRY_MODES:
        raise ValueError("asr_retry_mode must be one of: off, dry_run, apply")
    return mode


def quality_preset_values(preset: object = None) -> dict[str, object]:
    normalized = normalize_quality_preset(preset)
    if normalized == "speed":
        return {
            "word_timestamps": False,
            "resegment_subtitles": False,
            "asr_retry_mode": "off",
        }
    if normalized == "balanced":
        return {
            "word_timestamps": True,
            "resegment_subtitles": True,
            "asr_retry_mode": "dry_run",
        }
    if normalized == "quality":
        return {
            "model": "large-v3",
            "word_timestamps": True,
            "resegment_subtitles": True,
            "asr_retry_mode": "dry_run",
        }
    return {}


def _bool_option(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def resolve_quality_loop_config(
    *,
    explicit: dict[str, object] | None = None,
    preset: object = None,
    profile_asr: dict | None = None,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Resolve v0.7 ASR quality-loop options and explain each source."""
    explicit = explicit or {}
    profile_asr = profile_asr or {}
    preset_name = normalize_quality_preset(preset)
    preset_map = quality_preset_values(preset_name)

    fields = {
        "quality_preset": "",
        "word_timestamps": False,
        "resegment_subtitles": False,
        "asr_retry_mode": "off",
        "asr_hotword_prompt": "",
    }
    if "model" in preset_map:
        fields["model"] = ""

    resolved: dict[str, object] = {"quality_preset": preset_name}
    sources: dict[str, dict[str, object]] = {
        "quality_preset": {"value": preset_name, "source": "explicit_request" if preset_name else "default"}
    }
    for field, default in fields.items():
        if field == "quality_preset":
            continue
        if field in explicit and explicit[field] is not None:
            value = explicit[field]
            source = "explicit_request"
        elif field in preset_map:
            value = preset_map[field]
            source = "quality_preset"
        elif field in profile_asr:
            value = profile_asr[field]
            source = "language_profile"
        else:
            value = default
            source = "default"
        if field in {"word_timestamps", "resegment_subtitles"}:
            value = _bool_option(value)
        if field == "asr_retry_mode":
            value = normalize_asr_retry_mode(value)
        if field == "asr_hotword_prompt":
            value = str(value or "").strip()
        resolved[field] = value
        sources[field] = {"value": value, "source": source}
    return resolved, sources


def uncovered_speech_intervals(
    speech_intervals: Iterable[tuple[float, float]],
    cues: Iterable[TranscriptionCue],
    *,
    cue_padding_seconds: float = 0.35,
    minimum_uncovered_seconds: float = 1.0,
    merge_gap_seconds: float = 0.5,
) -> list[dict[str, float]]:
    """Return VAD speech regions not covered by final cues.

    This is diagnostic-only: callers must not use it to rerun or replace ASR
    output automatically.
    """
    padded_cues = _merge_intervals(
        (
            max(0.0, cue.start - cue_padding_seconds),
            max(cue.start, cue.end + cue_padding_seconds),
        )
        for cue in cues
    )
    uncovered: list[tuple[float, float]] = []
    for speech_start, speech_end in _merge_intervals(speech_intervals):
        fragments = [(speech_start, speech_end)]
        for cue_start, cue_end in padded_cues:
            if cue_end <= speech_start:
                continue
            if cue_start >= speech_end:
                break
            next_fragments: list[tuple[float, float]] = []
            for start, end in fragments:
                if cue_end <= start or cue_start >= end:
                    next_fragments.append((start, end))
                    continue
                if cue_start > start:
                    next_fragments.append((start, min(cue_start, end)))
                if cue_end < end:
                    next_fragments.append((max(cue_end, start), end))
            fragments = next_fragments
            if not fragments:
                break
        uncovered.extend(
            (start, end)
            for start, end in fragments
            if end - start >= minimum_uncovered_seconds
        )

    merged = _merge_intervals(uncovered, max_gap=merge_gap_seconds)
    return [
        {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        }
        for start, end in merged
        if end - start >= minimum_uncovered_seconds
    ]


def _merge_intervals(
    intervals: Iterable[tuple[float, float]],
    *,
    max_gap: float = 0.0,
) -> list[tuple[float, float]]:
    ordered = sorted(
        (max(0.0, float(start)), max(0.0, float(end)))
        for start, end in intervals
        if float(end) > float(start)
    )
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + max_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def deduplicate_boundary_cues(
    cues: Iterable[TranscriptionCue],
    *,
    similarity_threshold: float = BOUNDARY_SIMILARITY_THRESHOLD,
    boundary_slack_seconds: float = 0.25,
) -> tuple[tuple[TranscriptionCue, ...], int]:
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end, cue.text))
    output: list[TranscriptionCue] = []
    removed = 0
    for cue in ordered:
        if not output:
            output.append(cue)
            continue
        previous = output[-1]
        temporal_match = cue.start <= previous.end + boundary_slack_seconds
        similarity = difflib.SequenceMatcher(
            None,
            _normalized_text(previous.text),
            _normalized_text(cue.text),
            autojunk=False,
        ).ratio()
        if temporal_match and similarity >= similarity_threshold:
            preferred = _preferred_cue(previous, cue)
            output[-1] = TranscriptionCue(
                start=min(previous.start, cue.start),
                end=max(previous.end, cue.end),
                text=preferred.text,
                words=preferred.words,
                avg_logprob=preferred.avg_logprob,
                compression_ratio=preferred.compression_ratio,
                no_speech_prob=preferred.no_speech_prob,
            )
            removed += 1
        else:
            output.append(cue)
    return tuple(output), removed


def _preferred_cue(left: TranscriptionCue, right: TranscriptionCue) -> TranscriptionCue:
    if left.avg_logprob is not None or right.avg_logprob is not None:
        left_score = left.avg_logprob if left.avg_logprob is not None else float("-inf")
        right_score = right.avg_logprob if right.avg_logprob is not None else float("-inf")
        if left_score != right_score:
            return left if left_score > right_score else right
    return left if len(left.text.strip()) >= len(right.text.strip()) else right


def _normalized_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)
