from __future__ import annotations

import json
from pathlib import Path

import pytest

import asr_benchmark as benchmark
import code_switch_metrics as metrics


def cue(index: int, start: float, end: float, text: str) -> benchmark.Cue:
    return benchmark.Cue(index, start, end, text)


def test_mixed_units_normalize_punctuation_and_keep_han_characters() -> None:
    assert metrics.mixed_error_units("Bonjour, 世界! [UNK]") == ["bonjour", "世", "界"]


def test_single_language_and_empty_reference_are_defined() -> None:
    spans = [metrics.LanguageSpan(0, 2, "fr")]
    result = metrics.calculate_code_switch_metrics(
        [cue(1, 0, 2, "bonjour")], [cue(1, 0, 2, "bonjour")], spans, spans
    )
    assert result["mer"] == 0
    assert result["switch_count"] == 0
    assert result["post_switch_first_token_error_rate"] == 0
    assert result["language_span_recall"] == 1
    assert metrics.calculate_code_switch_metrics([], [cue(1, 0, 1, "extra")], None)["mer"] == 1


def test_one_and_multiple_switches_measure_first_token_errors() -> None:
    reference = [
        cue(1, 0, 1, "bonjour"), cue(2, 1, 2, "hello"), cue(3, 2, 3, "salut"),
    ]
    hypothesis = [
        cue(1, 0, 1, "bonjour"), cue(2, 1, 2, "wrong"), cue(3, 2, 3, "salut"),
    ]
    spans = [
        metrics.LanguageSpan(0, 1, "fr"), metrics.LanguageSpan(1, 2, "en"),
        metrics.LanguageSpan(2, 3, "fr"),
    ]
    result = metrics.calculate_code_switch_metrics(reference, hypothesis, spans, spans)
    assert result["switch_count"] == 2
    assert result["post_switch_first_token_error_rate"] == 0.5
    assert result["language_span_recall"] == 1


def test_missing_prediction_spans_return_null_with_warning() -> None:
    spans = [metrics.LanguageSpan(0, 1, "fr"), metrics.LanguageSpan(1, 2, "en")]
    result = metrics.calculate_code_switch_metrics(
        [cue(1, 0, 1, "bonjour"), cue(2, 1, 2, "hello")],
        [cue(1, 0, 1, "bonjour"), cue(2, 1, 2, "hello")], spans,
    )
    assert result["language_span_recall"] is None
    assert "hypothesis language spans unavailable" in result["warnings"]


def test_annotation_validation(tmp_path: Path) -> None:
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "spans": [
            {"start": 0, "end": 1, "language": "fr"},
            {"start": 1, "end": 2, "language": "en"},
        ],
    }), encoding="utf-8")
    assert len(metrics.load_language_annotations(path)) == 2
    path.write_text(json.dumps({
        "schema_version": 1,
        "spans": [{"start": 1, "end": 0, "language": "fr"}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="greater"):
        metrics.load_language_annotations(path)
