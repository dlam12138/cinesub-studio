from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any


MIN_CUE_SECONDS = 0.001
OVERLAP_GAP_SECONDS = 0.001


def assemble_routed_srt(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    """Assemble routed, timestamped ASR segments into a UTF-8 SRT file."""
    cues: list[dict[str, Any]] = []
    metadata = {
        "cue_count": 0,
        "dropped_segment_count": 0,
        "adjusted_overlap_count": 0,
        "skipped_window_count": 0,
        "selected_run_counts": {},
    }

    windows = payload.get("windows") if isinstance(payload, dict) else None
    if not isinstance(windows, list):
        raise ValueError("routed payload requires windows list")

    for window in windows:
        if not isinstance(window, dict):
            metadata["skipped_window_count"] += 1
            continue

        selected_run = str(window.get("selected_run") or "unknown")
        metadata["selected_run_counts"][selected_run] = (
            metadata["selected_run_counts"].get(selected_run, 0) + 1
        )

        segments = window.get("segments")
        if not isinstance(segments, list):
            metadata["skipped_window_count"] += 1
            continue

        try:
            window_start = float(window.get("start_seconds", 0.0) or 0.0)
            window_end = float(window.get("end_seconds", window_start) or window_start)
        except (TypeError, ValueError):
            metadata["skipped_window_count"] += 1
            continue
        if not _finite(window_start) or not _finite(window_end) or window_end < window_start:
            metadata["skipped_window_count"] += 1
            continue

        timestamp_scope = str(window.get("timestamp_scope") or "local").strip().lower()
        for segment in segments:
            cue = _segment_to_cue(
                segment=segment,
                window_start=window_start,
                window_end=window_end,
                timestamp_scope=timestamp_scope,
            )
            if cue is None:
                metadata["dropped_segment_count"] += 1
                continue
            cue["selected_run"] = selected_run
            cues.append(cue)

    cues.sort(key=lambda item: (item["start"], item["end"], item["text"]))
    for index in range(1, len(cues)):
        previous = cues[index - 1]
        current = cues[index]
        if current["start"] < previous["end"]:
            adjusted_start = previous["end"] + OVERLAP_GAP_SECONDS
            if adjusted_start >= current["end"]:
                current["end"] = adjusted_start + MIN_CUE_SECONDS
            current["start"] = adjusted_start
            metadata["adjusted_overlap_count"] += 1

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f"{output_path.name}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as file:
            for index, cue in enumerate(cues, start=1):
                file.write(f"{index}\n")
                file.write(f"{_format_srt_time(cue['start'])} --> {_format_srt_time(cue['end'])}\n")
                file.write(f"{cue['text']}\n\n")
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    metadata["cue_count"] = len(cues)
    return metadata


def _segment_to_cue(
    *,
    segment: Any,
    window_start: float,
    window_end: float,
    timestamp_scope: str,
) -> dict[str, Any] | None:
    if not isinstance(segment, dict):
        return None
    text = str(segment.get("text") or "").strip()
    if not text:
        return None
    try:
        start = float(segment["start"])
        end = float(segment["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _finite(start) or not _finite(end) or end <= start:
        return None

    if timestamp_scope != "global":
        start = window_start + start
        end = window_start + end
        start = max(window_start, start)
        end = min(window_end, end)
        if end <= start:
            return None

    return {
        "start": max(0.0, start),
        "end": max(0.0, end),
        "text": text,
    }


def _finite(value: float) -> bool:
    return math.isfinite(value)


def _format_srt_time(seconds: float) -> str:
    milliseconds_total = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"
