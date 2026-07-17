from __future__ import annotations

import json

import diagnostic_bundle
from conftest import MemoryTestServer, call_test_handler, json_test_handler
from web_server import Handler


def _request(server, path: str, method: str = "GET"):
    headers = {}
    body = b"{}" if method == "POST" else b""
    if method == "POST":
        _status, _response_headers, session = json_test_handler(
            server,
            Handler,
            method="GET",
            path="/api/session",
        )
        headers = {
            "Content-Type": "application/json",
            "X-CineSub-Token": session["token"],
        }
    return call_test_handler(
        server,
        Handler,
        method=method,
        path=path,
        headers=headers,
        body=body,
    )


def test_diagnostic_bundle_http_create_and_restricted_download() -> None:
    server = MemoryTestServer()
    try:
        status, _headers, body = _request(server, "/api/runtime/diagnostic-bundle", "POST")
        assert status == 201
        payload = json.loads(body)
        assert payload["restricted"] is True
        status, headers, body = _request(
            server,
            "/api/runtime/diagnostic-bundle/download?file=" + payload["file"],
        )
        assert status == 200
        assert headers["Content-Type"] == "application/zip"
        assert body[:2] == b"PK"
        status, _headers, _body = _request(
            server,
            "/api/runtime/diagnostic-bundle/download?file=../secret.zip",
        )
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_diagnostic_bundle_http_returns_409_when_busy() -> None:
    assert diagnostic_bundle._LOCK.acquire(blocking=False)
    server = MemoryTestServer()
    try:
        status, _headers, _body = _request(
            server,
            "/api/runtime/diagnostic-bundle",
            "POST",
        )
        assert status == 409
    finally:
        diagnostic_bundle._LOCK.release()
        server.shutdown()
        server.server_close()


def test_diagnostic_bundle_safety_scan_rejects_secret() -> None:
    try:
        diagnostic_bundle._validate_entries({"unsafe.txt": b"api_key=plain-secret-value"})
    except RuntimeError as exc:
        assert "safety scan failed" in str(exc)
    else:
        raise AssertionError("unsafe entry was accepted")
