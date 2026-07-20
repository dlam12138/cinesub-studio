from __future__ import annotations

import secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

MUTATING_METHODS = {"POST", "PUT", "DELETE"}
MULTIPART_PATHS = {"/api/jobs", "/api/runtime/import-package"}


def ensure_session_token(server: object) -> str:
    token = getattr(server, "cinesub_session_token", "")
    if not token:
        token = secrets.token_urlsafe(32)
        server.cinesub_session_token = token
    return token


def expected_origin(handler: BaseHTTPRequestHandler) -> str:
    port = int(handler.server.server_address[1])
    return f"http://127.0.0.1:{port}"


def validate_request(handler: BaseHTTPRequestHandler) -> tuple[int, str, str] | None:
    """Return an HTTP error tuple without ever exposing the session token."""
    expected = expected_origin(handler)
    expected_host = expected.removeprefix("http://")
    if handler.headers.get("Host", "").strip().lower() != expected_host.lower():
        return 403, "invalid_origin", "Request host is not the active local server."

    origin = handler.headers.get("Origin", "").strip()
    if origin and origin.rstrip("/").lower() != expected.lower():
        return 403, "invalid_origin", "Cross-origin requests are not allowed."

    method = handler.command.upper()
    if method not in MUTATING_METHODS:
        return None
    supplied = handler.headers.get("X-CineSub-Token", "")
    expected_token = ensure_session_token(handler.server)
    if not supplied or not secrets.compare_digest(supplied, expected_token):
        return 403, "invalid_local_session", "A valid local session is required."

    if method in {"POST", "PUT"}:
        path = urlparse(handler.path).path
        raw_content_type = handler.headers.get("Content-Type", "")
        content_type = raw_content_type.split(";", 1)[0].strip().lower()
        allowed = {"application/json"}
        if path in MULTIPART_PATHS:
            allowed.add("multipart/form-data")
        if content_type not in allowed:
            return 415, "unsupported_media_type", "Use application/json or a supported multipart upload."
        if content_type == "multipart/form-data" and "boundary=" not in raw_content_type.lower():
            return 415, "unsupported_media_type", "Multipart uploads require a boundary."
    return None


def security_headers() -> tuple[tuple[str, str], ...]:
    return (
        ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
    )
