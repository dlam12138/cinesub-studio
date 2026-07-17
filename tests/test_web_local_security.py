from __future__ import annotations

from types import SimpleNamespace

from conftest import MemoryTestServer, json_test_handler
from web_server import Handler


class _DisconnectedWriter:
    def write(self, _data):
        raise ConnectionAbortedError("client closed")


def _serve():
    server = MemoryTestServer()
    return server, None, server


def _call(base, path, *, method="GET", headers=None, data=None):
    return json_test_handler(
        base,
        Handler,
        method=method,
        path=path,
        headers=headers,
        body=data or b"",
    )


def test_session_token_guards_mutations_and_is_not_logged(capsys):
    server, thread, base = _serve()
    try:
        status, headers, session = _call(base, "/api/session")
        assert status == 200
        assert session["token"]
        assert headers["X-Content-Type-Options"] == "nosniff"

        status, _, payload = _call(
            base, "/api/storage/cleanup", method="POST",
            headers={"Content-Type": "application/json"}, data=b"{}",
        )
        assert (status, payload["code"]) == (403, "invalid_local_session")

        status, _, payload = _call(
            base, "/api/storage/cleanup", method="POST",
            headers={"Content-Type": "application/json", "X-CineSub-Token": "wrong-token"},
            data=b"{}",
        )
        assert (status, payload["code"]) == (403, "invalid_local_session")

        status, _, payload = _call(
            base, "/api/storage/cleanup", method="POST",
            headers={"Content-Type": "application/json", "X-CineSub-Token": session["token"]},
            data=b"{}",
        )
        assert status == 200
        assert session["token"] not in capsys.readouterr().out
    finally:
        server.shutdown()
        server.server_close()


def test_origin_host_and_content_type_are_strict():
    server, thread, base = _serve()
    try:
        _, _, session = _call(base, "/api/session")
        token_headers = {"X-CineSub-Token": session["token"], "Content-Type": "application/json"}
        status, _, payload = _call(base, "/api/storage/cleanup", method="POST", headers={**token_headers, "Origin": "https://evil.invalid"}, data=b"{}")
        assert (status, payload["code"]) == (403, "invalid_origin")
        status, _, payload = _call(base, "/api/storage/cleanup", method="POST", headers={"X-CineSub-Token": session["token"], "Content-Type": "text/plain"}, data=b"{}")
        assert (status, payload["code"]) == (415, "unsupported_media_type")
        status, _, payload = _call(
            base, "/api/storage/cleanup", method="POST",
            headers={**token_headers, "Origin": "http://127.0.0.1:7860"}, data=b"{}",
        )
        assert status == 200
        status, _, payload = _call(base, "/api/session", headers={"Host": "localhost.invalid"})
        assert (status, payload["code"]) == (403, "invalid_origin")
    finally:
        server.shutdown()
        server.server_close()


def test_malformed_json_and_unsupported_multipart_fail_before_business_logic():
    server, thread, base = _serve()
    try:
        _, _, session = _call(base, "/api/session")
        token = session["token"]
        status, _, payload = _call(
            base, "/api/providers", method="POST",
            headers={"X-CineSub-Token": token, "Content-Type": "application/json"},
            data=b'{"id":',
        )
        assert (status, payload["code"]) == (400, "invalid_json")

        status, _, payload = _call(
            base, "/api/jobs", method="POST",
            headers={"X-CineSub-Token": token, "Content-Type": "multipart/form-data"},
            data=b"",
        )
        assert (status, payload["code"]) == (415, "unsupported_media_type")

        boundary = "CineSubBoundary"
        multipart = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nsmall\r\n"
            f"--{boundary}--\r\n"
        ).encode("ascii")
        status, _, _ = _call(
            base, "/api/jobs", method="POST",
            headers={
                "X-CineSub-Token": token,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            data=multipart,
        )
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()


def test_json_response_tolerates_local_client_disconnect():
    response = SimpleNamespace(
        wfile=_DisconnectedWriter(),
        send_response=lambda _status: None,
        send_header=lambda _name, _value: None,
        end_headers=lambda: None,
        _send_security_headers=lambda: None,
    )

    Handler.send_json(response, {"ok": True})
