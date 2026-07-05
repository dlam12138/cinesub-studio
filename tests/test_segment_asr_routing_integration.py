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


def _patch_apply_evidence(monkeypatch, tmp_path, windows):
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
            "summary": {"total_windows": len(windows)},
            "windows": windows,
        },
    )


def _candidate_files(report_root: Path) -> list[Path]:
    return list((report_root / routing.REPORT_DIR_NAME / "candidates").glob("*.candidate.srt"))


def test_transcribe_default_segment_routing_is_off(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["transcribe.py", "movie.wav"])

    args = transcribe.parse_args()

    assert args.segment_asr_routing == "off"
    assert args.segment_routing_confidence_threshold == 0.70
    assert args.segment_routing_min_segments == 1
    assert args.segment_routing_strict is False


def test_batch_config_default_segment_routing_is_off():
    config = BatchConfig()

    assert config.segment_asr_routing == "off"
    assert config.segment_routing_confidence_threshold == 0.70
    assert config.segment_routing_min_segments == 1
    assert config.segment_routing_strict is False


def test_off_mode_writes_no_report_and_does_not_call_m7(monkeypatch, tmp_path):
    called = {"prototype": False}

    def fail_if_called(args):
        called["prototype"] = True
        raise AssertionError("off mode must not run M7 prototype")

    monkeypatch.setattr(routing, "run_prototype_cli", fail_if_called)

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(mode="off"),
        media_path=tmp_path / "movie.mkv",
        routing_input_path=tmp_path / "movie.wav",
        report_root=tmp_path / "reports",
        model_name="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )

    assert result.status == "off"
    assert result.report_path == ""
    assert called["prototype"] is False
    assert not (tmp_path / "reports").exists()


def test_dry_run_writes_metadata_and_window_settings(monkeypatch, tmp_path):
    media = tmp_path / "movie.mkv"
    audio = tmp_path / "movie.wav"
    media.write_bytes(b"media")
    audio.write_bytes(b"audio")
    prototype_json = tmp_path / "prototype.json"
    prototype_json.write_text(json.dumps({"windows": []}), encoding="utf-8")

    def fake_prototype(args):
        assert args.samples == routing.DEFAULT_SAMPLE_COUNT
        assert args.sample_every_seconds is None
        assert args.window_seconds == routing.DEFAULT_WINDOW_SECONDS
        assert args.window == []
        assert args.allow_model_download is False
        return {"json_path": str(prototype_json), "markdown_path": str(tmp_path / "prototype.md")}

    def fake_analyze(input_files, settings):
        assert input_files == [str(prototype_json)]
        assert settings.confidence_threshold == 0.8
        assert settings.min_segments == 2
        return {
            "summary": {"total_windows": 1, "keep_auto": 1},
            "windows": [{"window_index": 1, "classification": "keep_auto"}],
        }

    monkeypatch.setattr(routing, "run_prototype_cli", fake_prototype)
    monkeypatch.setattr(routing, "analyze_reports", fake_analyze)

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(
            mode="dry_run",
            confidence_threshold=0.8,
            min_segments=2,
        ),
        media_path=media,
        routing_input_path=audio,
        report_root=tmp_path / "reports",
        model_name="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )

    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["segment_asr_routing_mode"] == "dry_run"
    assert report["subtitle_output_affected"] is False
    assert report["fallback_used"] is False
    assert report["experimental"] is True
    assert report["window_planning"] == {
        "samples": routing.DEFAULT_SAMPLE_COUNT,
        "sample_every_seconds": None,
        "window_seconds": routing.DEFAULT_WINDOW_SECONDS,
        "manual_windows": [],
    }
    assert report["windows"][0]["classification"] == "keep_auto"


def test_dry_run_failure_falls_back_unless_strict(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "run_prototype_cli", lambda args: (_ for _ in ()).throw(RuntimeError("boom")))

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(mode="dry_run"),
        media_path=tmp_path / "movie.mkv",
        routing_input_path=tmp_path / "movie.wav",
        report_root=tmp_path / "reports",
        model_name="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )

    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert result.status == "fallback"
    assert report["fallback_used"] is True
    assert "boom" in report["fallback_reason"]
    assert report["subtitle_output_affected"] is False


