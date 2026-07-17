"""Transcript-local metrics for mixed-language ASR evaluation.

The functions in this module deliberately return metrics only. Reference and
hypothesis text stays in the ignored benchmark workspace and is never copied
into public benchmark reports.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


SUPPORTED_LANGUAGES = {"ar", "en", "fr", "zh"}


@dataclass(frozen=True)
class LanguageSpan:
    start: float
    end: float
    language: str


def mixed_error_units(text: str) -> list[str]:
    """Tokenize Latin/Arabic words and individual Han characters for MER."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    normalized = re.sub(r"(?i)\[UNK\]", " ", normalized)
    units: list[str] = []
    word: list[str] = []

    def flush() -> None:
        if word:
            units.append("".join(word))
            word.clear()

    for char in normalized:
        category = unicodedata.category(char)
        if "\u4e00" <= char <= "\u9fff":
            flush()
            units.append(char)
        elif category.startswith(("L", "N", "M")):
            word.append(char)
        else:
            flush()
    flush()
    return units


def _distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for index, reference_item in enumerate(reference, start=1):
        current = [index]
        for hyp_index, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hyp_index] + 1,
                    previous[hyp_index - 1] + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def _rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _distance(reference, hypothesis) / len(reference)


def load_language_annotations(path: str | Path) -> list[LanguageSpan]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("language annotations schema_version must be 1")
    raw_spans = data.get("spans")
    if not isinstance(raw_spans, list) or not raw_spans:
        raise ValueError("language annotations require a non-empty spans list")
    spans: list[LanguageSpan] = []
    for raw in raw_spans:
        if not isinstance(raw, dict):
            raise ValueError("each language span must be an object")
        start = float(raw.get("start"))
        end = float(raw.get("end"))
        language = str(raw.get("language") or "").strip().lower()
        if start < 0 or end <= start:
            raise ValueError("language span end must be greater than start")
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language span: {language}")
        if spans and start < spans[-1].end:
            raise ValueError("language spans must be ordered and non-overlapping")
        spans.append(LanguageSpan(start, end, language))
    return spans


def _cue_text(cues: Iterable[Any], start: float | None = None, end: float | None = None) -> str:
    selected: list[str] = []
    for cue in cues:
        cue_start = float(getattr(cue, "start"))
        cue_end = float(getattr(cue, "end"))
        if start is not None and end is not None and (cue_end <= start or cue_start >= end):
            continue
        selected.append(str(getattr(cue, "text", "")))
    return " ".join(selected)


def _predicted_language(reference: LanguageSpan, predictions: Sequence[LanguageSpan]) -> str | None:
    overlaps: list[tuple[float, str]] = []
    for prediction in predictions:
        overlap = max(0.0, min(reference.end, prediction.end) - max(reference.start, prediction.start))
        if overlap:
            overlaps.append((overlap, prediction.language))
    return max(overlaps, default=(0.0, ""))[1] or None


def calculate_code_switch_metrics(
    reference_cues: Sequence[Any],
    hypothesis_cues: Sequence[Any],
    reference_spans: Sequence[LanguageSpan] | None,
    hypothesis_spans: Sequence[LanguageSpan] | None = None,
) -> dict[str, Any]:
    """Return MER, post-switch first-token error rate, and span recall.

    Language-span recall is intentionally null when span-level hypothesis
    language evidence is unavailable. A global language guess is not promoted
    to span evidence for mixed-language material.
    """
    warnings: list[str] = []
    reference_units = mixed_error_units(_cue_text(reference_cues))
    hypothesis_units = mixed_error_units(_cue_text(hypothesis_cues))
    mer = round(_rate(reference_units, hypothesis_units), 6)

    spans = list(reference_spans or [])
    if not spans:
        warnings.append("reference language spans unavailable")
        return {
            "mer": mer,
            "post_switch_first_token_error_rate": None,
            "language_span_recall": None,
            "switch_count": 0,
            "reference_span_count": 0,
            "warnings": warnings,
        }

    switch_spans = [
        span for previous, span in zip(spans, spans[1:]) if previous.language != span.language
    ]
    first_token_errors = 0
    valid_switches = 0
    for span in switch_spans:
        reference = mixed_error_units(_cue_text(reference_cues, span.start, span.end))
        hypothesis = mixed_error_units(_cue_text(hypothesis_cues, span.start, span.end))
        if not reference:
            warnings.append("switch span has no reference token")
            continue
        valid_switches += 1
        if not hypothesis or hypothesis[0] != reference[0]:
            first_token_errors += 1
    first_token_rate = (
        round(first_token_errors / valid_switches, 6) if valid_switches else 0.0
    )

    span_recall: float | None = None
    if hypothesis_spans is None:
        warnings.append("hypothesis language spans unavailable")
    else:
        correct = sum(
            _predicted_language(span, hypothesis_spans) == span.language for span in spans
        )
        span_recall = round(correct / len(spans), 6)

    return {
        "mer": mer,
        "post_switch_first_token_error_rate": first_token_rate,
        "language_span_recall": span_recall,
        "switch_count": len(switch_spans),
        "reference_span_count": len(spans),
        "warnings": sorted(set(warnings)),
    }
