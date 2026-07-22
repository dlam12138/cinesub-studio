from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import batch_worker
import pipeline_reliability
import pipeline_api
import pytest
from batch_worker import BatchConfig, BatchPipeline
from pipeline_reliability import (
    PipelineRunLock,
    artifact_fingerprint,
    build_pipeline_plan,
    process_identity_matches,
    task_identity,
    windows_process_creation_filetime,
    write_run_record,
)
from stage_event_log import write_stage_event
from task_state import TaskState, set_state_root_provider


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".wav"}


def _config(tmp_path: Path, *, translate: bool = False) -> BatchConfig:
    return BatchConfig(
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        model_dir=tmp_path / "models",
        model="small",
        translate=translate,
        move_completed=False,
    )


def test_task_identity_normalizes_relative_path_and_avoids_same_stem_collisions(tmp_path: Path):
    root = tmp_path / "input"
    first = root / "A" / "采访.mp4"
    second = root / "B" / "采访.mkv"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    first_id, first_relative = task_identity(first, root)
    second_id, second_relative = task_identity(second, root)

    assert first_id.startswith("采访-")
    assert second_id.startswith("采访-")
    assert first_id != second_id
    assert first_relative == "a/采访.mp4"
    assert second_relative == "b/采访.mkv"


def test_plan_persists_display_relative_case_separately_from_identity_key(tmp_path: Path):
    config = _config(tmp_path)
    media = config.input_dir / "FestivalCut" / "Movie.MP4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"media")

    _task_id, identity_key = task_identity(media, config.input_dir)
    plan = build_pipeline_plan(
        config, state_dir=config.work_dir / "states", video_extensions=VIDEO_EXTENSIONS
    )

    assert identity_key == "festivalcut/movie.mp4"
    assert plan.tasks[0].relative_input_path == "FestivalCut/Movie.MP4"


def test_read_only_plan_has_no_filesystem_side_effects_and_uses_collision_safe_outputs(tmp_path: Path):
    config = _config(tmp_path)
    (config.input_dir / "one").mkdir(parents=True)
    (config.input_dir / "two").mkdir(parents=True)
    (config.input_dir / "one" / "movie.mp4").write_bytes(b"one")
    (config.input_dir / "two" / "movie.mkv").write_bytes(b"two")
    state_dir = config.work_dir / "states"

    plan = build_pipeline_plan(
        config, state_dir=state_dir, video_extensions=VIDEO_EXTENSIONS
    )

    assert plan.ok
    assert len({item.task_id for item in plan.tasks}) == 2
    assert {item.output_stem for item in plan.tasks} == {item.task_id for item in plan.tasks}
    assert {item.relative_input_path for item in plan.tasks} == {"one/movie.mp4", "two/movie.mkv"}
    assert not config.work_dir.exists()
    assert not config.output_dir.exists()
    assert not config.model_dir.exists()


@pytest.mark.parametrize(
    "relative",
    [
        "source/movie.small.srt",
        "zh/movie.small.translated.zh-CN.srt",
        "bilingual/movie.small.bilingual.zh-CN.srt",
        "reports/movie.small.quality_report.json",
        "reports/movie.small.review_needed.srt",
    ],
)
def test_every_unowned_existing_output_is_a_blocker(tmp_path: Path, relative: str):
    config = _config(tmp_path)
    config.input_dir.mkdir(parents=True)
    media = config.input_dir / "movie.mp4"
    media.write_bytes(b"media")
    existing = config.output_dir / relative
    existing.parent.mkdir(parents=True)
    existing.write_text("existing", encoding="utf-8")

    plan = build_pipeline_plan(
        config, state_dir=config.work_dir / "states", video_extensions=VIDEO_EXTENSIONS
    )

    assert any(row.code == "unowned_output_collision" for row in plan.blockers)


def test_legacy_state_migration_is_planned_then_applied_atomically(monkeypatch, tmp_path: Path):
    config = _config(tmp_path)
    config.input_dir.mkdir(parents=True)
    media = config.input_dir / "movie.mp4"
    media.write_bytes(b"media")
    states = config.work_dir / "states"
    states.mkdir(parents=True)
    legacy = TaskState(file=media.name, input_path=str(media.resolve()))
    set_state_root_provider(lambda: states)
    legacy.save()
    old_path = states / "movie.state.json"
    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", states)

    plan = batch_worker.build_pipeline_plan(config)
    assert plan.tasks[0].planned_migration is True
    assert old_path.exists()

    pipeline = BatchPipeline(config, plan=plan, run_id="run-test")
    tasks = pipeline._materialize_plan(plan)
    new_path = states / f"{tasks[0].task_id}.state.json"

    assert new_path.is_file()
    assert not old_path.exists()
    set_state_root_provider(lambda: batch_worker.DIR_WORK_STATES)


