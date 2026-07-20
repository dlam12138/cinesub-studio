from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


SEMANTIC_REVIEW_VERSION = "semantic-review-v1"
SEMANTIC_PROMPT_VERSION = "semantic-review-prompts-v4-cross-line-fidelity"
SEMANTIC_CACHE_SCHEMA_VERSION = 1

REVIEW_ISSUE_TYPES = {
    "missing",
    "added",
    "mistranslation",
    "terminology",
    "pronoun",
    "continuity",
}
CONSISTENCY_ISSUE_TYPES = {
    "terminology",
    "person",
    "pronoun",
    "continuity",
    "common_word",
}
TERM_TYPES = {
    "person",
    "place",
    "organization",
    "technical_term",
    "work_title",
    "common_term",
    "other",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}
SEVERITY_VALUES = {"severe", "moderate"}

_TERM_TYPE_ALIASES = {
    "people": "person",
    "speaker": "person",
    "character": "person",
    "location": "place",
    "org": "organization",
    "company": "organization",
    "institution": "organization",
    "technical": "technical_term",
    "term": "technical_term",
    "concept": "technical_term",
    "title": "work_title",
    "book": "work_title",
    "film": "work_title",
    "common_word": "common_term",
    "common_noun": "common_term",
    "ordinary_word": "common_term",
    "proper_noun": "other",
}


def semantic_cache_path(translation_cache_path: Path) -> Path:
    return translation_cache_path.with_name(
        f"{translation_cache_path.stem}.semantic-review.json"
    )


def empty_semantic_cache() -> dict[str, Any]:
    return {
        "schema_version": SEMANTIC_CACHE_SCHEMA_VERSION,
        "strategy_version": SEMANTIC_REVIEW_VERSION,
        "prompt_version": SEMANTIC_PROMPT_VERSION,
        "analysis": {"scenes": {}},
        "batches": {},
    }


def load_semantic_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_semantic_cache()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_semantic_cache()
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SEMANTIC_CACHE_SCHEMA_VERSION
        or payload.get("strategy_version") != SEMANTIC_REVIEW_VERSION
        or payload.get("prompt_version") != SEMANTIC_PROMPT_VERSION
        or not isinstance(payload.get("analysis"), dict)
        or not isinstance(payload.get("batches"), dict)
    ):
        return empty_semantic_cache()
    payload["analysis"].setdefault("scenes", {})
    return payload


def save_semantic_cache(path: Path, payload: dict[str, Any]) -> None:
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


def _int_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _confidence(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        return "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "certain": "high",
        "strong": "high",
        "uncertain": "low",
        "weak": "low",
    }
    return aliases.get(normalized, normalized)


def _analysis_confidence(value: object) -> str:
    return _confidence(value) or "low"


def _term_type(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _TERM_TYPE_ALIASES.get(normalized, normalized)


def _validate_evidence_ids(value: object, allowed_ids: set[int]) -> list[int]:
    if not isinstance(value, list):
        raise RuntimeError("evidence_ids must be an array")
    result: list[int] = []
    for raw in value:
        item_id = _int_id(raw)
        if item_id is None or item_id not in allowed_ids:
            raise RuntimeError("evidence_ids contain an unknown subtitle id")
        if item_id not in result:
            result.append(item_id)
    return result


def _validate_speakers(value: object, allowed_ids: set[int]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError("speakers must be an array")
    result: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            raise RuntimeError("each speaker must be an object")
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "role": str(row.get("role") or "").strip(),
            "gender": str(row.get("gender") or "unknown").strip() or "unknown",
            "forms_of_address": [
                str(item).strip() for item in row.get("forms_of_address", [])
                if str(item).strip()
            ] if isinstance(row.get("forms_of_address", []), list) else [],
            "tone": str(row.get("tone") or "").strip(),
            "evidence_ids": _validate_evidence_ids(
                row.get("evidence_ids", []), allowed_ids
            ),
        })
    return result


