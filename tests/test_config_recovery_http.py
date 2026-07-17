from __future__ import annotations

import json

import language_profile_store
import provider_store
from conftest import MemoryTestServer, json_test_handler
from web_server import Handler


def _call(server, path, *, method="GET", token="", body=None):
    headers = {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if token:
        headers["X-CineSub-Token"] = token
    status, _response_headers, payload = json_test_handler(
        server,
        Handler,
        method=method,
        path=path,
        headers=headers,
        body=data or b"",
    )
    return status, payload


def test_config_status_and_recovery_are_safe_and_token_protected(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    provider_path = config_dir / "providers.local.json"
    profile_path = config_dir / "language_profiles.local.json"
    secret = "sk-http-recovery-secret-value"
    original = ('{"api_key":"' + secret + '", broken').encode("utf-8")
    provider_path.write_bytes(original)

    monkeypatch.setattr(provider_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(provider_store, "CONFIG_PATH", provider_path)
    monkeypatch.setattr(language_profile_store, "CONFIG_PATH", profile_path)
    provider_store._cache = None
    language_profile_store._clear_cache()

    server = MemoryTestServer()
    try:
        status, session = _call(server, "/api/session")
        assert status == 200
        token = session["token"]

        status, payload = _call(server, "/api/config/status")
        assert status == 200
        stores = {item["store"]: item for item in payload["stores"]}
        assert stores["providers"]["status"] == "config_error"
        assert stores["language_profiles"]["status"] == "not_configured"
        assert secret not in json.dumps(payload)

        status, payload = _call(server, "/api/providers")
        assert (status, payload["code"]) == (409, "config_corrupt")

        request_body = {"store": "providers", "action": "backup_and_reset"}
        status, payload = _call(server, "/api/config/recover", method="POST", body=request_body)
        assert (status, payload["code"]) == (403, "invalid_local_session")

        status, payload = _call(
            server, "/api/config/recover", method="POST", token=token, body=request_body,
        )
        assert status == 200
        assert payload == {
            "ok": True,
            "store": "providers",
            "status": "ok",
            "backup_created": True,
        }
        backups = list(config_dir.glob("providers.local.corrupt.*.json"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == original
        assert secret not in capsys.readouterr().out

        status, payload = _call(
            server, "/api/config/recover", method="POST", token=token, body=request_body,
        )
        assert (status, payload["code"]) == (409, "config_recovery_failed")
    finally:
        server.shutdown()
        server.server_close()
