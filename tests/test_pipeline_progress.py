import json
import time
from pathlib import Path

import pipeline_api


def _write_state(states_dir: Path, name: str, payload: dict) -> Path:
    path = states_dir / f"{name}.state.json"
    data = {
        "file": f"{name}.mp4",
        "input_path": str(states_dir / f"{name}.mp4"),
        "status": "pending",
        "stage": "pending",
        "updated_at": time.time(),
    }
    data.update(payload)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_pipeline_progress_reports_recovery_fields(monkeypatch, tmp_path):
    states_dir = tmp_path / "states"
    states_dir.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    completed_srt = output / "done.srt"
    completed_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nok\n", encoding="utf-8")
    reusable_audio = output / "reuse.wav"
    reusable_audio.write_bytes(b"audio")

    _write_state(states_dir, "failed", {
        "status": "failed",
        "stage": "translating",
        "error": f"api_key=secret {tmp_path.resolve()}\\private.srt",
    })
    _write_state(
        states_dir,
        "completed",
        {"status": "completed", "stage": "completed", "source_srt": str(completed_srt)},
    )
    _write_state(states_dir, "running", {"status": "running", "stage": "transcribing"})
    _write_state(
        states_dir,
        "pending",
        {"status": "pending", "stage": "pending", "audio_path": str(reusable_audio)},
    )

    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states_dir)
    monkeypatch.setattr(
        pipeline_api,
        "get_pipeline_task",
        lambda: {"running": False, "pid": None, "action": "", "started_at": 0},
    )

    progress = pipeline_api.pipeline_progress()
    actions = {task["file"]: task["recovery_action"] for task in progress["tasks"]}

    assert progress["total"] == 4
    assert set(progress["counts"]) >= {"pending", "running", "completed", "failed", "stale"}
    assert progress["counts"]["pending"] == 1
    assert progress["counts"]["completed"] == 1
    assert progress["counts"]["failed"] == 1
    assert progress["counts"]["stale"] == 1
    assert progress["recoverable_failed_count"] == 1
    assert progress["can_retry_failed"] is True
    assert progress["stale_running_count"] == 1
    assert actions["failed.mp4"] == "retry_failed"
    assert actions["completed.mp4"] == "skip_completed"
    assert actions["running.mp4"] == "stale_running_warning"
    assert actions["pending.mp4"] == "reuse_outputs"
    failed = next(task for task in progress["tasks"] if task["file"] == "failed.mp4")
    assert "input_path" not in failed
    assert "error" not in failed
    assert "secret" not in failed["error_summary"]
    assert str(tmp_path.resolve()) not in json.dumps(progress, ensure_ascii=False)