def test_strict_dry_run_failure_raises_controlled_error(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "run_prototype_cli", lambda args: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(routing.SegmentAsrRoutingError, match="dry_run failed"):
        routing.run_segment_asr_routing(
            options=routing.SegmentAsrRoutingOptions(mode="dry_run", strict=True),
            media_path=tmp_path / "movie.mkv",
            routing_input_path=tmp_path / "movie.wav",
            report_root=tmp_path / "reports",
            model_name="small",
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )


def test_apply_success_writes_routed_report_and_affects_subtitle(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [
            {
                "window_index": 1,
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "classification": "prefer_forced_fr",
            }
        ],
    )
    monkeypatch.setattr(
        routing,
        "get_full_routed_segments",
        lambda **kwargs: {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 10.0,
                    "end_seconds": 20.0,
                    "runs": {
                        "auto": {"segments": [{"start": 0.0, "end": 1.0, "text": "auto"}]},
                        "forced-fr": {"segments": [{"start": 1.0, "end": 2.0, "text": "bonjour"}]},
                    },
                }
            ]
        },
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
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))

    assert result.status == "apply_complete"
    assert result.subtitle_output_affected is True
    assert result.fallback_used is False
    assert report["subtitle_output_affected"] is True
    assert report["fallback_used"] is False
    assert report["apply_attempted"] is True
    assert report["apply_succeeded"] is True
    assert report["full_routed_segments_available"] is True
    assert report["preview_only_rejected"] is False
    assert report["candidate_accepted"] is True
    assert report["metadata"]["routed_srt_path"] == str(final_srt.resolve())
    assert report["candidate_srt_path"]
    assert not Path(report["candidate_srt_path"]).exists()
    assert report["assembler"]["selected_run_counts"] == {"forced-fr": 1}
    assert result.routed_srt_path == str(final_srt.resolve())
    assert _candidate_files(tmp_path / "reports") == []
    assert "bonjour" in final_srt.read_text(encoding="utf-8")
    assert "normal baseline" not in final_srt.read_text(encoding="utf-8")


def test_apply_missing_full_segments_falls_back_with_report(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [
            {
                "window_index": 1,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "classification": "keep_auto",
            }
        ],
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
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))

    assert result.status == "fallback"
    assert result.fallback_used is True
    assert result.subtitle_output_affected is False
    assert result.fallback_reason == routing.APPLY_SEGMENTS_UNAVAILABLE_REASON
    assert report["apply_attempted"] is True
    assert report["apply_succeeded"] is False
    assert report["full_routed_segments_available"] is False
    assert report["preview_only_rejected"] is True
    assert report["candidate_accepted"] is False
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"


def test_apply_zero_cue_candidate_preserves_normal_srt_on_fallback(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [
            {
                "window_index": 1,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "classification": "keep_auto",
            }
        ],
    )
    monkeypatch.setattr(
        routing,
        "get_full_routed_segments",
        lambda **kwargs: {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "runs": {
                        "auto": {"segments": [{"start": 2.0, "end": 2.0, "text": "invalid"}]},
                    },
                }
            ]
        },
    )
    report_root = tmp_path / "reports"
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    result = routing.run_segment_asr_routing(
        options=routing.SegmentAsrRoutingOptions(mode="apply"),
        media_path=tmp_path / "movie.mkv",
        routing_input_path=tmp_path / "movie.wav",
        report_root=report_root,
        model_name="small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
        normal_srt_path=final_srt,
        routed_srt_path=final_srt,
    )
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))

    assert result.status == "fallback"
    assert result.subtitle_output_affected is False
    assert result.fallback_reason == routing.APPLY_SEGMENTS_UNAVAILABLE_REASON
    assert report["subtitle_output_affected"] is False
    assert report["candidate_accepted"] is False
    assert report["candidate_srt_path"]
    assert not Path(report["candidate_srt_path"]).exists()
    assert _candidate_files(report_root) == []
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"