def test_legacy_state_requires_verified_path_or_input_fingerprint(tmp_path: Path):
    config = _config(tmp_path)
    config.input_dir.mkdir(parents=True)
    media = config.input_dir / "movie.mp4"
    media.write_bytes(b"media")
    states = config.work_dir / "states"
    states.mkdir(parents=True)
    (states / "movie.state.json").write_text(
        json.dumps({"file": "movie.mp4", "input_path": "elsewhere/movie.mp4"}),
        encoding="utf-8",
    )

    plan = build_pipeline_plan(config, state_dir=states, video_extensions=VIDEO_EXTENSIONS)

    assert any(row.code == "legacy_state_input_unverified" for row in plan.blockers)
    assert plan.tasks[0].planned_migration is False


def test_historical_output_owner_forces_collision_safe_output_stem(tmp_path: Path):
    config = _config(tmp_path)
    config.input_dir.mkdir(parents=True)
    media = config.input_dir / "movie.mp4"
    media.write_bytes(b"new media")
    states = config.work_dir / "states"
    states.mkdir(parents=True)
    (states / "historical-id.state.json").write_text(
        json.dumps({
            "task_id": "historical-id",
            "file": "movie.mp4",
            "output_stem": "movie",
            "input_location": "archive",
        }),
        encoding="utf-8",
    )

    plan = build_pipeline_plan(config, state_dir=states, video_extensions=VIDEO_EXTENSIONS)

    assert plan.tasks[0].output_stem == plan.tasks[0].task_id


def test_ambiguous_same_stem_legacy_state_blocks_migration(tmp_path: Path):
    config = _config(tmp_path)
    (config.input_dir / "a").mkdir(parents=True)
    (config.input_dir / "b").mkdir(parents=True)
    (config.input_dir / "a" / "movie.mp4").write_bytes(b"a")
    (config.input_dir / "b" / "movie.mkv").write_bytes(b"b")
    states = config.work_dir / "states"
    states.mkdir(parents=True)
    (states / "movie.state.json").write_text(
        json.dumps({"file": "movie.mp4", "input_path": "movie.mp4"}), encoding="utf-8"
    )

    plan = build_pipeline_plan(config, state_dir=states, video_extensions=VIDEO_EXTENSIONS)

    assert sum(row.code == "ambiguous_legacy_state" for row in plan.blockers) == 2


def test_large_artifact_hash_is_reused_while_metadata_is_stable(monkeypatch, tmp_path: Path):
    artifact = tmp_path / "audio.wav"
    artifact.write_bytes(b"audio")
    first = artifact_fingerprint(artifact, force_full=True)
    monkeypatch.setattr(
        pipeline_reliability,
        "_sha256_file",
        lambda _path: (_ for _ in ()).throw(AssertionError("unexpected rehash")),
    )

    assert artifact_fingerprint(artifact, first) == first


def test_archive_updates_location_metadata(monkeypatch, tmp_path: Path):
    config = _config(tmp_path)
    config.input_dir.mkdir(parents=True)
    media = config.input_dir / "movie.mp4"
    media.write_bytes(b"media")
    states = config.work_dir / "states"
    archive = tmp_path / "archive"
    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", states)
    monkeypatch.setattr(batch_worker, "DIR_ARCHIVE", archive)
    set_state_root_provider(lambda: states)
    task_id, relative = task_identity(media, config.input_dir)
    task = TaskState(
        file=media.name,
        input_path=str(media.resolve()),
        task_id=task_id,
        original_relative_path=relative,
        output_stem=media.stem,
        status="completed",
    )
    pipeline = BatchPipeline(config)
    pipeline._current_task = task

    pipeline._archive_completed(task)

    saved = TaskState.load(task.state_path())
    assert saved is not None
    assert saved.input_location == "archive"
    assert Path(saved.current_input_path).is_file()
    assert saved.archived_at > 0
    assert saved.original_relative_path == relative
    set_state_root_provider(lambda: batch_worker.DIR_WORK_STATES)


