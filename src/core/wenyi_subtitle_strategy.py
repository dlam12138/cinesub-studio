"""Immutable-SRT adapter for the pinned WenYi v0.3.2 concepts.

This module contains subtitle-specific policy and deterministic safeguards.
It does not call a provider directly; subtitle_translate supplies the existing
request tracker and Provider transport.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from wenyi_vendor import (
    ADAPTER_VERSION,
    CACHE_SCHEMA_VERSION,
    PROMPT_VERSION,
    REPORT_SCHEMA_VERSION,
    UPSTREAM_COMMIT,
    UPSTREAM_LICENSE,
    UPSTREAM_PROJECT,
    UPSTREAM_RELEASE,
)


WENYI_STRATEGY_VERSION = (
    f"wenyi-review:{UPSTREAM_RELEASE}:{UPSTREAM_COMMIT[:8]}:{ADAPTER_VERSION}"
)
SEMANTIC_WENYI_STRATEGY_VERSION = (
    f"semantic-wenyi-review-v1:{UPSTREAM_RELEASE}:"
    f"{UPSTREAM_COMMIT[:8]}:{ADAPTER_VERSION}"
)
MODEL_TIERS = {"fast": "flash", "cheap": "pro", "strong": "pro"}
TERM_TYPES = {"person", "place", "organization", "work", "technical"}

_CONTINUATION_START = re.compile(
    r"^\s*(?:and|but|or|because|since|which|who|that|so|yet|if|then|"
    r"mais|et|ou|car|parce que|puisque|puisqu['’]|qui|que|donc|pourtant|"
    r"do it|le faire)\b",
    re.I,
)
_STRONG_CROSS_REFERENCE = re.compile(
    r"^\s*(?:do it|le faire|which|who|qui|que)\b",
    re.I,
)
_SPLIT_RELATION = re.compile(
    r"(?:\b(?:not|never|ne|more|less|plus|moins|because|since|car|puisque|"
    r"parce)\b|n['’])[^.\n!?。！？…]*\n\s*"
    r"(?:pas|plus|jamais|than|que|because|since|car|puisque|puisqu['’]|"
    r"parce|do it|le faire)\b",
    re.I,
)
_END_PUNCTUATION = re.compile(r"[.!?。！？…][\"'”’）)]*\s*$")


def resolve_model_tier(tier: str, *, flash_model: str, pro_model: str) -> str:
    mapped = MODEL_TIERS.get(str(tier or "").strip().lower())
    if mapped is None:
        raise ValueError(f"unknown WenYi model tier: {tier!r}")
    if mapped == "flash":
        if not str(flash_model or "").strip():
            raise ValueError("wenyi_review requires a configured Flash/llm_model")
        return str(flash_model).strip()
    if not str(pro_model or "").strip():
        raise ValueError(
            "wenyi_review requires translation_quality_model for cheap/strong "
            "stages; Pro will not fall back to Flash"
        )
    return str(pro_model).strip()


def immutable_records(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {"id": int(item.index), "time_line": str(item.time_line), "source": str(item.text)}
        for item in items
    ]


def assert_immutable(records: list[dict], items: Iterable[Any]) -> None:
    actual = immutable_records(items)
    if actual != records:
        raise RuntimeError("wenyi_review attempted to change subtitle ids, order, timing, or source")


def detect_cross_line_windows(items: list[Any]) -> list[dict[str, Any]]:
    """Return deterministic 2-3 cue candidates; never mutates cue boundaries."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, ...]] = set()
    for index in range(1, len(items)):
        previous = str(items[index - 1].text or "").strip()
        current = str(items[index].text or "").strip()
        reasons: list[str] = []
        previous_unfinished = bool(
            previous and not _END_PUNCTUATION.search(previous)
        )
        if previous_unfinished and _CONTINUATION_START.search(current):
            reasons.append("continuation_start")
        if _STRONG_CROSS_REFERENCE.search(current):
            reasons.append("cross_reference")
        if _SPLIT_RELATION.search(f"{previous}\n{current}"):
            reasons.append("split_relation")
        if not reasons:
            continue
        start = max(0, index - (2 if index > 1 and not _END_PUNCTUATION.search(
            str(items[index - 2].text or "").strip()
        ) else 1))
        window = tuple(int(item.index) for item in items[start:index + 1])
        if window in seen:
            continue
        seen.add(window)
        result.append({"ids": list(window), "reasons": sorted(set(reasons))})
    return result


def merge_profile_glossary(
    profile_glossary: object, initial_terms: list[dict], dynamic_terms: list[dict]
) -> list[dict]:
    """Profile mappings win; only high-confidence proper/technical terms persist."""
    merged: dict[str, dict] = {}
    for row in dynamic_terms + initial_terms:
        if (
            isinstance(row, dict)
            and row.get("type") in TERM_TYPES
            and row.get("confidence") == "high"
            and str(row.get("source") or "").strip()
            and str(row.get("target") or "").strip()
        ):
            key = str(row["source"]).strip().casefold()
            merged.setdefault(key, dict(row))
    if isinstance(profile_glossary, list):
        for row in profile_glossary:
            if not isinstance(row, dict):
                continue
            source = str(row.get("source") or "").strip()
            target = str(row.get("target") or "").strip()
            if source and target:
                merged[source.casefold()] = {
                    "source": source,
                    "target": target,
                    "type": str(row.get("type") or "technical"),
                    "confidence": "high",
                    "evidence_ids": [],
                    "authority": "language_profile",
                }
    return sorted(merged.values(), key=lambda row: row["source"].casefold())


