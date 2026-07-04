from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

import segment_asr_routing_sandbox as sandbox
from segment_asr_report_analyzer import Settings, analyze_reports


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


def test_baseline_run_over_single_fixture(tmp_path):
    path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
                {"mode": "forced-en", **_run(language="en", probability=0.52, preview="short")},
            ]
        ),
    )
    result = sandbox.analyze_reports([str(path)], Settings())
    summary = result["summary"]
    assert summary["total_windows"] == 1
    assert summary["keep_auto"] == 1


def test_baseline_run_over_multiple_fixtures(tmp_path):
    p1 = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
            ]
        ),
        name="r1.json",
    )
    p2 = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="en", probability=0.25)},
                {"mode": "forced-en", **_run(language="en", probability=0.86, segments=5)},
            ]
        ),
        name="r2.json",
    )
    result = sandbox.analyze_reports([str(p1), str(p2)], Settings())
    summary = result["summary"]
    assert summary["total_windows"] == 2
    assert summary["keep_auto"] == 1
    assert summary["prefer_forced_en"] == 1


def test_sweep_confidence_threshold_changes_classifications(tmp_path):
    path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.65)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.88, segments=4)},
                {"mode": "forced-en", **_run(language="fr", probability=0.40, segments=1)},
            ]
        ),
    )
    thresholds = [0.60, 0.70]
    mins = [1]
    runs, baseline_settings = sandbox._run_sweep([str(path)], thresholds, mins)

    assert len(runs) == 2
    assert runs[0]["settings"]["confidence_threshold"] == 0.60
    assert runs[1]["settings"]["confidence_threshold"] == 0.70
    # At 0.60 auto is confident (0.65 >= 0.60) → keep_auto
    assert runs[0]["summary"]["keep_auto"] == 1
    # At 0.70 auto is not confident (0.65 < 0.70) → prefer_forced_fr
    assert runs[1]["summary"]["prefer_forced_fr"] == 1
    assert runs[1]["changed_from_baseline"] == 1


def test_sweep_min_segments_changes_classifications(tmp_path):
    path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91, segments=5)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95, segments=5)},
                {"mode": "forced-en", **_run(language="en", probability=0.52, preview="short", segments=5)},
            ]
        ),
    )
    thresholds = [0.70]
    mins = [1, 3]
    runs, _ = sandbox._run_sweep([str(path)], thresholds, mins)

    assert len(runs) == 2
    assert runs[0]["settings"]["min_segments"] == 1
    assert runs[1]["settings"]["min_segments"] == 3
    # At min_segments=1, all runs are usable; auto is confident → keep_auto
    assert runs[0]["summary"]["keep_auto"] == 1
    # At min_segments=3, auto still has 5 segments so is usable and confident → keep_auto
    assert runs[1]["summary"]["keep_auto"] == 1
    # No classification change because auto confidence unchanged
    assert runs[1]["changed_from_baseline"] == 0


def test_changed_from_baseline_is_deterministic(tmp_path):
    path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.65)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.88, segments=4)},
                {"mode": "forced-en", **_run(language="fr", probability=0.40, segments=1)},
            ]
        ),
    )
    thresholds = [0.60, 0.70, 0.80]
    mins = [1]
    runs, _ = sandbox._run_sweep([str(path)], thresholds, mins)

    assert runs[0]["changed_from_baseline"] == 0
    assert runs[1]["changed_from_baseline"] == 1
    assert runs[2]["changed_from_baseline"] == 1


def test_markdown_contains_required_sections_and_limitation_text():
    data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": ["/tmp/a.json"],
        "baseline_settings": {"confidence_threshold": 0.70, "min_segments": 1},
        "runs": [
            {
                "settings": {"confidence_threshold": 0.70, "min_segments": 1},
                "summary": {
                    "total_windows": 1,
                    "keep_auto": 1,
                    "prefer_forced_fr": 0,
                    "prefer_forced_en": 0,
                    "needs_review": 0,
                    "skip_window": 0,
                },
                "changed_from_baseline": 0,
            }
        ],
        "notes": list(sandbox.NOTES),
    }
    markdown = sandbox.render_markdown(data)
    assert "# M7.4 Segment ASR Routing Sandbox Replay" in markdown
    assert "## Inputs" in markdown
    assert "## Baseline Settings" in markdown
    assert "## Parameter Sweep" in markdown
    assert "## Classification Distribution" in markdown
    assert "## Changes From Baseline" in markdown
    assert "## Notes And Limitations" in markdown
    assert sandbox.LIMITATION_TEXT in markdown


