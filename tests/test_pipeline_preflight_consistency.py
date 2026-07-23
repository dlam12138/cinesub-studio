"""Pipeline preflight consistency tests for PR #8 (T1-T12).

These tests pin the contract that the Web plan/run/retry-failed paths share one
config-resolution path (one ``effective_config_hash``), that read-only previews
never mutate state, and that the worker's second-pass preflight aborts cleanly
(no partial task-state writes, no stale run record) when the plan drifts between
the Web preflight and execution.

The implementation under ``src/`` is frozen; this file is the only deliverable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import asr_model_api
import batch_worker
import pipeline_api
import pipeline_reliability
import pytest
import web_server
from conftest import MemoryTestServer, json_test_handler
from pipeline_reliability import (
    build_pipeline_plan as build_read_only_pipeline_plan,
    retry_fingerprint,
    task_identity,
)
from task_state import (
    RetryPlanChanged,
    TaskState,
    apply_retry_failed_plan,
    plan_retry_failed_tasks,
    set_state_root_provider,
)
from web_server import Handler


VIDEO_EXTENSIONS = batch_worker.VIDEO_EXTENSIONS


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _neutralize_stores(monkeypatch):
    """Make Web/worker config resolution deterministic regardless of the
    project's real provider/language-profile store state."""
    import language_profile_store
    import provider_store

    monkeypatch.setattr(
        language_profile_store, "resolve_language_profile_config", lambda _id=None: {}
    )
    monkeypatch.setattr(provider_store, "resolve_provider_config", lambda _id=None: {})
    monkeypatch.setattr(pipeline_api, "_active_provider_id", lambda: "")
    monkeypatch.setattr(pipeline_api, "_active_language_profile_id", lambda: "")


def _call(server, path, *, method="GET", headers=None, body=b""):
    return json_test_handler(
        server, Handler, method=method, path=path, headers=headers, body=body
    )


def _session_headers(server):
    status, _, payload = _call(server, "/api/session")
    assert status == 200
    return {"X-CineSub-Token": payload["token"], "Content-Type": "application/json"}


def _worker_argv(input_dir, work_dir, output_dir, models_dir, *,
                 model="small", no_translate=True, retry=False, extra=()):
    """Build the worker argv tail (mirrors what the Web spawner would emit)."""
    argv = [
        "--input", str(input_dir),
        "--work-dir", str(work_dir),
        "--output-dir", str(output_dir),
        "--model-dir", str(models_dir),
        "--model", model,
    ]
    if no_translate:
        argv.append("--no-translate")
    if retry:
        argv.append("--retry-failed")
    argv.extend(extra)
    return argv


def _worker_config(argv_tail):
    """Resolve the same BatchConfig the worker builds from the given argv.

    Uses ``_batch_config_from_command`` (the Web -> worker round-trip), so the
    config is identical to what ``batch_worker.main()`` produces for the same
    argv -- and thus its ``effective_config_hash`` matches the worker's.
    """
    batch_worker_path = pipeline_api.SRC_ROOT / "pipeline" / "batch_worker.py"
    command = [sys.executable, "-B", str(batch_worker_path)] + argv_tail
    return pipeline_api._batch_config_from_command(command)


def _plan_for(config, states_dir):
    return build_read_only_pipeline_plan(
        config, state_dir=states_dir, video_extensions=VIDEO_EXTENSIONS
    )


def _run_worker(monkeypatch, argv_tail, *, expected_plan,
                run_id="preflight-run", server_pid=None):
    """Drive ``batch_worker.main()`` in-process with a controlled argv/env."""
    if server_pid is None:
        server_pid = os.getpid()
    monkeypatch.setattr(sys, "argv", ["batch_worker.py", *argv_tail])
    monkeypatch.setenv("CINESUB_PIPELINE_EXPECTED_PLAN", expected_plan)
    monkeypatch.setenv("CINESUB_PIPELINE_RUN_ID", run_id)
    monkeypatch.setenv("CINESUB_PIPELINE_SERVER_PID", str(server_pid))
    monkeypatch.delenv("CINESUB_PIPELINE_LOCK_PATH", raising=False)
    monkeypatch.delenv("CINESUB_PIPELINE_LOCK_ACK", raising=False)
    return batch_worker.main()