def _validate_typed_glossary(
    value: object, allowed_ids: set[int]
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError("typed_glossary must be an array")
    result: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            raise RuntimeError("each typed glossary entry must be an object")
        source = str(row.get("source") or "").strip()
        if not source:
            continue
        target = str(row.get("target") or "").strip()
        term_type = _term_type(row.get("type")) or "other"
        if term_type not in TERM_TYPES:
            term_type = "other"
        confidence = _analysis_confidence(row.get("confidence"))
        if (
            term_type not in TERM_TYPES
            or confidence not in CONFIDENCE_VALUES
        ):
            raise RuntimeError(
                "invalid typed glossary entry "
                f"(source_present={bool(source)}, type={term_type!r}, "
                f"confidence={confidence!r})"
            )
        result.append({
            "source": source,
            "target": target,
            "type": term_type,
            "confidence": confidence,
            "note": str(row.get("note") or "").strip(),
            "evidence_ids": _validate_evidence_ids(
                row.get("evidence_ids", []), allowed_ids
            ),
        })
    return result


def _validate_asr_errors(
    value: object, allowed_ids: set[int]
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError("suspected_asr_errors must be an array")
    result: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            raise RuntimeError("each suspected ASR error must be an object")
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        reason = str(row.get("reason") or "").strip()
        confidence = _analysis_confidence(row.get("confidence"))
        if not token or not reason or confidence not in CONFIDENCE_VALUES:
            raise RuntimeError("invalid suspected ASR error")
        result.append({
            "token": token,
            "reason": reason,
            "confidence": confidence,
            "evidence_ids": _validate_evidence_ids(
                row.get("evidence_ids", []), allowed_ids
            ),
        })
    return result


def validate_scene_analysis(
    payload: dict[str, Any], expected_ids: Iterable[int]
) -> dict[str, Any]:
    allowed_ids = set(expected_ids)
    summary = str(payload.get("scene_summary") or "").strip()
    if not summary:
        raise RuntimeError("scene analysis requires scene_summary")
    return {
        "scene_summary": summary[:1600],
        "speakers": _validate_speakers(payload.get("speakers"), allowed_ids),
        "typed_glossary": _validate_typed_glossary(
            payload.get("typed_glossary"), allowed_ids
        ),
        "suspected_asr_errors": _validate_asr_errors(
            payload.get("suspected_asr_errors"), allowed_ids
        ),
    }


def validate_video_analysis(
    payload: dict[str, Any], expected_ids: Iterable[int]
) -> dict[str, Any]:
    allowed_ids = set(expected_ids)
    summary = str(payload.get("video_summary") or "").strip()
    if not summary:
        raise RuntimeError("video analysis requires video_summary")
    return {
        "video_summary": summary[:2400],
        "speakers": _validate_speakers(payload.get("speakers"), allowed_ids),
        "typed_glossary": _validate_typed_glossary(
            payload.get("typed_glossary"), allowed_ids
        ),
        "suspected_asr_errors": _validate_asr_errors(
            payload.get("suspected_asr_errors"), allowed_ids
        ),
    }


def validate_semantic_review(
    payload: dict[str, Any], expected_ids: Iterable[int]
) -> dict[int, list[dict[str, Any]]]:
    ids = list(expected_ids)
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise RuntimeError("semantic review requires an items array")
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("each semantic review item must be an object")
        item_id = _int_id(row.get("id"))
        raw_issues = row.get("issues")
        if item_id is None or item_id in result or not isinstance(raw_issues, list):
            raise RuntimeError("invalid or duplicate semantic review id")
        issues: list[dict[str, Any]] = []
        for issue in raw_issues:
            if not isinstance(issue, dict):
                raise RuntimeError("each semantic issue must be an object")
            issue_type = str(issue.get("type") or "").strip()
            severity = str(issue.get("severity") or "").strip().lower()
            confidence = _confidence(issue.get("confidence"))
            detail = str(issue.get("detail") or "").strip()
            suggestion = str(issue.get("suggestion") or "").strip()
            evidence = str(issue.get("evidence") or "").strip()
            if (
                issue_type not in REVIEW_ISSUE_TYPES
                or severity not in SEVERITY_VALUES
                or confidence not in CONFIDENCE_VALUES
                or not detail
                or not suggestion
                or not evidence
            ):
                raise RuntimeError(
                    "invalid semantic review issue "
                    f"(type={issue_type!r}, severity={severity!r}, "
                    f"confidence={confidence!r}, detail_present={bool(detail)}, "
                    f"suggestion_present={bool(suggestion)}, "
                    f"evidence_present={bool(evidence)})"
                )
            issues.append({
                "type": issue_type,
                "severity": severity,
                "confidence": confidence,
                "detail": detail,
                "suggestion": suggestion,
                "evidence": evidence,
            })
        result[item_id] = issues
    if set(result) != set(ids):
        raise RuntimeError("semantic review ids do not exactly match requested ids")
    return result


def validate_judgment(payload: dict[str, Any], expected_id: int) -> dict[str, Any]:
    item_id = _int_id(payload.get("id"))
    choice = str(payload.get("choice") or "").strip().upper()
    confidence = _confidence(payload.get("confidence"))
    reason = str(payload.get("reason") or "").strip()
    if (
        item_id != expected_id
        or choice not in {"A", "B", "TIE"}
        or confidence not in CONFIDENCE_VALUES
        or not reason
    ):
        raise RuntimeError("invalid semantic judgment")
    return {
        "id": expected_id,
        "choice": choice,
        "confidence": confidence,
        "reason": reason,
    }


def validate_consistency(
    payload: dict[str, Any], expected_ids: Iterable[int]
) -> list[dict[str, Any]]:
    allowed_ids = set(expected_ids)
    rows = payload.get("issues")
    if not isinstance(rows, list):
        raise RuntimeError("consistency output requires an issues array")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("each consistency issue must be an object")
        item_id = _int_id(row.get("id"))
        issue_type = str(row.get("type") or "").strip()
        detail = str(row.get("detail") or "").strip()
        related = _validate_evidence_ids(row.get("related_ids", []), allowed_ids)
        if item_id not in allowed_ids or issue_type not in CONSISTENCY_ISSUE_TYPES or not detail:
            raise RuntimeError("invalid consistency issue")
        result.append({
            "id": item_id,
            "type": issue_type,
            "detail": detail,
            "related_ids": related,
        })
    return result


def deterministic_ab_mapping(cache_key: str, item_id: int) -> dict[str, str]:
    digest = hashlib.sha256(
        f"{SEMANTIC_REVIEW_VERSION}|{cache_key}|{item_id}".encode("utf-8")
    ).digest()
    repair_label = "A" if digest[0] % 2 == 0 else "B"
    return {
        "initial": "B" if repair_label == "A" else "A",
        "repair": repair_label,
    }


_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_DUPLICATE_PUNCTUATION_RE = re.compile(r"([，。！？；：、])\1+")


def deterministic_subtitle_format(text: str, *, max_line_chars: int = 18) -> str:
    value = re.sub(r"[ \t\u3000]+", " ", str(text or "")).strip()
    value = re.sub(r"\s*\n\s*", "", value)
    if _HAN_RE.search(value):
        value = value.translate(str.maketrans({
            ",": "，",
            ".": "。",
            "!": "！",
            "?": "？",
            ";": "；",
            ":": "：",
        }))
    value = _DUPLICATE_PUNCTUATION_RE.sub(r"\1", value)
    if max_line_chars < 1 or len(value) <= max_line_chars:
        return value

    lines: list[str] = []
    remaining = value
    preferred = "，。！？；：、"
    while len(remaining) > max_line_chars:
        window = remaining[: max_line_chars + 1]
        split = max((window.rfind(mark) + 1 for mark in preferred), default=0)
        if split < max(4, max_line_chars // 2):
            split = max_line_chars
        lines.append(remaining[:split])
        remaining = remaining[split:]
    if remaining:
        lines.append(remaining)
    return "\n".join(lines)


def relevant_analysis(
    analysis: dict[str, Any], item_ids: Iterable[int], source_text: str
) -> dict[str, Any]:
    wanted = set(item_ids)
    glossary = []
    for term in analysis.get("typed_glossary", []):
        evidence = set(term.get("evidence_ids", []))
        source = str(term.get("source") or "")
        if evidence & wanted or source and source.casefold() in source_text.casefold():
            glossary.append(term)
    errors = [
        row for row in analysis.get("suspected_asr_errors", [])
        if set(row.get("evidence_ids", [])) & wanted
    ]
    return {
        "typed_glossary": glossary,
        "suspected_asr_errors": errors,
    }