def relevant_glossary(terms: list[dict], source_text: str) -> list[dict]:
    folded = source_text.casefold()
    return [row for row in terms if str(row.get("source") or "").casefold() in folded]


def deterministic_mapping(scope: str, item_id: int) -> dict[str, str]:
    digest = hashlib.sha256(f"{scope}:{item_id}".encode("utf-8")).digest()
    return {"current": "A", "candidate": "B"} if digest[0] % 2 == 0 else {
        "current": "B", "candidate": "A"
    }


def stable_sample(ids: Iterable[int], task_seed: str, limit: int = 12) -> list[int]:
    ranked = sorted(
        set(int(value) for value in ids),
        key=lambda item_id: hashlib.sha256(
            f"{task_seed}:{item_id}".encode("utf-8")
        ).hexdigest(),
    )
    return ranked[:max(0, limit)]


def cache_path(
    translation_cache_path: Path, *, strategy_mode: str = "wenyi_review"
) -> Path:
    suffix = (
        f"wenyi-{UPSTREAM_RELEASE}-{ADAPTER_VERSION}"
        if strategy_mode == "wenyi_review"
        else f"semantic-wenyi-{UPSTREAM_RELEASE}-{ADAPTER_VERSION}"
    )
    return translation_cache_path.with_name(
        f"{translation_cache_path.stem}.{suffix}.json"
    )


def report_path(
    output_path: Path, *, strategy_mode: str = "wenyi_review"
) -> Path:
    return output_path.with_name(
        f"{output_path.stem}.{strategy_mode}_report.json"
    )


def review_srt_path(
    output_path: Path, *, strategy_mode: str = "wenyi_review"
) -> Path:
    return output_path.with_name(
        f"{output_path.stem}.{strategy_mode}_needed.srt"
    )


def new_cache(*, fingerprint: str) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "strategy_version": WENYI_STRATEGY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "fingerprint": fingerprint,
        "analysis": None,
        "batches": {},
        "consistency": None,
    }


def load_cache(path: Path, *, fingerprint: str) -> dict[str, Any]:
    if not path.is_file():
        return new_cache(fingerprint=fingerprint)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return new_cache(fingerprint=fingerprint)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CACHE_SCHEMA_VERSION
        or payload.get("strategy_version") != WENYI_STRATEGY_VERSION
        or payload.get("prompt_version") != PROMPT_VERSION
        or payload.get("fingerprint") != fingerprint
        or not isinstance(payload.get("batches"), dict)
    ):
        return new_cache(fingerprint=fingerprint)
    return payload


def save_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def cache_fingerprint(
    *, records: list[dict], profile_glossary: object, profile_prompt: str,
    flash_model: str, pro_model: str, target_language: str, context_window: int,
    strategy_mode: str = "wenyi_review",
    baseline_translations: object = None,
) -> str:
    payload = {
        "upstream": [UPSTREAM_RELEASE, UPSTREAM_COMMIT],
        "adapter": ADAPTER_VERSION,
        "prompt": PROMPT_VERSION,
        "records": records,
        "profile_glossary": profile_glossary,
        "profile_prompt": profile_prompt,
        "models": {"fast": flash_model, "cheap": pro_model, "strong": pro_model},
        "target_language": target_language,
        "context_window": context_window,
        "strategy_mode": strategy_mode,
        "baseline_translations": baseline_translations,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def report_envelope(
    *, strategy_mode: str = "wenyi_review", **payload: Any
) -> dict[str, Any]:
    hybrid = strategy_mode == "semantic_wenyi_review"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "strategy_mode": strategy_mode,
        "strategy_version": (
            SEMANTIC_WENYI_STRATEGY_VERSION
            if hybrid else WENYI_STRATEGY_VERSION
        ),
        "prompt_version": PROMPT_VERSION,
        "upstream": {
            "project": UPSTREAM_PROJECT,
            "release": UPSTREAM_RELEASE,
            "commit": UPSTREAM_COMMIT,
            "license": UPSTREAM_LICENSE,
        },
        "promotion_status": "no_go",
        "web_exposed": True,
        **payload,
    }


