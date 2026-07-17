from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
import pytest

import asr_benchmark as benchmark


def _write_srt(path: Path, rows: list[tuple[float, float, str]]) -> None:
    def stamp(value: float) -> str:
        milliseconds = round(value * 1000)
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        seconds, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

    blocks = [
        f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}"
        for index, (start, end, text) in enumerate(rows, start=1)
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _manifest(tmp_path: Path) -> Path:
    media = tmp_path / "sample.wav"
    media.write_bytes(b"media")
    reference = tmp_path / "reference.srt"
    _write_srt(reference, [(0, 1, "Bonjour le monde")])
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routing_config_id": "small-cpu-int8",
                "configurations": [
                    {
                        "id": "small-cpu-int8",
                        "model": "small",
                        "device": "cpu",
                        "compute_type": "int8",
                    }
                ],
                "samples": [
                    {
                        "id": "fr-clean",
                        "media": str(media),
                        "reference_srt": str(reference),
                        "language": "fr",
                        "acoustic_tags": ["clean"],
                        "authorization": "owned",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_normalization_and_unknown_tokens() -> None:
    assert benchmark.normalize_for_cer(" Hé, [UNK] MONDE！ ") == "hémonde"
    assert benchmark.normalize_for_wer("Hello, [UNK] WORLD!") == ["hello", "world"]


def test_levenshtein_and_error_rate() -> None:
    assert benchmark.levenshtein_distance("kitten", "sitting") == 3
    assert benchmark.error_rate([], []) == 0
    assert benchmark.error_rate([], ["x"]) == 1
    assert benchmark.error_rate(["a", "b"], ["a"]) == 0.5


def test_parse_srt_accepts_bom_and_skips_invalid_blocks(tmp_path: Path) -> None:
    path = tmp_path / "sample.srt"
    path.write_text(
        "\ufeff1\n00:00:01,000 --> 00:00:02,500\nBonjour\n\ninvalid\n",
        encoding="utf-8",
    )
    assert benchmark.parse_srt(path) == [benchmark.Cue(1, 1.0, 2.5, "Bonjour")]


def test_metrics_cover_cer_wer_missed_duplicates_and_timing() -> None:
    reference = [
        benchmark.Cue(1, 0.0, 1.0, "hello world"),
        benchmark.Cue(2, 3.0, 4.0, "again"),
    ]
    hypothesis = [
        benchmark.Cue(1, 0.1, 1.2, "hello word"),
        benchmark.Cue(2, 1.3, 2.0, "hello word"),
    ]
    metrics = benchmark.calculate_metrics(reference, hypothesis, "en")
    assert metrics["cer"] > 0
    assert metrics["wer"] == 1.0
    assert metrics["missed_cue_count"] == 1
    assert metrics["missed_cue_rate"] == 0.5
    assert metrics["duplicate_cue_count"] == 1
    assert metrics["duplicate_cue_rate"] == 0.5
    assert metrics["timing_start_offset_seconds"]["median"] == 0.1
    assert metrics["timing_end_offset_seconds"]["median"] == 0.2


def test_mixed_language_does_not_report_wer() -> None:
    cue = benchmark.Cue(1, 0, 1, "你好 hello")
    assert benchmark.calculate_metrics([cue], [cue], "mixed")["wer"] is None


def test_load_manifest_normalizes_and_forces_local_only(tmp_path: Path) -> None:
    data = benchmark.load_manifest(_manifest(tmp_path))
    assert data["samples"][0]["id"] == "fr-clean"
    assert data["configurations"][0]["local_files_only"] is True
    assert data["configurations"][0]["beam_size"] == 5
    assert data["configurations"][0]["vad_filter"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data["samples"][0].update(language="xx"), "unsupported language"),
        (lambda data: data["samples"][0].update(authorization=""), "authorization"),
        (lambda data: data["samples"].append(dict(data["samples"][0])), "duplicate sample"),
    ],
)
def test_manifest_validation_errors(tmp_path: Path, mutation, message: str) -> None:
    path = _manifest(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(benchmark.BenchmarkError, match=message):
        benchmark.load_manifest(path)


def test_corpus_fingerprint_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    data = benchmark.load_manifest(_manifest(tmp_path))
    first = benchmark.corpus_fingerprint(data["samples"])
    assert first == benchmark.corpus_fingerprint(list(reversed(data["samples"])))
    data["samples"][0]["media"].write_bytes(b"changed")
    assert first != benchmark.corpus_fingerprint(data["samples"])


def test_aggregate_results_keeps_null_gpu_values() -> None:
    configs = [{"id": "cpu"}]
    results = [
        {
            "configuration_id": "cpu",
            "runs": [
                {
                    "status": "completed",
                    "metrics": {
                        "cer": 0.1,
                        "wer": 0.2,
                        "missed_cue_rate": 0.0,
                        "duplicate_cue_rate": 0.0,
                    },
                    "performance": {
                        "elapsed_seconds": 2.0,
                        "real_time_factor": 0.2,
                        "peak_working_set_bytes": 100,
                        "peak_gpu_memory_mib": None,
                    },
                }
            ],
        }
    ]
    summary = benchmark.aggregate_results(results, configs)["cpu"]
    assert summary["metrics"]["cer"]["mean"] == 0.1
    assert summary["performance"]["peak_gpu_memory_mib"]["mean"] is None


def test_baseline_comparison_requires_matching_corpus() -> None:
    summary = {
        "cpu": {
            "metrics": {"cer": {"mean": 0.2}},
            "performance": {"real_time_factor": {"mean": 0.5}},
        }
    }
    current = {"corpus_fingerprint": "A", "configuration_summaries": summary}
    baseline = {
        "schema_version": 1,
        "corpus_fingerprint": "A",
        "configuration_summaries": {
            "cpu": {
                "metrics": {"cer": {"mean": 0.1}},
                "performance": {"real_time_factor": {"mean": 0.4}},
            }
        },
    }
    compared = benchmark.compare_with_baseline(current, baseline)
    assert compared["compatible_corpus"] is True
    assert compared["configurations"]["cpu"]["metrics"]["cer"] == 0.1
    assert compared["configurations"]["cpu"]["performance"]["real_time_factor"] == 0.1
    baseline["corpus_fingerprint"] = "B"
    assert benchmark.compare_with_baseline(current, baseline)["compatible_corpus"] is False


def test_gpu_sampling_parses_only_target_pid(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="10, 100\n20, 250\n20, 50\n", stderr=""
    )
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *args, **kwargs: completed)
    assert benchmark._gpu_memory_mib(20) == 300
    assert benchmark._gpu_memory_mib(30) is None


