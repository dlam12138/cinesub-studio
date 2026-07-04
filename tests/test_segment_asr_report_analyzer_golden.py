"""
Golden fixture regression tests for M7.2 segment ASR report analyzer.

These tests load pre-defined M7.1 report fixtures and verify:
- routing classification stability (golden expectations must not drift)
- output schema invariants (field presence and types)
- settings sensitivity (threshold changes affect routing as expected)
- multi-window aggregation correctness
- CLI end-to-end without model or audio dependencies

All fixtures are deterministic JSON; no Whisper model, no GPU, no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import segment_asr_report_analyzer as analyzer


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "asr_evidence"

# Mapping from fixture filename to expected single-window classification.
# These expectations are the "golden" baseline; if code changes make any
# of these drift, the acceptance document must explain why.
GOLDEN_EXPECTATIONS = {
    "en_auto_confident.json": "keep_auto",
    "fr_auto_confident.json": "keep_auto",
    "fr_auto_opp_contradiction.json": "needs_review",
    "low_conf_auto_strong_fr.json": "prefer_forced_fr",
    "low_conf_auto_strong_en.json": "prefer_forced_en",
    "low_conf_both_strong_conflict.json": "needs_review",
    "all_error.json": "skip_window",
    "mapping_shape.json": "keep_auto",
    "auto_only_usable.json": "needs_review",
    "confidence_threshold_edge.json": "prefer_forced_fr",
    "min_segments_edge.json": "skip_window",
    "empty_preview.json": "skip_window",
}

# Multi-window fixture: expected per-window classifications in order.
MULTI_WINDOW_EXPECTATIONS = [
    "keep_auto",          # window 1: confident English
    "prefer_forced_fr",   # window 2: low-confidence auto, strong forced-fr
    "needs_review",       # window 3: both forced strong and conflicting
]


def _load_fixture(name: str) -> Path:
    path = FIXTURE_DIR / name
    if not path.is_file():
        pytest.fail(f"Missing fixture: {path}")
    return path


def _analyze(path: Path, settings: analyzer.Settings | None = None) -> dict:
    return analyzer.analyze_reports([str(path)], settings or analyzer.Settings())


@pytest.mark.parametrize("fixture_name,expected", GOLDEN_EXPECTATIONS.items())
def test_golden_fixture_routing(fixture_name: str, expected: str) -> None:
    """Each fixture must produce the expected classification."""
    path = _load_fixture(fixture_name)
    summary = _analyze(path)
    assert summary["summary"]["total_windows"] == 1
    window = summary["windows"][0]
    assert window["classification"] == expected, (
        f"Fixture {fixture_name}: expected {expected}, got {window['classification']}"
    )


def test_multi_window_aggregate() -> None:
    """Multi-window fixture must aggregate per-window counts correctly."""
    path = _load_fixture("multi_window.json")
    summary = _analyze(path)

    assert summary["summary"]["total_windows"] == 3
    for idx, expected in enumerate(MULTI_WINDOW_EXPECTATIONS):
        assert summary["windows"][idx]["classification"] == expected

    # Aggregate counts must match
    assert summary["summary"]["keep_auto"] == 1
    assert summary["summary"]["prefer_forced_fr"] == 1
    assert summary["summary"]["needs_review"] == 1
    assert summary["summary"]["prefer_forced_en"] == 0
    assert summary["summary"]["skip_window"] == 0


@pytest.mark.parametrize(
    "fixture_name,low_threshold,high_threshold,expected_low,expected_high",
    [
        # When threshold is lowered, edge case becomes keep_auto
        (
            "confidence_threshold_edge.json",
            0.60,
            0.70,
            "keep_auto",
            "prefer_forced_fr",
        ),
        # When threshold is raised, confident case becomes prefer_forced_fr
        (
            "fr_auto_confident.json",
            0.70,
            0.95,
            "keep_auto",
            "prefer_forced_fr",
        ),
    ],
)
def test_settings_sensitivity(
    fixture_name: str,
    low_threshold: float,
    high_threshold: float,
    expected_low: str,
    expected_high: str,
) -> None:
    """Changing confidence_threshold must shift routing as expected."""
    path = _load_fixture(fixture_name)

    low = _analyze(path, analyzer.Settings(confidence_threshold=low_threshold))
    high = _analyze(path, analyzer.Settings(confidence_threshold=high_threshold))

    assert low["windows"][0]["classification"] == expected_low
    assert high["windows"][0]["classification"] == expected_high

    # Settings must be reflected in output
    assert low["settings"]["confidence_threshold"] == low_threshold
    assert high["settings"]["confidence_threshold"] == high_threshold


def test_schema_invariants() -> None:
    """Analyzer output must contain a stable set of top-level fields."""
    path = _load_fixture("en_auto_confident.json")
    summary = _analyze(path)

    # Top-level fields
    assert summary["schema_version"] == 1
    assert isinstance(summary["input_files"], list)
    assert isinstance(summary["settings"], dict)
    assert isinstance(summary["summary"], dict)
    assert isinstance(summary["windows"], list)

    # Settings fields
    settings = summary["settings"]
    assert "confidence_threshold" in settings
    assert "min_segments" in settings

    # Summary fields
    counts = summary["summary"]
    assert "total_windows" in counts
    for classification in analyzer.CLASSIFICATIONS:
        assert classification in counts

    # Window fields
    window = summary["windows"][0]
    assert "source_file" in window
    assert "window_index" in window
    assert "start_seconds" in window
    assert "end_seconds" in window
    assert "classification" in window
    assert "reason" in window
    assert "auto" in window
    assert "forced_fr" in window
    assert "forced_en" in window

    # Run summary fields
    auto = window["auto"]
    assert "detected_language" in auto
    assert "detected_language_probability" in auto
    assert "segment_count" in auto
    assert "has_error" in auto
    assert "usable" in auto


def test_output_schema_version() -> None:
    """schema_version must be 1 for M7.3 baseline."""
    path = _load_fixture("en_auto_confident.json")
    summary = _analyze(path)
    assert summary["schema_version"] == 1


def test_multiple_input_files_aggregate() -> None:
    """Analyzing multiple fixtures together must aggregate counts."""
    paths = [
        _load_fixture("en_auto_confident.json"),
        _load_fixture("low_conf_auto_strong_fr.json"),
        _load_fixture("all_error.json"),
    ]
    summary = analyzer.analyze_reports(
        [str(p) for p in paths], analyzer.Settings()
    )
    assert summary["summary"]["total_windows"] == 3
    assert summary["summary"]["keep_auto"] == 1
    assert summary["summary"]["prefer_forced_fr"] == 1
    assert summary["summary"]["skip_window"] == 1

    # input_files must list all resolved paths
    assert len(summary["input_files"]) == 3


def test_reason_text_is_present() -> None:
    """Every classification must carry a non-empty human-readable reason."""
    path = _load_fixture("multi_window.json")
    summary = _analyze(path)
    for window in summary["windows"]:
        assert window["reason"]
        assert isinstance(window["reason"], str)


def test_cli_end_to_end_with_fixture(tmp_path: Path) -> None:
    """CLI must process a fixture and produce JSON + Markdown without models."""
    fixture = _load_fixture("fr_auto_confident.json")
    output_json = tmp_path / "routing.json"
    output_md = tmp_path / "routing.md"

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_report_analyzer.py",
            str(fixture),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "keep_auto" in result.stdout
    assert output_json.is_file()
    assert output_md.is_file()

    # Verify JSON output is valid and follows schema
    data = json.loads(output_json.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["summary"]["keep_auto"] == 1

    # Verify Markdown contains limitation text
    md = output_md.read_text(encoding="utf-8")
    assert analyzer.LIMITATION_TEXT in md
    assert "faster_whisper" not in Path(
        "src/tools/segment_asr_report_analyzer.py"
    ).read_text(encoding="utf-8")
