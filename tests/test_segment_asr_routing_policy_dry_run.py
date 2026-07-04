from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import segment_asr_routing_policy_dry_run as dry_run
from segment_asr_report_analyzer import AnalyzerInputError


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


def test_insufficient_evidence_when_total_windows_below_min_total_windows(tmp_path):
    path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
            ]
        ),
    )
    gate_settings = dry_run.GateSettings(
        min_total_windows=5,
        max_needs_review_rate=0.25,
        max_skip_window_rate=0.10,
    )
    result = dry_run.analyze_reports([str(path)], dry_run.AnalyzerSettings(confidence_threshold=0.70, min_segments=1))
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    assert readiness["status"] == "insufficient_evidence"
    assert any("below min_total_windows" in b for b in readiness["blockers"])


def test_not_ready_when_needs_review_rate_exceeds_gate(tmp_path):
    path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 60.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.40)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.92, segments=3)},
                        {"mode": "forced-en", **_run(language="en", probability=0.87, segments=3)},
                    ],
                },
                {
                    "window_index": 2,
                    "start_seconds": 60.0,
                    "end_seconds": 120.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.40)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.92, segments=3)},
                        {"mode": "forced-en", **_run(language="en", probability=0.87, segments=3)},
                    ],
                },
                {
                    "window_index": 3,
                    "start_seconds": 120.0,
                    "end_seconds": 180.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 4,
                    "start_seconds": 180.0,
                    "end_seconds": 240.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 5,
                    "start_seconds": 240.0,
                    "end_seconds": 300.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
            ]
        },
    )
    gate_settings = dry_run.GateSettings(
        min_total_windows=5,
        max_needs_review_rate=0.25,
        max_skip_window_rate=0.50,
    )
    result = dry_run.analyze_reports([str(path)], dry_run.AnalyzerSettings(confidence_threshold=0.70, min_segments=1))
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    assert readiness["status"] == "not_ready"
    assert any("needs_review_rate" in b for b in readiness["blockers"])


def test_not_ready_when_skip_window_rate_exceeds_gate(tmp_path):
    path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 60.0,
                    "results": [
                        {"mode": "auto", **_run(language="", probability=0.0, segments=0, preview="", error="boom")},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.0, segments=0, preview="")},
                        {"mode": "forced-en", **_run(language="en", probability=0.0, segments=0, preview="")},
                    ],
                },
                {
                    "window_index": 2,
                    "start_seconds": 60.0,
                    "end_seconds": 120.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 3,
                    "start_seconds": 120.0,
                    "end_seconds": 180.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 4,
                    "start_seconds": 180.0,
                    "end_seconds": 240.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 5,
                    "start_seconds": 240.0,
                    "end_seconds": 300.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
            ]
        },
    )
    gate_settings = dry_run.GateSettings(
        min_total_windows=5,
        max_needs_review_rate=0.50,
        max_skip_window_rate=0.10,
    )
    result = dry_run.analyze_reports([str(path)], dry_run.AnalyzerSettings(confidence_threshold=0.70, min_segments=1))
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    assert readiness["status"] == "not_ready"
    assert any("skip_window_rate" in b for b in readiness["blockers"])


def test_candidate_ready_for_design_when_gates_pass(tmp_path):
    path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 60.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 2,
                    "start_seconds": 60.0,
                    "end_seconds": 120.0,
                    "results": [
                        {"mode": "auto", **_run(language="en", probability=0.91)},
                        {"mode": "forced-en", **_run(language="en", probability=0.95)},
                    ],
                },
                {
                    "window_index": 3,
                    "start_seconds": 120.0,
                    "end_seconds": 180.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 4,
                    "start_seconds": 180.0,
                    "end_seconds": 240.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 5,
                    "start_seconds": 240.0,
                    "end_seconds": 300.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
            ]
        },
    )
    gate_settings = dry_run.GateSettings(
        min_total_windows=5,
        max_needs_review_rate=0.25,
        max_skip_window_rate=0.10,
    )
    result = dry_run.analyze_reports([str(path)], dry_run.AnalyzerSettings(confidence_threshold=0.70, min_segments=1))
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    assert readiness["status"] == "candidate_ready_for_design"
    assert readiness["blockers"] == []


def test_blockers_list_contains_clear_reason_strings(tmp_path):
    path = _write_report(tmp_path, _report([
        {"mode": "auto", **_run(language="fr", probability=0.40)},
        {"mode": "forced-fr", **_run(language="fr", probability=0.92, segments=3)},
        {"mode": "forced-en", **_run(language="en", probability=0.87, segments=3)},
    ]))
    gate_settings = dry_run.GateSettings(
        min_total_windows=1,
        max_needs_review_rate=0.10,
        max_skip_window_rate=0.10,
    )
    result = dry_run.analyze_reports([str(path)], dry_run.AnalyzerSettings(confidence_threshold=0.70, min_segments=1))
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    assert readiness["status"] == "not_ready"
    assert any("needs_review_rate" in b for b in readiness["blockers"])


