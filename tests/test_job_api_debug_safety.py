from __future__ import annotations

import threading

import job_api


def _create_job(tmp_path):
    media = tmp_path / "debug-race.wav"
    media.write_bytes(b"audio")
    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()
    return job_api.create_job({
        "path": str(media),
        "model": "small",
        "device": "cpu",
    })


def test_append_log_uses_sanitized_fallback_when_job_is_missing():
    logs = job_api.append_log(
        "missing-job",
        'api_key="json-secret" Authorization: Bearer bearer-secret',
        ["seed"],
    )

    combined = "\n".join(logs)
    assert logs[0] == "seed"
    assert "json-secret" not in combined
    assert "bearer-secret" not in combined
    assert "***" in combined


def test_clean_log_line_redacts_supported_secret_shapes():
    raw = (
        'sk-supersecret123 {"api_key":"json-secret"} '
        "token=plain-secret Authorization: Bearer bearer-secret"
    )

    cleaned = job_api.clean_log_line(raw)

    for secret in ("supersecret123", "json-secret", "plain-secret", "bearer-secret"):
        assert secret not in cleaned
    assert "sk-***" in cleaned


def test_run_job_handles_record_removed_while_subprocess_starts(monkeypatch, tmp_path):
    job = _create_job(tmp_path)

    class FakeProcess:
        stdout = iter(["token=thread-secret\n", "transcription completed\n"])

        @staticmethod
        def wait():
            return 0

    def fake_popen(*_args, **_kwargs):
        with job_api.JOBS_LOCK:
            job_api.JOBS.clear()
        return FakeProcess()

    monkeypatch.setattr(job_api.subprocess, "Popen", fake_popen)
    thread = threading.Thread(target=job_api.run_job, args=(job["id"],))

    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert job_api.get_job(job["id"]) is None


def test_run_job_marks_spawn_failure_and_always_removes_api_key(monkeypatch, tmp_path):
    job = _create_job(tmp_path)
    with job_api.JOBS_LOCK:
        job_api.JOBS[job["id"]]["_api_key"] = "raw-api-secret"

    def fail_popen(*_args, **_kwargs):
        raise OSError('api_key="spawn-secret" Authorization: Bearer bearer-secret')

    monkeypatch.setattr(job_api.subprocess, "Popen", fail_popen)

    job_api.run_job(job["id"])

    safe = job_api.get_job(job["id"])
    assert safe is not None
    assert safe["status"] == "failed"
    assert safe["stage"] == "failed"
    assert safe["returncode"] == -1
    serialized = str(safe)
    assert "spawn-secret" not in serialized
    assert "bearer-secret" not in serialized
    with job_api.JOBS_LOCK:
        assert "_api_key" not in job_api.JOBS[job["id"]]
