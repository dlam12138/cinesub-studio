from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import segment_asr_report_analyzer as analyzer


def _run(
    *,
    language: str,
    probability: float,
    segments: int = 2,
    preview: str = "preview text",
    error: str = "",
) -> dict:
    return {
        "detected_language": language,
        "language_probability": probability,
        "segment_count": segments,
        "text_preview": preview,
        "error": error,
    }


def _report(results: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "report_type": "segment_asr_prototype",
        "windows": [
            {
                "window_index": 1,
                "start_seconds": 300.0,
                "end_seconds": 420.0,
                "results": results,
            }
        ],
    }


def _write_report(tmp_path: Path, report: dict, name: str = "report.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _analyze(tmp_path: Path, report: dict) -> dict:
    path = _write_report(tmp_path, report)
    return analyzer.analyze_reports([str(path)], analyzer.Settings())


def test_keep_auto_for_confident_auto_with_matching_forced_result(tmp_path):
    summary = _analyze(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                {"mode": "forced-en", **_run(language="en", probability=0.52, preview="short")},
            ]
        ),
    )

    window = summary["windows"][0]
    assert window["classification"] == "keep_auto"
    assert summary["summary"]["keep_auto"] == 1
    assert window["auto"]["detected_language_probability"] == 0.91


def test_prefer_forced_fr_when_auto_is_low_confidence(tmp_path):
    summary = _analyze(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.32)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.88, segments=4)},
                {"mode": "forced-en", **_run(language="fr", probability=0.40, segments=1)},
            ]
        ),
    )

    assert summary["windows"][0]["classification"] == "prefer_forced_fr"
    assert summary["summary"]["prefer_forced_fr"] == 1


def test_prefer_forced_en_when_auto_is_low_confidence(tmp_path):
    summary = _analyze(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="en", probability=0.25)},
                {"mode": "forced-fr", **_run(language="en", probability=0.30, segments=1)},
                {"mode": "forced-en", **_run(language="en", probability=0.86, segments=5)},
            ]
        ),
    )

    assert summary["windows"][0]["classification"] == "prefer_forced_en"
    assert summary["summary"]["prefer_forced_en"] == 1


def test_needs_review_when_forced_runs_conflict(tmp_path):
    summary = _analyze(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.40)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.92, segments=3)},
                {"mode": "forced-en", **_run(language="en", probability=0.87, segments=3)},
            ]
        ),
    )

    assert summary["windows"][0]["classification"] == "needs_review"
    assert summary["summary"]["needs_review"] == 1


def test_skip_window_when_all_runs_are_errored_or_empty(tmp_path):
    summary = _analyze(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="", probability=0.0, segments=0, preview="", error="boom")},
                {"mode": "forced-fr", **_run(language="fr", probability=0.0, segments=0, preview="")},
                {"mode": "forced-en", **_run(language="en", probability=0.0, segments=0, preview="")},
            ]
        ),
    )

    assert summary["windows"][0]["classification"] == "skip_window"
    assert summary["summary"]["skip_window"] == 1


def test_accepts_runs_mapping_shape(tmp_path):
    path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window": {"start_seconds": 0.0, "end_seconds": 60.0},
                    "runs": {
                        "auto": {
                            "detected_language": "en",
                            "detected_language_probability": 0.93,
                            "segment_count": 2,
                            "preview": "hello",
                            "error": None,
                        },
                        "forced-en": {
                            "detected_language": "en",
                            "detected_language_probability": 0.95,
                            "segment_count": 2,
                            "preview": "hello",
                            "error": None,
                        },
                    },
                }
            ]
        },
    )

    summary = analyzer.analyze_reports([str(path)], analyzer.Settings())

    assert summary["windows"][0]["classification"] == "keep_auto"
    assert summary["windows"][0]["forced_fr"]["usable"] is False


def test_malformed_input_raises_controlled_exception(tmp_path):
    path = _write_report(tmp_path, {"windows": [{"start_seconds": 0.0, "end_seconds": 1.0}]})

    with pytest.raises(analyzer.AnalyzerInputError, match="requires runs object or results list"):
        analyzer.analyze_reports([str(path)], analyzer.Settings())


def test_markdown_contains_aggregate_counts_and_limitation_text(tmp_path):
    summary = _analyze(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
            ]
        ),
    )

    markdown = analyzer.render_markdown(summary)

    assert "## Aggregate Results" in markdown
    assert "- keep_auto: `1`" in markdown
    assert analyzer.LIMITATION_TEXT in markdown


def test_cli_does_not_require_model_or_audio(tmp_path):
    report_path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
            ]
        ),
    )
    output_json = tmp_path / "summary.json"
    output_md = tmp_path / "summary.md"

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_report_analyzer.py",
            str(report_path),
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

    assert result.returncode == 0
    assert "M7.2 Segment ASR Routing Evidence Summary" in result.stdout
    assert output_json.is_file()
    assert output_md.is_file()
    assert "faster_whisper" not in Path("src/tools/segment_asr_report_analyzer.py").read_text(
        encoding="utf-8"
    )
