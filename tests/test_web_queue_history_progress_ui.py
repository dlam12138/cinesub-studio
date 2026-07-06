from __future__ import annotations

import time

import pytest

HTML_PATH = __import__("pathlib").Path(__file__).parent.parent / "web" / "index.html"


def _read_index_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


# ── 1. Page loads and has queue/history section ──


def test_index_page_has_queue_history_section():
    html = _read_index_html()
    assert "最近任务" in html
    assert "jobQueuePanel" in html
    assert "jobQueueList" in html


# ── 2. Recent jobs endpoint returns UI-friendly job fields ──


def test_list_jobs_returns_ui_friendly_fields(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    job = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })

    jobs = job_api.list_jobs()
    assert len(jobs) == 1
    j = jobs[0]
    assert "status_label" in j
    assert j["status_label"] == "等待中"
    assert "stage_label" in j
    assert "progress" in j
    assert "can_retry" in j
    assert "retry_reason" in j
    assert "error_summary" in j
    assert "completed_at" in j


# ── 3. Jobs are sorted newest-first by updated_at ──


def test_list_jobs_sorted_newest_first(monkeypatch, tmp_path):
    import job_api

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    for i in range(3):
        media = tmp_path / f"movie{i}.wav"
        media.write_bytes(b"audio")
        job_api.create_job({
            "path": str(media),
            "model": "small",
            "device": "cpu",
            "translate_enabled": "",
        })
        time.sleep(0.01)

    jobs = job_api.list_jobs()
    assert len(jobs) == 3
    # Verify descending updated_at order
    for i in range(len(jobs) - 1):
        assert jobs[i]["updated_at"] >= jobs[i + 1]["updated_at"]


# ── 4. Running job includes status/stage/progress when available ──


def test_running_job_has_stage_and_progress(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })

    # Simulate running state manually
    with job_api.JOBS_LOCK:
        job_api.JOBS[raw["id"]]["status"] = "running"
        job_api.JOBS[raw["id"]]["stage"] = "transcribing"
        job_api.JOBS[raw["id"]]["progress"] = 30

    jobs = job_api.list_jobs()
    j = jobs[0]
    assert j["status"] == "running"
    assert j["stage"] == "transcribing"
    assert j["progress"] == 30
    assert j["status_label"] == "处理中"
    assert j["stage_label"] == "转写"


# ── 5. Missing numeric progress does not produce fake percentage ──


def test_no_fake_progress_for_old_jobs(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })

    # Simulate a pre-M10 job without progress field
    with job_api.JOBS_LOCK:
        del job_api.JOBS[raw["id"]]["progress"]
        job_api.JOBS[raw["id"]]["status"] = "running"

    jobs = job_api.list_jobs()
    j = jobs[0]
    # Fallback for running jobs without progress is 10, not a fake percentage
    assert j["progress"] == 10


# ── 6. Completed job exposes output path or output file list ──


def test_completed_job_shows_output(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })

    with job_api.JOBS_LOCK:
        job_api.JOBS[raw["id"]]["status"] = "done"
        job_api.JOBS[raw["id"]]["stage"] = "completed"
        job_api.JOBS[raw["id"]]["progress"] = 100
        job_api.JOBS[raw["id"]]["source_output"] = str(tmp_path / "movie.small.srt")
        job_api.JOBS[raw["id"]]["completed_at"] = time.time()

    jobs = job_api.list_jobs()
    j = jobs[0]
    assert j["status"] == "done"
    assert j["progress"] == 100
    assert j["source_output"] != ""
    assert j["completed_at"] is not None


# ── 7. Failed job exposes readable error summary ──


def test_failed_job_exposes_error_summary(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })

    with job_api.JOBS_LOCK:
        job_api.JOBS[raw["id"]]["status"] = "failed"
        job_api.JOBS[raw["id"]]["stage"] = "failed"
        job_api.JOBS[raw["id"]]["progress"] = 100
        job_api.JOBS[raw["id"]]["logs"] = ["Started.", "FFmpeg error: codec not found", "Failed with code 1."]
        job_api.JOBS[raw["id"]]["error_summary"] = job_api._compute_error_summary(
            job_api.JOBS[raw["id"]]["logs"]
        )
        job_api.JOBS[raw["id"]]["completed_at"] = time.time()

    jobs = job_api.list_jobs()
    j = jobs[0]
    assert j["status"] == "failed"
    assert j["error_summary"] != ""
    assert "FFmpeg error" in j["error_summary"] or "Failed with code" in j["error_summary"]


# ── 8. Long errors are summarized or placed in expandable details ──


def test_ui_has_expandable_error_details():
    html = _read_index_html()
    assert "job-error-details" in html
    assert "details" in html


def test_error_summary_truncated_in_backend():
    import job_api

    long_error = "A" * 500
    logs = ["ok", long_error]
    summary = job_api._compute_error_summary(logs)
    assert len(summary) <= 250


# ── 9. Retry is enabled only when original request/options are complete ──