def test_strict_apply_zero_cue_candidate_preserves_normal_srt(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [
            {
                "window_index": 1,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "classification": "keep_auto",
            }
        ],
    )
    monkeypatch.setattr(
        routing,
        "get_full_routed_segments",
        lambda **kwargs: {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "runs": {
                        "auto": {"segments": [{"start": 2.0, "end": 2.0, "text": "invalid"}]},
                    },
                }
            ]
        },
    )
    report_root = tmp_path / "reports"
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    with pytest.raises(routing.SegmentAsrRoutingError, match="no routed subtitle output was accepted"):
        routing.run_segment_asr_routing(
            options=routing.SegmentAsrRoutingOptions(mode="apply", strict=True),
            media_path=tmp_path / "movie.mkv",
            routing_input_path=tmp_path / "movie.wav",
            report_root=report_root,
            model_name="small",
            device="cpu",
            compute_type="int8",
            local_files_only=True,
            normal_srt_path=final_srt,
            routed_srt_path=final_srt,
        )

    reports = list((report_root / routing.REPORT_DIR_NAME).glob("*.segment_asr_routing.json"))
    assert reports
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "apply_failed"
    assert report["subtitle_output_affected"] is False
    assert report["candidate_accepted"] is False
    assert report["candidate_srt_path"]
    assert not Path(report["candidate_srt_path"]).exists()
    assert _candidate_files(report_root) == []
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"


def test_strict_apply_failure_raises_controlled_error_and_writes_failure_report(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [
            {
                "window_index": 1,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "classification": "keep_auto",
            }
        ],
    )
    report_root = tmp_path / "reports"
    final_srt = tmp_path / "movie.srt"
    final_srt.write_text("normal baseline", encoding="utf-8")

    with pytest.raises(routing.SegmentAsrRoutingError, match="no routed subtitle output was accepted"):
        routing.run_segment_asr_routing(
            options=routing.SegmentAsrRoutingOptions(mode="apply", strict=True),
            media_path=tmp_path / "movie.mkv",
            routing_input_path=tmp_path / "movie.wav",
            report_root=report_root,
            model_name="small",
            device="cpu",
            compute_type="int8",
            local_files_only=True,
            normal_srt_path=final_srt,
            routed_srt_path=final_srt,
        )

    reports = list((report_root / routing.REPORT_DIR_NAME).glob("*.segment_asr_routing.json"))
    assert reports
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "apply_failed"
    assert report["fallback_used"] is False
    assert report["apply_succeeded"] is False
    assert report["candidate_accepted"] is False
    assert report["strict_failure_note"] == "no routed subtitle output was accepted"
    assert final_srt.read_text(encoding="utf-8") == "normal baseline"


def test_apply_rejects_preview_only_payload(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [
            {
                "window_index": 1,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "classification": "keep_auto",
            }
        ],
    )
    monkeypatch.setattr(
        routing,
        "get_full_routed_segments",
        lambda **kwargs: {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "runs": {
                        "auto": {
                            "segment_count": 1,
                            "text_preview": "preview must not become subtitle",
                        }
                    },
                }
            ]
        },
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
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))

    assert result.status == "fallback"
    assert report["preview_only_rejected"] is True
    assert "preview must not become subtitle" not in final_srt.read_text(encoding="utf-8")