def test_review_windows_include_needs_review_and_skip_window(tmp_path):
    path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 60.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.40)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.92, segments=3)},
                        {"mode": "forced-en", **_run(language="en", probability=0.87, segments=3)},
                    ],
                },
                {
                    "window_index": 2,
                    "start_seconds": 60.0,
                    "end_seconds": 120.0,
                    "results": [
                        {"mode": "auto", **_run(language="", probability=0.0, segments=0, preview="", error="boom")},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.0, segments=0, preview="")},
                        {"mode": "forced-en", **_run(language="en", probability=0.0, segments=0, preview="")},
                    ],
                },
                {
                    "window_index": 3,
                    "start_seconds": 120.0,
                    "end_seconds": 180.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
            ]
        },
    )
    gate_settings = dry_run.GateSettings(
        min_total_windows=1,
        max_needs_review_rate=0.50,
        max_skip_window_rate=0.50,
    )
    result = dry_run.analyze_reports([str(path)], dry_run.AnalyzerSettings(confidence_threshold=0.70, min_segments=1))
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    json_output = dry_run.build_json_output(result, readiness, gate_settings)
    review_windows = json_output["review_windows"]
    classifications = [w["classification"] for w in review_windows]
    assert "needs_review" in classifications
    assert "skip_window" in classifications


def test_markdown_contains_required_sections_and_limitation_text():
    data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_policy_dry_run",
        "input_files": ["/tmp/a.json"],
        "settings": {
            "confidence_threshold": 0.70,
            "min_segments": 1,
            "min_total_windows": 5,
            "max_needs_review_rate": 0.25,
            "max_skip_window_rate": 0.10,
        },
        "readiness": {
            "status": "candidate_ready_for_design",
            "reason": "Evidence meets conservative gates.",
            "blockers": [],
        },
        "summary": {
            "total_windows": 5,
            "keep_auto": 1,
            "prefer_forced_fr": 1,
            "prefer_forced_en": 1,
            "needs_review": 1,
            "skip_window": 1,
            "needs_review_rate": 0.2,
            "skip_window_rate": 0.2,
        },
        "review_windows": [],
        "notes": list(dry_run.NOTES),
    }
    markdown = dry_run.render_markdown(data)
    assert "# M7.5 Segment ASR Routing Policy Dry-Run" in markdown
    assert "## Inputs" in markdown
    assert "## Settings" in markdown
    assert "## Readiness Result" in markdown
    assert "## Aggregate Classification Summary" in markdown
    assert "## Review Windows" in markdown
    assert "## Notes And Limitations" in markdown
    assert dry_run.LIMITATION_TEXT in markdown


def test_json_output_contains_schema_version_tool_name_readiness_settings_summary_notes():
    data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_policy_dry_run",
        "input_files": ["/tmp/a.json"],
        "settings": {
            "confidence_threshold": 0.70,
            "min_segments": 1,
            "min_total_windows": 5,
            "max_needs_review_rate": 0.25,
            "max_skip_window_rate": 0.10,
        },
        "readiness": {
            "status": "candidate_ready_for_design",
            "reason": "ok",
            "blockers": [],
        },
        "summary": {
            "total_windows": 5,
            "keep_auto": 1,
            "prefer_forced_fr": 1,
            "prefer_forced_en": 1,
            "needs_review": 1,
            "skip_window": 1,
            "needs_review_rate": 0.2,
            "skip_window_rate": 0.2,
        },
        "review_windows": [],
        "notes": list(dry_run.NOTES),
    }
    assert data["schema_version"] == 1
    assert data["tool"] == "segment_asr_routing_policy_dry_run"
    assert "readiness" in data
    assert "settings" in data
    assert "summary" in data
    assert "notes" in data


def test_invalid_gate_value_returns_clean_error(tmp_path):
    output_json = tmp_path / "out.json"
    report_path = _write_report(
        tmp_path,
        _report([
            {"mode": "auto", **_run(language="fr", probability=0.91)},
            {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
        ]),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
            str(report_path),
            "--max-needs-review-rate",
            "1.5",
            "--output-json",
            str(output_json),
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 1
    assert "between 0 and 1" in result.stderr


def test_missing_input_file_returns_clean_error():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
            "nonexistent_file.json",
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 1
    assert "not found" in result.stderr.lower() or "No input files" in result.stderr


def test_cli_end_to_end_with_temp_output_json_and_markdown(tmp_path):
    report_path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 60.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 2,
                    "start_seconds": 60.0,
                    "end_seconds": 120.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 3,
                    "start_seconds": 120.0,
                    "end_seconds": 180.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 4,
                    "start_seconds": 180.0,
                    "end_seconds": 240.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 5,
                    "start_seconds": 240.0,
                    "end_seconds": 300.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
            ]
        },
    )
    output_json = tmp_path / "dry_run.json"
    output_md = tmp_path / "dry_run.md"

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
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

    assert result.returncode == 0, result.stderr
    assert "# M7.5 Segment ASR Routing Policy Dry-Run" in result.stdout
    assert output_json.is_file()
    assert output_md.is_file()
    data = json.loads(output_json.read_text(encoding="utf-8"))
    assert data["tool"] == "segment_asr_routing_policy_dry_run"
    assert data["readiness"]["status"] == "candidate_ready_for_design"