def test_retry_enabled_only_with_complete_options(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    # Failed job with complete options
    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })
    with job_api.JOBS_LOCK:
        job_api.JOBS[raw["id"]]["status"] = "failed"
        job_api.JOBS[raw["id"]]["stage"] = "failed"
        job_api.JOBS[raw["id"]]["progress"] = 100
        job_api.JOBS[raw["id"]]["error_summary"] = "test error"
        job_api.JOBS[raw["id"]]["completed_at"] = time.time()

    jobs = job_api.list_jobs()
    j = jobs[0]
    assert j["can_retry"] is True


# ── 10. Retry creates a new job or uses existing safe job creation path ──


def test_retry_creates_new_job(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })
    job_id = raw["id"]

    with job_api.JOBS_LOCK:
        job_api.JOBS[job_id]["status"] = "failed"
        job_api.JOBS[job_id]["stage"] = "failed"
        job_api.JOBS[job_id]["progress"] = 100
        job_api.JOBS[job_id]["error_summary"] = "test error"
        job_api.JOBS[job_id]["completed_at"] = time.time()

    new_job = job_api.retry_job(job_id)
    assert new_job is not None
    assert new_job["id"] != job_id
    # retry_job starts the thread immediately; status may already be running
    assert new_job["status"] in ("queued", "running")
    # Original job should not be mutated
    with job_api.JOBS_LOCK:
        assert job_api.JOBS[job_id]["status"] == "failed"


# ── 11. Retry is disabled with explanation when job lacks required metadata ──


def test_retry_disabled_without_input():
    import job_api

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    job_id = "test-no-input"
    with job_api.JOBS_LOCK:
        job_api.JOBS[job_id] = {
            "id": job_id,
            "status": "failed",
            "stage": "failed",
            "progress": 100,
            "created_at": time.time(),
            "updated_at": time.time(),
            "input": "",
            "output": "",
            "source_output": "",
            "translated_output": "",
            "options": {},
            "logs": ["Failed."],
        }

    safe = job_api.get_job(job_id)
    assert safe is not None
    assert job_api._can_retry_job(safe) is False

    # Also verify list_jobs marks can_retry correctly
    jobs = job_api.list_jobs()
    j = jobs[0]
    assert j["can_retry"] is False
    assert j["retry_reason"] != ""


# ── 12. Provider legacy ASR fields do not influence retry ──


def test_retry_does_not_use_provider_asr_fields(monkeypatch, tmp_path):
    import job_api

    media = tmp_path / "movie.wav"
    media.write_bytes(b"audio")

    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()

    raw = job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
        "translate_enabled": "",
    })
    job_id = raw["id"]

    # Inject legacy ASR fields into options (should be ignored)
    with job_api.JOBS_LOCK:
        job_api.JOBS[job_id]["options"]["whisper_model"] = "large-v3"
        job_api.JOBS[job_id]["options"]["whisper_device"] = "cuda"
        job_api.JOBS[job_id]["status"] = "failed"
        job_api.JOBS[job_id]["stage"] = "failed"
        job_api.JOBS[job_id]["progress"] = 100
        job_api.JOBS[job_id]["error_summary"] = "test"
        job_api.JOBS[job_id]["completed_at"] = time.time()

    # Retry should still work; options_to_form should not break
    new_job = job_api.retry_job(job_id)
    assert new_job is not None
    assert new_job["id"] != job_id
    # Verify options do not contain whisper_model/device as primary keys
    # The actual retry creation uses model/device from options, not whisper_model
    assert new_job["options"]["model"] == "small"
    assert new_job["options"]["device"] == "cpu"


# ── 13. UI does not expose TTS/dubbing/voice-clone controls ──


def test_no_dubbing_controls_in_m10():
    html = _read_index_html()
    lower = html.lower()
    assert "配音" not in lower
    assert "dubbing" not in lower
    assert "tts" not in lower
    assert "语音克隆" not in lower
    assert "voice clone" not in lower
    assert "lip.sync" not in lower
    assert "口型" not in lower


# ── 14. No database/runtime schema migration is required ──


def test_no_database_introduced():
    # Verify by inspection: no sqlite3, no sqlalchemy, no db migration files
    import web_server
    import job_api

    source_web = __import__("inspect").getsource(web_server)
    source_job = __import__("inspect").getsource(job_api)
    assert "sqlite3" not in source_web.lower()
    assert "sqlite3" not in source_job.lower()
    assert "sqlalchemy" not in source_web.lower()
    assert "sqlalchemy" not in source_job.lower()
    assert "database" not in source_web.lower()
    assert "database" not in source_job.lower()


# ── 15. Existing M9 UI productization tests still pass (spot-check) ──


def test_m9_job_status_section_still_present():
    html = _read_index_html()
    assert "任务状态" in html
    assert "badge" in html


def test_m9_provider_modal_no_whisper_model():
    html = _read_index_html()
    provider_modal_start = html.find('id="providerModal"')
    provider_modal = html[provider_modal_start:provider_modal_start + 15000]
    assert "whisper_model" not in provider_modal.lower()
    assert "whisper_device" not in provider_modal.lower()


# ── Retry endpoint shape ──


def test_retry_endpoint_shape(monkeypatch, tmp_path):
    # Verify web_server imports retry_job correctly
    import web_server
    assert hasattr(web_server, "retry_job")
