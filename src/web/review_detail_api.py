from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable


class ReviewDetailError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _integer(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ReviewDetailError("offset and limit must be integers") from exc
    if parsed < minimum or parsed > maximum:
        raise ReviewDetailError(
            f"value must be between {minimum} and {maximum}"
        )
    return parsed


def _load_report(path_text: str, output_dir: Path) -> tuple[Path, dict]:
    if not str(path_text or "").strip():
        raise ReviewDetailError("Review report is not available", 404)
    try:
        path = Path(path_text).resolve()
        root = output_dir.resolve()
    except OSError as exc:
        raise ReviewDetailError("Review report path is invalid", 404) from exc
    if not path.is_relative_to(root):
        raise ReviewDetailError("Review report path is outside project output", 403)
    if not path.is_file():
        raise ReviewDetailError("Review report is missing", 404)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewDetailError("Review report is malformed", 422) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("review_items"), list):
        raise ReviewDetailError("Review report has an unsupported schema", 422)
    for row in payload["review_items"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("category"), str)
            or not isinstance(row.get("id"), int)
        ):
            raise ReviewDetailError("Review report contains malformed items", 422)
    return path, payload


def review_detail(
    *,
    report_path: str,
    output_dir: Path,
    categories: object = "",
    offset: object = 0,
    limit: object = 50,
) -> dict[str, Any]:
    path, report = _load_report(report_path, output_dir)
    raw_categories = (
        categories if isinstance(categories, (list, tuple))
        else str(categories or "").split(",")
    )
    selected = {
        str(value).strip() for value in raw_categories if str(value).strip()
    }
    items = report["review_items"]
    counts = Counter(str(row["category"]) for row in items)
    filtered = [
        row for row in items
        if not selected or str(row["category"]) in selected
    ]
    page_offset = _integer(offset, default=0, minimum=0, maximum=10_000_000)
    page_limit = _integer(limit, default=50, minimum=1, maximum=100)
    return {
        "ok": True,
        "strategy_mode": report.get("strategy_mode", ""),
        "strategy_version": report.get("strategy_version", ""),
        "upstream": report.get("upstream", {}),
        "report": path.name,
        "category_counts": dict(sorted(counts.items())),
        "selected_categories": sorted(selected),
        "total": len(filtered),
        "offset": page_offset,
        "limit": page_limit,
        "review_items": filtered[page_offset:page_offset + page_limit],
    }


def job_review_detail(
    *,
    job: dict | None,
    output_dir: Path,
    categories: object = "",
    offset: object = 0,
    limit: object = 50,
) -> dict[str, Any]:
    if job is None:
        raise ReviewDetailError("Job not found", 404)
    return review_detail(
        report_path=str(job.get("semantic_review_report") or ""),
        output_dir=output_dir,
        categories=categories,
        offset=offset,
        limit=limit,
    )


def pipeline_review_detail(
    *,
    task_id: str,
    states_dir: Path,
    output_dir: Path,
    categories: object = "",
    offset: object = 0,
    limit: object = 50,
) -> dict[str, Any]:
    clean = str(task_id or "").strip()
    if (
        not clean or clean in {".", ".."} or "/" in clean or "\\" in clean
        or Path(clean).name != clean
    ):
        raise ReviewDetailError("Invalid task id", 400)
    state_path = states_dir / f"{clean}.state.json"
    try:
        if not state_path.resolve().is_relative_to(states_dir.resolve()):
            raise ReviewDetailError("Invalid task id", 400)
    except OSError as exc:
        raise ReviewDetailError("Invalid task id", 400) from exc
    if not state_path.is_file():
        raise ReviewDetailError("Task state not found", 404)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewDetailError("Task state is malformed", 422) from exc
    return review_detail(
        report_path=str(state.get("semantic_review_report") or ""),
        output_dir=output_dir,
        categories=categories,
        offset=offset,
        limit=limit,
    )