def test_apply_needs_review_uses_auto_and_skip_window_is_counted(monkeypatch, tmp_path):
    _patch_apply_evidence(
        monkeypatch,
        tmp_path,
        [
            {
                "window_index": 1,
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "classification": "needs_review",
            },
            {
                "window_index": 2,
                "start_seconds": 5.0,
                "end_seconds": 10.0,
                "classification": "skip_window",
            },
        ],
    )
    monkeypatch.setattr(
        routing,
        "get_full_routed_segments",
        lambda **kwargs: {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "runs": {
                        "auto": {"segments": [{"start": 0.0, "end": 1.0, "text": "auto usable"}]},
                        "forced-fr": {"segments": [{"start": 0.0, "end": 1.0, "text": "forced"}]},
                    },
                },
                {
                    "window_index": 2,
                    "start_seconds": 5.0,
                    "end_seconds": 10.0,
                    "runs": {},
                },
            ]
        },
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
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))

    assert result.status == "apply_complete"
    assert "auto usable" in final_srt.read_text(encoding="utf-8")
    assert "forced" not in final_srt.read_text(encoding="utf-8")
    assert report["assembler"]["selected_run_counts"]["auto"] == 1
    assert report["assembler"]["skipped_window_count"] == 1


def test_invalid_routing_options_rejected_cleanly(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["transcribe.py", "movie.wav", "--segment-asr-routing", "bad"])

    with pytest.raises(SystemExit):
        transcribe.parse_args()

    with pytest.raises(routing.SegmentAsrRoutingError, match="between 0 and 1"):
        routing.validate_options(
            routing.SegmentAsrRoutingOptions(mode="dry_run", confidence_threshold=1.5)
        )


def test_web_single_job_defaults_and_command_pass_through(monkeypatch, tmp_path):
    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")
    default_job = job_api.create_job({"path": str(media)})

    assert default_job["options"]["segment_asr_routing"] == "off"

    dry_job = job_api.create_job(
        {
            "path": str(media),
            "segment_asr_routing": "dry_run",
            "segment_routing_confidence_threshold": "0.81",
            "segment_routing_min_segments": "3",
            "segment_routing_strict": "on",
        }
    )
    monkeypatch.setattr(job_api, "set_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(job_api, "_job_env", lambda: {})
    captured = {}

    class FakeProcess:
        stdout = iter(["ok\n"])

        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(job_api.subprocess, "Popen", fake_popen)

    job_api.run_job(dry_job["id"])

    command = captured["command"]
    assert "--segment-asr-routing" in command
    assert command[command.index("--segment-asr-routing") + 1] == "dry_run"
    assert "--segment-routing-strict" in command

    captured.clear()
    job_api.run_job(default_job["id"])
    assert "--segment-asr-routing" not in captured["command"]


def test_pipeline_command_passes_non_default_routing(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline_api, "_active_provider_id", lambda: "")
    monkeypatch.setattr(pipeline_api, "_active_language_profile_id", lambda: "")

    command = pipeline_api._build_background_command(
        action="run",
        provider_id="",
        language_profile_id="",
        input_dir="",
        model="small",
        device="auto",
        compute_type="",
        translate_enabled=True,
        language="",
        local_files_only=False,
        subtitle_formats=["srt"],
        ass_style_id="clean-cn",
        segment_asr_routing="dry_run",
        segment_routing_confidence_threshold=0.82,
        segment_routing_min_segments=4,
        segment_routing_strict=True,
    )

    assert "--segment-asr-routing" in command
    assert command[command.index("--segment-asr-routing") + 1] == "dry_run"
    assert "--segment-routing-confidence-threshold" in command
    assert "--segment-routing-min-segments" in command
    assert "--segment-routing-strict" in command


def test_batch_completed_skip_not_changed_by_routing(monkeypatch, tmp_path):
    states_dir = tmp_path / "states"
    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", states_dir)
    monkeypatch.setattr(batch_worker, "DIR_ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(batch_worker, "DIR_FAILED", tmp_path / "failed")
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    media = input_dir / "movie.mp4"
    media.write_bytes(b"media")
    pipeline = BatchPipeline(
        BatchConfig(
            input_dir=input_dir,
            work_dir=tmp_path / "work",
            output_dir=tmp_path / "output",
            model_dir=tmp_path / "models",
            model="small",
            translate=False,
            segment_asr_routing="dry_run",
        )
    )
    task = TaskState(file=media.name, input_path=str(media.resolve()), status="completed")
    task.save()
    for output in pipeline.required_final_outputs(task):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ok")

    assert pipeline.scan() == []
