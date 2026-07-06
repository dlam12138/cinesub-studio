from __future__ import annotations

import json
from pathlib import Path

import pytest

import segment_asr_smoke_report as smoke


def _write_report(tmp_path: Path, name: str, report: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _base_report(**overrides) -> dict:
    report = {
        "schema_version": 1,
        "report_type": "segment_asr_routing_integration",
        "generated_at": "2026-07-06T10:00:00+00:00",
        "status": "apply_complete",
        "experimental": True,
        "segment_asr_routing_mode": "apply",
        "subtitle_output_affected": True,
        "fallback_used": False,
        "fallback_reason": None,
        "apply_attempted": True,
        "apply_succeeded": True,
        "full_routed_segments_available": True,
        "preview_only_rejected": False,
        "candidate_srt_path": "",
        "candidate_accepted": True,
        "coverage_full": True,
        "coverage_rate": 1.0,
        "gap_count": 0,
        "needs_review_window_count": 0,
        "skip_window_count": 0,
        "selected_run_counts": {"auto": 1},
        "confidence_threshold": 0.7,
        "min_segments": 1,
        "strict": False,
        "window_planning": {"mode": "full_coverage", "window_seconds": 120.0, "window_count": 1},
        "metadata": {
            "media_path": str(Path.home() / "work" / "sample_16k.wav"),
            "routing_input_path": "work/sample_16k.wav",
            "model": "small",
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": True,
            "normal_srt_path": "output/sample.small.srt",
            "routed_srt_path": "output/sample.small.routed.srt",
        },
        "runtime_guardrails": {
            "duration_seconds": 125.0,
            "window_seconds": 120.0,
            "planned_window_count": 2,
            "estimated_asr_calls": 6,
            "max_windows": 20,
            "cap_exceeded": False,
            "allow_large_run": False,
        },
        "coverage": {
            "full_coverage": True,
            "coverage_rate": 1.0,
            "gap_count": 0,
            "covered_seconds": 125.0,
            "duration_seconds": 125.0,
        },
        "user_summary": {
            "status": "applied",
            "title": "Segment ASR routing applied",
            "message": "Routed SRT was accepted after full coverage and candidate validation.",
            "next_action": "Review the generated SRT before using it as final subtitles.",
        },
        "decision_summary": {
            "mode": "apply",
            "apply_attempted": True,
            "apply_succeeded": True,
            "subtitle_output_affected": True,
            "fallback_used": False,
            "fallback_reason": None,
        },
        "safety_summary": {
            "duration_known": True,
            "coverage_full": True,
            "candidate_accepted": True,
            "preview_only_rejected": False,
            "guardrail_cap_exceeded": False,
        },
        "prototype_report": {
            "json_path": "output/reports/segment_asr_routing/prototype/report.json",
            "markdown_path": "output/reports/segment_asr_routing/prototype/report.md",
        },
        "analyzer": {
            "summary": {"total_windows": 1},
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 125.0,
                    "classification": "keep_auto",
                    "selected_run": "auto",
                }
            ],
        },
        "assembler": {"cue_count": 3, "output_path": "output/sample.small.routed.srt"},
    }
    report.update(overrides)
    return report


def test_reads_routing_report_json_and_extracts_safe_metadata(tmp_path):
    report = _base_report()
    path = _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    result_path = smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    assert Path(result_path).exists()
    text = output_md.read_text(encoding="utf-8")
    assert "Segment ASR Routing Smoke Summary" in text
    assert "apply_complete" in text
    assert "apply" in text
    assert "small" in text
    assert "cpu" in text
    assert "125.000" in text or "125" in text


def test_redacts_transcript_text_and_segment_payloads(tmp_path):
    report = _base_report(
        windows=[
            {
                "window_index": 1,
                "start_seconds": 0.0,
                "end_seconds": 125.0,
                "segments": [{"start": 0.0, "end": 2.0, "text": "secret transcript line"}],
                "runs": {
                    "auto": {
                        "full_segments_available": True,
                        "segments": [{"start": 0.0, "end": 2.0, "text": "another secret"}],
                    }
                },
            }
        ]
    )
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "secret transcript line" not in text
    assert "another secret" not in text
    assert '"redacted": true' in text
    assert '"count": 1' in text


def test_writes_markdown_summary_with_table(tmp_path):
    report = _base_report()
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
            title="Custom Smoke Title",
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "# Custom Smoke Title" in text
    assert "| Report | Mode | Status |" in text
    assert "Scenario 1" in text
    assert "Redacted Report Snapshots" in text