@pytest.fixture
def worker_dirs(tmp_path, monkeypatch):
    """Tmp input/output/work/states/models roots + DIR_WORK_STATES patch +
    state-root provider, so an in-process worker never touches project state."""
    roots = {
        "input": tmp_path / "input",
        "output": tmp_path / "output",
        "work": tmp_path / "work",
        "states": tmp_path / "work" / "states",
        "models": tmp_path / "models",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", roots["states"])
    monkeypatch.setattr(batch_worker, "DIR_ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(batch_worker, "DIR_FAILED", tmp_path / "failed")
    monkeypatch.setattr(
        batch_worker, "STAGE_EVENT_LOG", tmp_path / "logs" / "pipeline.events.jsonl"
    )
    set_state_root_provider(lambda: roots["states"])
    yield roots
    set_state_root_provider(lambda: batch_worker.DIR_WORK_STATES)


@pytest.fixture
def model_available(monkeypatch):
    """Make the worker's local preflight treat any model as available so it
    reaches the plan-fingerprint compare (avoids needing a real model on disk)."""
    monkeypatch.setattr(asr_model_api, "missing_model_payload", lambda *a, **k: None)


def _failed_state(media, input_dir, states_dir, *, task_id=None):
    """Write a failed TaskState matching an input media file."""
    tid = task_id or task_identity(media, input_dir)[0]
    set_state_root_provider(lambda: states_dir)
    TaskState(
        file=media.name,
        input_path=str(media.resolve()),
        task_id=tid,
        status="failed",
        stage="failed",
    ).save()
    return tid


# --------------------------------------------------------------------------- #
# T1: plan/run/retry-failed SAME payload -> SAME effective_config_hash + paths
# --------------------------------------------------------------------------- #

def test_t1_plan_run_retry_share_effective_config_hash_and_paths(monkeypatch, tmp_path):
    _neutralize_stores(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "movie.mp4").write_bytes(b"media")

    body = {"input_dir": str(input_dir), "model": "small", "translate_enabled": False}
    parsed = web_server.parse_pipeline_request_payload(body)

    plan_result = pipeline_api.plan_pipeline(**parsed)
    h_plan = plan_result["effective_config_hash"]
    assert h_plan

    config_run, _ = pipeline_api.resolve_pipeline_request_config(action="run", **parsed)
    config_retry, _ = pipeline_api.resolve_pipeline_request_config(
        action="retry-failed", **parsed
    )
    h_run = _plan_for(config_run, states).effective_config_hash
    h_retry = _plan_for(config_retry, states).effective_config_hash

    # All three actions resolve the same effective config -> same hash.
    assert h_plan == h_run == h_retry
    # action only selects the --run/--retry-failed flag; paths are action-independent.
    assert config_run.input_dir == config_retry.input_dir
    assert config_run.output_dir == config_retry.output_dir
    assert config_run.work_dir == config_retry.work_dir
    assert config_run.model_dir == config_retry.model_dir


# --------------------------------------------------------------------------- #
# T2: plan and scan are read-only (no .state.json, input bytes unchanged)
# --------------------------------------------------------------------------- #

def test_t2_plan_and_scan_do_not_mutate_state_or_input(monkeypatch, tmp_path):
    _neutralize_stores(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    media = input_dir / "movie.mp4"
    media.write_bytes(b"media-bytes-snapshot")
    snapshot = media.read_bytes()

    body = {"input_dir": str(input_dir), "model": "small", "translate_enabled": False}
    parsed = web_server.parse_pipeline_request_payload(body)

    pipeline_api.plan_pipeline(**parsed)
    pipeline_api.scan_pipeline(input_dir=str(input_dir))

    assert media.read_bytes() == snapshot
    assert list(states.glob("*.state.json")) == []
    # Input directory gained no new entries.
    assert len(list(input_dir.iterdir())) == 1


# --------------------------------------------------------------------------- #
# T3: changing an explicit param (model) -> effective_config_hash CHANGES,
# and plan/run/retry stay mutually consistent.
# --------------------------------------------------------------------------- #

def test_t3_config_param_change_alters_hash_and_keeps_parity(monkeypatch, tmp_path):
    _neutralize_stores(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "movie.mp4").write_bytes(b"media")

    def hashes_for(model):
        body = {"input_dir": str(input_dir), "model": model, "translate_enabled": False}
        parsed = web_server.parse_pipeline_request_payload(body)
        h_plan = pipeline_api.plan_pipeline(**parsed)["effective_config_hash"]
        c_run, _ = pipeline_api.resolve_pipeline_request_config(action="run", **parsed)
        c_retry, _ = pipeline_api.resolve_pipeline_request_config(
            action="retry-failed", **parsed
        )
        h_run = _plan_for(c_run, states).effective_config_hash
        h_retry = _plan_for(c_retry, states).effective_config_hash
        return h_plan, h_run, h_retry

    small_plan, small_run, small_retry = hashes_for("small")
    large_plan, large_run, large_retry = hashes_for("large-v3")

    # Each action set stays internally consistent.
    assert small_plan == small_run == small_retry
    assert large_plan == large_run == large_retry
    # The model change is bound into the effective config -> hash changes.
    assert small_plan != large_plan


# --------------------------------------------------------------------------- #
# T4: retry-failed with a plan blocker -> 409, state bytes unchanged, no
# preparing/running run record left behind.
# --------------------------------------------------------------------------- #

def test_t4_retry_failed_blocker_returns_409_without_state_or_run_record(
    monkeypatch, worker_dirs
):
    # translate on + no resolvable provider -> missing_api_key (and
    # missing_translation_model) plan blockers; stores neutralized so this is
    # deterministic regardless of project state.
    _neutralize_stores(monkeypatch)
    states = worker_dirs["states"]
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_RECORD",
                        worker_dirs["work"] / "pipeline_run.json")
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_LOCK",
                        worker_dirs["work"] / "pipeline_run.lock")
    monkeypatch.setattr(pipeline_api, "PIPELINE_LOG",
                        worker_dirs["work"] / "pipeline.log")

    input_dir = worker_dirs["input"]
    media = input_dir / "movie.mp4"
    media.write_bytes(b"media")
    # A failed task so the retry path is exercised (blockers fire before the
    # empty-set short-circuit).
    _failed_state(media, input_dir, states)
    state_bytes = {p.name: p.read_bytes() for p in states.glob("*.state.json")}

    body = {"input_dir": str(input_dir), "model": "small", "translate_enabled": True}
    parsed = web_server.parse_pipeline_request_payload(body)

    payload, status = pipeline_api.start_pipeline_background(
        action="retry-failed", **parsed
    )

    assert status == 409
    assert payload.get("blockers")
    codes = {b["code"] for b in payload["blockers"]}
    assert "missing_api_key" in codes

    # No worker spawned -> state files untouched.
    for path in states.glob("*.state.json"):
        assert path.read_bytes() == state_bytes[path.name]
    # No preparing/running run record was written (blockers return before it).
    run_record = worker_dirs["work"] / "pipeline_run.json"
    assert not run_record.exists() or json.loads(
        run_record.read_text(encoding="utf-8")
    ).get("status") not in ("preparing", "running")


# --------------------------------------------------------------------------- #
# T5: after plan, add input -> worker with stale EXPECTED_PLAN aborts as
# plan_fingerprint_mismatch; no task state written; run record is terminal.
# --------------------------------------------------------------------------- #

def test_t5_stale_plan_fingerprint_aborts_without_state_writes(
    monkeypatch, worker_dirs, model_available
):
    _neutralize_stores(monkeypatch)
    input_dir = worker_dirs["input"]
    (input_dir / "a.mp4").write_bytes(b"a")

    argv_tail = _worker_argv(
        input_dir, worker_dirs["work"], worker_dirs["output"],
        worker_dirs["models"], model="small", no_translate=True,
    )
    # Real plan fingerprint with one input (computed via the same path the
    # worker uses), then add a second input so the observed plan differs.
    config = _worker_config(argv_tail)
    expected = _plan_for(config, worker_dirs["states"]).plan_fingerprint
    (input_dir / "b.mp4").write_bytes(b"b")

    rc = _run_worker(monkeypatch, argv_tail, expected_plan=expected)

    assert rc == 1
    record = json.loads(
        (worker_dirs["work"] / "pipeline_run.json").read_text(encoding="utf-8")
    )
    assert record["status"] == "aborted_plan_changed"
    assert record["abort_reason"] == "plan_fingerprint_mismatch"
    # Abort happens before any task-state write.
    assert list(worker_dirs["states"].glob("*.state.json")) == []


# --------------------------------------------------------------------------- #
# T6: change provider content (api_base) between plan and worker -> aborted
# with plan_fingerprint_mismatch or configuration_unavailable_after_preflight.
# --------------------------------------------------------------------------- #

def test_t6_provider_content_change_aborts_worker(
    monkeypatch, worker_dirs, model_available
):
    import language_profile_store
    import provider_store

    input_dir = worker_dirs["input"]
    (input_dir / "a.mp4").write_bytes(b"a")

    provider_a = {
        "api_provider": "openai-compatible",
        "api_base": "http://a.example.test/v1",
        "api_key": "k",
        "llm_model": "m",
    }
    monkeypatch.setattr(
        language_profile_store, "resolve_language_profile_config", lambda _id=None: {}
    )
    monkeypatch.setattr(
        provider_store, "resolve_provider_config", lambda _id=None: dict(provider_a)
    )

    # translate ON with an explicit provider + api key so the worker passes the
    # api-key gate and reaches the fingerprint compare.
    argv_tail = _worker_argv(
        input_dir, worker_dirs["work"], worker_dirs["output"],
        worker_dirs["models"], model="small", no_translate=False,
        extra=["--provider", "P", "--api-key", "k"],
    )
    config = _worker_config(argv_tail)
    expected = _plan_for(config, worker_dirs["states"]).plan_fingerprint

    # Flip the provider's api_base; the resolved config (and thus
    # effective_config_hash + plan_fingerprint) changes.
    provider_b = dict(provider_a)
    provider_b["api_base"] = "http://b.example.test/v1"
    monkeypatch.setattr(
        provider_store, "resolve_provider_config", lambda _id=None: dict(provider_b)
    )

    rc = _run_worker(monkeypatch, argv_tail, expected_plan=expected)

    assert rc == 1
    record = json.loads(
        (worker_dirs["work"] / "pipeline_run.json").read_text(encoding="utf-8")
    )
    assert record["status"] == "aborted_plan_changed"
    assert record["abort_reason"] in (
        "plan_fingerprint_mismatch", "configuration_unavailable_after_preflight",
    )
    assert list(worker_dirs["states"].glob("*.state.json")) == []


# --------------------------------------------------------------------------- #
# T7: GET /api/pipeline/scan?input_dir=<dir> -> 200 + preview_scope=default_config
# --------------------------------------------------------------------------- #

def test_t7_get_scan_returns_default_config_scope(monkeypatch, tmp_path):
    _neutralize_stores(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "movie.mp4").write_bytes(b"m")

    server = MemoryTestServer()
    headers = _session_headers(server)
    status, _, payload = _call(
        server,
        "/api/pipeline/scan?input_dir=" + quote(str(input_dir)),
        method="GET",
        headers=headers,
    )

    assert status == 200
    assert payload.get("preview_scope") == "default_config"


# --------------------------------------------------------------------------- #
# T8: Chinese + spaces in an input filename are handled (task appears).
# --------------------------------------------------------------------------- #

def test_t8_chinese_and_spaces_in_filename_handled(monkeypatch, tmp_path):
    _neutralize_stores(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    media = input_dir / "电影 样本.wav"
    media.write_bytes(b"audio")

    body = {"input_dir": str(input_dir), "model": "small", "translate_enabled": False}
    parsed = web_server.parse_pipeline_request_payload(body)
    result = pipeline_api.plan_pipeline(**parsed)

    tasks = result.get("tasks", [])
    assert len(tasks) == 1
    task = tasks[0]
    # display name preserves the original Chinese + spaces.
    assert task["display_name"] == "电影 样本.wav"
    # task_id is collision-safe (spaces collapsed to dashes by sanitize_stem).
    assert task["task_id"].startswith("电影-样本-")
    assert task["relative_input_path"] == "电影 样本.wav"


# --------------------------------------------------------------------------- #
# T9: apply_retry_failed_plan two-phase; modifying the second task's state
# between plan and apply raises RetryPlanChanged with zero writes.
# --------------------------------------------------------------------------- #

def test_t9_apply_retry_failed_plan_zero_writes_on_validation_failure(
    monkeypatch, tmp_path
):
    states = tmp_path / "states"
    states.mkdir()
    set_state_root_provider(lambda: states)
    try:
        TaskState(
            file="a.mp4", input_path=str((tmp_path / "a.mp4").resolve()),
            task_id="a-id", status="failed", stage="failed",
        ).save()
        TaskState(
            file="b.mp4", input_path=str((tmp_path / "b.mp4").resolve()),
            task_id="b-id", status="failed", stage="failed",
        ).save()
        state_files = sorted(states.glob("*.state.json"))

        plan = plan_retry_failed_tasks(state_files)
        assert plan.selected_task_ids == ["a-id", "b-id"]

        # Modify the SECOND task's status to completed between plan and apply.
        b_path = states / "b-id.state.json"
        b_loaded = TaskState.load(b_path)
        b_loaded.status = "completed"
        b_loaded.stage = "completed"
        b_loaded.save()

        after_edit = {p.name: p.read_bytes() for p in state_files}

        with pytest.raises(RetryPlanChanged) as exc:
            apply_retry_failed_plan(plan, run_id="retry-run")
        assert exc.value.task_id == "b-id"

        # Phase 1 validation failure -> zero writes; both files unchanged.
        for path in state_files:
            assert path.read_bytes() == after_edit[path.name]
    finally:
        set_state_root_provider(lambda: batch_worker.DIR_WORK_STATES)


# --------------------------------------------------------------------------- #
# T10: model-availability parity across plan/run/retry-failed + runtime>env.
# --------------------------------------------------------------------------- #

def test_t10_model_availability_parity_and_runtime_priority(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_model_api, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_model_api, "HF_CACHE_DIR", tmp_path / "cache" / "hub")
    _neutralize_stores(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    run_record = tmp_path / "work" / "pipeline_run.json"
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_RECORD", run_record)
    monkeypatch.setattr(pipeline_api, "PIPELINE_RUN_LOCK",
                        tmp_path / "work" / "pipeline_run.lock")
    monkeypatch.setattr(pipeline_api, "PIPELINE_LOG",
                        tmp_path / "logs" / "pipeline.log")

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "movie.mp4").write_bytes(b"m")

    server = MemoryTestServer()
    headers = _session_headers(server)
    body = json.dumps(
        {"input_dir": str(input_dir), "model": "large-v3", "translate_enabled": False}
    ).encode("utf-8")

    started = []
    monkeypatch.setattr(
        web_server,
        "start_pipeline_background",
        lambda **kwargs: started.append(kwargs) or ({"ok": True}, 202),
    )

    # POST /api/pipeline/plan -> 200 with environmental_blockers (model missing).
    status, _, payload = _call(
        server, "/api/pipeline/plan", method="POST", headers=headers, body=body
    )
    assert status == 200
    env = payload.get("environmental_blockers", [])
    assert any(b.get("code") == "asr_model_required" for b in env)

    # POST /api/pipeline/run -> 409 asr_model_required, worker never started.
    status, _, payload = _call(
        server, "/api/pipeline/run", method="POST", headers=headers, body=body
    )
    assert status == 409
    assert payload.get("code") == "asr_model_required"
    assert started == []

    # POST /api/pipeline/retry-failed -> 409 asr_model_required.
    status, _, payload = _call(
        server, "/api/pipeline/retry-failed", method="POST", headers=headers, body=body
    )
    assert status == 409
    assert payload.get("code") == "asr_model_required"
    assert started == []

    # download-in-progress -> runtime blocker wins (runtime > environmental).
    monkeypatch.setattr(web_server, "download_is_running", lambda: True)
    status, _, payload = _call(
        server, "/api/pipeline/run", method="POST", headers=headers, body=body
    )
    assert status == 409
    assert payload.get("code") == "asr_model_download_busy"
    assert started == []

    # No preparing/running run record and no task state written anywhere.
    assert not run_record.exists()
    assert list(states.glob("*.state.json")) == []


# --------------------------------------------------------------------------- #
# T11 (static): web/index.html scan routes to POST /api/pipeline/plan via
# buildPipelineBody, and the live renderScan's default_config branch lacks
# the "可直接运行" ready message.
# --------------------------------------------------------------------------- #

def test_t11_index_scan_routes_to_plan_and_default_config_lacks_ready_message():
    html = (Path(web_server.WEB_ROOT) / "index.html").read_text(encoding="utf-8")

    # scan action POSTs and routes to the plan endpoint via buildPipelineBody.
    assert "buildPipelineBody()" in html
    assert "action === 'scan' ? 'plan'" in html

    # The LIVE renderScan references preview_scope; the dead commented-out copy
    # (inside a /* */ block, which uses the legacy parseScanOutput path) does
    # not. Find the occurrence whose body references preview_scope.
    target = "function renderScan(data)"
    live = None
    idx = 0
    while True:
        pos = html.find(target, idx)
        if pos == -1:
            break
        window = html[pos:pos + 2600]
        if "preview_scope" in window:
            live = window
            break
        idx = pos + len(target)
    assert live is not None, "live renderScan referencing preview_scope not found"

    ready = "配置就绪，可直接运行"
    assert ready in live

    full_idx = live.find("scope === 'full_config'")
    ready_idx = live.find(ready)
    dc_idx = live.find("scope === 'default_config'")
    assert full_idx != -1 and ready_idx != -1 and dc_idx != -1

    # Order in the live renderScan: default_config branch, then the full_config
    # guard, then the rendered ready message (which lives inside that guard).
    # The ready message is therefore NOT shown for the default_config branch.
    assert dc_idx < full_idx < ready_idx
    assert ready not in live[dc_idx:full_idx]


# --------------------------------------------------------------------------- #
# T12 (worker-level): Web preflight selects two failed tasks; before the worker
# apply, change the second task's state -> aborted_plan_changed with
# plan_fingerprint_mismatch or retry_state_changed; both state files unchanged.
# --------------------------------------------------------------------------- #

def test_t12_worker_retry_state_change_aborts_without_writes(
    monkeypatch, worker_dirs, model_available
):
    _neutralize_stores(monkeypatch)
    input_dir = worker_dirs["input"]
    media_a = input_dir / "a.mp4"
    media_b = input_dir / "b.mp4"
    media_a.write_bytes(b"a")
    media_b.write_bytes(b"b")

    a_id = _failed_state(media_a, input_dir, worker_dirs["states"])
    b_id = _failed_state(media_b, input_dir, worker_dirs["states"])

    argv_tail = _worker_argv(
        input_dir, worker_dirs["work"], worker_dirs["output"],
        worker_dirs["models"], model="small", no_translate=True, retry=True,
    )
    config = _worker_config(argv_tail)
    plan = _plan_for(config, worker_dirs["states"])
    active = {item.task_id for item in plan.tasks}
    assert {a_id, b_id}.issubset(active)

    # Web preflight: select both failed tasks and capture the retry fingerprint.
    retry_plan = plan_retry_failed_tasks(
        sorted(worker_dirs["states"].glob("*.state.json")),
        allowed_task_ids=active,
    )
    assert set(retry_plan.selected_task_ids) == {a_id, b_id}
    expected = retry_fingerprint(plan, retry_plan.selected_task_ids)

    # Before the worker apply, change the SECOND task's status to completed.
    # The worker's own re-plan will select only the first task, so the retry
    # fingerprint changes -> plan_fingerprint_mismatch (retry_state_changed is
    # also acceptable per spec).
    b_path = worker_dirs["states"] / f"{b_id}.state.json"
    b_loaded = TaskState.load(b_path)
    b_loaded.status = "completed"
    b_loaded.stage = "completed"
    b_loaded.save()

    snapshot = {p.name: p.read_bytes() for p in worker_dirs["states"].glob("*.state.json")}

    rc = _run_worker(monkeypatch, argv_tail, expected_plan=expected)

    assert rc == 1
    record = json.loads(
        (worker_dirs["work"] / "pipeline_run.json").read_text(encoding="utf-8")
    )
    assert record["status"] == "aborted_plan_changed"
    assert record["abort_reason"] in ("plan_fingerprint_mismatch", "retry_state_changed")

    # Abort happens before apply_retry_failed_plan -> zero task-state writes.
    for path in worker_dirs["states"].glob("*.state.json"):
        assert path.read_bytes() == snapshot[path.name]
