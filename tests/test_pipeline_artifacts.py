import json
from pathlib import Path
from types import SimpleNamespace

import pipeline_api


def _write_state(states_dir: Path, name: str, payload: dict) -> Path:
    path = states_dir / f"{name}.state.json"
    data = {
        "file": f"{name}.mp4",
        "input_path": str(states_dir / f"{name}.mp4"),
        "status": "completed",
        "stage": "completed",
    }
    data.update(payload)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_pipeline_artifacts_expose_output_download_and_outside_source_copy_only(monkeypatch, tmp_path):
    states_dir = tmp_path / "states"
    output_dir = tmp_path / "output"
    outside_dir = tmp_path / "outside"
    states_dir.mkdir()
    output_dir.mkdir()
    outside_dir.mkdir()

    source_srt = outside_dir / "movie.small.srt"
    source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nBonjour\n", encoding="utf-8")
    bilingual_srt = output_dir / "bilingual" / "movie.small.bilingual.zh-CN.srt"
    bilingual_srt.parent.mkdir()
    bilingual_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nBonjour\n你好\n", encoding="utf-8")
    report = output_dir / "reports" / "movie.small.quality_report.json"
    report.parent.mkdir()
    report.write_text(
        json.dumps({"status": "warning", "summary": {"total_issues": 1, "errors": 0, "warnings": 1}}),
        encoding="utf-8",
    )

    _write_state(
        states_dir,
        "movie",
        {
            "source_srt": str(source_srt),
            "bilingual_srt": str(bilingual_srt),
            "translated_srt": str(bilingual_srt),
            "quality_report": str(report),
            "language_detection": {"source_language": "en", "language_probability": 0.98},
        },
    )

    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states_dir)
    monkeypatch.setattr(pipeline_api, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(
        pipeline_api,
        "get_pipeline_task",
        lambda: {"running": False, "pid": None, "action": "", "started_at": 0},
    )

    task = pipeline_api.pipeline_progress()["tasks"][0]

    assert task["language_detection"]["source_language"] == "en"
    assert task["quality_summary"]["status"] == "warning"
    assert task["artifacts"]["source"]["exists"] is True
    assert task["artifacts"]["source"]["downloadable"] is False
    assert task["artifacts"]["source"]["download_url"] == ""
    assert task["artifacts"]["bilingual"]["downloadable"] is True
    assert task["artifacts"]["bilingual"]["download_url"].startswith("/api/pipeline/artifact?")


def test_resolve_pipeline_artifact_rejects_unknown_traversal_and_outside_output(monkeypatch, tmp_path):
    states_dir = tmp_path / "states"
    output_dir = tmp_path / "output"
    outside_dir = tmp_path / "outside"
    states_dir.mkdir()
    output_dir.mkdir()
    outside_dir.mkdir()

    source_srt = outside_dir / "movie.small.srt"
    source_srt.write_text("outside", encoding="utf-8")
    bilingual_srt = output_dir / "movie.small.bilingual.zh-CN.srt"
    bilingual_srt.write_text("inside", encoding="utf-8")
    _write_state(
        states_dir,
        "movie",
        {
            "source_srt": str(source_srt),
            "bilingual_srt": str(bilingual_srt),
        },
    )

    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states_dir)
    monkeypatch.setattr(pipeline_api, "OUTPUT_DIR", output_dir)

    path, error = pipeline_api.resolve_pipeline_artifact("movie", "bilingual")
    assert path == bilingual_srt.resolve()
    assert error == ""

    path, error = pipeline_api.resolve_pipeline_artifact("movie", "source")
    assert path is None
    assert "not downloadable" in error

    path, error = pipeline_api.resolve_pipeline_artifact("../movie", "bilingual")
    assert path is None
    assert "Invalid task id" in error

    path, error = pipeline_api.resolve_pipeline_artifact("movie", "secret")
    assert path is None
    assert "Unknown artifact type" in error


def test_review_returncode_one_is_issues_found_only_for_valid_summary(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="Review summary - 2 report(s)\nReports: output/reports\n",
            stderr="",
        )

    monkeypatch.setattr(pipeline_api.subprocess, "run", fake_run)

    result = pipeline_api.run_pipeline_command("review")

    assert result["ok"] is True
    assert result["returncode"] == 1
    assert result["review_status"] == "issues_found"


def test_review_returncode_one_without_summary_remains_failure(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="Traceback: boom\n", stderr="")

    monkeypatch.setattr(pipeline_api.subprocess, "run", fake_run)

    result = pipeline_api.run_pipeline_command("review")

    assert result["ok"] is False
    assert result["review_status"] == "failed"
