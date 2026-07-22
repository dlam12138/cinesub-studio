from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from asr_runtime import TranscriptionArtifact, TranscriptionCue, TranscriptionWord


@dataclass(frozen=True)
class ResegmentResult:
    artifact: TranscriptionArtifact
    summary: dict


class SubtitleResegmenter:
    def __init__(
        self,
        *,
        min_duration: float = 0.7,
        target_duration: float = 4.5,
        max_duration: float = 6.5,
        min_gap: float = 0.02,
        max_units: float = 44.0,
        max_units_per_second: float = 12.0,
    ) -> None:
        self.min_duration = min_duration
        self.target_duration = target_duration
        self.max_duration = max_duration
        self.min_gap = min_gap
        self.max_units = max_units
        self.max_units_per_second = max_units_per_second

    def resegment(self, artifact: TranscriptionArtifact, *, enabled: bool) -> ResegmentResult:
        empty_summary = {
            "enabled": bool(enabled),
            "applied": False,
            "fallback_reason": None if enabled else None,
            "input_cue_count": len(artifact.cues),
            "output_cue_count": len(artifact.cues),
            "word_timing_count": sum(len(cue.words) for cue in artifact.cues),
        }
        if not enabled:
            return ResegmentResult(artifact, empty_summary)

        words = _flatten_words(artifact)
        if not words or not any(_has_valid_timing(word) for word in words):
            empty_summary["fallback_reason"] = "no_word_timestamps"
            return ResegmentResult(artifact, empty_summary)

        groups = self._group_words(words)
        cues = tuple(self._group_to_cue(group) for group in groups if group)
        candidate = TranscriptionArtifact(
            cues=cues,
            language=artifact.language,
            language_probability=artifact.language_probability,
            duration_seconds=artifact.duration_seconds,
            backend_versions=artifact.backend_versions,
            metadata={**artifact.metadata, "resegmented": True},
        )
        fallback = _validate_resegment(artifact, candidate)
        summary = {
            **empty_summary,
            "applied": not bool(fallback),
            "fallback_reason": fallback,
            "output_cue_count": len(candidate.cues) if not fallback else len(artifact.cues),
        }
        return ResegmentResult(candidate if not fallback else artifact, summary)

    def _group_words(self, words: list[TranscriptionWord]) -> list[list[TranscriptionWord]]:
        groups: list[list[TranscriptionWord]] = []
        current: list[TranscriptionWord] = []
        for word in words:
            if not current:
                current.append(word)
                continue
            current.append(word)
            if self._should_break(current, word):
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        return groups

    def _should_break(self, group: list[TranscriptionWord], word: TranscriptionWord) -> bool:
        start, end = _group_time_bounds(group)
        if start is None or end is None:
            return False
        duration = max(0.001, end - start)
        units = _reading_units("".join(item.text for item in group))
        strong_punct = bool(re.search(r"[。！？!?\.]\s*$", word.text.strip()))
        soft_punct = bool(re.search(r"[，,;；:：]\s*$", word.text.strip()))
        if duration >= self.max_duration:
            return True
        if units >= self.max_units:
            return True
        if units / duration > self.max_units_per_second and duration >= self.min_duration:
            return True
        if strong_punct and duration >= self.min_duration:
            return True
        if soft_punct and duration >= self.target_duration:
            return True
        return False

    def _group_to_cue(self, group: list[TranscriptionWord]) -> TranscriptionCue:
        start, end = _group_time_bounds(group)
        start = start or 0.0
        end = end or max(start + 0.001, start)
        text = _join_word_text(group)
        return TranscriptionCue(
            start=max(0.0, start),
            end=max(start + 0.001, end),
            text=text,
            words=tuple(group),
        )


def _flatten_words(artifact: TranscriptionArtifact) -> list[TranscriptionWord]:
    words: list[TranscriptionWord] = []
    for cue in artifact.cues:
        for word in cue.words:
            text = str(word.text or "")
            if not text.strip():
                continue
            words.append(word)
    return words


def _group_time_bounds(group: list[TranscriptionWord]) -> tuple[float | None, float | None]:
    valid = [word for word in group if _has_valid_timing(word)]
    if not valid:
        return None, None
    return _word_start(valid[0]), _word_end(valid[-1])


def _has_valid_timing(word: TranscriptionWord) -> bool:
    start = _word_start(word)
    end = _word_end(word)
    return start is not None and end is not None and end > start


def _validate_resegment(source: TranscriptionArtifact, candidate: TranscriptionArtifact) -> str | None:
    if not candidate.cues:
        return "empty_resegment"
    previous_end = -1.0
    for cue in candidate.cues:
        if cue.start < 0 or cue.end <= cue.start:
            return "invalid_timestamps"
        if previous_end >= 0 and cue.start < previous_end:
            return "overlap"
        previous_end = cue.end
    old_text = _normalize_text("".join(cue.text for cue in source.cues))
    new_text = _normalize_text("".join(cue.text for cue in candidate.cues))
    if old_text != new_text:
        return "text_conservation_failed"
    old_words = sum(len(cue.words) for cue in source.cues)
    new_words = sum(len(cue.words) for cue in candidate.cues)
    if old_words != new_words:
        return "word_conservation_failed"
    return None


def _word_start(word: TranscriptionWord) -> float | None:
    if word.start is None:
        return None
    value = float(word.start)
    return value if math.isfinite(value) else None


def _word_end(word: TranscriptionWord) -> float | None:
    if word.end is None:
        return None
    value = float(word.end)
    return value if math.isfinite(value) else None


def _join_word_text(words: list[TranscriptionWord]) -> str:
    text = ""
    for word in words:
        value = str(word.text or "")
        if not text:
            text = value.strip()
            continue
        if _needs_space(text[-1], value[:1]):
            text += " "
        text += value.strip()
    return text.strip()


def _needs_space(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if re.match(r"[\s,.;:!?，。；：！？）\]\}]", right):
        return False
    if re.match(r"[\s（\[\{]", left):
        return False
    return _is_latin(left) and _is_latin(right)


def _is_latin(value: str) -> bool:
    return bool(value) and ("LATIN" in unicodedata.name(value[0], ""))


def _reading_units(text: str) -> float:
    units = 0.0
    in_word = False
    for char in text:
        if char.isspace():
            in_word = False
            continue
        east_asian = unicodedata.east_asian_width(char)
        if east_asian in {"W", "F"} or re.match(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", char):
            units += 1.0
            in_word = False
        elif char.isalnum():
            units += 0.5
            if not in_word:
                units += 0.5
                in_word = True
        else:
            units += 0.25
            in_word = False
    return units


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()