def test_json_output_contains_required_fields():
    data = {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": ["/tmp/a.json"],
        "baseline_settings": {"confidence_threshold": 0.70, "min_segments": 1},
        "runs": [
            {
                "settings": {"confidence_threshold": 0.70, "min_segments": 1},
                "summary": {
                    "total_windows": 1,
                    "keep_auto": 1,
                    "prefer_forced_fr": 0,
                    "prefer_forced_en": 0,
                    "needs_review": 0,
                    "skip_window": 0,
                },
                "changed_from_baseline": 0,
            }
        ],
        "notes": list(sandbox.NOTES),
    }
    assert data["schema_version"] == 1
    assert data["tool"] == "segment_asr_routing_sandbox"
    assert data["input_files"] == ["/tmp/a.json"]
    assert "confidence_threshold" in data["baseline_settings"]
    assert "min_segments" in data["baseline_settings"]
    assert len(data["runs"]) == 1
    assert "settings" in data["runs"][0]
    assert "summary" in data["runs"][0]
    assert "total_windows" in data["runs"][0]["summary"]


def test_invalid_sweep_value_returns_clean_error():
    # Test threshold out of range via _resolve_sweep
    ns = argparse.Namespace(
        confidence_threshold=0.70,
        min_segments=1,
        sweep_confidence_thresholds="0.5,1.5",
        sweep_min_segments=None,
    )
    with pytest.raises(ValueError, match="between 0 and 1"):
        sandbox._resolve_sweep(ns)
    # Test negative min segments via _resolve_sweep
    ns2 = argparse.Namespace(
        confidence_threshold=0.70,
        min_segments=1,
        sweep_confidence_thresholds=None,
        sweep_min_segments="-1,2",
    )
    with pytest.raises(ValueError, match="must be >= 0"):
        sandbox._resolve_sweep(ns2)


def test_missing_input_file_returns_clean_error(tmp_path):
    missing = str(tmp_path / "nonexistent.json")
    with pytest.raises(Exception):  # AnalyzerInputError from analyze_reports
        sandbox.analyze_reports([missing], Settings())


def test_cli_end_to_end_with_temp_outputs(tmp_path):
    report_path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.91)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.95)},
            ]
        ),
    )
    output_json = tmp_path / "sandbox.json"
    output_md = tmp_path / "sandbox.md"

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_sandbox.py",
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
    assert "# M7.4 Segment ASR Routing Sandbox Replay" in result.stdout
    assert output_json.is_file()
    assert output_md.is_file()
    data = json.loads(output_json.read_text(encoding="utf-8"))
    assert data["tool"] == "segment_asr_routing_sandbox"


def test_sandbox_reuses_m7_2_analyzer(monkeypatch, tmp_path):
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
                "total_windows": 0,
                "keep_auto": 0,
                "prefer_forced_fr": 0,
                "prefer_forced_en": 0,
                "needs_review": 0,
                "skip_window": 0,
            },
            "windows": [],
        }

    monkeypatch.setattr(sandbox, "analyze_reports", fake_analyze_reports)
    path = _write_report(tmp_path, _report([]), name="empty.json")
    thresholds = [0.60, 0.70]
    mins = [1, 2]
    runs, _ = sandbox._run_sweep([str(path)], thresholds, mins)

    assert len(called) == 4
    assert called[0] == ([str(path)], 0.60, 1)
    assert called[1] == ([str(path)], 0.60, 2)
    assert called[2] == ([str(path)], 0.70, 1)
    assert called[3] == ([str(path)], 0.70, 2)


