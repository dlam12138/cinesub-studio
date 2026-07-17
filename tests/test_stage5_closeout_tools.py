from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

import asr_benchmark as benchmark
import prepare_stage5_review_pack as review_pack
import stage5_go_no_go as decision


def _run(cer: float, *, missed: float = 0, duplicate: float = 0, timing: float = 0.1):
    return {
        "status": "completed",
        "metrics": {
            "cer": cer, "wer": cer, "missed_cue_rate": missed,
            "duplicate_cue_rate": duplicate,
            "timing_start_offset_seconds": {"p95": timing},
            "timing_end_offset_seconds": {"p95": timing},
        },
        "performance": {
            "elapsed_seconds": 1, "real_time_factor": 0.1,
            "peak_working_set_bytes": 1, "peak_gpu_memory_mib": None,
        },
        "code_switch_metrics": {
            "mer": cer, "post_switch_first_token_error_rate": 0,
            "language_span_recall": None, "warnings": [],
        },
        "paired_performance": {
            "baseline_elapsed_seconds": 10,
            "candidate_incremental_seconds": 2,
        },
    }


def _report(cer: float, candidate: bool = False) -> dict:
    config = {"id": "large-v3-cuda-float16", "model": "large-v3"}
    if candidate:
        config["candidate_id"] = "local-retry-selective-v2"
    return {
        "report_type": "asr_benchmark", "status": "completed",
        "corpus_fingerprint": "FIXED", "local_files_only": True,
        "configurations": [config],
        "results": [{
            "sample_id": "target", "configuration_id": config["id"],
            "acoustic_tags": ["noise"], "runs": [_run(cer)],
        }],
    }


def test_go_no_go_requires_two_rounds_and_manual_review(tmp_path: Path) -> None:
    baseline = _report(0.2)
    candidate = _report(0.15, candidate=True)
    pending = decision.build_decision(
        baseline, [candidate, candidate], config_id="large-v3-cuda-float16",
        manual_review=None,
    )
    assert pending["decision"] == "pending_manual_review"
    assert pending["apply_allowed"] is False
    manual = tmp_path / "manual.json"
    manual.write_text(json.dumps({
        "schema_version": 1,
        "items": [
            {"category": category, "review_status": "completed", "candidate_net_degradation": False}
            for category in review_pack.CATEGORIES
        ],
    }), encoding="utf-8")
    approved = decision.build_decision(
        baseline, [candidate, candidate], config_id="large-v3-cuda-float16",
        manual_review=manual,
    )
    assert approved["decision"] == "go"
    assert approved["production_default_must_remain_off"] is True


def test_go_no_go_rejects_regression_and_fingerprint_mismatch() -> None:
    baseline = _report(0.2)
    candidate = _report(0.19, candidate=True)
    result = decision.evaluate_round(baseline, candidate, config_id="large-v3-cuda-float16")
    assert result["passed"] is False
    candidate["corpus_fingerprint"] = "OTHER"
    with pytest.raises(decision.DecisionError, match="fingerprints"):
        decision.evaluate_round(baseline, candidate, config_id="large-v3-cuda-float16")


def test_mixed_route_screen_stays_no_go_without_language_metric_evidence() -> None:
    result = decision.evaluate_mixed_route_screen({
        "routing_dry_run": [{
            "status": "dry_run_complete", "subtitle_output_affected": False,
            "classification_counts": {
                "total_windows": 10, "needs_review": 10,
                "prefer_forced_fr": 0, "prefer_forced_en": 0,
            },
        }],
    })
    assert result["promotion_ready"] is False
    assert result["needs_review_rate"] == 1
    assert any("MER" in blocker for blocker in result["blockers"])