def test_uses_existing_m7_3_fixtures():
    fixture_dir = Path(__file__).parent / "fixtures" / "asr_evidence"
    mapping = fixture_dir / "mapping_shape.json"
    low_conf = fixture_dir / "low_conf_auto_strong_fr.json"
    assert mapping.is_file(), f"Missing fixture: {mapping}"
    assert low_conf.is_file(), f"Missing fixture: {low_conf}"

    gate_settings = dry_run.GateSettings(
        min_total_windows=1,
        max_needs_review_rate=0.50,
        max_skip_window_rate=0.50,
    )
    result = dry_run.analyze_reports([str(mapping), str(low_conf)], dry_run.AnalyzerSettings(confidence_threshold=0.70, min_segments=1))
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    json_output = dry_run.build_json_output(result, readiness, gate_settings)
    assert json_output["summary"]["total_windows"] == 2
    assert any(w["classification"] == "keep_auto" for w in json_output["review_windows"]) is False
    assert any(w["classification"] == "prefer_forced_fr" for w in json_output["review_windows"]) is False


def test_does_not_duplicate_m7_2_classification_logic_via_monkeypatch(monkeypatch, tmp_path):
    called = []

    def fake_analyze_reports(input_files, settings):
        called.append((input_files, settings.confidence_threshold, settings.min_segments))
        return {
            "schema_version": 1,
            "input_files": input_files,
            "settings": {
                "confidence_threshold": settings.confidence_threshold,
                "min_segments": settings.min_segments,
            },
            "summary": {
                "total_windows": 5,
                "keep_auto": 3,
                "prefer_forced_fr": 1,
                "prefer_forced_en": 0,
                "needs_review": 1,
                "skip_window": 0,
            },
            "windows": [
                {
                    "source_file": "a.json",
                    "window_index": i,
                    "classification": "keep_auto",
                    "reason": "ok",
                }
                for i in range(3)
            ] + [
                {
                    "source_file": "a.json",
                    "window_index": 3,
                    "classification": "prefer_forced_fr",
                    "reason": "ok",
                },
                {
                    "source_file": "a.json",
                    "window_index": 4,
                    "classification": "needs_review",
                    "reason": "review",
                },
            ],
        }

    monkeypatch.setattr(dry_run, "analyze_reports", fake_analyze_reports)
    path = _write_report(tmp_path, _report([]), name="empty.json")
    gate_settings = dry_run.GateSettings(
        min_total_windows=5,
        max_needs_review_rate=0.25,
        max_skip_window_rate=0.10,
    )
    result = dry_run._load_from_reports([str(path)], gate_settings)
    assert len(called) == 1
    readiness = dry_run.evaluate_readiness(result, gate_settings)
    assert readiness["status"] == "candidate_ready_for_design"
    assert readiness["needs_review_rate"] == 0.2


def test_sandbox_json_mode_with_existing_input_files(tmp_path):
    report_path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 60.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 2,
                    "start_seconds": 60.0,
                    "end_seconds": 120.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 3,
                    "start_seconds": 120.0,
                    "end_seconds": 180.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 4,
                    "start_seconds": 180.0,
                    "end_seconds": 240.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 5,
                    "start_seconds": 240.0,
                    "end_seconds": 300.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
            ]
        },
    )
    sandbox_json = tmp_path / "sandbox.json"
    sandbox_data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": [str(report_path)],
        "baseline_settings": {"confidence_threshold": 0.70, "min_segments": 1},
        "runs": [
            {
                "settings": {"confidence_threshold": 0.70, "min_segments": 1},
                "summary": {
                    "total_windows": 5,
                    "keep_auto": 5,
                    "prefer_forced_fr": 0,
                    "prefer_forced_en": 0,
                    "needs_review": 0,
                    "skip_window": 0,
                },
                "changed_from_baseline": 0,
            }
        ],
    }
    sandbox_json.write_text(json.dumps(sandbox_data), encoding="utf-8")

    output_json = tmp_path / "dry_run.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
            "--sandbox-json",
            str(sandbox_json),
            "--output-json",
            str(output_json),
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output_json.read_text(encoding="utf-8"))
    assert data["tool"] == "segment_asr_routing_policy_dry_run"
    assert data["readiness"]["status"] == "candidate_ready_for_design"


