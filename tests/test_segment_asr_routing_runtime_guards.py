from __future__ import annotations

import json
from pathlib import Path

import pytest

import segment_asr_routing_integration as routing


def test_runtime_guardrails_count_final_partial_window():
    guardrails, reason = routing.build_apply_runtime_guardrails(
        duration_seconds=125,
        window_seconds=60,
        max_windows=80,
        allow_large_run=False,
    )

    assert reason is None
    assert guardrails["planned_window_count"] == 3
    assert guardrails["estimated_asr_calls"] == 9
    assert guardrails["cap_exceeded"] is False


def test_runtime_guardrails_are_deterministic():
    first, first_reason = routing.build_apply_runtime_guardrails(
        duration_seconds=7200,
        window_seconds=120,
        max_windows=80,
        allow_large_run=False,
    )
    second, second_reason = routing.build_apply_runtime_guardrails(
        duration_seconds=7200.0,
        window_seconds=120.0,
        max_windows=80,
        allow_large_run=False,
    )

    assert first_reason is None
    assert second_reason is None
    assert first == second
    assert first["planned_window_count"] == 60
    assert first["estimated_asr_calls"] == 180


def test_runtime_guardrails_unknown_duration_returns_reason():
    guardrails, reason = routing.build_apply_runtime_guardrails(
        duration_seconds=None,
        window_seconds=120,
        max_windows=80,
        allow_large_run=False,
    )

    assert reason == routing.APPLY_UNKNOWN_DURATION_REASON
    assert guardrails["planned_window_count"] is None
    assert guardrails["estimated_asr_calls"] is None


def test_runtime_guardrails_cap_exceeded_returns_reason():
    guardrails, reason = routing.build_apply_runtime_guardrails(
        duration_seconds=14520,
        window_seconds=120,
        max_windows=80,
        allow_large_run=False,
    )

    assert reason == "segment routing apply window count 121 exceeds max 80"
    assert guardrails["planned_window_count"] == 121
    assert guardrails["estimated_asr_calls"] == 363
    assert guardrails["cap_exceeded"] is True


def test_runtime_guardrails_allow_large_run_bypasses_cap_only():
    guardrails, reason = routing.build_apply_runtime_guardrails(
        duration_seconds=14520,
        window_seconds=120,
        max_windows=80,
        allow_large_run=True,
    )

    assert reason is None
    assert guardrails["planned_window_count"] == 121
    assert guardrails["cap_exceeded"] is True
    assert guardrails["allow_large_run"] is True


def test_runtime_guardrails_reject_invalid_values():
    with pytest.raises(routing.SegmentAsrRoutingError, match="window-seconds"):
        routing.build_apply_runtime_guardrails(
            duration_seconds=60,
            window_seconds=0,
            max_windows=80,
            allow_large_run=False,
        )

    with pytest.raises(routing.SegmentAsrRoutingError, match="max-windows"):
        routing.build_apply_runtime_guardrails(
            duration_seconds=60,
            window_seconds=120,
            max_windows=0,
            allow_large_run=False,
        )


def test_apply_cap_exceeded_falls_back_before_full_asr(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "_probe_media_duration", lambda media_path: 14520.0)
    monkeypatch.setattr(
        routing,
        "run_prototype_cli",
        lambda args: (_ for _ in ()).throw(AssertionError("full routed ASR must not start")),
    )
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(mode="apply", window_seconds=120, max_windows=80),
        media_path=tmp_path / "movie.mkv",
        routing_input_path=tmp_path / "movie.wav",
        report_root=tmp_path / "reports",
        model_name="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
        normal_srt_path=final_srt,
        routed_srt_path=final_srt,
    )

    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert result.status == "fallback"
    assert result.fallback_reason == "segment routing apply window count 121 exceeds max 80"
    assert report["runtime_guardrails"]["cap_exceeded"] is True
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"


def test_strict_apply_cap_exceeded_fails_before_full_asr(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "_probe_media_duration", lambda media_path: 14520.0)
    monkeypatch.setattr(
        routing,
        "run_prototype_cli",
        lambda args: (_ for _ in ()).throw(AssertionError("full routed ASR must not start")),
    )
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    with pytest.raises(routing.SegmentAsrRoutingError, match="no routed subtitle output was accepted"):
        routing.run_segment_asr_routing(
            options=routing.SegmentAsrRoutingOptions(
                mode="apply",
                strict=True,
                window_seconds=120,
                max_windows=80,
            ),
            media_path=tmp_path / "movie.mkv",
            routing_input_path=tmp_path / "movie.wav",
            report_root=tmp_path / "reports",
            model_name="small",
            device="cpu",
            compute_type="int8",
            local_files_only=True,
            normal_srt_path=final_srt,
            routed_srt_path=final_srt,
        )

    report_path = next((tmp_path / "reports" / routing.REPORT_DIR_NAME).glob("*.segment_asr_routing.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["apply_failure_reason"] == "segment routing apply window count 121 exceeds max 80"
    assert report["runtime_guardrails"]["cap_exceeded"] is True
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"


def test_allow_large_run_still_rejects_incomplete_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "_probe_media_duration", lambda media_path: 14520.0)
    prototype_json = tmp_path / "prototype.json"
    prototype_json.write_text(json.dumps({"windows": []}), encoding="utf-8")
    monkeypatch.setattr(
        routing,
        "run_prototype_cli",
        lambda args: {"json_path": str(prototype_json), "markdown_path": str(tmp_path / "prototype.md")},
    )
    monkeypatch.setattr(
        routing,
        "analyze_reports",
        lambda input_files, settings: {
            "summary": {"total_windows": 1},
            "windows": [{"window_index": 1, "classification": "keep_auto"}],
        },
    )
    monkeypatch.setattr(
        routing,
        "get_full_routed_segments",
        lambda **kwargs: {
            "schema_version": 1,
            "duration_seconds": 14520.0,
            "window_planning": {"mode": "full_coverage", "window_seconds": 120.0, "window_count": 1},
            "coverage": {
                "full_coverage": False,
                "coverage_rate": 0.5,
                "gap_count": 1,
                "covered_seconds": 60.0,
                "duration_seconds": 14520.0,
            },
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 120.0,
                    "runs": {"auto": {"segments": [{"start": 0.0, "end": 1.0, "text": "auto"}]}},
                }
            ],
        },
    )
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(
            mode="apply",
            window_seconds=120,
            max_windows=80,
            allow_large_run=True,
        ),
        media_path=tmp_path / "movie.mkv",
        routing_input_path=tmp_path / "movie.wav",
        report_root=tmp_path / "reports",
        model_name="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
        normal_srt_path=final_srt,
        routed_srt_path=final_srt,
    )

    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert result.status == "fallback"
    assert result.fallback_reason == routing.APPLY_COVERAGE_INCOMPLETE_REASON
    assert report["runtime_guardrails"]["cap_exceeded"] is True
    assert report["runtime_guardrails"]["allow_large_run"] is True
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"
