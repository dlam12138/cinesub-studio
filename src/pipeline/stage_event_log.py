from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path


_LOCK = threading.Lock()
_SECRET = re.compile(r"(?i)(api[_-]?key|authorization|bearer|token|secret|password)(\s*[:=]\s*)(\S+)")
_ABSOLUTE = re.compile(r"(?i)(?:[a-z]:[\\/]|/home/|/Users/)[^\s\"']+")


def sanitize_event_text(value: object) -> str:
    text = str(value or "")[:1000]
    text = _SECRET.sub(r"\1\2[redacted]", text)
    return _ABSOLUTE.sub("[project-path]", text)


def write_stage_event(
    path: Path,
    *,
    task_id: str,
    stage: str,
    event: str,
    status: str,
    duration_seconds: float | None = None,
    returncode: int | None = None,
    error_category: str = "",
    summary: str = "",
) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": sanitize_event_text(task_id),
        "stage": stage,
        "event": event,
        "duration_seconds": round(duration_seconds, 6) if duration_seconds is not None else None,
        "status": status,
        "returncode": returncode,
        "error_category": sanitize_event_text(error_category),
        "summary": sanitize_event_text(summary),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
