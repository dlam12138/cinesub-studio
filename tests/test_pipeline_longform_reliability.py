from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import batch_worker
import pipeline_reliability
import pipeline_api
import pytest
from _pipeline_process_helper import HELPER_PATH, PipelineProbe, ProbeError, build_probe_env
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


def test_task_identity_hash_uses_casefolded_relative_key(tmp_path: Path):
    root = tmp_path / "input"
    media = root / "MixedCase" / "Movie.MP4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"media")

    task_id, identity_key = task_identity(media, root)
    expected_hash = hashlib.sha256("mixedcase/movie.mp4".encode("utf-8")).hexdigest()[:12]

    assert task_id == f"Movie-{expected_hash}"
    assert identity_key == "mixedcase/movie.mp4"


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


# --- Cross-process lock/lease handoff helpers ------------------------------
#
# The original versions of the next tests spawned ``python -c`` children that
# depended on the calling terminal's ambient PYTHONPATH: pytest's
# ``pythonpath`` ini option only patches the pytest process' sys.path, so in
# shells without that variable the children died with a ModuleNotFoundError on
# a stderr pipe nobody drained, and bare ``readline() == "locked"`` asserts
# reported ``'' == 'locked'`` (Issue #10). CI injects PYTHONPATH in the
# workflow, which is why the same tests looked green there and flaky locally.
#
# PipelineProbe children self-bootstrap their import path and speak an
# explicit JSON event handshake (started/waiting_for_lease/locked/leased/
# released/error) with monotonic deadlines — no fixed sleeps decide progress.


def _reset_pipeline_task() -> None:
    with pipeline_api.PIPELINE_TASK_LOCK:
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


def _offsets_free(lock_path: Path) -> tuple[bool, bool]:
    """Probe whether both lock offsets are currently free (then free them again)."""
    results = []
    held = []
    for offset in (0, 1):
        lock = PipelineRunLock(lock_path, offset=offset)
        acquired = lock.acquire()
        if acquired:
            held.append(lock)
        results.append(acquired)
    for lock in held:
        lock.release()
    return results[0], results[1]


def wait_for_background_terminal_state(run_id: str, timeout: float = 20.0) -> dict:
    """Bounded wait until the background pipeline task reports a terminal state."""
    deadline = time.monotonic() + timeout
    last_task: dict = {}
    while time.monotonic() < deadline:
        task = pipeline_api.get_pipeline_task()
        last_task = task
        if task.get("returncode") is not None:
            return task
        time.sleep(0.02)
    record = pipeline_api.read_run_record(pipeline_api.PIPELINE_RUN_RECORD)
    gate_free, lease_free = _offsets_free(pipeline_api.PIPELINE_RUN_LOCK)
    raise AssertionError(
        f"background pipeline {run_id!r} did not reach a terminal state "
        f"within {timeout:.1f}s\n"
        f"  last task: {last_task}\n"
        f"  pid: {last_task.get('pid')}\n"
        f"  run record: {record}\n"
        f"  gate_free: {gate_free} lease_free: {lease_free}"
    )


def _start_background_run(monkeypatch, tmp_path: Path, run_id: str, command: list[str]):
    work_dir = tmp_path / "work"
    lock_path = work_dir / "pipeline_run.lock"
    monkeypatch.setattr(pipeline_api, "WORK_DIR", work_dir)
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_LOCK", lock_path)
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_RECORD", work_dir / "pipeline_run.json")
    monkeypatch.setattr(pipeline_api, "PIPELINE_LOG", tmp_path / "logs" / "pipeline.log")
    monkeypatch.setattr(pipeline_api, "_pipeline_env", build_probe_env)
    _reset_pipeline_task()
    launch_gate = PipelineRunLock(lock_path)
    parent_lease = PipelineRunLock(lock_path, offset=1)
    assert launch_gate.acquire()
    assert parent_lease.acquire()
    thread = threading.Thread(
        target=pipeline_api.run_pipeline_background,
        kwargs={
            "action": "run",
            "asr_mode": "auto",
            "_command": command,
            "_run_lock": launch_gate,
            "_worker_lease": parent_lease,
            "_run_id": run_id,
            "_plan_fingerprint": "plan",
        },
        daemon=True,
    )
    thread.start()
    return thread, launch_gate, parent_lease, lock_path


