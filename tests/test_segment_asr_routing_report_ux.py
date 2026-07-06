from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import batch_worker
import job_api
import pipeline_api
import segment_asr_routing_integration as routing
import transcribe
from batch_worker import BatchConfig, BatchPipeline, TaskState
from output_paths import plan_pipeline_outputs


def _patch_apply_evidence(monkeypatch, tmp_path, windows, duration_seconds=10.0):
    prototype_json = tmp_path / "prototype.json"
    prototype_json.write_text(json.dumps({"windows": []}), encoding="utf-8")
    monkeypatch.setattr(routing, "_probe_media_duration", lambda media_path: duration_seconds)
    monkeypatch.setattr(
        routing,
        "run_prototype_cli",
        lambda args: {"json_path": str(prototype_json), "markdown_path": str(tmp_path / "prototype.md")},
    )
    monkeypatch.setattr(
        routing,
        "analyze_reports",
        lambda input_files, settings: {"summary": {"total_windows": len(windows)}, "windows": windows},
    )


def _full_payload(windows, duration_seconds=10.0):
    return {
        "schema_version": 1,
        "duration_seconds": duration_seconds,
        "window_planning": {"mode": "full_coverage", "window_seconds": duration_seconds, "window_count": len(windows)},
        "coverage": {
            "full_coverage": True,
            "coverage_rate": 1.0,
            "gap_count": 0,
            "covered_seconds": duration_seconds,
            "duration_seconds": duration_seconds,
        },
        "windows": windows,
    }


def _read_report(report_path: str) -> dict:
    return json.loads(Path(report_path).read_text(encoding="utf-8"))


def test_apply_success_report_has_derived_user_decision_and_safety_summaries(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [{"window_index": 1, "start_seconds": 0.0, "end_seconds": 10.0, "classification": "keep_auto"}],
    )
    monkeypatch.setattr(
        routing,
        "get_full_routed_segments",
        lambda **kwargs: _full_payload(
            [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 10.0,
                    "runs": {"auto": {"segments": [{"start": 0.0, "end": 1.0, "text": "real text"}]}},
                }
            ]
        ),
    )
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(mode="apply"),
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

    report = _read_report(result.report_path)
    assert result.status == "apply_complete"
    assert result.user_status == "applied"
    assert result.message == "Segment ASR routing: applied routed SRT successfully."
    assert report["user_summary"]["status"] == "applied"
    assert report["decision_summary"] == {
        "mode": "apply",
        "apply_attempted": True,
        "apply_succeeded": True,
        "subtitle_output_affected": True,
        "fallback_used": False,
        "fallback_reason": None,
    }
    assert report["safety_summary"]["duration_known"] is True
    assert report["safety_summary"]["coverage_full"] is True
    assert report["safety_summary"]["candidate_accepted"] is True
    assert report["status"] == "apply_complete"
    assert report["apply_succeeded"] is True
    assert report["fallback_used"] is False


def test_fallback_report_has_user_summary_and_preserves_machine_fields(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [{"window_index": 1, "start_seconds": 0.0, "end_seconds": 5.0, "classification": "keep_auto"}],
        duration_seconds=5.0,
    )
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(mode="apply"),
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

    report = _read_report(result.report_path)
    assert result.status == "fallback"
    assert result.user_status == "fallback"
    assert report["user_summary"]["status"] == "fallback"
    assert report["decision_summary"]["fallback_used"] is True
    assert report["decision_summary"]["fallback_reason"] == routing.APPLY_SEGMENTS_UNAVAILABLE_REASON
    assert report["safety_summary"]["preview_only_rejected"] is True
    assert report["fallback_used"] is True
    assert report["apply_attempted"] is True
    assert report["subtitle_output_affected"] is False
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"


def test_strict_failure_report_has_clean_user_summary(monkeypatch, tmp_path):
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
            options=routing.SegmentAsrRoutingOptions(mode="apply", strict=True, window_seconds=120, max_windows=80),
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
    report = _read_report(str(report_path))
    assert report["user_summary"]["status"] == "failed"
    assert report["decision_summary"]["apply_attempted"] is True
    assert report["safety_summary"]["guardrail_cap_exceeded"] is True
    assert report["apply_failure_reason"] == "segment routing apply window count 121 exceeds max 80"
    assert "Strict mode prevents fallback" in report["user_summary"]["message"]