def test_routing_helper_is_always_dry_run_and_redacted(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    def fake_run(command, **kwargs):
        seen.extend(command)
        output_dir = Path(command[command.index("--output-dir") + 1])
        report_dir = output_dir / "reports"
        report_dir.mkdir(parents=True)
        (report_dir / "zzz-prototype.json").write_text(
            json.dumps({"report_type": "segment_asr_prototype", "status": "wrong"}),
            encoding="utf-8",
        )
        (report_dir / "routing.json").write_text(
            json.dumps(
                {
                    "report_type": "segment_asr_routing_integration",
                    "status": "completed",
                    "segment_asr_routing_mode": "dry_run",
                    "subtitle_output_affected": False,
                    "fallback_used": False,
                    "coverage_rate": 1.0,
                    "analyzer": {"summary": {"keep_auto": 2}},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)
    result = benchmark.run_routing_dry_run(
        {"id": "sample"},
        {
            "model": "small",
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 5,
            "language": None,
        },
        tmp_path / "audio.wav",
        tmp_path,
    )
    assert seen[seen.index("--segment-asr-routing") + 1] == "dry_run"
    assert "apply" not in seen
    assert result["subtitle_output_affected"] is False
    assert result["classification_counts"] == {"keep_auto": 2}


def test_failed_mock_worker_does_not_touch_production_output(tmp_path: Path, monkeypatch) -> None:
    production = tmp_path / "production.srt"
    production.write_text("keep", encoding="utf-8")

    class FailedProcess:
        pid = 123
        returncode = 1

        def poll(self):
            return 1

        def communicate(self):
            return "", "ASR failed at C:\\private\\movie.wav"

    monkeypatch.setattr(benchmark.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    result = benchmark.run_worker_process(
        {"id": "sample", "reference_srt": tmp_path / "ref.srt", "language": "fr"},
        {
            "id": "cpu",
            "model": "small",
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 5,
            "vad_filter": True,
            "condition_on_previous_text": True,
            "language": None,
        },
        1,
        tmp_path / "audio.wav",
        60.0,
        tmp_path / "benchmark",
    )
    assert result["status"] == "failed"
    assert "C:\\private" not in result["error"]
    assert production.read_text(encoding="utf-8") == "keep"


def test_dry_run_creates_no_report_or_asr_output(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    output = tmp_path / "reports"
    monkeypatch.setattr(benchmark, "find_ffmpeg", lambda root: "ffmpeg.exe")
    monkeypatch.setattr(benchmark, "_model_available", lambda model: True)
    args = argparse.Namespace(
        manifest=str(manifest),
        output_dir=str(output),
        sample=[],
        config=[],
        repeat=3,
        dry_run=True,
        baseline=None,
        include_routing_dry_run=False,
    )
    assert benchmark.run_benchmark(args) is None
    assert not output.exists()


def test_markdown_contains_no_paths_or_transcripts() -> None:
    report = {
        "generated_at": "now",
        "corpus_fingerprint": "ABC",
        "git_commit": "deadbeef",
        "sample_count": 1,
        "configuration_count": 1,
        "repeat_count": 3,
        "configuration_summaries": {
            "cpu": {
                "successful_runs": 1,
                "metrics": {
                    name: {"mean": 0.0}
                    for name in ("cer", "wer", "missed_cue_rate", "duplicate_cue_rate")
                },
                "performance": {
                    name: {"mean": None}
                    for name in (
                        "elapsed_seconds",
                        "real_time_factor",
                        "peak_working_set_bytes",
                        "peak_gpu_memory_mib",
                    )
                },
            }
        },
        "routing_dry_run": [],
    }
    markdown = benchmark.render_markdown(report)
    assert "C:\\" not in markdown
    assert "secret transcript" not in markdown
    assert "metrics and hashes only" in markdown


def test_example_manifest_has_expected_corpus_and_configs() -> None:
    path = Path(__file__).parent / "asr_benchmark" / "manifest.example.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["samples"]) == 10
    assert [sample["language"] for sample in data["samples"]].count("fr") == 6
    assert [sample["language"] for sample in data["samples"]].count("en") == 2
    assert [sample["language"] for sample in data["samples"]].count("mixed") == 2
    assert [config["id"] for config in data["configurations"]] == [
        "small-cpu-int8",
        "small-cuda-float16",
        "large-v3-cuda-float16",
    ]


def test_candidate_dry_run_keeps_configuration_ids_and_adds_fixed_decode_options(tmp_path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    monkeypatch.setattr(benchmark, "find_ffmpeg", lambda root: "ffmpeg.exe")
    monkeypatch.setattr(benchmark, "_model_available", lambda model: True)
    args = argparse.Namespace(
        manifest=str(manifest), output_dir=str(tmp_path / "reports"), sample=[], config=[],
        repeat=1, dry_run=True, baseline=None, include_routing_dry_run=False,
        candidate="vad-sensitive-v1",
    )
    assert benchmark.run_benchmark(args) is None


def test_routing_only_is_an_explicit_additive_cli_flag() -> None:
    args = benchmark._build_parser().parse_args(["--manifest", "manifest.json", "--routing-only"])
    assert args.routing_only is True
    assert args.include_routing_dry_run is False


def test_paired_summary_uses_same_run_baseline_and_candidate() -> None:
    results = [{
        "configuration_id": "gpu", "runs": [{
            "status": "completed",
            "metrics": {"cer": 0.4, "wer": None, "missed_cue_rate": 0.0, "duplicate_cue_rate": 0.0},
            "performance": {
                "elapsed_seconds": 12.0, "real_time_factor": 0.2,
                "peak_working_set_bytes": 100, "peak_gpu_memory_mib": 200,
            },
            "paired_baseline_metrics": {"cer": 0.5},
            "paired_performance": {
                "baseline_elapsed_seconds": 10.0, "candidate_incremental_seconds": 2.0,
                "retried_window_count": 2, "accepted_window_count": 1,
                "rejected_window_count": 1, "model_reused": True,
            },
        }],
    }]
    summary = benchmark.aggregate_results(results, [{"id": "gpu"}])["gpu"]["paired"]
    assert summary["baseline_cer"]["mean"] == 0.5
    assert summary["candidate_cer"]["mean"] == 0.4
    assert summary["candidate_incremental_seconds"]["mean"] == 2.0
    assert summary["model_reused_all_runs"] is True