def test_pipeline_lock_contends_across_real_processes(tmp_path: Path):
    lock_path = (tmp_path / "pipeline_run.lock").resolve()
    holder = PipelineProbe(
        "--mode", "lock", "--lock-path", lock_path, "--offset", "0"
    )
    try:
        holder.wait_for_event("locked", offset=0)
        contender = PipelineProbe("--mode", "launcher", "--lock-path", lock_path)
        try:
            assert contender.wait_for_event("blocked")["offset"] == 0
            contender.wait_for_exit()
        finally:
            contender.cleanup()
        holder.release_and_wait()
    finally:
        holder.cleanup()
    assert holder.proc.poll() == 0
    contender_lock = PipelineRunLock(lock_path)
    assert contender_lock.acquire() is True
    contender_lock.release()


def test_worker_lease_handoff_blocks_new_launcher(tmp_path: Path):
    lock_path = (tmp_path / "pipeline_run.lock").resolve()
    launch_gate = PipelineRunLock(lock_path)
    parent_lease = PipelineRunLock(lock_path, offset=1)
    assert launch_gate.acquire()
    assert parent_lease.acquire()
    worker = PipelineProbe(
        "--mode", "lease-worker", "--lock-path", lock_path, "--offset", "1"
    )
    try:
        # The child must reach the lease-polling state before the parent
        # hands the lease over — no fixed sleep guesses that it started.
        worker.wait_for_event("waiting_for_lease", offset=1)
        parent_lease.release()
        worker.wait_for_event("leased", offset=1)

        # While the launch gate is held, a new launcher is rejected at offset 0
        # even though the parent lease has already moved to the worker.
        gated_contender = PipelineProbe("--mode", "launcher", "--lock-path", lock_path)
        try:
            assert gated_contender.wait_for_event("blocked")["offset"] == 0
            gated_contender.wait_for_exit()
        finally:
            gated_contender.cleanup()

        launch_gate.release()

        # With the gate free, a launcher takes offset 0 but must still be
        # blocked by the worker lease at offset 1 — the two offsets are not
        # conflated.
        lease_contender = PipelineProbe("--mode", "launcher", "--lock-path", lock_path)
        try:
            assert lease_contender.wait_for_event("blocked")["offset"] == 1
            lease_contender.wait_for_exit()
        finally:
            lease_contender.cleanup()

        worker.release_and_wait()
    finally:
        worker.cleanup()
        launch_gate.release()
        parent_lease.release()
    gate_after = PipelineRunLock(lock_path)
    lease_after = PipelineRunLock(lock_path, offset=1)
    assert gate_after.acquire() is True
    assert lease_after.acquire() is True
    gate_after.release()
    lease_after.release()


def test_background_runner_completes_explicit_worker_lease_handoff(monkeypatch, tmp_path: Path):
    run_id = "handoff-run"
    command = [
        sys.executable, "-u", str(HELPER_PATH),
        "--mode", "fake-worker",
        "--offset", "1",
        "--hold-seconds", "0.3",
    ]
    thread, launch_gate, parent_lease, lock_path = _start_background_run(
        monkeypatch, tmp_path, run_id, command
    )
    try:
        task = wait_for_background_terminal_state(run_id, timeout=20.0)
        assert task["returncode"] == 0
        assert task["running"] is False
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        launch_gate.release()
        parent_lease.release()
    gate_after = PipelineRunLock(lock_path)
    lease_after = PipelineRunLock(lock_path, offset=1)
    assert gate_after.acquire()
    assert lease_after.acquire()
    gate_after.release()
    lease_after.release()


