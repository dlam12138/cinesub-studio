import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


class MemoryTestServer(SimpleNamespace):
    def __init__(self):
        super().__init__(server_address=("127.0.0.1", 7860))

    def shutdown(self):
        return None

    def server_close(self):
        return None


class _MemorySocket:
    def __init__(self, request: bytes):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, _buffering=None):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)

    def close(self):
        return None


def call_test_handler(
    server,
    handler_class,
    *,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
):
    """Exercise BaseHTTPRequestHandler parsing without a listening socket/thread."""
    request_headers = {"Host": "127.0.0.1:7860", "Connection": "close", **(headers or {})}
    if body:
        request_headers["Content-Length"] = str(len(body))
    head = [f"{method} {path} HTTP/1.1"]
    head.extend(f"{name}: {value}" for name, value in request_headers.items())
    raw_request = ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body
    connection = _MemorySocket(raw_request)
    handler_class(connection, ("127.0.0.1", 12345), server)
    raw_response = connection.output.getvalue()
    header_bytes, _, response_body = raw_response.partition(b"\r\n\r\n")
    header_lines = header_bytes.decode("iso-8859-1").splitlines()
    status = int(header_lines[0].split(" ", 2)[1])
    response_headers = {}
    for line in header_lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            response_headers[name.strip()] = value.strip()
    return status, response_headers, response_body


def json_test_handler(
    server,
    handler_class,
    *,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
):
    status, response_headers, response_body = call_test_handler(
        server,
        handler_class,
        method=method,
        path=path,
        headers=headers,
        body=body,
    )
    return status, response_headers, json.loads(response_body)


@pytest.fixture
def tmp_path(request):
    """Workspace-local temp directory that avoids cleanup in restricted sandboxes."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:80]
    path = Path("work") / "pytest-artifacts" / f"{safe_name}-{uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=False)
    return path
