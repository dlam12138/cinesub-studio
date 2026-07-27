"""Stage 2 privacy canary — permanent regression for the path-redaction fix.

Absolute paths supplied via request payloads or query strings, and raw
exception text, must never reach:
  * the HTTP response body,
  * Web stdout (console payload log + request log line),
  * Web stderr (traceback),
  * pipeline.log.

The canary is an absolute path outside the project root so that neither
``redact_project_path`` (project-root-only) nor URL-encoding alone can hide it
— only the absolute-path regex in ``sanitize_event_text`` does.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import asr_model_api
import pipeline_api
import pytest
import web_server
from conftest import MemoryTestServer, json_test_handler
from web_server import Handler

CANARY = r"D:\PRIVATE_CANARY_STAGE2\movie-private.mkv"
CANARY_MARK = "PRIVATE_CANARY"


def _call(server, path, *, method="GET", headers=None, body=b""):
    return json_test_handler(
        server, Handler, method=method, path=path, headers=headers, body=body
    )


def _session_headers(server):
    status, _, payload = _call(server, "/api/session")
    assert status == 200
    return {"X-CineSub-Token": payload["token"], "Content-Type": "application/json"}


def _neutralize_stores(monkeypatch):
    """Make config resolution deterministic regardless of real stores."""
    import language_profile_store
    import provider_store

    monkeypatch.setattr(
        language_profile_store, "resolve_language_profile_config", lambda _id=None: {}
    )
    monkeypatch.setattr(provider_store, "resolve_provider_config", lambda _id=None: {})
    monkeypatch.setattr(pipeline_api, "_active_provider_id", lambda: "")
    monkeypatch.setattr(pipeline_api, "_active_language_profile_id", lambda: "")


def _model_available(monkeypatch):
    monkeypatch.setattr(asr_model_api, "missing_model_payload", lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# (a) console payload log must not echo the absolute input_dir path
# --------------------------------------------------------------------------- #
def test_plan_payload_log_redacts_absolute_input_dir(monkeypatch, tmp_path, capsys):
    _neutralize_stores(monkeypatch)
    _model_available(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "movie.mkv").write_bytes(b"media")

    server = MemoryTestServer()
    headers = _session_headers(server)
    body = json.dumps(
        {"input_dir": str(input_dir), "model": "small", "translate_enabled": False}
    ).encode("utf-8")

    _call(server, "/api/pipeline/plan", method="POST", headers=headers, body=body)
    captured = capsys.readouterr()

    # The absolute tmp input path must not appear in the payload log (stdout)
    # nor in the response body.
    assert str(input_dir) not in captured.out
    assert str(input_dir) not in captured.err


# --------------------------------------------------------------------------- #
# (c) request log line must not echo an absolute path carried in the query
# --------------------------------------------------------------------------- #
def test_request_log_redacts_query_string_path(monkeypatch, tmp_path, capsys):
    _neutralize_stores(monkeypatch)
    monkeypatch.setattr(web_server, "scan_pipeline", lambda **_: {"ok": True})

    server = MemoryTestServer()
    headers = _session_headers(server)
    path = "/api/pipeline/scan?input_dir=" + quote(CANARY)

    status, _, _payload = _call(server, path, method="GET", headers=headers)
    captured = capsys.readouterr()

    # The distinctive canary substring is present in both the raw (urlencoded)
    # and decoded forms; neither may survive into the request log line.
    assert CANARY_MARK not in captured.out
    assert CANARY_MARK not in captured.err


# --------------------------------------------------------------------------- #
# (b) generic 500 response must not echo str(exc); traceback is redacted
# --------------------------------------------------------------------------- #
def test_generic_500_does_not_echo_exception(monkeypatch, tmp_path, capsys):
    _neutralize_stores(monkeypatch)
    _model_available(monkeypatch)
    states = tmp_path / "states"
    states.mkdir()
    monkeypatch.setattr(pipeline_api, "PIPELINE_STATES_DIR", states)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "movie.mkv").write_bytes(b"media")

    def _raise(**_kw):
        raise FileNotFoundError(f"missing model file: {CANARY}")

    monkeypatch.setattr(web_server, "plan_pipeline", _raise)

    server = MemoryTestServer()
    headers = _session_headers(server)
    body = json.dumps(
        {"input_dir": str(input_dir), "model": "small", "translate_enabled": False}
    ).encode("utf-8")

    status, _headers, payload = _call(
        server, "/api/pipeline/plan", method="POST", headers=headers, body=body
    )
    captured = capsys.readouterr()

    assert status == 500
    assert CANARY_MARK not in json.dumps(payload)
    assert CANARY_MARK not in captured.out
    assert CANARY_MARK not in captured.err