def run_wenyi_review(
    *,
    items: list[Any],
    cached_translations: dict[int, str],
    translation_cache_path: Path,
    output_path: Path,
    target_language: str,
    profile_prompt: str,
    profile_glossary: object,
    flash_model: str,
    pro_model: str,
    batch_size: int,
    temperature: float,
    api_provider: str,
    api_base: str,
    api_key: str,
    context_window: int,
    scene_gap_seconds: float,
    max_cps_zh: float,
    max_chars_per_subtitle_zh: int,
    tracker: Any,
    summary: Any,
    progress_callback: Any = None,
    strategy_mode: str = "wenyi_review",
    baseline_translations: dict[int, str] | None = None,
    source_warning_ids: Iterable[int] = (),
    semantic_baseline_report: dict[str, Any] | None = None,
) -> None:
    """Run the subtitle adapter using subtitle_translate's existing transport."""
    import subtitle_translate as transport
    from translation_strategy import batch_cache_key, build_scene_batches
    from wenyi_vendor.context import RollingContext
    from wenyi_vendor import prompts
    from wenyi_vendor.validation import (
        validate_analysis,
        validate_consistency,
        validate_items,
        validate_judgment,
        validate_review,
    )
    from translation_reliability import TranslationTotalRequestLimitExceeded

    def propagate_request_limit(exc: BaseException) -> None:
        if isinstance(exc, TranslationTotalRequestLimitExceeded):
            raise exc

    if strategy_mode not in {"wenyi_review", "semantic_wenyi_review"}:
        raise ValueError(f"unsupported WenYi strategy mode: {strategy_mode}")
    resolve_model_tier("fast", flash_model=flash_model, pro_model=pro_model)
    resolve_model_tier("cheap", flash_model=flash_model, pro_model=pro_model)
    records = immutable_records(items)
    all_ids = [row["id"] for row in records]
    item_by_id = {item.index: item for item in items}
    original_by_id = {item.index: item.text for item in items}
    normalized_baseline: dict[int, str] | None = None
    if baseline_translations is not None:
        normalized_baseline = validate_items(
            {
                "items": [
                    {"id": item_id, "translation": text}
                    for item_id, text in baseline_translations.items()
                ]
            },
            all_ids,
        )
    fingerprint = cache_fingerprint(
        records=records,
        profile_glossary=profile_glossary,
        profile_prompt=profile_prompt,
        flash_model=flash_model,
        pro_model=pro_model,
        target_language=target_language,
        context_window=context_window,
        strategy_mode=strategy_mode,
        baseline_translations=(
            {str(key): value for key, value in sorted(normalized_baseline.items())}
            if normalized_baseline is not None else None
        ),
    )
    stages_path = cache_path(
        translation_cache_path, strategy_mode=strategy_mode
    )
    stages = load_cache(stages_path, fingerprint=fingerprint)
    stage_errors: list[dict[str, str]] = []

    analysis = stages.get("analysis")
    try:
        analysis = validate_analysis(analysis, all_ids)
    except RuntimeError:
        try:
            before = tracker.actual_requests
            analysis_payload = transport._request_json_object_stage(
                stage="wenyi_analysis",
                batch={"items": records},
                prompt=prompts.analysis_prompt(target_language),
                model=pro_model,
                temperature=0.0,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                validator=lambda value: validate_analysis(value, all_ids),
            )
            analysis = validate_analysis(analysis_payload, all_ids)
            summary.wenyi_analysis_requests += tracker.actual_requests - before
            stages["analysis"] = analysis
            save_cache(stages_path, stages)
        except Exception as exc:
            propagate_request_limit(exc)
            analysis = {
                "video_summary": "",
                "scene_summaries": [],
                "discourse_style": "unknown",
                "typed_glossary": [],
                "asr_warnings": [],
            }
            stage_errors.append({"stage": "analysis", "error": str(exc)[:500]})
    blocked_source_ids = {
        int(value) for value in source_warning_ids
        if isinstance(value, int) or str(value).isdigit()
    }
    blocked_source_ids.update(
        warning["id"] for warning in analysis["asr_warnings"]
    )

    batches = build_scene_batches(
        items,
        batch_size=batch_size,
        context_window=context_window,
        scene_gap_seconds=scene_gap_seconds,
    )
    scene_summaries = {
        int(row.get("scene_index")): str(row.get("summary") or "")
        for row in analysis["scene_summaries"]
        if isinstance(row, dict) and str(row.get("scene_index") or "").isdigit()
    }
    cross_windows = detect_cross_line_windows(items)
    cross_by_last: dict[int, list[dict]] = {}
    for window in cross_windows:
        cross_by_last.setdefault(window["ids"][-1], []).append(window)
    rolling = RollingContext()
    dynamic_terms: list[dict] = []
    initial_by_id: dict[int, str] = {}
    final_source_by_id: dict[int, str] = {}
    issue_by_id: dict[int, list[dict]] = {item_id: [] for item_id in all_ids}
    judgments: dict[str, dict] = {}
    repair_candidates: dict[str, str] = {}
    shortening_judgments: dict[str, dict] = {}
    shortening_candidates: dict[str, str] = {}
    unresolved: set[int] = set()
    budget_ids: set[int] = set()

    budget_by_id = {
        item.index: transport._item_translation_budget(
            item,
            max_cps_zh=max_cps_zh,
            max_chars_per_subtitle_zh=max_chars_per_subtitle_zh,
        )
        for item in items
    }

    def translate_structured(
        *,
        stage: str,
        payload: dict,
        expected_ids: list[int],
        prompt: str,
        model: str,
        request_temperature: float,
    ) -> dict[int, str]:
        """WenYi loose-JSON request with deterministic split fallback."""
        try:
            response = transport._request_json_object_stage(
                stage=stage,
                batch=payload,
                prompt=prompt,
                model=model,
                temperature=request_temperature,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                validator=lambda value: validate_items(value, expected_ids),
            )
            return validate_items(response, expected_ids)
        except Exception as exc:
            propagate_request_limit(exc)
            if len(expected_ids) <= 1:
                raise
            midpoint = len(expected_ids) // 2
            result: dict[int, str] = {}
            for subset in (expected_ids[:midpoint], expected_ids[midpoint:]):
                selected = set(subset)
                child = {
                    key: value for key, value in payload.items() if key != "items"
                }
                child["items"] = [
                    row for row in payload.get("items", [])
                    if int(row.get("id")) in selected
                ]
                result.update(translate_structured(
                    stage=stage,
                    payload=child,
                    expected_ids=subset,
                    prompt=prompt,
                    model=model,
                    request_temperature=request_temperature,
                ))
            return result

    for ordinal, batch in enumerate(batches, start=1):
        expected_ids = [int(row["id"]) for row in batch["items"]]
        key = batch_cache_key(batch)
        cached = stages["batches"].setdefault(key, {})
        cached_final = cached.get("final")
        try:
            normalized_final = validate_items(
                {"items": [
                    {"id": int(item_id), "translation": text}
                    for item_id, text in (cached_final or {}).items()
                ]},
                expected_ids,
            )
        except (RuntimeError, ValueError):
            normalized_final = {}
        if normalized_final:
            for item_id, text in normalized_final.items():
                item_by_id[item_id].translation = text
                cached_translations[item_id] = text
                initial_by_id[item_id] = str(
                    (cached.get("initial") or {}).get(str(item_id), text)
                )
                final_source_by_id[item_id] = str(
                    (cached.get("final_sources") or {}).get(str(item_id), "initial")
                )
                issue_by_id[item_id].extend(
                    row for row in (cached.get("issues") or {}).get(str(item_id), [])
                    if isinstance(row, dict)
                )
            for item_id, value in (cached.get("judgments") or {}).items():
                if isinstance(value, dict):
                    judgments[str(item_id)] = value
            for item_id, value in (cached.get("repair_candidates") or {}).items():
                if isinstance(value, str):
                    repair_candidates[str(item_id)] = value
            for item_id, value in (cached.get("shortening_judgments") or {}).items():
                if isinstance(value, dict):
                    shortening_judgments[str(item_id)] = value
            for item_id, value in (cached.get("shortening_candidates") or {}).items():
                if isinstance(value, str):
                    shortening_candidates[str(item_id)] = value
            dynamic_terms.extend(
                row for row in cached.get("dynamic_terms", [])
                if isinstance(row, dict)
            )
            unresolved.update(
                int(value) for value in cached.get("unresolved_ids", [])
                if isinstance(value, int) or str(value).isdigit()
            )
            budget_ids.update(
                int(value) for value in cached.get("budget_ids", [])
                if isinstance(value, int) or str(value).isdigit()
            )
            summary.wenyi_repair_accepted_ids.extend(
                int(value) for value in cached.get("repair_accepted_ids", [])
                if isinstance(value, int) or str(value).isdigit()
            )
            summary.wenyi_shortening_accepted_ids.extend(
                int(value) for value in cached.get("shortening_accepted_ids", [])
                if isinstance(value, int) or str(value).isdigit()
            )
            rolling.add([
                {"id": item_id, "translation": normalized_final[item_id]}
                for item_id in expected_ids
            ])
            summary.wenyi_cached_batches += 1
            continue

        glossary = merge_profile_glossary(
            profile_glossary, analysis["typed_glossary"], dynamic_terms
        )
        source_blob = "\n".join(original_by_id[item_id] for item_id in expected_ids)
        prompt = prompts.translation_prompt(
            target_language=target_language,
            profile=profile_prompt,
            video_summary=analysis["video_summary"],
            scene_summary=scene_summaries.get(int(batch["scene_index"]), ""),
            discourse_style=analysis["discourse_style"],
            glossary=relevant_glossary(glossary, source_blob),
            recent=rolling.render(context_window),
            context_before=batch.get("context_before", []),
            context_after=batch.get("context_after", []),
        )
        payload = {
            **batch,
            "items": [
                {
                    "id": item_id,
                    "source": original_by_id[item_id],
                    "max_target_chars": budget_by_id[item_id],
                }
                for item_id in expected_ids
            ],
        }
        initial = cached.get("initial")
        try:
            initial_map = validate_items(
                {"items": [
                    {"id": int(item_id), "translation": text}
                    for item_id, text in (initial or {}).items()
                ]},
                expected_ids,
            )
        except (RuntimeError, ValueError):
            if normalized_baseline is not None:
                initial_map = {
                    item_id: normalized_baseline[item_id]
                    for item_id in expected_ids
                }
            else:
                before = tracker.actual_requests
                initial_map = translate_structured(
                    stage="wenyi_translation",
                    payload=payload,
                    expected_ids=expected_ids,
                    prompt=prompt,
                    model=flash_model,
                    request_temperature=temperature,
                )
                summary.wenyi_translation_requests += (
                    tracker.actual_requests - before
                )
            cached["initial"] = {str(item_id): text for item_id, text in initial_map.items()}
            save_cache(stages_path, stages)
        initial_by_id.update(initial_map)
        final = dict(initial_map)
        final_sources = {item_id: "initial" for item_id in expected_ids}

        review_payload = {
            "video_summary": analysis["video_summary"],
            "scene_summary": scene_summaries.get(int(batch["scene_index"]), ""),
            "glossary": relevant_glossary(glossary, source_blob),
            "items": [
                {
                    "id": item_id,
                    "source": original_by_id[item_id],
                    "translation": initial_map[item_id],
                }
                for item_id in expected_ids
            ],
            "context_before": batch.get("context_before", []),
            "context_after": batch.get("context_after", []),
        }
        batch_issues: list[dict] = []
        try:
            before = tracker.actual_requests
            review = transport._request_json_object_stage(
                stage="wenyi_review",
                batch=review_payload,
                prompt=prompts.reviewer_prompt(),
                model=pro_model,
                temperature=0.0,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                validator=lambda value: validate_review(value, expected_ids),
            )
            batch_issues, terms = validate_review(review, expected_ids)
            dynamic_terms.extend(row for row in terms if row["confidence"] == "high")
            summary.wenyi_review_requests += tracker.actual_requests - before
        except Exception as exc:
            propagate_request_limit(exc)
            stage_errors.append({
                "stage": f"review:{expected_ids[0]}-{expected_ids[-1]}",
                "error": str(exc)[:500],
            })

        batch_cross_windows = [
            window
            for last_id in expected_ids
            for window in cross_by_last.get(last_id, [])
        ]
        if batch_cross_windows:
            cross_ids = sorted({
                item_id
                for window in batch_cross_windows
                for item_id in window["ids"]
            })
            try:
                before = tracker.actual_requests
                cross = transport._request_json_object_stage(
                    stage="wenyi_cross_line",
                    batch={
                        "windows": [
                            {
                                "ids": window["ids"],
                                "reasons": window["reasons"],
                                "items": [
                                    {
                                        "id": item_id,
                                        "source": original_by_id[item_id],
                                        "translation": (
                                            initial_map.get(item_id)
                                            or cached_translations.get(item_id, "")
                                        ),
                                    }
                                    for item_id in window["ids"]
                                ],
                            }
                            for window in batch_cross_windows
                        ]
                    },
                    prompt=prompts.reviewer_prompt(cross_line=True),
                    model=pro_model,
                    temperature=0.0,
                    api_provider=api_provider,
                    api_base=api_base,
                    api_key=api_key,
                    tracker=tracker,
                    validator=lambda value, ids=cross_ids: validate_review(value, ids),
                )
                cross_issues, _ = validate_review(cross, cross_ids)
                for issue in cross_issues:
                    if issue["id"] in expected_ids:
                        batch_issues.append({**issue, "origin": "cross_line"})
                summary.wenyi_cross_line_requests += tracker.actual_requests - before
            except Exception as exc:
                propagate_request_limit(exc)
                stage_errors.append({
                    "stage": (
                        f"cross_line_batch:{expected_ids[0]}-{expected_ids[-1]}"
                    ),
                    "error": str(exc)[:500],
                })

        deduplicated: dict[tuple, dict] = {}
        for issue in batch_issues:
            deduplicated.setdefault(
                (issue["id"], issue["type"], issue["detail"].strip().casefold()),
                issue,
            )
        for issue in deduplicated.values():
            issue_by_id[issue["id"]].append(issue)

        for item_id in expected_ids:
            eligible = [
                issue for issue in issue_by_id[item_id]
                if issue.get("confidence") == "high"
            ]
            if not eligible:
                continue
            if item_id in blocked_source_ids:
                unresolved.add(item_id)
                continue
            repair_key = str(item_id)
            repair = (cached.get("repairs") or {}).get(repair_key)
            if not isinstance(repair, str) or not repair.strip():
                try:
                    before = tracker.actual_requests
                    repaired = translate_structured(
                        stage="wenyi_repair",
                        payload={
                            "items": [{
                                "id": item_id,
                                "source": original_by_id[item_id],
                                "current_translation": final[item_id],
                                "issues": eligible,
                            }],
                            "context_before": batch.get("context_before", []),
                            "context_after": batch.get("context_after", []),
                        },
                        expected_ids=[item_id],
                        prompt=prompts.repair_prompt(),
                        model=pro_model,
                        request_temperature=0.0,
                    )
                    repair = repaired[item_id]
                    cached.setdefault("repairs", {})[repair_key] = repair
                    summary.wenyi_repair_requests += tracker.actual_requests - before
                    save_cache(stages_path, stages)
                except Exception as exc:
                    propagate_request_limit(exc)
                    unresolved.add(item_id)
                    stage_errors.append({"stage": f"repair:{item_id}", "error": str(exc)[:500]})
                    continue
            mapping = deterministic_mapping(key, item_id)
            repair_candidates[str(item_id)] = str(repair)
            options = {mapping["current"]: final[item_id], mapping["candidate"]: repair}
            try:
                before = tracker.actual_requests
                judged = transport._request_json_object_stage(
                    stage="wenyi_judge",
                    batch={
                        "id": item_id,
                        "source": original_by_id[item_id],
                        "option_a": options["A"],
                        "option_b": options["B"],
                        "issues": eligible,
                    },
                    prompt=prompts.judge_prompt(
                        strict=strategy_mode == "semantic_wenyi_review"
                    ),
                    model=pro_model,
                    temperature=0.0,
                    api_provider=api_provider,
                    api_base=api_base,
                    api_key=api_key,
                    tracker=tracker,
                    validator=lambda value, expected=item_id: validate_judgment(
                        value,
                        expected,
                        strict=strategy_mode == "semantic_wenyi_review",
                    ),
                )
                judgment = validate_judgment(
                    judged,
                    item_id,
                    strict=strategy_mode == "semantic_wenyi_review",
                )
                judgments[str(item_id)] = {**judgment, "mapping": mapping}
                summary.wenyi_judge_requests += tracker.actual_requests - before
                strict_confirmed = (
                    strategy_mode != "semantic_wenyi_review"
                    or all(
                        judgment.get(field) is True
                        for field in (
                            "facts", "negation", "numbers", "entities",
                            "references", "logic", "issue_resolved",
                            "no_new_error",
                        )
                    )
                )
                if (
                    judgment["confidence"] == "high"
                    and judgment["choice"] == mapping["candidate"]
                    and strict_confirmed
                ):
                    final[item_id] = repair
                    final_sources[item_id] = "repair"
                    summary.wenyi_repair_accepted_ids.append(item_id)
                else:
                    unresolved.add(item_id)
            except Exception as exc:
                propagate_request_limit(exc)
                unresolved.add(item_id)
                stage_errors.append({"stage": f"judge:{item_id}", "error": str(exc)[:500]})

        for item_id in expected_ids:
            final[item_id] = transport.deterministic_subtitle_format(
                final[item_id], max_line_chars=min(18, max_chars_per_subtitle_zh)
            )
            if transport._target_character_count(final[item_id]) <= budget_by_id[item_id]:
                continue
            budget_ids.add(item_id)
            if item_id in blocked_source_ids:
                unresolved.add(item_id)
                continue
            try:
                before = tracker.actual_requests
                shortened = translate_structured(
                    stage="wenyi_shortening",
                    payload={"items": [{
                        "id": item_id,
                        "source": original_by_id[item_id],
                        "current_translation": final[item_id],
                        "max_target_chars": budget_by_id[item_id],
                    }]},
                    expected_ids=[item_id],
                    prompt=prompts.shortening_prompt(),
                    model=pro_model,
                    request_temperature=0.0,
                )
                candidate = transport.deterministic_subtitle_format(
                    shortened[item_id], max_line_chars=min(18, max_chars_per_subtitle_zh)
                )
                summary.wenyi_shortening_requests += tracker.actual_requests - before
                shortening_candidates[str(item_id)] = candidate
                if transport._target_character_count(candidate) > budget_by_id[item_id]:
                    unresolved.add(item_id)
                    continue
                mapping = deterministic_mapping(f"{key}:short", item_id)
                options = {mapping["current"]: final[item_id], mapping["candidate"]: candidate}
                before = tracker.actual_requests
                judged = transport._request_json_object_stage(
                    stage="wenyi_shortening_judge",
                    batch={
                        "id": item_id,
                        "source": original_by_id[item_id],
                        "option_a": options["A"],
                        "option_b": options["B"],
                        "max_target_chars": budget_by_id[item_id],
                    },
                    prompt=prompts.judge_prompt(
                        shortening=True,
                        strict=strategy_mode == "semantic_wenyi_review",
                    ),
                    model=pro_model,
                    temperature=0.0,
                    api_provider=api_provider,
                    api_base=api_base,
                    api_key=api_key,
                    tracker=tracker,
                    validator=lambda value, expected=item_id: validate_judgment(
                        value,
                        expected,
                        equivalence=True,
                        strict=strategy_mode == "semantic_wenyi_review",
                    ),
                )
                judgment = validate_judgment(
                    judged,
                    item_id,
                    equivalence=True,
                    strict=strategy_mode == "semantic_wenyi_review",
                )
                shortening_judgments[str(item_id)] = {**judgment, "mapping": mapping}
                summary.wenyi_shortening_judge_requests += tracker.actual_requests - before
                equivalence_confirmed = all(
                    judgment.get(field) is True
                    for field in (
                        "facts",
                        "negation",
                        "numbers",
                        "entities",
                        "references",
                        "logic",
                    )
                )
                strict_confirmed = (
                    strategy_mode != "semantic_wenyi_review"
                    or (
                        judgment.get("issue_resolved") is True
                        and judgment.get("no_new_error") is True
                    )
                )
                if (
                    judgment["confidence"] == "high"
                    and judgment["choice"] == mapping["candidate"]
                    and equivalence_confirmed
                    and strict_confirmed
                ):
                    final[item_id] = candidate
                    final_sources[item_id] = "shortening"
                    summary.wenyi_shortening_accepted_ids.append(item_id)
                    budget_ids.discard(item_id)
                else:
                    unresolved.add(item_id)
            except Exception as exc:
                propagate_request_limit(exc)
                unresolved.add(item_id)
                stage_errors.append({"stage": f"shortening:{item_id}", "error": str(exc)[:500]})

        cached["final"] = {str(item_id): final[item_id] for item_id in expected_ids}
        cached["final_sources"] = {
            str(item_id): final_sources[item_id] for item_id in expected_ids
        }
        cached["issues"] = {
            str(item_id): issue_by_id[item_id] for item_id in expected_ids
        }
        cached["judgments"] = {
            str(item_id): judgments[str(item_id)]
            for item_id in expected_ids if str(item_id) in judgments
        }
        cached["repair_candidates"] = {
            str(item_id): repair_candidates[str(item_id)]
            for item_id in expected_ids if str(item_id) in repair_candidates
        }
        cached["shortening_candidates"] = {
            str(item_id): shortening_candidates[str(item_id)]
            for item_id in expected_ids if str(item_id) in shortening_candidates
        }
        cached["shortening_judgments"] = {
            str(item_id): shortening_judgments[str(item_id)]
            for item_id in expected_ids if str(item_id) in shortening_judgments
        }
        cached["dynamic_terms"] = dynamic_terms
        cached["unresolved_ids"] = sorted(
            item_id for item_id in unresolved if item_id in expected_ids
        )
        cached["budget_ids"] = sorted(
            item_id for item_id in budget_ids if item_id in expected_ids
        )
        cached["repair_accepted_ids"] = [
            item_id for item_id in summary.wenyi_repair_accepted_ids
            if item_id in expected_ids
        ]
        cached["shortening_accepted_ids"] = [
            item_id for item_id in summary.wenyi_shortening_accepted_ids
            if item_id in expected_ids
        ]
        save_cache(stages_path, stages)
        for item_id in expected_ids:
            item_by_id[item_id].translation = final[item_id]
            cached_translations[item_id] = final[item_id]
            final_source_by_id[item_id] = final_sources[item_id]
        rolling.add([
            {"id": item_id, "translation": final[item_id]}
            for item_id in expected_ids
        ])
        transport._save_translation_cache(translation_cache_path, cached_translations)
        if progress_callback:
            progress_callback({
                "phase": strategy_mode,
                "translation_stage": strategy_mode,
                "completed_batches": ordinal,
                "total_batches": len(batches),
                "completed_items": len(cached_translations),
                "total_items": len(items),
            })

    consistency_groups: list[dict] = []
    cached_consistency = stages.get("consistency")
    try:
        consistency_groups = validate_consistency(cached_consistency, all_ids)
    except RuntimeError:
        consistency_groups = []
    if not consistency_groups and cached_consistency != {"groups": []}:
        try:
            before = tracker.actual_requests
            consistency_payload = transport._request_json_object_stage(
                stage="wenyi_consistency",
                batch={
                    "glossary": merge_profile_glossary(
                        profile_glossary, analysis["typed_glossary"], dynamic_terms
                    ),
                    "items": [
                        {
                            "id": item.index,
                            "source": item.text,
                            "translation": item.translation,
                        }
                        for item in items
                    ],
                },
                prompt=prompts.consistency_prompt(),
                model=pro_model,
                temperature=0.0,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                validator=lambda value: validate_consistency(value, all_ids),
            )
            consistency_groups = validate_consistency(consistency_payload, all_ids)
            summary.wenyi_consistency_requests += tracker.actual_requests - before
            stages["consistency"] = {"groups": consistency_groups}
            save_cache(stages_path, stages)
        except Exception as exc:
            propagate_request_limit(exc)
            stage_errors.append({"stage": "consistency", "error": str(exc)[:500]})

    for group in consistency_groups:
        if group["classification"] == "definite_error":
            for variant in group["variants"]:
                unresolved.update(variant["ids"])
    unresolved.update(blocked_source_ids)
    unresolved.update(budget_ids)
    random_ids = stable_sample(
        (item_id for item_id in all_ids if item_id not in unresolved and final_source_by_id.get(item_id, "initial") == "initial"),
        task_seed=output_path.stem,
    )
    review_items: list[dict] = []

    def add_review(category: str, item_id: int, reason: str, **extra: Any) -> None:
        review_items.append({
            "category": category,
            "id": item_id,
            "source": original_by_id[item_id],
            "flash_translation": initial_by_id.get(item_id, ""),
            "candidate": extra.pop("candidate", ""),
            "final_translation": item_by_id[item_id].translation,
            "reason": reason,
            "judgment": extra.pop("judgment", None),
            "context_before": [
                {"id": row.index, "source": row.text, "translation": row.translation}
                for row in items[max(0, all_ids.index(item_id) - 2):all_ids.index(item_id)]
            ],
            "context_after": [
                {"id": row.index, "source": row.text, "translation": row.translation}
                for row in items[all_ids.index(item_id) + 1:all_ids.index(item_id) + 3]
            ],
            **extra,
        })

    for item_id in sorted(set(summary.wenyi_repair_accepted_ids)):
        add_review(
            "adopted_repair", item_id, "blind_judge_selected_high_confidence_repair",
            candidate=repair_candidates.get(str(item_id), ""),
            judgment=judgments.get(str(item_id)),
        )
    for item_id in sorted(set(summary.wenyi_shortening_accepted_ids)):
        add_review(
            "adopted_shortening",
            item_id,
            "six_field_equivalence_judge_selected_high_confidence_shortening",
            candidate=shortening_candidates.get(str(item_id), ""),
            judgment=shortening_judgments.get(str(item_id)),
            max_target_chars=budget_by_id[item_id],
        )
    for item_id in sorted(budget_ids):
        add_review(
            "unresolved_budget", item_id, "shortening_not_proven_equivalent",
            candidate=shortening_candidates.get(str(item_id), ""),
            judgment=shortening_judgments.get(str(item_id)),
            max_target_chars=budget_by_id[item_id],
        )
    for group in consistency_groups:
        category = (
            "consistency_definite"
            if group["classification"] == "definite_error"
            else "consistency_variant"
        )
        ids = sorted({
            item_id for variant in group["variants"] for item_id in variant["ids"]
        })
        for item_id in ids:
            add_review(category, item_id, group.get("detail", ""), consistency=group)
    for warning in analysis["asr_warnings"]:
        add_review("asr_warning", warning["id"], warning.get("detail", ""), asr=warning)
    cross_issue_ids = sorted({
        item_id
        for item_id, issues in issue_by_id.items()
        if any(issue.get("origin") == "cross_line" for issue in issues)
    })
    for item_id in cross_issue_ids:
        add_review(
            "cross_line_issue",
            item_id,
            "cross_line_reviewer_reported_high_confidence_issue",
            issues=[
                issue
                for issue in issue_by_id[item_id]
                if issue.get("origin") == "cross_line"
            ],
        )
    for window in cross_windows:
        for item_id in window["ids"]:
            add_review("cross_line_window", item_id, ",".join(window["reasons"]), window=window)
    for item_id in random_ids:
        add_review("random_sample", item_id, "stable_task_seed_sample")

    # De-duplicate repeated category/id pairs while preserving evidence order.
    unique_review_items: list[dict] = []
    seen_review: set[tuple[str, int]] = set()
    for row in review_items:
        key = (row["category"], row["id"])
        if key not in seen_review:
            seen_review.add(key)
            unique_review_items.append(row)

    summary.budget_violation_ids.extend(sorted(budget_ids))
    summary.unresolved_ids.extend(sorted(unresolved))
    summary.repaired_ids.extend(summary.wenyi_repair_accepted_ids)
    summary.review_required = bool(unresolved)
    assert_immutable(records, items)
    review_rows = [item_by_id[item_id] for item_id in sorted(unresolved)]
    transport._atomic_write_srt(
        review_rows,
        review_srt_path(output_path, strategy_mode=strategy_mode),
    )
    transport._atomic_write_json(
        report_path(output_path, strategy_mode=strategy_mode),
        report_envelope(
            strategy_mode=strategy_mode,
            model_mapping={"fast": flash_model, "cheap": pro_model, "strong": pro_model},
            analysis=analysis,
            semantic_baseline=(
                {str(k): v for k, v in normalized_baseline.items()}
                if normalized_baseline is not None else None
            ),
            semantic_baseline_report=semantic_baseline_report,
            initial_translations={str(k): v for k, v in initial_by_id.items()},
            repair_candidates=repair_candidates,
            final_sources={str(k): final_source_by_id.get(k, "initial") for k in all_ids},
            repair_judgments=judgments,
            shortening_candidates=shortening_candidates,
            shortening_judgments=shortening_judgments,
            cross_line_windows=cross_windows,
            budget_violation_ids=sorted(budget_ids),
            consistency_issues=consistency_groups,
            asr_warnings=analysis["asr_warnings"],
            stage_errors=stage_errors,
            source_warning_ids=sorted(blocked_source_ids),
            review_needed_srt=str(
                review_srt_path(output_path, strategy_mode=strategy_mode)
            ),
            review_items=unique_review_items,
        ),
    )