def test_child_startup_failure_is_diagnosable(tmp_path: Path):
    """A child that dies at import/runtime must surface rc + stderr, not ''."""
    lock_path = (tmp_path / "pipeline_run.lock").resolve()
    probe = PipelineProbe("--mode", "fail-import", "--lock-path", lock_path)
    with pytest.raises(ProbeError) as excinfo:
        probe.wait_for_event("locked", offset=0, timeout=15.0)
    message = str(excinfo.value)
    assert "returncode:" in message  # exit code is surfaced, never hidden
    assert "ModuleNotFoundError" in message
    assert "no_such_probe_module_xyz" in message
    assert "stderr tail:" in message  # child stderr is captured for diagnosis
    assert probe.proc.poll() not in (None, 0)  # reaped with a failure code

    crash = PipelineProbe("--mode", "fail-runtime", "--lock-path", lock_path)
    with pytest.raises(ProbeError) as crash_info:
        crash.wait_for_event("locked", offset=0, timeout=15.0)
    crash_message = str(crash_info.value)
    assert "returncode:" in crash_message
    assert "intentional runtime failure" in crash_message
    assert crash.proc.poll() not in (None, 0)


def test_abrupt_child_exit_releases_lock(tmp_path: Path):
    """If a lock holder dies without releasing, the OS frees the lock."""
    lock_path = (tmp_path / "pipeline_run.lock").resolve()
    probe = PipelineProbe(
        "--mode", "abrupt-exit",
        "--lock-path", lock_path, "--offset", "0", "--exit-code", "3",
    )
    try:
        probe.wait_for_event("locked", offset=0)
        assert probe.wait_for_exit(expected=3) == 3
        deadline = time.monotonic() + 5.0
        recheck = PipelineRunLock(lock_path)
        while not recheck.acquire():
            if time.monotonic() > deadline:
                raise AssertionError("lock was not released after abrupt child exit")
            time.sleep(0.02)
        recheck.release()
    finally:
        probe.cleanup()
    assert lock_path.exists()  # releasing the lock never deletes the file


def test_lease_handoff_has_no_observable_gap(monkeypatch, tmp_path: Path):
    """No launcher may acquire both offsets at any point during the handoff.

    Phase 1 (spawn -> ack): the parent launch gate (offset 0) must stay held.
    Phase 2 (ack -> worker hold end): the worker lease (offset 1) must be
    held. Any successful in-process launcher probe is a regression.
    """
    run_id = "gap-run"
    hold_seconds = 1.5
    command = [
        sys.executable, "-u", str(HELPER_PATH),
        "--mode", "fake-worker",
        "--offset", "1",
        "--hold-seconds", str(hold_seconds),
    ]
    thread, launch_gate, parent_lease, lock_path = _start_background_run(
        monkeypatch, tmp_path, run_id, command
    )
    work_dir = lock_path.parent
    ack_path = work_dir / f".pipeline-lock-handoff-{run_id}.ack"
    launcher_probes = 0
    failure = None
    try:
        # A real subprocess launcher started during the handoff must end up
        # blocked (at the gate or the lease depending on exact timing).
        subprocess_launcher = PipelineProbe("--mode", "launcher", "--lock-path", lock_path)
        start = time.monotonic()
        acked_at = None
        deadline = start + 25.0
        while time.monotonic() < deadline:
            # Probe exactly like a real launcher: gate first, then lease,
            # all-or-nothing. The run is safe while every such probe is
            # blocked by at least one offset — by the parent's launch gate
            # before the ack, then by the worker's lease. (Probing the gate
            # alone is NOT a valid invariant: the server may release the gate
            # a few ms after the ack file appears, while the test has not
            # observed the ack yet — the worker lease already covers that
            # instant.)
            held = []
            launcher_would_start = True
            for offset in (0, 1):
                candidate = PipelineRunLock(lock_path, offset=offset)
                if candidate.acquire():
                    held.append(candidate)
                else:
                    launcher_would_start = False
                    break
            for candidate in held:
                candidate.release()
            if launcher_would_start:
                failure = (
                    "a launcher acquired both the launch gate (offset 0) and "
                    "the worker lease (offset 1) during the run — a "
                    "concurrent pipeline could have started"
                )
                break
            launcher_probes += 1
            if ack_path.exists():
                if acked_at is None:
                    acked_at = time.monotonic()
                elif time.monotonic() > acked_at + hold_seconds * 0.6:
                    break
            time.sleep(0.005)
        try:
            assert subprocess_launcher.wait_for_event("blocked")["offset"] in (0, 1)
            subprocess_launcher.wait_for_exit()
        finally:
            subprocess_launcher.cleanup()
        assert failure is None, failure
        assert launcher_probes >= 20, (
            f"too few launcher probes to be meaningful: {launcher_probes}"
        )
        task = wait_for_background_terminal_state(run_id, timeout=20.0)
        assert task["returncode"] == 0
        thread.join(timeout=10)
        assert not thread.is_alive()
        # After completion a new launcher may legitimately proceed.
        after_completion = PipelineProbe("--mode", "launcher", "--lock-path", lock_path)
        try:
            after_completion.wait_for_event("acquired")
            after_completion.wait_for_exit()
        finally:
            after_completion.cleanup()
    finally:
        launch_gate.release()
        parent_lease.release()


