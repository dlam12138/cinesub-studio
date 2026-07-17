"""Read-only, bounded SRT previews for local task-detail UI surfaces."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from subtitle_translate import read_srt

DEFAULT_PREVIEW_LIMIT = 60
MAX_PREVIEW_LIMIT = 100
PIPELINE_PREVIEW_ARTIFACTS = {"source", "translated", "bilingual", "review_needed"}
JOB_PREVIEW_ARTIFACTS = {"source", "translated", "review_needed"}
TIME_LINE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})$"
)


class SubtitlePreviewError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _bounded_integer(value: object, *, name: str, default: int, maximum: int | None = None) -> int:
    if value in (None, ""):
        return default
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise SubtitlePreviewError(f"{name} must be an integer") from exc
    if result < 0:
        raise SubtitlePreviewError(f"{name} must not be negative")
    if maximum is not None:
        result = min(result, maximum)
    return result


def preview_srt(path: Path, *, artifact: str, offset: object = 0, limit: object = None) -> dict:
    resolved = Path(path).resolve()
    if resolved.suffix.lower() != ".srt" or not resolved.is_file():
        raise SubtitlePreviewError("Subtitle artifact was not found", status=404)
    try:
        raw_nonempty = bool(resolved.read_text(encoding="utf-8-sig").strip())
        items = read_srt(resolved)
    except (OSError, UnicodeError) as exc:
        raise SubtitlePreviewError("Subtitle artifact could not be read", status=422) from exc
    if raw_nonempty and not items:
        raise SubtitlePreviewError("Subtitle artifact is malformed", status=422)

    parsed_offset = _bounded_integer(offset, name="offset", default=0)
    parsed_limit = _bounded_integer(
        limit, name="limit", default=DEFAULT_PREVIEW_LIMIT, maximum=MAX_PREVIEW_LIMIT
    )
    if parsed_limit == 0:
        raise SubtitlePreviewError("limit must be greater than zero")
    cues = []
    for item in items[parsed_offset:parsed_offset + parsed_limit]:
        match = TIME_LINE.fullmatch(item.time_line.strip())
        if not match:
            raise SubtitlePreviewError("Subtitle artifact contains an invalid time line", status=422)
        cues.append({
            "index": item.index,
            "start": match.group(1).replace(".", ","),
            "end": match.group(2).replace(".", ","),
            "text_lines": [line for line in item.text.splitlines() if line.strip()],
        })
    total = len(items)
    return {
        "ok": True,
        "artifact": artifact,
        "offset": parsed_offset,
        "limit": parsed_limit,
        "total": total,
        "has_more": parsed_offset + len(cues) < total,
        "cues": cues,
    }


def pipeline_subtitle_preview(
    *,
    task_id: str,
    artifact: str,
    offset: object,
    limit: object,
    resolver: Callable[[str, str], tuple[Path | None, str]],
) -> dict:
    artifact = str(artifact or "").strip()
    if artifact not in PIPELINE_PREVIEW_ARTIFACTS:
        raise SubtitlePreviewError("Unknown subtitle artifact")
    path, error = resolver(task_id, artifact)
    if path is None:
        raise SubtitlePreviewError(error or "Subtitle artifact was not found", status=404)
    payload = preview_srt(path, artifact=artifact, offset=offset, limit=limit)
    payload["task_id"] = task_id
    return payload


def job_subtitle_preview(
    *,
    job_id: str,
    artifact: str,
    offset: object,
    limit: object,
    job: dict | None,
    output_dir: Path,
) -> dict:
    artifact = str(artifact or "").strip()
    if artifact not in JOB_PREVIEW_ARTIFACTS:
        raise SubtitlePreviewError("Unknown subtitle artifact")
    if not job:
        raise SubtitlePreviewError("Job not found", status=404)
    key = {
        "source": "source_output",
        "translated": "translated_output",
        "review_needed": "review_needed",
    }[artifact]
    raw_path = str(job.get(key) or "").strip()
    if not raw_path:
        raise SubtitlePreviewError("Subtitle artifact was not found", status=404)
    path = Path(raw_path).resolve()
    try:
        inside_output = path.is_relative_to(output_dir.resolve())
    except OSError:
        inside_output = False
    if not inside_output:
        raise SubtitlePreviewError("Subtitle artifact was not found", status=404)
    payload = preview_srt(path, artifact=artifact, offset=offset, limit=limit)
    payload["job_id"] = job_id
    return payload
