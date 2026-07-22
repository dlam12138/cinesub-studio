from __future__ import annotations

import json
from pathlib import Path

import asr_model_api
import job_api
import web_server
from conftest import MemoryTestServer, json_test_handler
from web_server import Handler


def _call(server, path: str, *, method: str = "GET", headers=None, body: bytes = b""):
    return json_test_handler(
        server,
        Handler,
        method=method,
        path=path,
        headers=headers,
        body=body,
    )


def _session_headers(server) -> dict[str, str]:
    status, _, payload = _call(server, "/api/session")
    assert status == 200
    return {
        "X-CineSub-Token": payload["token"],
        "Content-Type": "application/json",
    }


def test_model_plan_get_is_read_only(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(asr_model_api, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_model_api, "HF_CACHE_DIR", tmp_path / "cache" / "hub")

    server = MemoryTestServer()
    status, _, payload = _call(
        server,
        "/api/runtime/asr-models?selected=large-v3&source=mirror"
    )

    assert status == 200
    assert payload["selected"]["available"] is False
    assert payload["selected"]["download_required"] is True
    assert payload["selected"]["source"] == "mirror"
    assert "large-v3" in payload["selected"]["repo_id"]
    assert not (tmp_path / "models").exists()


def test_missing_model_rejects_single_and_pipeline_before_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(asr_model_api, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_model_api, "HF_CACHE_DIR", tmp_path / "cache" / "hub")
    media = tmp_path / "电影 样本.mkv"
    media.write_bytes(b"sample")
    with job_api.JOBS_LOCK:
        job_api.JOBS.clear()
    server = MemoryTestServer()

    boundary = "CineSubModelBoundary"
    multipart = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"path\"\r\n\r\n{media}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nlarge-v3\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    headers = _session_headers(server)
    status, _, payload = _call(
        server,
        "/api/jobs",
        method="POST",
        headers={
            "X-CineSub-Token": headers["X-CineSub-Token"],
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        body=multipart,
    )
    assert status == 409
    assert payload["code"] == "asr_model_required"
    assert payload["model"] == "large-v3"
    assert payload["confirmation_required"] is True
    assert not job_api.JOBS

    started = []
    monkeypatch.setattr(
        web_server,
        "start_pipeline_background",
        lambda **kwargs: started.append(kwargs) or ({"ok": True}, 202),
    )
    status, _, payload = _call(
        server,
        "/api/pipeline/run",
        method="POST",
        headers=headers,
        body=json.dumps(
            {"input_dir": str(tmp_path), "model": "large-v3"}
        ).encode("utf-8"),
    )
    assert status == 409
    assert payload["code"] == "asr_model_required"
    assert payload["confirmation_required"] is True
    assert started == []


def test_download_post_requires_session_and_explicit_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(asr_model_api, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(asr_model_api, "HF_CACHE_DIR", tmp_path / "cache" / "hub")
    server = MemoryTestServer()
    headers = _session_headers(server)
    body = json.dumps(
        {"model": "small", "source": "official", "confirmed": False}
    ).encode("utf-8")

    status, _, payload = _call(
        server,
        "/api/runtime/asr-model-download",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=body,
    )
    assert (status, payload["code"]) == (403, "invalid_local_session")

    status, _, payload = _call(
        server,
        "/api/runtime/asr-model-download",
        method="POST",
        headers=headers,
        body=body,
    )
    assert status == 400
    assert payload["code"] == "invalid_asr_model_download"
    assert not (tmp_path / "models").exists()