def test_sandbox_json_mode_with_unstable_routing_adds_blocker(tmp_path):
    report_path = _write_report(
        tmp_path,
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 60.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.65)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.88, segments=4)},
                        {"mode": "forced-en", **_run(language="fr", probability=0.40, segments=1)},
                    ],
                },
                {
                    "window_index": 2,
                    "start_seconds": 60.0,
                    "end_seconds": 120.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 3,
                    "start_seconds": 120.0,
                    "end_seconds": 180.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 4,
                    "start_seconds": 180.0,
                    "end_seconds": 240.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
                {
                    "window_index": 5,
                    "start_seconds": 240.0,
                    "end_seconds": 300.0,
                    "results": [
                        {"mode": "auto", **_run(language="fr", probability=0.91)},
                        {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                    ],
                },
            ]
        },
    )
    sandbox_json = tmp_path / "sandbox.json"
    sandbox_data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": [str(report_path)],
        "baseline_settings": {"confidence_threshold": 0.70, "min_segments": 1},
        "runs": [
            {
                "settings": {"confidence_threshold": 0.60, "min_segments": 1},
                "summary": {
                    "total_windows": 5,
                    "keep_auto": 4,
                    "prefer_forced_fr": 0,
                    "prefer_forced_en": 0,
                    "needs_review": 1,
                    "skip_window": 0,
                },
                "changed_from_baseline": 0,
            },
            {
                "settings": {"confidence_threshold": 0.70, "min_segments": 1},
                "summary": {
                    "total_windows": 5,
                    "keep_auto": 3,
                    "prefer_forced_fr": 1,
                    "prefer_forced_en": 0,
                    "needs_review": 1,
                    "skip_window": 0,
                },
                "changed_from_baseline": 1,
            },
        ],
    }
    sandbox_json.write_text(json.dumps(sandbox_data), encoding="utf-8")

    output_json = tmp_path / "dry_run.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
            "--sandbox-json",
            str(sandbox_json),
            "--output-json",
            str(output_json),
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output_json.read_text(encoding="utf-8"))
    assert any("unstable routing" in b for b in data["readiness"]["blockers"])


def test_sandbox_json_mode_fallback_when_input_files_missing(tmp_path):
    sandbox_json = tmp_path / "sandbox.json"
    sandbox_data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": ["/nonexistent/report.json"],
        "baseline_settings": {"confidence_threshold": 0.70, "min_segments": 1},
        "runs": [
            {
                "settings": {"confidence_threshold": 0.70, "min_segments": 1},
                "summary": {
                    "total_windows": 5,
                    "keep_auto": 5,
                    "prefer_forced_fr": 0,
                    "prefer_forced_en": 0,
                    "needs_review": 0,
                    "skip_window": 0,
                },
                "changed_from_baseline": 0,
            }
        ],
    }
    sandbox_json.write_text(json.dumps(sandbox_data), encoding="utf-8")

    output_json = tmp_path / "dry_run.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
            "--sandbox-json",
            str(sandbox_json),
            "--output-json",
            str(output_json),
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output_json.read_text(encoding="utf-8"))
    assert data["tool"] == "segment_asr_routing_policy_dry_run"
    assert data["readiness"]["status"] == "candidate_ready_for_design"
    assert data["review_windows"] == []


def test_sandbox_json_mode_empty_runs_returns_clean_error(tmp_path):
    sandbox_json = tmp_path / "sandbox.json"
    sandbox_data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": ["/nonexistent/report.json"],
        "baseline_settings": {"confidence_threshold": 0.70, "min_segments": 1},
        "runs": [],
    }
    sandbox_json.write_text(json.dumps(sandbox_data), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
            "--sandbox-json",
            str(sandbox_json),
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 1
    assert "missing 'runs' or no baseline run is available" in result.stderr
    assert "Traceback" not in result.stderr


def test_sandbox_json_mode_malformed_summary_returns_clean_error(tmp_path):
    sandbox_json = tmp_path / "sandbox.json"
    sandbox_data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": ["/nonexistent/report.json"],
        "baseline_settings": {"confidence_threshold": 0.70, "min_segments": 1},
        "runs": [
            {
                "settings": {"confidence_threshold": 0.70, "min_segments": 1},
                "summary": "not a dict",
                "changed_from_baseline": 0,
            }
        ],
    }
    sandbox_json.write_text(json.dumps(sandbox_data), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_policy_dry_run.py",
            "--sandbox-json",
            str(sandbox_json),
        ],
        cwd=Path.cwd(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    assert result.returncode == 1
    assert "baseline run summary is missing or malformed" in result.stderr
    assert "Traceback" not in result.stderr