def test_pipeline_lock_contends_across_real_processes(tmp_path: Path):
    lock_path = (tmp_path / "pipeline_run.lock").resolve()
    code = (
        "import sys,time; from pathlib import Path; "
        "from pipeline_reliability import PipelineRunLock; "
        "lock=PipelineRunLock(Path(sys.argv[1])); ok=lock.acquire(); "
        "print('locked' if ok else 'blocked', flush=True); "
        "time.sleep(float(sys.argv[2])) if ok else None"
    )
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(lock_path), "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        contender = subprocess.run(
            [sys.executable, "-B", "-c", code, str(lock_path), "0"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        assert contender.stdout.strip() == "blocked"
    finally:
        child.wait(timeout=10)
    contender = PipelineRunLock(lock_path)
    assert contender.acquire() is True
    contender.release()


def test_worker_lease_handoff_blocks_new_launcher(tmp_path: Path):
    lock_path = (tmp_path / "pipeline_run.lock").resolve()
    launch_gate = PipelineRunLock(lock_path)
    parent_lease = PipelineRunLock(lock_path, offset=1)
    assert launch_gate.acquire()
    assert parent_lease.acquire()
    code = (
        "import sys,time; from pathlib import Path; "
        "from pipeline_reliability import PipelineRunLock; "
        "lock=PipelineRunLock(Path(sys.argv[1]),offset=1); "
        "deadline=time.monotonic()+5; "
        "ok=False; "
        "\nwhile time.monotonic()<deadline and not ok:\n"
        " ok=lock.acquire(); time.sleep(0.02) if not ok else None\n"
        "print('leased' if ok else 'failed',flush=True); time.sleep(2)"
    )
    worker = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        parent_lease.release()
        assert worker.stdout is not None
        assert worker.stdout.readline().strip() == "leased"
        launch_gate.release()
        contender_gate = PipelineRunLock(lock_path)
        contender_lease = PipelineRunLock(lock_path, offset=1)
        assert contender_gate.acquire() is True
        assert contender_lease.acquire() is False
        contender_gate.release()
    finally:
        worker.wait(timeout=10)
        launch_gate.release()
        parent_lease.release()
    contender = PipelineRunLock(lock_path)
    assert contender.acquire()
    contender.release()


def test_background_runner_completes_explicit_worker_lease_handoff(monkeypatch, tmp_path: Path):
    work_dir = tmp_path / "work"
    lock_path = work_dir / "pipeline_run.lock"
    record_path = work_dir / "pipeline_run.json"
    log_path = tmp_path / "logs" / "pipeline.log"
    monkeypatch.setattr(pipeline_api, "WORK_DIR", work_dir)
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_LOCK", lock_path)
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_RECORD", record_path)
    monkeypatch.setattr(pipeline_api, "PIPELINE_LOG", log_path)
    monkeypatch.setattr(pipeline_api, "_pipeline_env", lambda: dict(os.environ))
    launch_gate = PipelineRunLock(lock_path)
    parent_lease = PipelineRunLock(lock_path, offset=1)
    assert launch_gate.acquire()
    assert parent_lease.acquire()
    code = (
        "import os,time; from pathlib import Path; "
        "from pipeline_reliability import PipelineRunLock; "
        "lease=PipelineRunLock(Path(os.environ['CINESUB_PIPELINE_LOCK_PATH']),offset=1); "
        "deadline=time.monotonic()+5; ok=False; "
        "\nwhile time.monotonic()<deadline and not ok:\n"
        " ok=lease.acquire(); time.sleep(0.02) if not ok else None\n"
        "assert ok; Path(os.environ['CINESUB_PIPELINE_LOCK_ACK']).write_text('ok'); "
        "time.sleep(0.2)"
    )

    pipeline_api.run_pipeline_background(
        action="run",
        asr_mode="auto",
        _command=[sys.executable, "-B", "-c", code],
        _run_lock=launch_gate,
        _worker_lease=parent_lease,
        _run_id="handoff-run",
        _plan_fingerprint="plan",
    )

    assert pipeline_api.get_pipeline_task()["returncode"] == 0
    gate_after = PipelineRunLock(lock_path)
    lease_after = PipelineRunLock(lock_path, offset=1)
    assert gate_after.acquire()
    assert lease_after.acquire()
    gate_after.release()
    lease_after.release()


def test_run_record_is_atomic_and_pid_filetime_matches_current_process(tmp_path: Path):
    record_path = tmp_path / "pipeline_run.json"
    payload = write_run_record(record_path, {
        "run_id": "run-1",
        "status": "running",
        "worker_pid": __import__("os").getpid(),
        "worker_creation_filetime": windows_process_creation_filetime(__import__("os").getpid()),
    })

    loaded = json.loads(record_path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == 1
    assert loaded["run_id"] == "run-1"
    assert loaded["updated_at"] == payload["updated_at"]
    assert not list(tmp_path.glob("*.tmp"))
    assert process_identity_matches(
        loaded["worker_pid"], loaded["worker_creation_filetime"]
    )
    assert not process_identity_matches(
        loaded["worker_pid"], loaded["worker_creation_filetime"] + 1
    )


def test_dead_worker_run_record_is_marked_stale_without_resetting_tasks(monkeypatch, tmp_path: Path):
    record_path = tmp_path / "pipeline_run.json"
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_RECORD", record_path)
    with pipeline_api.PIPELINE_TASK_LOCK:
        previous = dict(pipeline_api.PIPELINE_TASK)
        pipeline_api.PIPELINE_TASK.update({
            "running": False,
            "pid": None,
            "action": "",
            "started_at": 0,
            "finished_at": 0,
            "returncode": None,
            "error": "",
            "run_id": "",
        })
    write_run_record(record_path, {
        "run_id": "dead-run",
        "action": "run",
        "status": "running",
        "worker_pid": 999999,
        "worker_creation_filetime": 1,
        "started_at": time.time() - 10,
    })
    try:
        task = pipeline_api.get_pipeline_task()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert task["running"] is False
        assert record["status"] == "stale"
    finally:
        with pipeline_api.PIPELINE_TASK_LOCK:
            pipeline_api.PIPELINE_TASK.clear()
            pipeline_api.PIPELINE_TASK.update(previous)


def test_service_restart_recognizes_live_worker_by_pid_filetime(monkeypatch, tmp_path: Path):
    record_path = tmp_path / "pipeline_run.json"
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_RECORD", record_path)
    pid = os.getpid()
    filetime = windows_process_creation_filetime(pid)
    with pipeline_api.PIPELINE_TASK_LOCK:
        previous = dict(pipeline_api.PIPELINE_TASK)
        pipeline_api.PIPELINE_TASK.update({
            "running": False,
            "pid": None,
            "action": "",
            "started_at": 0,
            "finished_at": 0,
            "returncode": None,
            "error": "",
            "run_id": "",
        })
    write_run_record(record_path, {
        "run_id": "live-run",
        "action": "run",
        "status": "running",
        "worker_pid": pid,
        "worker_creation_filetime": filetime,
        "started_at": time.time() - 1,
    })
    try:
        task = pipeline_api.get_pipeline_task()
        assert task["running"] is True
        assert task["run_id"] == "live-run"
        assert json.loads(record_path.read_text(encoding="utf-8"))["status"] == "running"
    finally:
        with pipeline_api.PIPELINE_TASK_LOCK:
            pipeline_api.PIPELINE_TASK.clear()
            pipeline_api.PIPELINE_TASK.update(previous)


def test_stage_events_are_bound_to_run_id(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    write_stage_event(
        path,
        run_id="run-1",
        task_id="movie-123",
        stage="transcribing",
        event="started",
        status="running",
    )

    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "run-1"


def test_sync_pipeline_api_and_logs_return_only_sanitized_summaries(monkeypatch):
    drive_path = r"D:\private\movie.srt"
    unc_path = r"\\server\share\private\movie.srt"
    secret = "sk-super-secret"
    raw = (
        f"api-key={secret}\n"
        f"prompt: reveal this prompt\n"
        f"transcript: private dialogue\n"
        f"command: python worker.py --api-key {secret}\n"
        f"paths {drive_path} {unc_path}\n"
    )
    monkeypatch.setattr(
        pipeline_api.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=raw, stderr=raw),
    )

    payload = pipeline_api.run_pipeline_command("scan")
    serialized = json.dumps(payload, ensure_ascii=False)
    cleaned_log = pipeline_api._clean_log_line(raw)

    for forbidden in (
        secret,
        "reveal this prompt",
        "private dialogue",
        "python worker.py",
        drive_path,
        unc_path,
    ):
        assert forbidden not in serialized
        assert forbidden not in cleaned_log
    assert "[redacted]" in serialized
    assert "[project-path]" in serialized