def test_atomic_checkpoint_resume_and_corruption(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    signature = benchmark._checkpoint_signature(
        fingerprint="A", configs=[{"id": "cpu"}], repeat_count=1,
        candidate_id=None, routing_only=False,
    )
    benchmark._save_checkpoint(path, signature, [{"sample_id": "s"}], [])
    assert benchmark._load_checkpoint(path, signature)["results"][0]["sample_id"] == "s"
    with pytest.raises(benchmark.BenchmarkError, match="differs"):
        benchmark._load_checkpoint(path, {**signature, "corpus_fingerprint": "B"})
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(benchmark.BenchmarkError, match="unreadable"):
        benchmark._load_checkpoint(path, signature)


def test_benchmark_resume_reuses_completed_sample_configuration(tmp_path: Path, monkeypatch) -> None:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"media")
    reference = tmp_path / "reference.srt"
    reference.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "configurations": [{
            "id": "cpu", "model": "small", "device": "cpu", "compute_type": "int8",
        }],
        "samples": [{
            "id": "sample", "media": str(media), "reference_srt": str(reference),
            "language": "en", "acoustic_tags": ["clean"], "authorization": "owned",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(benchmark, "preflight", lambda samples, configs: [])
    monkeypatch.setattr(benchmark, "_prepare_audio", lambda sample, root: (media, 60.0))
    calls = []

    def fake_worker(*args, **kwargs):
        calls.append(1)
        return _run(0.1)

    monkeypatch.setattr(benchmark, "run_worker_process", fake_worker)
    common = dict(
        manifest=str(manifest), output_dir=str(tmp_path / "reports"), sample=[], config=[],
        repeat=1, dry_run=False, baseline=None, candidate=None,
        include_routing_dry_run=False, routing_only=False,
        routing_timeout_seconds=None, run_id="resume-test",
    )
    assert benchmark.run_benchmark(argparse.Namespace(**common, resume=False))["status"] == "completed"
    assert benchmark.run_benchmark(argparse.Namespace(**common, resume=True))["status"] == "completed"
    assert len(calls) == 1


def test_routing_timeout_is_a_redacted_failed_item(tmp_path: Path, monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

    monkeypatch.setattr(benchmark.subprocess, "run", timeout)
    result = benchmark.run_routing_dry_run(
        {"id": "mixed"},
        {"model": "small", "device": "cpu", "compute_type": "int8", "beam_size": 5},
        tmp_path / "audio.wav", tmp_path, timeout_seconds=1,
    )
    assert result["status"] == "failed"
    assert result["failure_category"] == "timeout"
    assert result["subtitle_output_affected"] is False


def test_review_initializer_records_unverified_mixed_media_gap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(review_pack, "PROJECT_ROOT", tmp_path)
    (tmp_path / "archive").mkdir()
    path = review_pack.initialize_manifest(tmp_path / "review")
    data = json.loads(path.read_text(encoding="utf-8"))
    mixed = next(item for item in data["items"] if item["category"] == "natural_mixed_language")
    assert mixed["media"] is None
    assert len(data["items"]) == 5


def test_review_pack_clips_and_anonymizes_without_public_paths(tmp_path: Path, monkeypatch) -> None:
    media = tmp_path / "media.mp4"
    media.write_bytes(b"media")
    baseline = tmp_path / "baseline.srt"
    candidate = tmp_path / "candidate.srt"
    content = "1\n00:00:00,000 --> 00:00:02,000\nhello\n"
    baseline.write_text(content, encoding="utf-8")
    candidate.write_text(content.replace("hello", "bonjour"), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "items": [{
            "id": category, "category": category, "media": str(media),
            "start_seconds": 0, "duration_seconds": 60,
            "baseline_srt": str(baseline), "candidate_srt": str(candidate),
        } for category in review_pack.CATEGORIES],
    }), encoding="utf-8")
    monkeypatch.setattr(review_pack, "find_ffmpeg", lambda root: tmp_path / "ffmpeg.exe")

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"wav")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(review_pack.subprocess, "run", fake_run)
    output = tmp_path / "out"
    summary = review_pack.build_pack(manifest, output)
    assert summary["ready_count"] == 5
    public = (output / "review_pack_summary.json").read_text(encoding="utf-8")
    assert str(media) not in public
    assert "hello" not in public and "bonjour" not in public
    assert (output / "private_mapping.json").is_file()
