from __future__ import annotations

from typing import Any, Iterable


TERM_TYPES = {"person", "place", "organization", "work", "technical"}
ASR_TYPES = {"low_confidence_name", "grammatically_impossible", "context_conflict"}
ISSUE_TYPES = {"missing", "added", "mistranslation", "terminology", "pronoun", "continuity"}
CONFIDENCE = {"high", "medium", "low"}
STYLE_TYPES = {"interview", "explanation", "speech", "dialogue", "mixed", "unknown"}
EQUIVALENCE_FIELDS = ("facts", "negation", "numbers", "entities", "references", "logic")


def _id(value: Any) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int):
        raise RuntimeError("id must be an integer")
    return value


def validate_analysis(payload: dict, valid_ids: Iterable[int]) -> dict:
    allowed = set(valid_ids)
    if not isinstance(payload, dict) or not isinstance(payload.get("video_summary"), str):
        raise RuntimeError("analysis requires video_summary")
    style = str(payload.get("discourse_style") or "unknown")
    if style not in STYLE_TYPES:
        raise RuntimeError("invalid discourse_style")
    scenes = payload.get("scene_summaries", [])
    if not isinstance(scenes, list):
        raise RuntimeError("scene_summaries must be a list")
    glossary: list[dict] = []
    for row in payload.get("typed_glossary", []):
        if not isinstance(row, dict) or row.get("type") not in TERM_TYPES:
            raise RuntimeError("invalid typed glossary entry")
        evidence = [_id(value) for value in row.get("evidence_ids", [])]
        if not evidence or not set(evidence) <= allowed:
            raise RuntimeError("glossary evidence ids are invalid")
        source, target = str(row.get("source") or "").strip(), str(row.get("target") or "").strip()
        if not source or not target or row.get("confidence") not in CONFIDENCE:
            raise RuntimeError("glossary entry is incomplete")
        glossary.append({**row, "source": source, "target": target, "evidence_ids": evidence})
    warnings: list[dict] = []
    for row in payload.get("asr_warnings", []):
        if not isinstance(row, dict) or row.get("category") not in ASR_TYPES:
            raise RuntimeError("invalid ASR warning")
        item_id = _id(row.get("id"))
        if item_id not in allowed or row.get("confidence") not in CONFIDENCE:
            raise RuntimeError("invalid ASR warning id/confidence")
        warnings.append({**row, "id": item_id})
    return {
        "video_summary": payload["video_summary"].strip()[:4000],
        "scene_summaries": scenes,
        "discourse_style": style,
        "typed_glossary": glossary,
        "asr_warnings": warnings,
    }


def validate_items(payload: dict, expected_ids: Iterable[int]) -> dict[int, str]:
    expected = list(expected_ids)
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("items must be an array")
    result: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("item must be an object")
        item_id = _id(row.get("id"))
        text = str(row.get("translation") or "").strip()
        if item_id in result or not text:
            raise RuntimeError("duplicate id or empty translation")
        result[item_id] = text
    if set(result) != set(expected):
        raise RuntimeError("returned ids do not exactly match requested ids")
    return result


def validate_review(payload: dict, valid_ids: Iterable[int]) -> tuple[list[dict], list[dict]]:
    allowed = set(valid_ids)
    issues: list[dict] = []
    for row in payload.get("issues", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            raise RuntimeError("review issue must be an object")
        item_id = _id(row.get("id"))
        if item_id not in allowed or row.get("type") not in ISSUE_TYPES:
            raise RuntimeError("review issue is invalid")
        if row.get("confidence") not in CONFIDENCE or not str(row.get("detail") or "").strip():
            raise RuntimeError("review issue is incomplete")
        issues.append({**row, "id": item_id})
    terms: list[dict] = []
    for row in payload.get("terms", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict) or row.get("type") not in TERM_TYPES:
            raise RuntimeError("review term is invalid")
        if row.get("confidence") not in CONFIDENCE:
            raise RuntimeError("review term confidence is invalid")
        evidence = [_id(value) for value in row.get("evidence_ids", [])]
        if not evidence or not set(evidence) <= allowed:
            raise RuntimeError("review term evidence is invalid")
        source, target = str(row.get("source") or "").strip(), str(row.get("target") or "").strip()
        if not source or not target:
            raise RuntimeError("review term is incomplete")
        terms.append({**row, "source": source, "target": target, "evidence_ids": evidence})
    return issues, terms


def validate_judgment(
    payload: dict,
    item_id: int,
    *,
    equivalence: bool = False,
    strict: bool = False,
) -> dict:
    if not isinstance(payload, dict) or _id(payload.get("id")) != item_id:
        raise RuntimeError("judgment id mismatch")
    if payload.get("choice") not in {"A", "B", "TIE"}:
        raise RuntimeError("invalid judgment choice")
    if payload.get("confidence") not in CONFIDENCE:
        raise RuntimeError("invalid judgment confidence")
    if equivalence or strict:
        for field in EQUIVALENCE_FIELDS:
            if not isinstance(payload.get(field), bool):
                raise RuntimeError(
                    f"equivalence field {field!r} must be boolean"
                )
    if strict:
        for field in ("issue_resolved", "no_new_error"):
            if not isinstance(payload.get(field), bool):
                raise RuntimeError(
                    f"strict judgment field {field!r} must be boolean"
                )
    return dict(payload)


def validate_consistency(payload: dict, valid_ids: Iterable[int]) -> list[dict]:
    allowed = set(valid_ids)
    groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        raise RuntimeError("consistency groups must be a list")
    result = []
    for group in groups:
        if not isinstance(group, dict) or group.get("term_type") not in TERM_TYPES:
            raise RuntimeError("invalid consistency group")
        if group.get("classification") not in {"definite_error", "acceptable_variant"}:
            raise RuntimeError("invalid consistency classification")
        variants = group.get("variants")
        if not isinstance(variants, list):
            raise RuntimeError("consistency variants must be a list")
        for variant in variants:
            ids = [_id(value) for value in variant.get("ids", [])]
            if not set(ids) <= allowed:
                raise RuntimeError("consistency variant ids are invalid")
            variant["ids"] = ids
        result.append(group)
    return result