def test_cli_off_is_quiet_and_apply_messages_are_concise(monkeypatch, tmp_path, capsys):
    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")
    srt_path = tmp_path / "out" / "movie.small.srt"

    monkeypatch.setattr(transcribe, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(transcribe, "prepare_audio", lambda input_path, work_dir: media)
    monkeypatch.setattr(
        transcribe,
        "transcribe_to_srt",
        lambda **kwargs: (kwargs["srt_path"].parent.mkdir(parents=True, exist_ok=True), kwargs["srt_path"].write_text("1\n", encoding="utf-8")) and None,
    )
    monkeypatch.setattr(sys, "argv", ["transcribe.py", str(media), "--output-dir", str(tmp_path / "out")])
    monkeypatch.setattr(
        transcribe,
        "run_segment_asr_routing",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("off mode must not run routing")),
    )

    assert transcribe.main() == 0
    assert "Segment ASR routing" not in capsys.readouterr().out

    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        transcribe,
        "run_segment_asr_routing",
        lambda **kwargs: routing.SegmentAsrRoutingResult(
            mode="apply",
            report_path=str(report_path),
            status="fallback",
            user_status="fallback",
            message="Segment ASR routing: fell back to normal ASR. Reason: routed segment coverage is incomplete.",
            fallback_used=True,
            fallback_reason=routing.APPLY_COVERAGE_INCOMPLETE_REASON,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["transcribe.py", str(media), "--output-dir", str(tmp_path / "out"), "--segment-asr-routing", "apply"],
    )

    assert transcribe.main() == 0
    output = capsys.readouterr().out
    assert "Segment ASR routing: fell back to normal ASR. Reason: routed segment coverage is incomplete." in output
    assert "Segment ASR routing report:" in output
    assert "windows" not in output


def test_web_help_text_and_log_extraction_are_additive():
    html = Path("web/index.html").read_text(encoding="utf-8")
    assert "Apply is experimental. It may fall back to normal ASR unless strict mode is enabled." in html
    assert "apply - 实验性应用路由结果" in html

    status, report, message = job_api._segment_routing_from_logs(
        [
            "Segment ASR routing: applied routed SRT successfully.",
            "Segment ASR routing report: output/reports/segment_asr_routing/movie.json",
        ]
    )
    assert status == "applied"
    assert report == "output/reports/segment_asr_routing/movie.json"
    assert message == "Segment ASR routing: applied routed SRT successfully."


def test_pipeline_progress_exposes_additive_routing_fields(monkeypatch, tmp_path):
    states_dir = tmp_path / "states"
    states_dir.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states_dir)
    state = {
        "file": "movie.mp4",
        "input_path": str(tmp_path / "movie.mp4"),
        "stage": "completed",
        "status": "completed",
        "segment_asr_routing_status": "fallback",
        "segment_asr_routing_report": "output/reports/segment_asr_routing/movie.json",
        "segment_asr_routing_message": "Segment ASR routing: fell back to normal ASR. Reason: routed segment coverage is incomplete.",
    }
    (states_dir / "movie.state.json").write_text(json.dumps(state), encoding="utf-8")

    progress = pipeline_api.pipeline_progress()
    task = progress["tasks"][0]
    assert task["segment_asr_routing_status"] == "fallback"
    assert task["segment_asr_routing_report"] == "output/reports/segment_asr_routing/movie.json"
    assert "fell back to normal ASR" in task["segment_asr_routing_message"]


def test_pipeline_records_routing_message_without_overwriting_errors(monkeypatch, tmp_path):
    states_dir = tmp_path / "states"
    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", states_dir)
    monkeypatch.setattr(batch_worker, "DIR_ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(batch_worker, "DIR_FAILED", tmp_path / "failed")
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    media = input_dir / "movie.mp4"
    media.write_bytes(b"media")
    work_dir = tmp_path / "work"
    audio = work_dir / "movie.16k.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")

    config = BatchConfig(
        input_dir=input_dir,
        work_dir=work_dir,
        output_dir=tmp_path / "output",
        model_dir=tmp_path / "models",
        model="small",
        translate=False,
        move_completed=False,
        segment_asr_routing="apply",
    )
    pipeline = BatchPipeline(config)
    outputs = plan_pipeline_outputs(config.output_dir, "movie", "small", config.target_language, config.translation_mode)
    outputs.source_srt.parent.mkdir(parents=True, exist_ok=True)
    outputs.source_srt.write_text("normal baseline", encoding="utf-8")
    task = TaskState(file=media.name, input_path=str(media.resolve()), audio_path=str(audio.resolve()))

    monkeypatch.setattr(
        batch_worker,
        "run_segment_asr_routing",
        lambda **kwargs: routing.SegmentAsrRoutingResult(
            mode="apply",
            report_path="output/reports/segment_asr_routing/movie.json",
            status="fallback",
            user_status="fallback",
            message="Segment ASR routing: fell back to normal ASR. Reason: routed segment coverage is incomplete.",
            fallback_used=True,
            fallback_reason=routing.APPLY_COVERAGE_INCOMPLETE_REASON,
        ),
    )

    pipeline._process_one(task)
    saved = TaskState.load(task.state_path())
    assert saved is not None
    assert saved.segment_asr_routing_status == "fallback"
    assert "fell back to normal ASR" in saved.segment_asr_routing_message
    assert saved.error == ""

    failing_task = TaskState(file="failed.mp4", input_path=str(media.resolve()), audio_path=str(audio.resolve()))
    failing_task.error = "existing failure reason"
    monkeypatch.setattr(
        batch_worker,
        "run_segment_asr_routing",
        lambda **kwargs: (_ for _ in ()).throw(routing.SegmentAsrRoutingError("strict cap exceeded")),
    )
    with pytest.raises(routing.SegmentAsrRoutingError):
        pipeline._process_one(failing_task)
    assert failing_task.segment_asr_routing_status == "failed"
    assert "failed in strict mode" in failing_task.segment_asr_routing_message
    assert failing_task.error == "existing failure reason"
