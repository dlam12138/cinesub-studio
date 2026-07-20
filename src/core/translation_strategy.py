from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


TRANSLATION_STRATEGY_VERSION = "three-pass-v2-budgeted"
VALID_TRANSLATION_STRATEGIES = {
    "standard",
    "three_pass",
    "semantic_review",
    "wenyi_review",
    "semantic_wenyi_review",
}
DEFAULT_SCENE_GAP_SECONDS = 30.0


def normalize_translation_strategy(value: object = None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    mode_value = value if isinstance(value, str) else raw.get("mode", "standard")
    mode = str(mode_value or "standard").strip().lower().replace("-", "_")
    if mode not in VALID_TRANSLATION_STRATEGIES:
        raise ValueError(
            "translation strategy mode must be 'standard', 'three_pass', "
            "'semantic_review', 'wenyi_review', or 'semantic_wenyi_review'"
        )
    try:
        scene_gap_seconds = float(raw.get("scene_gap_seconds", DEFAULT_SCENE_GAP_SECONDS))
    except (TypeError, ValueError) as exc:
        raise ValueError("translation scene_gap_seconds must be a number") from exc
    if not 1 <= scene_gap_seconds <= 600:
        raise ValueError("translation scene_gap_seconds must be between 1 and 600")
    return {"mode": mode, "scene_gap_seconds": scene_gap_seconds}


def parse_srt_range(time_line: str) -> tuple[float, float]:
    match = re.fullmatch(
        r"\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
        r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*",
        str(time_line or ""),
    )
    if not match:
        raise ValueError(f"invalid SRT time range: {time_line!r}")
    values = [int(value) for value in match.groups()]
    start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
    end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
    return start, end


def _split_at_largest_gaps(items: list[Any], max_batch_size: int) -> list[list[Any]]:
    if len(items) <= max_batch_size:
        return [items]
    minimum_side = max(1, min(max_batch_size // 4, 8))
    lower = minimum_side
    upper = len(items) - minimum_side
    if upper <= lower:
        split = max_batch_size
    else:
        split = max(
            range(lower, upper + 1),
            key=lambda index: (
                parse_srt_range(items[index].time_line)[0]
                - parse_srt_range(items[index - 1].time_line)[1],
                -abs(index - len(items) / 2),
            ),
        )
    return (
        _split_at_largest_gaps(items[:split], max_batch_size)
        + _split_at_largest_gaps(items[split:], max_batch_size)
    )


def build_scene_batches(
    items: list[Any],
    *,
    batch_size: int,
    context_window: int,
    scene_gap_seconds: float,
) -> list[dict[str, Any]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    scenes: list[list[Any]] = []
    current: list[Any] = []
    previous_end: float | None = None
    for item in items:
        start, end = parse_srt_range(item.time_line)
        if current and previous_end is not None and start - previous_end >= scene_gap_seconds:
            scenes.append(current)
            current = []
        current.append(item)
        previous_end = end
    if current:
        scenes.append(current)

    position = {item.index: index for index, item in enumerate(items)}
    result: list[dict[str, Any]] = []
    for scene_index, scene in enumerate(scenes, start=1):
        for batch_index, batch_items in enumerate(
            _split_at_largest_gaps(scene, batch_size), start=1
        ):
            first = position[batch_items[0].index]
            last = position[batch_items[-1].index] + 1
            payload: dict[str, Any] = {
                "scene_index": scene_index,
                "batch_index": batch_index,
                "items": [{"id": item.index, "text": item.text} for item in batch_items],
            }
            if context_window:
                before = items[max(0, first - context_window):first]
                after = items[last:last + context_window]
                if before:
                    payload["context_before"] = [
                        {"id": item.index, "text": item.text} for item in before
                    ]
                if after:
                    payload["context_after"] = [
                        {"id": item.index, "text": item.text} for item in after
                    ]
            result.append(payload)
    return result


def batch_cache_key(batch: dict[str, Any]) -> str:
    stable = {
        "scene_index": batch.get("scene_index"),
        "batch_index": batch.get("batch_index"),
        "items": batch.get("items", []),
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_stage_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "strategy_version": TRANSLATION_STRATEGY_VERSION, "batches": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "strategy_version": TRANSLATION_STRATEGY_VERSION, "batches": {}}
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("strategy_version") != TRANSLATION_STRATEGY_VERSION
        or not isinstance(payload.get("batches"), dict)
    ):
        return {"schema_version": 1, "strategy_version": TRANSLATION_STRATEGY_VERSION, "batches": {}}
    return payload


def save_stage_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def unwrap_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    match = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", value, re.DOTALL | re.IGNORECASE)
    if match:
        value = match.group(1).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("model output was not a valid JSON object") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("model output must be a JSON object")
    return payload


def validate_reflection(
    payload: dict[str, Any], expected_ids: Iterable[int]
) -> tuple[dict[int, list[str]], str]:
    ids = list(expected_ids)
    rows = payload.get("issues")
    if not isinstance(rows, list):
        raise RuntimeError("reflection output requires an issues array")
    result: dict[int, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("each reflection issue must be an object")
        item_id = row.get("id")
        if isinstance(item_id, str) and item_id.isdigit():
            item_id = int(item_id)
        issues = row.get("issues")
        if not isinstance(item_id, int) or not isinstance(issues, list):
            raise RuntimeError("each reflection issue requires id and issues")
        if item_id in result or not all(isinstance(value, str) for value in issues):
            raise RuntimeError("reflection ids must be unique and issue values must be strings")
        result[item_id] = [value.strip() for value in issues if value.strip()]
    if set(result) != set(ids):
        raise RuntimeError("reflection output ids do not exactly match the requested ids")
    summary = payload.get("scene_summary", "")
    if not isinstance(summary, str):
        raise RuntimeError("scene_summary must be a string")
    return result, summary.strip()[:1200]


def stage_cache_path(translation_cache_path: Path) -> Path:
    return translation_cache_path.with_name(
        f"{translation_cache_path.stem}.three-pass.json"
    )