def test_handles_fallback_report(tmp_path):
    report = _base_report(
        status="fallback",
        subtitle_output_affected=False,
        apply_succeeded=False,
        fallback_used=True,
        fallback_reason="routed segment coverage is incomplete",
        candidate_accepted=False,
        coverage_full=False,
        coverage_rate=0.5,
        gap_count=1,
        user_summary={
            "status": "fallback",
            "title": "Segment ASR routing fell back to normal ASR",
            "message": "Routed output was not accepted because routed segment coverage is incomplete.",
            "next_action": "Use dry_run or smaller window settings.",
        },
        decision_summary={
            "mode": "apply",
            "apply_attempted": True,
            "apply_succeeded": False,
            "subtitle_output_affected": False,
            "fallback_used": True,
            "fallback_reason": "routed segment coverage is incomplete",
        },
        safety_summary={
            "duration_known": True,
            "coverage_full": False,
            "candidate_accepted": False,
            "preview_only_rejected": True,
            "guardrail_cap_exceeded": False,
        },
    )
    _write_report(tmp_path, "fallback.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "fallback" in text
    assert "routed segment coverage is incomplete" in text
    assert "Applied: attempted" in text or "attempted" in text


def test_handles_strict_failure_report(tmp_path):
    report = _base_report(
        status="apply_failed",
        subtitle_output_affected=False,
        apply_succeeded=False,
        fallback_used=False,
        strict=True,
        candidate_accepted=False,
        coverage_full=False,
        apply_failure_reason="segment routing apply window count 121 exceeds max 80",
        runtime_guardrails={
            "duration_seconds": 14520.0,
            "window_seconds": 120.0,
            "planned_window_count": 121,
            "estimated_asr_calls": 363,
            "max_windows": 80,
            "cap_exceeded": True,
            "allow_large_run": False,
        },
        user_summary={
            "status": "failed",
            "title": "Segment ASR routing failed in strict mode",
            "message": "Strict mode prevents fallback to normal ASR. Reason: segment routing apply window count 121 exceeds max 80.",
            "next_action": "Disable strict mode or inspect the routing report.",
        },
        decision_summary={
            "mode": "apply",
            "apply_attempted": True,
            "apply_succeeded": False,
            "subtitle_output_affected": False,
            "fallback_used": False,
            "fallback_reason": None,
        },
        safety_summary={
            "duration_known": True,
            "coverage_full": False,
            "candidate_accepted": False,
            "preview_only_rejected": False,
            "guardrail_cap_exceeded": True,
        },
    )
    _write_report(tmp_path, "strict.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "apply_failed" in text
    assert "**Strict:** yes" in text
    assert "121" in text
    assert "363" in text
    assert "**Cap exceeded:** yes" in text


def test_handles_missing_optional_fields_cleanly(tmp_path):
    minimal = {
        "report_type": "segment_asr_routing_integration",
        "status": "dry_run_complete",
        "segment_asr_routing_mode": "dry_run",
    }
    _write_report(tmp_path, "minimal.json", minimal)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "dry_run_complete" in text
    assert "dry_run" in text


def test_ignores_non_routing_report_files(tmp_path):
    _write_report(tmp_path, "other.json", {"report_type": "something_else", "status": "ok"})
    _write_report(tmp_path, "routing.json", _base_report(status="dry_run_complete", segment_asr_routing_mode="dry_run"))
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "Generated from 1 report(s)" in text
    assert "something_else" not in text


def test_main_cli_writes_output(tmp_path, capsys):
    report = _base_report()
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    assert smoke.main([str(tmp_path / "*.json"), "--output-md", str(output_md)]) == 0
    assert output_md.exists()
    captured = capsys.readouterr()
    assert "Smoke summary written to" in captured.out


def test_redacts_absolute_media_path(tmp_path):
    report = _base_report(
        metadata={
            "media_path": str(Path.home() / "movies" / "secret_movie.mkv"),
            "routing_input_path": "work/sample_16k.wav",
            "model": "small",
            "device": "cpu",
        }
    )
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "secret_movie.mkv" not in text
    assert "~/movies/secret_movie.mkv" not in text


def test_cli_help_does_not_require_arguments():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-B", "src/tools/segment_asr_smoke_report.py", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0
    assert "--output-md" in result.stdout
    assert "input_patterns" in result.stdout


def test_redacts_top_level_candidate_srt_path(tmp_path):
    report = _base_report(
        candidate_srt_path="output/private_movie.routed.srt",
    )
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "private_movie.routed.srt" not in text
    assert "output/[REDACTED]" in text


def test_redacts_nested_analyzer_run_preview_and_text_preview(tmp_path):
    report = _base_report(
        analyzer={
            "summary": {"total_windows": 1},
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 125.0,
                    "runs": {
                        "auto": {
                            "preview": "sensitive transcript preview text here",
                            "text_preview": "another preview text",
                        }
                    },
                }
            ],
        }
    )
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "sensitive transcript preview text here" not in text
    assert "another preview text" not in text
    assert "[REDACTED]" in text


def test_redacts_nested_arbitrary_path_fields(tmp_path):
    report = _base_report(
        analyzer={
            "summary": {"total_windows": 1},
            "windows": [
                {
                    "window_index": 1,
                    "some_output_path": "output/secret/result.json",
                }
            ],
        },
        metadata={
            "media_path": "work/sample.wav",
            "custom_path": "data/backup/private.mkv",
            "model": "small",
            "device": "cpu",
        },
    )
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "private.mkv" not in text
    assert "result.json" not in text


def test_transcript_text_in_preview_not_leaked(tmp_path):
    report = _base_report(
        analyzer={
            "summary": {"total_windows": 1},
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 125.0,
                    "runs": {
                        "auto": {
                            "preview": "Hello, this is actual spoken dialogue.",
                        }
                    },
                }
            ],
        }
    )
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "Hello, this is actual spoken dialogue." not in text
    assert "actual spoken dialogue" not in text


def test_sensitive_filenames_not_in_markdown(tmp_path):
    report = _base_report(
        candidate_srt_path="output/private_output.srt",
        metadata={
            "media_path": str(Path.home() / "movies" / "secret_movie.mkv"),
            "routing_input_path": "work/sample_16k.wav",
            "model": "small",
            "device": "cpu",
        },
    )
    _write_report(tmp_path, "report.json", report)
    output_md = tmp_path / "summary.md"

    smoke.summarize_smoke_reports(
        smoke.SmokeSummaryOptions(
            input_patterns=[str(tmp_path / "*.json")],
            output_md=str(output_md),
        )
    )

    text = output_md.read_text(encoding="utf-8")
    assert "secret_movie.mkv" not in text
    assert "private_output.srt" not in text
    assert "[REDACTED]" in text