def test_expand_input_paths_deduplicates_and_preserves_order(tmp_path):
    p1 = _write_report(tmp_path, _report([]), name="a.json")
    p2 = _write_report(tmp_path, _report([]), name="b.json")
    p3 = _write_report(tmp_path, _report([]), name="c.json")

    inputs = [str(p1), str(p2), str(p1), str(p3)]
    expanded = sandbox.expand_input_paths(inputs)
    expected = [str(Path(p).resolve()) for p in (p1, p2, p3)]
    assert expanded == expected


def test_sweep_cli_produces_multiple_runs(tmp_path):
    report_path = _write_report(
        tmp_path,
        _report(
            [
                {"mode": "auto", **_run(language="fr", probability=0.65)},
                {"mode": "forced-fr", **_run(language="fr", probability=0.88, segments=4)},
                {"mode": "forced-en", **_run(language="fr", probability=0.40, segments=1)},
            ]
        ),
    )
    output_json = tmp_path / "sweep.json"

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "src/tools/segment_asr_routing_sandbox.py",
            str(report_path),
            "--sweep-confidence-thresholds",
            "0.60,0.70",
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
    assert len(data["runs"]) == 2
    assert data["runs"][0]["changed_from_baseline"] == 0
    assert data["runs"][1]["changed_from_baseline"] == 1


def test_glob_expansion_supports_wildcards(tmp_path):
    for name in ("x1.json", "x2.json", "y1.json"):
        _write_report(tmp_path, _report([]), name=name)
    pattern = str(tmp_path / "x*.json")
    expanded = sandbox.expand_input_paths([pattern])
    assert len(expanded) == 2
    assert all("x" in Path(p).name for p in expanded)


def test_sandbox_runs_real_m7_3_fixtures():
    """Use actual M7.3 golden fixtures to verify deterministic sandbox output."""
    fixture_dir = Path(__file__).parent / "fixtures" / "asr_evidence"
    mapping = fixture_dir / "mapping_shape.json"
    low_conf = fixture_dir / "low_conf_auto_strong_fr.json"
    assert mapping.is_file(), f"Missing fixture: {mapping}"
    assert low_conf.is_file(), f"Missing fixture: {low_conf}"

    input_files = [str(mapping), str(low_conf)]
    result = sandbox.analyze_reports(input_files, Settings())
    summary = result["summary"]
    assert summary["total_windows"] == 2
    # mapping_shape.json: auto confident fr, matching forced-fr usable
    windows = result["windows"]
    assert windows[0]["classification"] == "keep_auto"
    # low_conf_auto_strong_fr.json: auto weak, forced-fr strong
    assert windows[1]["classification"] == "prefer_forced_fr"
    # Deterministic: same settings should always give same classification
    result2 = sandbox.analyze_reports(input_files, Settings())
    assert [w["classification"] for w in result2["windows"]] == [w["classification"] for w in windows]


def test_sandbox_sweep_over_real_m7_3_fixtures_changes():
    """Sweep confidence threshold over M7.3 fixtures and observe classification changes."""
    fixture_dir = Path(__file__).parent / "fixtures" / "asr_evidence"
    mapping = fixture_dir / "mapping_shape.json"
    low_conf = fixture_dir / "low_conf_auto_strong_fr.json"
    assert mapping.is_file() and low_conf.is_file()

    input_files = [str(mapping), str(low_conf)]
    thresholds = [0.70, 0.95]
    mins = [1]
    runs, _ = sandbox._run_sweep(input_files, thresholds, mins)

    assert len(runs) == 2
    # baseline 0.70: mapping_shape (0.93 >= 0.70) → keep_auto, low_conf (0.3 < 0.70) → prefer_forced_fr
    assert runs[0]["changed_from_baseline"] == 0
    # 0.95: mapping_shape (0.93 < 0.95) → prefer_forced_fr (since forced-fr is strong), low_conf still prefer_forced_fr
    assert runs[1]["changed_from_baseline"] == 1  # mapping_shape changed from keep_auto to prefer_forced_fr
