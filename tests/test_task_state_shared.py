from __future__ import annotations

from pathlib import Path

import batch_worker
from task_state import (
    TaskState,
    apply_retry_failed_plan,
    prepare_retry_failed_tasks,
    recovery_state,
    set_state_root_provider,
)


def test_shared_retry_selects_only_failed_and_preserves_stale_running(tmp_path: Path) -> None:
    states = tmp_path / "states"
    set_state_root_provider(lambda: states)
    failed = TaskState("失败 电影.mkv", str(tmp_path / "失败 电影.mkv"), status="failed", error="x")
    running = TaskState("running.mkv", str(tmp_path / "running.mkv"), status="running")
    completed = TaskState("done.mkv", str(tmp_path / "done.mkv"), status="completed")
    for task in (failed, running, completed):
        task.save()
    plan = prepare_retry_failed_tasks(sorted(states.glob("*.state.json")))
    assert plan.selected_task_ids == ["失败 电影.mkv"]
    assert plan.untouched_count == 2
    assert TaskState.load(failed.state_path()).status == "failed"
    apply_retry_failed_plan(plan, run_id="retry-run")
    assert TaskState.load(failed.state_path()).status == "pending"
    assert TaskState.load(running.state_path()).status == "running"
    assert TaskState.load(completed.state_path()).status == "completed"
    set_state_root_provider(lambda: batch_worker.DIR_WORK_STATES)


def test_shared_recovery_requires_non_empty_completed_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source.srt"
    raw = {"source_srt": str(source)}
    assert recovery_state(raw, "completed")["recovery_action"] == "not_recoverable"
    source.write_text("subtitle", encoding="utf-8")
    assert recovery_state(raw, "completed")["recovery_action"] == "skip_completed"
    assert recovery_state(raw, "stale")["recovery_action"] == "stale_running_warning"