def test_background_worker_exit_zero_is_observable(monkeypatch, tmp_path: Path):
    run_id = "exit0-run"
    command = [
        sys.executable, "-u", str(HELPER_PATH),
        "--mode", "fake-worker", "--offset", "1", "--hold-seconds", "0.2",
    ]
    thread, launch_gate, parent_lease, lock_path = _start_background_run(
        monkeypatch, tmp_path, run_id, command
    )
    try:
        task = wait_for_background_terminal_state(run_id, timeout=20.0)
        assert task["returncode"] == 0
        assert task["running"] is False
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        launch_gate.release()
        parent_lease.release()
    assert _offsets_free(lock_path) == (True, True)


def test_background_worker_nonzero_exit_is_observable(monkeypatch, tmp_path: Path):
    run_id = "exit7-run"
    command = [
        sys.executable, "-u", str(HELPER_PATH),
        "--mode", "fake-worker", "--offset", "1",
        "--hold-seconds", "0.2", "--exit-code", "7",
    ]
    thread, launch_gate, parent_lease, lock_path = _start_background_run(
        monkeypatch, tmp_path, run_id, command
    )
    try:
        task = wait_for_background_terminal_state(run_id, timeout=20.0)
        assert task["returncode"] == 7
        assert task["running"] is False
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        launch_gate.release()
        parent_lease.release()
    assert _offsets_free(lock_path) == (True, True)


def test_background_worker_startup_failure_is_observable(monkeypatch, tmp_path: Path):
    run_id = "failstart-run"
    command = [sys.executable, "-u", str(HELPER_PATH), "--mode", "fail-import"]
    thread, launch_gate, parent_lease, lock_path = _start_background_run(
        monkeypatch, tmp_path, run_id, command
    )
    try:
        task = wait_for_background_terminal_state(run_id, timeout=20.0)
        assert task["running"] is False
        assert task["returncode"] == 2, (
            "startup failure must record the observed worker exit code, "
            f"got task={task}"
        )
        assert "did not acquire" in task["error"]
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        launch_gate.release()
        parent_lease.release()
    assert _offsets_free(lock_path) == (True, True)


def test_background_worker_fast_exit_before_first_poll_is_observable(monkeypatch, tmp_path: Path):
    run_id = "fastexit-run"
    command = [
        sys.executable, "-u", str(HELPER_PATH),
        "--mode", "fake-worker", "--offset", "1", "--hold-seconds", "0",
    ]
    thread, launch_gate, parent_lease, lock_path = _start_background_run(
        monkeypatch, tmp_path, run_id, command
    )
    try:
        # Deliberately poll late: the worker must already be in a terminal
        # state, not stuck at returncode=None because the exit was missed.
        time.sleep(0.8)
        task = wait_for_background_terminal_state(run_id, timeout=20.0)
        assert task["returncode"] == 0
        assert task["running"] is False
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        launch_gate.release()
        parent_lease.release()
    assert _offsets_free(lock_path) == (True, True)


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
