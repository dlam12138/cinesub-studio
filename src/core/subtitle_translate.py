from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from encoding_utils import read_json
from encoding_utils import read_text as read_utf8_text
from runtime_paths import resolve_runtime_paths
from semantic_review_strategy import (
    SEMANTIC_PROMPT_VERSION,
    SEMANTIC_REVIEW_VERSION,
    deterministic_ab_mapping,
    deterministic_subtitle_format,
    load_semantic_cache,
    relevant_analysis,
    save_semantic_cache,
    semantic_cache_path,
    validate_consistency,
    validate_judgment,
    validate_scene_analysis,
    validate_semantic_review,
    validate_video_analysis,
)
from translation_reliability import (
    REPAIR_REQUEST_TEMPERATURE,
    REPAIR_STRATEGY_VERSION,
    ProgressCallback,
    TranslationReliabilityError,
    TranslationRequestTracker,
    TranslationRunSummary,
    adjacent_translation_overlap_count,
    blocking_translation_issues,
    build_repair_windows,
    normalize_reliability_config,
)
from translation_strategy import (
    TRANSLATION_STRATEGY_VERSION,
    batch_cache_key,
    build_scene_batches,
    load_stage_cache,
    normalize_translation_strategy,
    parse_srt_range,
    save_stage_cache,
    stage_cache_path,
    unwrap_json_object,
    validate_reflection,
)


@dataclass
class SubtitleItem:
    index: int
    time_line: str
    text: str
    translation: str = ""


def read_srt(path: Path) -> list[SubtitleItem]:
    """Parse an SRT file into a list of SubtitleItem objects."""
    raw = read_utf8_text(path, user_input=True).strip()
    items: list[SubtitleItem] = []
    blocks = re.split(r"\n\s*\n", raw)

    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) < 3:
            continue

        try:
            index = int(lines[0])
        except ValueError:
            continue

        time_line = lines[1]
        text = "\n".join(lines[2:])
        items.append(SubtitleItem(index=index, time_line=time_line, text=text))

    return items


def write_srt(items: list[SubtitleItem], path: Path) -> None:
    """Write SubtitleItem list to an SRT file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for item in items:
            file.write(f"{item.index}\n")
            file.write(f"{item.time_line}\n")
            text_lines = item.text.split("\n")
            if item.translation:
                text_lines = text_lines + [item.translation]
            file.write("\n".join(text_lines) + "\n\n")


def _atomic_write_srt(items: list[SubtitleItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    try:
        write_srt(items, Path(temporary))
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _translation_cache_path(
    input_path: Path,
    *,
    api_provider: str,
    llm_model: str,
    target_language: str,
    translation_mode: str,
    effective_prompt: str,
    reliability_mode: str = "off",
    translation_quality_model: str = "",
    translation_strategy_mode: str = "standard",
    scene_gap_seconds: float = 30.0,
    context_window: int = 3,
    max_cps_zh: float = 8.0,
    max_chars_per_subtitle_zh: int = 36,
    preserve_unknown_names: bool = True,
    profile_glossary: object = None,
) -> Path:
    try:
        source_digest = hashlib.sha256()
        with input_path.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                source_digest.update(chunk)
        input_sig = f"{input_path.resolve()}|sha256:{source_digest.hexdigest()}"
    except OSError:
        input_sig = str(input_path.resolve())

    key_payload = {
        "input": input_sig,
        "api_provider": api_provider,
        "llm_model": llm_model,
        "target_language": target_language,
        "translation_mode": translation_mode,
        "effective_prompt": effective_prompt,
    }
    if reliability_mode == "preview":
        key_payload["translation_reliability"] = REPAIR_STRATEGY_VERSION
        key_payload["translation_quality_model"] = (
            translation_quality_model or llm_model
        )
    if translation_strategy_mode in {
        "three_pass",
        "semantic_review",
        "wenyi_review",
        "semantic_wenyi_review",
    }:
        if translation_strategy_mode in {"wenyi_review", "semantic_wenyi_review"}:
            from wenyi_subtitle_strategy import (
                SEMANTIC_WENYI_STRATEGY_VERSION,
                WENYI_STRATEGY_VERSION,
            )

            strategy_version = (
                SEMANTIC_WENYI_STRATEGY_VERSION
                if translation_strategy_mode == "semantic_wenyi_review"
                else WENYI_STRATEGY_VERSION
            )
        elif translation_strategy_mode == "three_pass":
            strategy_version = TRANSLATION_STRATEGY_VERSION
        else:
            strategy_version = f"{SEMANTIC_REVIEW_VERSION}:{SEMANTIC_PROMPT_VERSION}"
        key_payload["translation_strategy"] = (
            strategy_version
        )
        key_payload["translation_strategy_mode"] = translation_strategy_mode
        key_payload["scene_gap_seconds"] = float(scene_gap_seconds)
        key_payload["context_window"] = int(context_window)
        key_payload["translation_quality_model"] = translation_quality_model or llm_model
        key_payload["max_cps_zh"] = float(max_cps_zh)
        key_payload["max_chars_per_subtitle_zh"] = int(max_chars_per_subtitle_zh)
        key_payload["preserve_unknown_names"] = bool(preserve_unknown_names)
        if translation_strategy_mode in {"wenyi_review", "semantic_wenyi_review"}:
            key_payload["profile_glossary"] = profile_glossary
    raw_key = json.dumps(
        key_payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
    project_root = resolve_runtime_paths(Path(__file__).resolve()).project_root
    return project_root / "work" / "translation-cache" / f"{digest}.json"


def _load_translation_cache(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    try:
        raw = read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    stored = raw.get("translations", raw)
    if not isinstance(stored, dict):
        return {}
    result: dict[int, str] = {}
    for key, value in stored.items():
        if isinstance(key, str) and key.isdigit() and isinstance(value, str):
            result[int(key)] = value
    return result


def _save_translation_cache(path: Path, translations: dict[int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "translations": {str(key): value for key, value in sorted(translations.items())}
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
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


def build_effective_translation_prompt(
    style_prompt: str = "",
    custom_prompt: str = "",
    glossary: list[dict] | None = None,
) -> str:
    """Build the profile/custom supplemental prompt used for translation.

    A custom prompt replaces the profile style text, but glossary terms are
    always appended so profile terminology still applies.
    """
    base = (custom_prompt or "").strip() or (style_prompt or "").strip()
    parts: list[str] = []
    if base:
        parts.append(base)

    glossary_lines: list[str] = []
    for entry in glossary or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "").strip()
        target = str(entry.get("target") or "").strip()
        note = str(entry.get("note") or "").strip()
        if not source or not target:
            continue
        line = f"- {source} => {target}"
        if note:
            line += f" ({note})"
        glossary_lines.append(line)

    if glossary_lines:
        parts.append(
            "Glossary terms. Use these translations consistently:\n"
            + "\n".join(glossary_lines)
        )

    return "\n\n".join(parts)


def translate_srt(
    input_path: Path,
    output_path: Path,
    *,
    api_provider: str,
    api_base: str,
    api_key: str,
    llm_model: str,
    translation_quality_model: str = "",
    target_language: str,
    batch_size: int,
    temperature: float,
    translation_mode: str,
    system_prompt: str = "",
    context_window: int = 3,
    reliability_mode: str = "off",
    max_extra_requests: int = 12,
    max_http_requests: int | None = None,
    translation_strategy_mode: str = "standard",
    scene_gap_seconds: float = 30.0,
    max_cps_zh: float = 8.0,
    max_chars_per_subtitle_zh: int = 36,
    preserve_unknown_names: bool = True,
    profile_glossary: object = None,
    progress_callback: ProgressCallback | None = None,
) -> TranslationRunSummary:
    """Translate an SRT file using an LLM API.

    Supports OpenAI-compatible Chat Completions and Anthropic Messages APIs.
    """
    if api_provider not in {"openai-compatible", "anthropic"}:
        raise ValueError(
            f"Invalid api_provider: {api_provider!r}. "
            f"Must be 'openai-compatible' or 'anthropic'."
        )
    reliability = normalize_reliability_config(
        reliability_mode, max_extra_requests=max_extra_requests
    )
    strategy = normalize_translation_strategy({
        "mode": translation_strategy_mode,
        "scene_gap_seconds": scene_gap_seconds,
    })
    if strategy["mode"] in {"wenyi_review", "semantic_wenyi_review"} and not str(
        translation_quality_model or ""
    ).strip():
        raise ValueError(
            f"{strategy['mode']} requires translation_quality_model; Pro "
            "stages do not fall back to the Flash model"
        )
    if max_http_requests is not None and int(max_http_requests) < 1:
        raise ValueError("max_http_requests must be at least 1 when configured")
    tracker = TranslationRequestTracker(
        **reliability,
        max_total_requests=(
            int(max_http_requests) if max_http_requests is not None else None
        ),
    )
    if not math.isfinite(float(max_cps_zh)) or float(max_cps_zh) <= 0:
        raise ValueError("max_cps_zh must be greater than zero")
    if int(max_chars_per_subtitle_zh) < 1:
        raise ValueError("max_chars_per_subtitle_zh must be at least 1")

    items = read_srt(input_path)
    total_items = len(items)

    if total_items == 0:
        raise ValueError(f"No subtitle entries found in {input_path}")

    default_prompt = _build_default_prompt(target_language)
    effective_prompt = _build_effective_prompt(default_prompt, system_prompt)
    cache_path = _translation_cache_path(
        input_path,
        api_provider=api_provider,
        llm_model=llm_model,
        target_language=target_language,
        translation_mode=translation_mode,
        effective_prompt=effective_prompt,
        reliability_mode=reliability["mode"],
        translation_quality_model=translation_quality_model or llm_model,
        translation_strategy_mode=strategy["mode"],
        scene_gap_seconds=strategy["scene_gap_seconds"],
        context_window=context_window,
        max_cps_zh=max_cps_zh,
        max_chars_per_subtitle_zh=max_chars_per_subtitle_zh,
        preserve_unknown_names=preserve_unknown_names,
        profile_glossary=profile_glossary,
    )
    cached_translations = _load_translation_cache(cache_path)
    summary = TranslationRunSummary(
        mode=reliability["mode"],
        total_items=total_items,
        strategy_mode=strategy["mode"],
        cache_hits=sum(1 for item in items if item.index in cached_translations),
        quality_model_fallback=(
            strategy["mode"] in {"three_pass", "semantic_review"}
            and not translation_quality_model
        ),
    )
    if cached_translations:
        print(f"Loaded {len(cached_translations)} cached translation(s): {cache_path}")
        for item in items:
            if item.index in cached_translations:
                item.translation = cached_translations[item.index]

    if strategy["mode"] == "three_pass":
        _translate_three_pass(
            items=items,
            cached_translations=cached_translations,
            cache_path=cache_path,
            target_language=target_language,
            effective_prompt=effective_prompt,
            llm_model=llm_model,
            translation_quality_model=translation_quality_model or llm_model,
            batch_size=batch_size,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            context_window=context_window,
            scene_gap_seconds=strategy["scene_gap_seconds"],
            max_cps_zh=float(max_cps_zh),
            max_chars_per_subtitle_zh=int(max_chars_per_subtitle_zh),
            preserve_unknown_names=bool(preserve_unknown_names),
            tracker=tracker,
            summary=summary,
            progress_callback=progress_callback,
        )
        batches = []
    elif strategy["mode"] == "semantic_review":
        _translate_semantic_review(
            items=items,
            cached_translations=cached_translations,
            cache_path=cache_path,
            output_path=output_path,
            target_language=target_language,
            effective_prompt=effective_prompt,
            llm_model=llm_model,
            translation_quality_model=translation_quality_model or llm_model,
            batch_size=batch_size,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            context_window=context_window,
            scene_gap_seconds=strategy["scene_gap_seconds"],
            max_cps_zh=float(max_cps_zh),
            max_chars_per_subtitle_zh=int(max_chars_per_subtitle_zh),
            tracker=tracker,
            summary=summary,
            progress_callback=progress_callback,
        )
        batches = []
    elif strategy["mode"] == "wenyi_review":
        from wenyi_subtitle_strategy import run_wenyi_review

        run_wenyi_review(
            items=items,
            cached_translations=cached_translations,
            translation_cache_path=cache_path,
            output_path=output_path,
            target_language=target_language,
            profile_prompt=effective_prompt,
            profile_glossary=profile_glossary,
            flash_model=llm_model,
            pro_model=translation_quality_model,
            batch_size=batch_size,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            context_window=context_window,
            scene_gap_seconds=strategy["scene_gap_seconds"],
            max_cps_zh=float(max_cps_zh),
            max_chars_per_subtitle_zh=int(max_chars_per_subtitle_zh),
            tracker=tracker,
            summary=summary,
            progress_callback=progress_callback,
        )
        batches = []
    elif strategy["mode"] == "semantic_wenyi_review":
        from wenyi_subtitle_strategy import run_wenyi_review

        semantic_baseline_cache_path = _translation_cache_path(
            input_path,
            api_provider=api_provider,
            llm_model=llm_model,
            target_language=target_language,
            translation_mode=translation_mode,
            effective_prompt=effective_prompt,
            reliability_mode=reliability["mode"],
            translation_quality_model=translation_quality_model or llm_model,
            translation_strategy_mode="semantic_review",
            scene_gap_seconds=strategy["scene_gap_seconds"],
            context_window=context_window,
            max_cps_zh=max_cps_zh,
            max_chars_per_subtitle_zh=max_chars_per_subtitle_zh,
            preserve_unknown_names=preserve_unknown_names,
            profile_glossary=profile_glossary,
        )
        semantic_cached = _load_translation_cache(
            semantic_baseline_cache_path
        )
        _translate_semantic_review(
            items=items,
            cached_translations=semantic_cached,
            cache_path=semantic_baseline_cache_path,
            output_path=output_path,
            target_language=target_language,
            effective_prompt=effective_prompt,
            llm_model=llm_model,
            translation_quality_model=translation_quality_model or llm_model,
            batch_size=batch_size,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            context_window=context_window,
            scene_gap_seconds=strategy["scene_gap_seconds"],
            max_cps_zh=float(max_cps_zh),
            max_chars_per_subtitle_zh=int(max_chars_per_subtitle_zh),
            tracker=tracker,
            summary=summary,
            progress_callback=progress_callback,
        )
        semantic_baseline = {
            item.index: item.translation for item in items
        }
        source_warning_ids: set[int] = set()
        baseline_report: dict = {}
        try:
            baseline_report = json.loads(
                _semantic_review_report_path(output_path).read_text(
                    encoding="utf-8"
                )
            )
            for warning in (
                baseline_report.get("video_analysis", {})
                .get("suspected_asr_errors", [])
            ):
                source_warning_ids.update(
                    int(value)
                    for value in warning.get("evidence_ids", [])
                    if isinstance(value, int) or str(value).isdigit()
                )
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            source_warning_ids = set()
            baseline_report = {}
        summary.unresolved_ids.clear()
        summary.budget_violation_ids.clear()
        summary.review_required = False
        run_wenyi_review(
            items=items,
            cached_translations=cached_translations,
            translation_cache_path=cache_path,
            output_path=output_path,
            target_language=target_language,
            profile_prompt=effective_prompt,
            profile_glossary=profile_glossary,
            flash_model=llm_model,
            pro_model=translation_quality_model,
            batch_size=batch_size,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            context_window=context_window,
            scene_gap_seconds=strategy["scene_gap_seconds"],
            max_cps_zh=float(max_cps_zh),
            max_chars_per_subtitle_zh=int(max_chars_per_subtitle_zh),
            tracker=tracker,
            summary=summary,
            progress_callback=progress_callback,
            strategy_mode="semantic_wenyi_review",
            baseline_translations=semantic_baseline,
            source_warning_ids=source_warning_ids,
            semantic_baseline_report=baseline_report,
        )
        batches = []
    else:
        batches = _build_batches(items, batch_size, context_window)
    total_batches = len(batches)
    print(f"Translating {total_items} subtitle entries in {total_batches} batch(es)")
    print(f"Provider: {api_provider}, Model: {llm_model}, Target: {target_language}")

    for batch_index, batch in enumerate(batches, start=1):
        expected_ids = [it["id"] for it in batch["items"]]
        if all(tid in cached_translations for tid in expected_ids):
            print(f"Using cached batch {batch_index}/{total_batches}")
            continue

        print(f"Translating batch {batch_index}/{total_batches}")
        missing_ids = [tid for tid in expected_ids if tid not in cached_translations]
        work_batch = _batch_for_ids(batch, missing_ids, context_window=context_window)

        def persist(translations: dict[int, str]) -> None:
            for tid, text in translations.items():
                cached_translations[tid] = text
                idx = tid - 1
                if 0 <= idx < total_items:
                    items[idx].translation = text
            _save_translation_cache(cache_path, cached_translations)
            if progress_callback:
                progress_callback({
                    "phase": "translating",
                    "completed_items": len(cached_translations),
                    "total_items": total_items,
                    "split_count": summary.split_count,
                })

        _translate_batch_adaptive(
            batch=work_batch,
            expected_ids=missing_ids,
            batch_index=batch_index,
            total_batches=total_batches,
            effective_prompt=effective_prompt,
            llm_model=llm_model,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            context_window=context_window,
            tracker=tracker,
            summary=summary,
            persist=persist,
        )

    if reliability["mode"] == "preview":
        _repair_blocking_translations(
            items=items,
            cached_translations=cached_translations,
            cache_path=cache_path,
            target_language=target_language,
            effective_prompt=effective_prompt,
            llm_model=llm_model,
            translation_quality_model=translation_quality_model or llm_model,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            context_window=context_window,
            tracker=tracker,
            summary=summary,
            progress_callback=progress_callback,
        )

    missing_final = [item.index for item in items if not item.translation.strip()]
    if reliability["mode"] == "preview" and missing_final:
        raise TranslationReliabilityError(
            f"Translation is incomplete for ids: {missing_final[:20]}",
            kind="incomplete_translation",
        )

    # For translated-only mode, swap text with translation
    if translation_mode == "translated":
        for item in items:
            if item.translation:
                item.text = item.translation
                item.translation = ""

    _atomic_write_srt(items, output_path)
    summary.actual_requests = tracker.actual_requests
    summary.extra_requests = tracker.extra_requests
    summary.budget_exhausted = tracker.budget_exhausted
    summary.total_request_limit_exhausted = tracker.total_request_limit_exhausted
    summary.review_required = bool(summary.unresolved_ids)
    print(f"Translation done: {output_path}")
    return summary


def _request_json_object_stage(
    *,
    stage: str,
    batch: dict,
    prompt: str,
    model: str,
    temperature: float,
    api_provider: str,
    api_base: str,
    api_key: str,
    tracker: TranslationRequestTracker,
    validator=None,
) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, 3):
        stage_prompt = prompt
        if attempt > 1:
            stage_prompt += (
                "\n\nThe previous response was invalid. Return one strict JSON object only, "
                "with every requested id exactly once and no Markdown."
            )
        body = _build_request_body(
            batch=batch,
            effective_prompt=stage_prompt,
            effective_model=model,
            temperature=temperature,
            api_provider=api_provider,
        )
        try:
            response = _call_llm_api(
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                body=body,
                tracker=tracker,
                request_is_extra=False,
                force_bounded_retry=True,
            )
            raw_payload = _parse_api_response(api_provider, response)
            if stage.startswith("wenyi_"):
                from wenyi_vendor.json_parser import parse_json_loose

                payload = parse_json_loose(raw_payload)
                if not isinstance(payload, dict):
                    raise RuntimeError("WenYi stage output must be a JSON object")
            else:
                payload = unwrap_json_object(raw_payload)
            if validator is not None:
                validator(payload)
            return payload
        except (RuntimeError, ValueError, TranslationReliabilityError) as exc:
            last_error = exc
            if isinstance(exc, TranslationReliabilityError) and exc.kind in {
                "authentication", "not_found"
            }:
                raise
    raise TranslationReliabilityError(
        f"Translation stage '{stage}' returned invalid structured output: {last_error}",
        kind=(
            f"semantic_{stage}"
            if stage.startswith("semantic_")
            else f"three_pass_{stage}"
        ),
        splittable=False,
    )


def _validate_translation_object(
    payload: dict, expected_ids: list[int]
) -> dict[int, str]:
    translations = _extract_translations(
        json.dumps(payload, ensure_ascii=False), expected_ids=expected_ids
    )
    if set(translations) != set(expected_ids):
        raise RuntimeError("translation ids do not exactly match requested ids")
    return translations


def _target_character_count(value: str) -> int:
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", str(value or "")))
    if cjk_count:
        return cjk_count
    return len(re.sub(r"\s+", "", str(value or "")))


def _item_translation_budget(
    item: SubtitleItem, *, max_cps_zh: float, max_chars_per_subtitle_zh: int
) -> int:
    start, end = parse_srt_range(item.time_line)
    duration_budget = max(1, math.floor(max(0.001, end - start) * max_cps_zh))
    return min(max_chars_per_subtitle_zh, duration_budget)


def _translate_three_pass(
    *,
    items: list[SubtitleItem],
    cached_translations: dict[int, str],
    cache_path: Path,
    target_language: str,
    effective_prompt: str,
    llm_model: str,
    translation_quality_model: str,
    batch_size: int,
    temperature: float,
    api_provider: str,
    api_base: str,
    api_key: str,
    context_window: int,
    scene_gap_seconds: float,
    max_cps_zh: float,
    max_chars_per_subtitle_zh: int,
    preserve_unknown_names: bool,
    tracker: TranslationRequestTracker,
    summary: TranslationRunSummary,
    progress_callback: ProgressCallback | None,
) -> None:
    batches = build_scene_batches(
        items,
        batch_size=batch_size,
        context_window=context_window,
        scene_gap_seconds=scene_gap_seconds,
    )
    stages_path = stage_cache_path(cache_path)
    stages = load_stage_cache(stages_path)
    stage_batches = stages["batches"]
    previous_scene_summary = ""
    item_by_id = {item.index: item for item in items}
    budget_by_id = {
        item.index: _item_translation_budget(
            item,
            max_cps_zh=max_cps_zh,
            max_chars_per_subtitle_zh=max_chars_per_subtitle_zh,
        )
        for item in items
    }
    for ordinal, batch in enumerate(batches, start=1):
        expected_ids = [int(row["id"]) for row in batch["items"]]
        key = batch_cache_key(batch)
        cached = stage_batches.get(key)
        if not isinstance(cached, dict):
            cached = {}
            stage_batches[key] = cached

        final_cached = cached.get("final")
        if isinstance(final_cached, dict):
            normalized = {
                int(key): value for key, value in final_cached.items()
                if str(key).isdigit() and isinstance(value, str)
            }
            if set(normalized) == set(expected_ids) and all(value.strip() for value in normalized.values()):
                for item_id, value in normalized.items():
                    item_by_id[item_id].translation = value
                    cached_translations[item_id] = value
                previous_scene_summary = str(cached.get("scene_summary") or previous_scene_summary)
                cached_violations = cached.get("budget_violation_ids", [])
                if isinstance(cached_violations, list):
                    summary.budget_violation_ids.extend(
                        item_id for item_id in cached_violations
                        if isinstance(item_id, int)
                    )
                    summary.unresolved_ids.extend(
                        item_id for item_id in cached_violations
                        if isinstance(item_id, int)
                    )
                summary.three_pass_cached_batches += 1
                continue

        payload = dict(batch)
        payload["items"] = [
            {
                **row,
                "max_target_chars": budget_by_id[int(row["id"])],
                "duration_seconds": round(
                    parse_srt_range(item_by_id[int(row["id"])].time_line)[1]
                    - parse_srt_range(item_by_id[int(row["id"])].time_line)[0],
                    3,
                ),
            }
            for row in batch["items"]
        ]
        if previous_scene_summary:
            payload["previous_scene_summary"] = previous_scene_summary

        try:
            initial = cached.get("initial")
            initial_ids = {
                int(item_id) for item_id in initial
                if isinstance(item_id, str) and item_id.isdigit()
            } if isinstance(initial, dict) else set()
            if not isinstance(initial, dict) or initial_ids != set(expected_ids):
                print(f"Translation stage: initial ({ordinal}/{len(batches)})")
                initial_prompt = (
                    f"{effective_prompt}\n\nTHREE-PASS STAGE 1 — INITIAL TRANSLATION:\n"
                    "- Translate only items in the items array.\n"
                    "- Read context_before, context_after, and previous_scene_summary as read-only context.\n"
                    "- Treat max_target_chars as a hard Chinese subtitle budget for each item.\n"
                    + (
                        "- Preserve unknown proper names in source spelling unless the supplied glossary gives a translation.\n"
                        if preserve_unknown_names else ""
                    )
                    +
                    "- Preserve every id exactly once; never merge or split subtitle items.\n"
                    "- Return strict JSON as {\"items\":[{\"id\":1,\"translation\":\"...\"}]}."
                )
                before_requests = tracker.actual_requests
                initial = _translate_batch_with_structured_retry(
                    batch=payload,
                    expected_ids=expected_ids,
                    batch_index=ordinal,
                    total_batches=len(batches),
                    effective_prompt=initial_prompt,
                    llm_model=llm_model,
                    temperature=temperature,
                    api_provider=api_provider,
                    api_base=api_base,
                    api_key=api_key,
                    tracker=tracker,
                    request_is_extra=False,
                )
                summary.initial_pass_requests += tracker.actual_requests - before_requests
                cached["initial"] = {str(item_id): text for item_id, text in initial.items()}
                save_stage_cache(stages_path, stages)
                if progress_callback:
                    progress_callback({
                        "phase": "translation_initial",
                        "translation_stage": "initial",
                        "completed_batches": ordinal - 1,
                        "total_batches": len(batches),
                    })
            else:
                initial = {int(item_id): text for item_id, text in initial.items()}

            reflection = cached.get("reflection")
            reflection_valid = (
                isinstance(reflection, dict)
                and isinstance(reflection.get("issues"), dict)
                and {
                    int(item_id) for item_id in reflection["issues"]
                    if isinstance(item_id, str) and item_id.isdigit()
                } == set(expected_ids)
                and isinstance(reflection.get("scene_summary", ""), str)
            )
            if not reflection_valid:
                print(f"Translation stage: reflection ({ordinal}/{len(batches)})")
                reflection_payload = dict(payload)
                reflection_payload["initial_translations"] = [
                    {"id": item_id, "translation": initial[item_id]} for item_id in expected_ids
                ]
                reflection_prompt = (
                    "You are the quality reviewer for a professional film subtitle translation.\n"
                    f"Target language: {target_language}.\n"
                    "THREE-PASS STAGE 2 — REFLECTION:\n"
                    "- Compare every initial translation with its source and surrounding context.\n"
                    "- Identify omissions, mistranslations, tone, names, terminology, pronouns, "
                    "cross-cue continuity, repetition, and subtitle readability problems.\n"
                    "- Check each initial translation against max_target_chars and report over-budget wording.\n"
                    + (
                        "- Unknown proper names absent from the glossary must keep source spelling; flag invented transliterations.\n"
                        if preserve_unknown_names else ""
                    )
                    +
                    "- Do not rewrite translations in this stage.\n"
                    "- Return every requested id exactly once, even when its issues array is empty.\n"
                    "- Return strict JSON only: "
                    "{\"issues\":[{\"id\":1,\"issues\":[]}],\"scene_summary\":\"short factual context\"}."
                )
                before_requests = tracker.actual_requests
                reflection_object = _request_json_object_stage(
                    stage="reflection",
                    batch=reflection_payload,
                    prompt=reflection_prompt,
                    model=translation_quality_model,
                    temperature=0.0,
                    api_provider=api_provider,
                    api_base=api_base,
                    api_key=api_key,
                    tracker=tracker,
                    validator=lambda value: validate_reflection(value, expected_ids),
                )
                issues, reflected_summary = validate_reflection(
                    reflection_object, expected_ids
                )
                summary.reflection_pass_requests += tracker.actual_requests - before_requests
                reflection = {
                    "issues": {str(item_id): values for item_id, values in issues.items()},
                    "scene_summary": reflected_summary,
                }
                cached["reflection"] = reflection
                save_stage_cache(stages_path, stages)
                if progress_callback:
                    progress_callback({
                        "phase": "translation_reflection",
                        "translation_stage": "reflection",
                        "completed_batches": ordinal - 1,
                        "total_batches": len(batches),
                    })

            print(f"Translation stage: final ({ordinal}/{len(batches)})")
            final_payload = dict(payload)
            final_payload["initial_translations"] = [
                {"id": item_id, "translation": initial[item_id]} for item_id in expected_ids
            ]
            final_payload["reflection_issues"] = [
                {"id": item_id, "issues": reflection["issues"].get(str(item_id), [])}
                for item_id in expected_ids
            ]
            final_prompt = (
                f"{effective_prompt}\n\nTHREE-PASS STAGE 3 — FINAL REVISION:\n"
                f"- Produce polished {target_language} film subtitles using source text, context, "
                "initial translations, and reflection issues.\n"
                "- Preserve meaning, tone, names, and glossary terminology.\n"
                "- If an id has no reflection issues, return its initial translation unchanged.\n"
                "- Keep each result within its max_target_chars budget, without commentary or invented plot details.\n"
                + (
                    "- Preserve unknown proper names in source spelling unless the supplied glossary gives a translation.\n"
                    if preserve_unknown_names else ""
                )
                +
                "- Preserve every requested id exactly once; never merge, split, add, or remove ids.\n"
                "- Return strict JSON only: "
                "{\"items\":[{\"id\":1,\"translation\":\"...\"}],"
                "\"scene_summary\":\"short factual context for the next batch\"}."
            )
            before_requests = tracker.actual_requests
            final_object = _request_json_object_stage(
                stage="final",
                batch=final_payload,
                prompt=final_prompt,
                model=translation_quality_model,
                temperature=0.0,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                validator=lambda value: _validate_translation_object(
                    value, expected_ids
                ),
            )
            final = _validate_translation_object(final_object, expected_ids)
            for item_id in expected_ids:
                if not reflection["issues"].get(str(item_id), []):
                    final[item_id] = initial[item_id]
            blockers = {
                item_id: blocking_translation_issues(
                    item_by_id[item_id].text, final[item_id], target_language
                )
                for item_id in expected_ids
            }
            blockers = {item_id: values for item_id, values in blockers.items() if values}
            if blockers:
                raise TranslationReliabilityError(
                    f"Three-pass final validation rejected ids: {sorted(blockers)}",
                    kind="three_pass_final_validation",
                )
            summary.final_pass_requests += tracker.actual_requests - before_requests

            over_budget = {
                item_id: budget_by_id[item_id]
                for item_id, text in final.items()
                if _target_character_count(text) > budget_by_id[item_id]
            }
            if over_budget:
                compression_payload = {
                    "items": [
                        {
                            "id": item_id,
                            "source": item_by_id[item_id].text,
                            "translation": final[item_id],
                            "max_target_chars": budget,
                        }
                        for item_id, budget in over_budget.items()
                    ],
                    "context_before": payload.get("context_before", []),
                    "context_after": payload.get("context_after", []),
                    "previous_scene_summary": payload.get("previous_scene_summary", ""),
                }
                compression_prompt = (
                    f"Compress only the requested {target_language} film subtitle translations.\n"
                    "- Preserve every fact, relationship, tone, negation, number, name, and glossary term.\n"
                    "- Each translation must fit max_target_chars; remove filler before meaning.\n"
                    + (
                        "- Preserve unknown proper names in source spelling.\n"
                        if preserve_unknown_names else ""
                    )
                    +
                    "- Preserve every requested id exactly once; never merge or split items.\n"
                    "- Return strict JSON only as "
                    "{\"items\":[{\"id\":1,\"translation\":\"...\"}]}."
                )
                before_compression = tracker.actual_requests
                compressed_object = _request_json_object_stage(
                    stage="compression",
                    batch=compression_payload,
                    prompt=compression_prompt,
                    model=translation_quality_model,
                    temperature=0.0,
                    api_provider=api_provider,
                    api_base=api_base,
                    api_key=api_key,
                    tracker=tracker,
                    validator=lambda value: _validate_translation_object(
                        value, list(over_budget)
                    ),
                )
                compressed = _validate_translation_object(
                    compressed_object, list(over_budget)
                )
                summary.compression_pass_requests += (
                    tracker.actual_requests - before_compression
                )
                for item_id, candidate in compressed.items():
                    if _target_character_count(candidate) <= _target_character_count(
                        final[item_id]
                    ):
                        final[item_id] = candidate

            remaining_over_budget = [
                item_id for item_id, text in final.items()
                if _target_character_count(text) > budget_by_id[item_id]
            ]
            summary.budget_violation_ids.extend(remaining_over_budget)
            summary.unresolved_ids.extend(remaining_over_budget)
            summary.three_pass_completed_batches += 1

            scene_summary = final_object.get("scene_summary")
            if not isinstance(scene_summary, str) or not scene_summary.strip():
                scene_summary = reflection.get("scene_summary", "")
            previous_scene_summary = str(scene_summary or "")[:1200]
            cached["final"] = {str(item_id): text for item_id, text in final.items()}
            cached["scene_summary"] = previous_scene_summary
            cached["budget_violation_ids"] = remaining_over_budget
            save_stage_cache(stages_path, stages)

            for item_id, text in final.items():
                item_by_id[item_id].translation = text
                cached_translations[item_id] = text
            _save_translation_cache(cache_path, cached_translations)
            if progress_callback:
                progress_callback({
                    "phase": "translation_final",
                    "translation_stage": "final",
                    "completed_batches": ordinal,
                    "total_batches": len(batches),
                    "completed_items": len(cached_translations),
                    "total_items": len(items),
                })
        except Exception:
            if not summary.failed_stage:
                if not cached.get("initial"):
                    summary.failed_stage = "initial"
                elif not cached.get("reflection"):
                    summary.failed_stage = "reflection"
                else:
                    summary.failed_stage = "final"
            save_stage_cache(stages_path, stages)
            raise


def _atomic_write_json(path: Path, payload: dict) -> None:
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


def _semantic_context(
    items: list[SubtitleItem],
    translations: dict[int, str],
    item_id: int,
    *,
    radius: int = 3,
) -> dict[str, list[dict[str, object]]]:
    positions = {item.index: index for index, item in enumerate(items)}
    position = positions[item_id]

    def rows(values: list[SubtitleItem]) -> list[dict[str, object]]:
        return [
            {
                "id": item.index,
                "source": item.text,
                "translation": translations.get(item.index, ""),
            }
            for item in values
        ]

    return {
        "context_before": rows(items[max(0, position - radius):position]),
        "context_after": rows(items[position + 1:position + radius + 1]),
    }


def _semantic_review_report_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.semantic_review_report.json")


def _semantic_review_srt_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.review_needed.srt")


def _translate_semantic_review(
    *,
    items: list[SubtitleItem],
    cached_translations: dict[int, str],
    cache_path: Path,
    output_path: Path,
    target_language: str,
    effective_prompt: str,
    llm_model: str,
    translation_quality_model: str,
    batch_size: int,
    temperature: float,
    api_provider: str,
    api_base: str,
    api_key: str,
    context_window: int,
    scene_gap_seconds: float,
    max_cps_zh: float,
    max_chars_per_subtitle_zh: int,
    tracker: TranslationRequestTracker,
    summary: TranslationRunSummary,
    progress_callback: ProgressCallback | None,
) -> None:
    batches = build_scene_batches(
        items,
        batch_size=batch_size,
        context_window=context_window,
        scene_gap_seconds=scene_gap_seconds,
    )
    stages_path = semantic_cache_path(cache_path)
    stages = load_semantic_cache(stages_path)
    analysis_cache = stages["analysis"]
    scene_cache = analysis_cache["scenes"]
    stage_batches = stages["batches"]
    item_by_id = {item.index: item for item in items}
    all_ids = [item.index for item in items]
    budget_by_id = {
        item.index: _item_translation_budget(
            item,
            max_cps_zh=max_cps_zh,
            max_chars_per_subtitle_zh=max_chars_per_subtitle_zh,
        )
        for item in items
    }
    scene_items: dict[int, list[dict[str, object]]] = {}
    for batch in batches:
        scene_index = int(batch["scene_index"])
        existing = scene_items.setdefault(scene_index, [])
        known = {int(row["id"]) for row in existing}
        for row in batch["items"]:
            if int(row["id"]) not in known:
                existing.append({"id": int(row["id"]), "text": str(row["text"])})
                known.add(int(row["id"]))

    analysis_prompt = (
        "You analyze a complete-video ASR transcript before subtitle translation.\n"
        "Return only factual context supported by the supplied ASR. Never invent a speaker, "
        "plot fact, name, or translation.\n"
        "Classify glossary entries as person, place, organization, technical_term, "
        "work_title, common_term, or other. A common noun must be common_term, not a "
        "proper name. Suspected ASR errors are warnings and must not become fixed terms.\n"
        "Every speaker, term, and ASR warning requires evidence_ids from this scene.\n"
        "Return strict JSON only with scene_summary, speakers, typed_glossary, and "
        "suspected_asr_errors."
    )
    for ordinal, (scene_index, rows) in enumerate(scene_items.items(), start=1):
        key = str(scene_index)
        cached_scene = scene_cache.get(key)
        try:
            validated_scene = (
                validate_scene_analysis(cached_scene, [int(row["id"]) for row in rows])
                if isinstance(cached_scene, dict)
                else None
            )
        except RuntimeError:
            validated_scene = None
        if validated_scene is None:
            print(
                f"Translation stage: semantic_analysis_scene "
                f"({ordinal}/{len(scene_items)})"
            )
            before_requests = tracker.actual_requests
            scene_object = _request_json_object_stage(
                stage="semantic_analysis_scene",
                batch={"scene_index": scene_index, "items": rows},
                prompt=analysis_prompt,
                model=translation_quality_model,
                temperature=0.0,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                validator=lambda value, ids=[int(row["id"]) for row in rows]: (
                    validate_scene_analysis(value, ids)
                ),
            )
            validated_scene = validate_scene_analysis(
                scene_object, [int(row["id"]) for row in rows]
            )
            scene_cache[key] = validated_scene
            summary.semantic_analysis_requests += (
                tracker.actual_requests - before_requests
            )
            save_semantic_cache(stages_path, stages)
        if progress_callback:
            progress_callback({
                "phase": "semantic_analysis",
                "translation_stage": "semantic_analysis",
                "completed_scenes": ordinal,
                "total_scenes": len(scene_items),
            })

    video_analysis = analysis_cache.get("video")
    try:
        validated_video = (
            validate_video_analysis(video_analysis, all_ids)
            if isinstance(video_analysis, dict)
            else None
        )
    except RuntimeError:
        validated_video = None
    if validated_video is None:
        print("Translation stage: semantic_analysis_video")
        synthesis_prompt = (
            "Synthesize the supplied scene analyses into a conservative complete-video "
            "translation brief. Use only supplied evidence. Deduplicate speakers and terms, "
            "keep evidence_ids, preserve common_term classification, and keep suspected ASR "
            "errors separate from glossary terms.\n"
            "Return strict JSON only with video_summary, speakers, typed_glossary, and "
            "suspected_asr_errors."
        )
        before_requests = tracker.actual_requests
        video_object = _request_json_object_stage(
            stage="semantic_analysis_video",
            batch={
                "scenes": [
                    {"scene_index": index, **scene_cache[str(index)]}
                    for index in scene_items
                ]
            },
            prompt=synthesis_prompt,
            model=translation_quality_model,
            temperature=0.0,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            tracker=tracker,
            validator=lambda value: validate_video_analysis(value, all_ids),
        )
        validated_video = validate_video_analysis(video_object, all_ids)
        analysis_cache["video"] = validated_video
        summary.semantic_analysis_requests += tracker.actual_requests - before_requests
        save_semantic_cache(stages_path, stages)
    summary.semantic_suspected_asr_error_count = len(
        validated_video.get("suspected_asr_errors", [])
    )

    recent_translations: list[dict[str, object]] = []
    final_source_by_id: dict[int, str] = {}
    unresolved: set[int] = set()
    issue_counts: dict[str, int] = {}

    for ordinal, batch in enumerate(batches, start=1):
        expected_ids = [int(row["id"]) for row in batch["items"]]
        key = batch_cache_key(batch)
        cached = stage_batches.get(key)
        if not isinstance(cached, dict):
            cached = {}
            stage_batches[key] = cached
        cached_final = cached.get("final")
        if isinstance(cached_final, dict):
            normalized = {
                int(item_id): str(value)
                for item_id, value in cached_final.items()
                if str(item_id).isdigit() and isinstance(value, str) and value.strip()
            }
            if set(normalized) == set(expected_ids):
                for item_id in expected_ids:
                    text = normalized[item_id]
                    item_by_id[item_id].translation = text
                    cached_translations[item_id] = text
                    final_source_by_id[item_id] = str(
                        cached.get("final_sources", {}).get(str(item_id), "initial")
                    )
                    recent_translations.append({"id": item_id, "translation": text})
                for item_id in cached.get("unresolved_ids", []):
                    if isinstance(item_id, int):
                        unresolved.add(item_id)
                summary.semantic_cached_batches += 1
                continue

        scene_index = int(batch["scene_index"])
        scene_analysis = scene_cache[str(scene_index)]
        source_text = "\n".join(str(row["text"]) for row in batch["items"])
        relevant = relevant_analysis(validated_video, expected_ids, source_text)
        payload = dict(batch)
        payload["items"] = [
            {
                **row,
                "duration_seconds": round(
                    parse_srt_range(item_by_id[int(row["id"])].time_line)[1]
                    - parse_srt_range(item_by_id[int(row["id"])].time_line)[0],
                    3,
                ),
                "max_target_chars": budget_by_id[int(row["id"])],
            }
            for row in batch["items"]
        ]
        payload["video_summary"] = validated_video["video_summary"]
        payload["scene_summary"] = scene_analysis["scene_summary"]
        payload["speakers"] = validated_video["speakers"]
        payload.update(relevant)
        if recent_translations:
            payload["recent_translations"] = recent_translations[-max(1, context_window):]

        initial = cached.get("initial")
        initial_valid = (
            isinstance(initial, dict)
            and {
                int(item_id) for item_id in initial
                if str(item_id).isdigit()
            } == set(expected_ids)
            and all(isinstance(value, str) and value.strip() for value in initial.values())
        )
        if not initial_valid:
            print(f"Translation stage: semantic_initial ({ordinal}/{len(batches)})")
            initial_prompt = (
                f"{effective_prompt}\n\nSEMANTIC REVIEW MODE - CONTEXTUAL INITIAL TRANSLATION:\n"
                "- Follow the context blocks in this order: profile instructions, "
                "video_summary, scene_summary, typed_glossary, recent_translations, "
                "read-only surrounding source, then items.\n"
                "- Profile glossary terms are authoritative. Auto typed_glossary is advisory "
                "unless a named or technical term is high confidence.\n"
                "- common_term entries are ordinary vocabulary and must be translated "
                "normally; do not preserve them as unknown names.\n"
                "- suspected_asr_errors are warnings, not facts or fixed terms.\n"
                "- Preserve every fact, negation, number, relationship, tone, and id.\n"
                "- Treat sentence fragments spanning adjacent ids as one semantic unit. "
                "Resolve elliptical references such as 'do it', 'le faire', or omitted "
                "objects from the surrounding source instead of translating them as a "
                "vague standalone statement.\n"
                "- Preserve the concrete action carried across lines. For example, when "
                "one line says babies cannot ask for explanations and the next says they "
                "have no language 'pour le faire', the second line means they cannot use "
                "language to ask; never reduce it to the vague literal 'they have no "
                "language'. Keep the ids separate while making that relation explicit.\n"
                "- Never merge or split items. Return strict JSON only as "
                "{\"items\":[{\"id\":1,\"translation\":\"...\"}]}."
            )
            before_requests = tracker.actual_requests
            initial_map = _translate_batch_with_structured_retry(
                batch=payload,
                expected_ids=expected_ids,
                batch_index=ordinal,
                total_batches=len(batches),
                effective_prompt=initial_prompt,
                llm_model=llm_model,
                temperature=temperature,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                request_is_extra=False,
            )
            summary.initial_pass_requests += tracker.actual_requests - before_requests
            initial = {str(item_id): text for item_id, text in initial_map.items()}
            cached["initial"] = initial
            save_semantic_cache(stages_path, stages)
        initial_map = {int(item_id): str(text) for item_id, text in initial.items()}

        review = cached.get("review")
        try:
            review_map = (
                validate_semantic_review(review, expected_ids)
                if isinstance(review, dict)
                else None
            )
        except RuntimeError:
            review_map = None
        if review_map is None:
            print(f"Translation stage: semantic_review ({ordinal}/{len(batches)})")
            review_payload = dict(payload)
            review_payload["initial_translations"] = [
                {"id": item_id, "translation": initial_map[item_id]}
                for item_id in expected_ids
            ]
            review_prompt = (
                "You are a conservative fidelity reviewer for film subtitles.\n"
                "Report only definite substantive errors: missing, added, mistranslation, "
                "terminology, pronoun, or continuity. Reasonable paraphrase, word order, "
                "style preference, and uncertain suspicions are not errors. When unsure, "
                "report no issue. Do not rewrite in this stage.\n"
                "Compare each translation against both its own source and the adjacent "
                "source/translation context. Check every semantic relation, especially "
                "negation, comparison, purpose/cause, pronouns, and elliptical references "
                "whose meaning is completed in a neighboring id. A grammatical translation "
                "that drops what 'do it'/'le faire'/an omitted object refers to is a "
                "definite missing or continuity error.\n"
                "Concrete regression rule: after a clause such as 'babies cannot ask for "
                "explanations', translating 'ils n'ont pas de langue pour le faire' merely "
                "as 'they have no language' drops the ask/explain action and must be "
                "reported as missing or continuity.\n"
                "Every issue requires type, severity (severe/moderate), confidence "
                "(high/medium/low), detail, source evidence, and a concrete suggestion.\n"
                "The evidence field must contain a non-empty source-language fragment "
                "that proves the issue. If you cannot quote such evidence, return no issue.\n"
                "Return every id exactly once as strict JSON using this exact shape: "
                "{\"items\":[{\"id\":1,\"issues\":[{\"type\":\"missing\","
                "\"severity\":\"moderate\",\"confidence\":\"high\","
                "\"detail\":\"what is wrong\",\"evidence\":\"source fragment\","
                "\"suggestion\":\"concrete correction\"}]},{\"id\":2,\"issues\":[]}]}."
            )
            before_requests = tracker.actual_requests
            review_object = _request_json_object_stage(
                stage="semantic_review",
                batch=review_payload,
                prompt=review_prompt,
                model=translation_quality_model,
                temperature=0.0,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                validator=lambda value: validate_semantic_review(
                    value, expected_ids
                ),
            )
            review_map = validate_semantic_review(review_object, expected_ids)
            cached["review"] = {
                "items": [
                    {"id": item_id, "issues": review_map[item_id]}
                    for item_id in expected_ids
                ]
            }
            summary.semantic_review_requests += (
                tracker.actual_requests - before_requests
            )
            save_semantic_cache(stages_path, stages)

        final = dict(initial_map)
        final_sources = {item_id: "initial" for item_id in expected_ids}
        cached_repairs = cached.setdefault("repairs", {})
        cached_judgments = cached.setdefault("judgments", {})
        for item_id in expected_ids:
            issues = review_map[item_id]
            for issue in issues:
                issue_counts[issue["type"]] = issue_counts.get(issue["type"], 0) + 1
            if not issues:
                summary.semantic_no_issue_ids.append(item_id)
                continue
            eligible = [
                issue for issue in issues if issue.get("confidence") == "high"
            ]
            if not eligible:
                unresolved.add(item_id)
                summary.semantic_repair_rejected_ids.append(item_id)
                continue
            summary.semantic_repair_candidate_ids.append(item_id)
            repair_text = cached_repairs.get(str(item_id))
            if not isinstance(repair_text, str) or not repair_text.strip():
                print(f"Translation stage: semantic_repair (id={item_id})")
                context = _semantic_context(
                    items, {**cached_translations, **initial_map}, item_id
                )
                repair_payload = {
                    "items": [{
                        "id": item_id,
                        "source": item_by_id[item_id].text,
                        "initial_translation": initial_map[item_id],
                        "issues": eligible,
                        "max_target_chars": budget_by_id[item_id],
                    }],
                    **context,
                    "video_summary": validated_video["video_summary"],
                    "scene_summary": scene_analysis["scene_summary"],
                    **relevant_analysis(
                        validated_video, [item_id], item_by_id[item_id].text
                    ),
                }
                repair_prompt = (
                    f"{effective_prompt}\n\nTARGETED FIDELITY REPAIR:\n"
                    "- Repair only the supplied id and only the definite review issues.\n"
                    "- Preserve all correct information from the initial translation.\n"
                    "- Use before/after source and translations as read-only context.\n"
                    "- Do not add style embellishment or information absent from source.\n"
                    "- Return strict JSON only as "
                    "{\"items\":[{\"id\":1,\"translation\":\"...\"}]}."
                )
                before_requests = tracker.actual_requests
                try:
                    repaired = _translate_batch_with_structured_retry(
                        batch=repair_payload,
                        expected_ids=[item_id],
                        batch_index=ordinal,
                        total_batches=len(batches),
                        effective_prompt=repair_prompt,
                        llm_model=translation_quality_model,
                        temperature=0.0,
                        api_provider=api_provider,
                        api_base=api_base,
                        api_key=api_key,
                        tracker=tracker,
                        request_is_extra=False,
                    )
                    repair_text = repaired[item_id]
                    cached_repairs[str(item_id)] = repair_text
                except Exception as exc:
                    cached_repairs[str(item_id)] = {
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                    }
                    unresolved.add(item_id)
                    summary.semantic_repair_rejected_ids.append(item_id)
                    save_semantic_cache(stages_path, stages)
                    continue
                finally:
                    summary.semantic_repair_requests += (
                        tracker.actual_requests - before_requests
                    )
                save_semantic_cache(stages_path, stages)

            judgment = cached_judgments.get(str(item_id))
            try:
                validated_judgment = (
                    validate_judgment(judgment, item_id)
                    if isinstance(judgment, dict)
                    else None
                )
            except RuntimeError:
                validated_judgment = None
            mapping = deterministic_ab_mapping(key, item_id)
            if validated_judgment is None:
                print(f"Translation stage: semantic_judge (id={item_id})")
                options = {
                    mapping["initial"]: initial_map[item_id],
                    mapping["repair"]: repair_text,
                }
                judge_payload = {
                    "id": item_id,
                    "source": item_by_id[item_id].text,
                    "option_a": options["A"],
                    "option_b": options["B"],
                    "issues": eligible,
                    **_semantic_context(
                        items, {**cached_translations, **initial_map}, item_id
                    ),
                    "video_summary": validated_video["video_summary"],
                    "scene_summary": scene_analysis["scene_summary"],
                    **relevant_analysis(
                        validated_video, [item_id], item_by_id[item_id].text
                    ),
                }
                judge_prompt = (
                    "Blindly compare two subtitle translations. You do not know which is "
                    "original or repaired. Judge only fidelity, completeness, negation, "
                    "numbers, names, terminology, pronouns, and contextual continuity. "
                    "Do not reward stylistic rewriting. Choose TIE unless one option is "
                    "clearly more faithful.\n"
                    "Return strict JSON only: "
                    "{\"id\":1,\"choice\":\"A|B|TIE\","
                    "\"confidence\":\"high|medium|low\",\"reason\":\"...\"}."
                )
                before_requests = tracker.actual_requests
                try:
                    judgment_object = _request_json_object_stage(
                        stage="semantic_judge",
                        batch=judge_payload,
                        prompt=judge_prompt,
                        model=translation_quality_model,
                        temperature=0.0,
                        api_provider=api_provider,
                        api_base=api_base,
                        api_key=api_key,
                        tracker=tracker,
                        validator=lambda value, expected=item_id: validate_judgment(
                            value, expected
                        ),
                    )
                    validated_judgment = validate_judgment(
                        judgment_object, item_id
                    )
                    cached_judgments[str(item_id)] = validated_judgment
                except Exception as exc:
                    cached_judgments[str(item_id)] = {
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                    }
                    unresolved.add(item_id)
                    summary.semantic_repair_rejected_ids.append(item_id)
                    save_semantic_cache(stages_path, stages)
                    continue
                finally:
                    summary.semantic_judge_requests += (
                        tracker.actual_requests - before_requests
                    )
                save_semantic_cache(stages_path, stages)

            if (
                validated_judgment["confidence"] == "high"
                and validated_judgment["choice"] == mapping["repair"]
            ):
                final[item_id] = str(repair_text)
                final_sources[item_id] = "repair"
                summary.semantic_repair_accepted_ids.append(item_id)
            elif validated_judgment["choice"] == "TIE":
                unresolved.add(item_id)
                summary.semantic_repair_tie_ids.append(item_id)
            else:
                unresolved.add(item_id)
                summary.semantic_repair_rejected_ids.append(item_id)

        for item_id in expected_ids:
            formatted = deterministic_subtitle_format(
                final[item_id], max_line_chars=min(18, max_chars_per_subtitle_zh)
            )
            final[item_id] = formatted
            if _target_character_count(formatted) > budget_by_id[item_id]:
                unresolved.add(item_id)
                summary.budget_violation_ids.append(item_id)

        cached["final"] = {str(item_id): final[item_id] for item_id in expected_ids}
        cached["final_sources"] = {
            str(item_id): final_sources[item_id] for item_id in expected_ids
        }
        cached["unresolved_ids"] = sorted(
            item_id for item_id in unresolved if item_id in set(expected_ids)
        )
        save_semantic_cache(stages_path, stages)
        for item_id in expected_ids:
            item_by_id[item_id].translation = final[item_id]
            cached_translations[item_id] = final[item_id]
            final_source_by_id[item_id] = final_sources[item_id]
            recent_translations.append({
                "id": item_id,
                "translation": final[item_id],
            })
        _save_translation_cache(cache_path, cached_translations)
        if progress_callback:
            progress_callback({
                "phase": "semantic_judgment",
                "translation_stage": "semantic_judgment",
                "completed_batches": ordinal,
                "total_batches": len(batches),
                "completed_items": len(cached_translations),
                "total_items": len(items),
            })

    consistency = stages.get("consistency")
    try:
        consistency_issues = (
            validate_consistency(consistency, all_ids)
            if isinstance(consistency, dict)
            else None
        )
    except RuntimeError:
        consistency_issues = None
    if consistency_issues is None:
        print("Translation stage: semantic_consistency")
        consistency_prompt = (
            "Audit the complete final subtitle translation without rewriting it. Report "
            "only clear whole-video inconsistencies in terminology, person naming/forms "
            "of address, pronouns, cross-scene continuity, or an ordinary word incorrectly "
            "left as a proper name. Return strict JSON only as "
            "{\"issues\":[{\"id\":1,\"type\":\"terminology|person|pronoun|continuity|"
            "common_word\",\"detail\":\"...\",\"related_ids\":[2]}]}."
        )
        before_requests = tracker.actual_requests
        consistency_object = _request_json_object_stage(
            stage="semantic_consistency",
            batch={
                "video_summary": validated_video["video_summary"],
                "speakers": validated_video["speakers"],
                "typed_glossary": validated_video["typed_glossary"],
                "items": [
                    {
                        "id": item.index,
                        "source": item.text,
                        "translation": item.translation,
                    }
                    for item in items
                ],
            },
            prompt=consistency_prompt,
            model=translation_quality_model,
            temperature=0.0,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            tracker=tracker,
            validator=lambda value: validate_consistency(value, all_ids),
        )
        consistency_issues = validate_consistency(consistency_object, all_ids)
        stages["consistency"] = {"issues": consistency_issues}
        summary.semantic_consistency_requests += (
            tracker.actual_requests - before_requests
        )
        save_semantic_cache(stages_path, stages)
    summary.semantic_consistency_issue_count = len(consistency_issues)
    unresolved.update(issue["id"] for issue in consistency_issues)
    for row in validated_video.get("suspected_asr_errors", []):
        unresolved.update(row.get("evidence_ids", []))

    summary.issue_counts.update(issue_counts)
    summary.unresolved_ids.extend(sorted(unresolved))
    summary.repaired_ids.extend(summary.semantic_repair_accepted_ids)
    summary.review_required = bool(unresolved)

    review_items = [item_by_id[item_id] for item_id in sorted(unresolved)]
    _atomic_write_srt(review_items, _semantic_review_srt_path(output_path))
    report = {
        "schema_version": 1,
        "strategy_version": SEMANTIC_REVIEW_VERSION,
        "prompt_version": SEMANTIC_PROMPT_VERSION,
        "strategy_mode": "semantic_review",
        "video_analysis": validated_video,
        "issue_counts": issue_counts,
        "consistency_issues": consistency_issues,
        "budget_violation_ids": sorted(set(summary.budget_violation_ids)),
        "unresolved_ids": sorted(unresolved),
        "final_sources": {
            str(item_id): final_source_by_id.get(item_id, "initial")
            for item_id in all_ids
        },
        "review_needed_srt": str(_semantic_review_srt_path(output_path)),
    }
    _atomic_write_json(_semantic_review_report_path(output_path), report)
    if progress_callback:
        progress_callback({
            "phase": "semantic_consistency",
            "translation_stage": "semantic_consistency",
            "completed_items": len(items),
            "total_items": len(items),
            "consistency_issue_count": len(consistency_issues),
        })


def _batch_for_ids(batch: dict, expected_ids: list[int], *, context_window: int) -> dict:
    wanted = set(expected_ids)
    selected = [item for item in batch.get("items", []) if item.get("id") in wanted]
    unselected = [item for item in batch.get("items", []) if item.get("id") not in wanted]
    before = list(batch.get("context_before", []))
    after = list(batch.get("context_after", []))
    if selected:
        first_id = selected[0]["id"]
        before.extend(item for item in unselected if item.get("id", 0) < first_id)
        after = [item for item in unselected if item.get("id", 0) > first_id] + after
    result = {"items": selected}
    if context_window > 0 and before:
        result["context_before"] = before[-context_window:]
    if context_window > 0 and after:
        result["context_after"] = after[:context_window]
    return result


def _translate_batch_adaptive(
    *,
    batch: dict,
    expected_ids: list[int],
    batch_index: int,
    total_batches: int,
    effective_prompt: str,
    llm_model: str,
    temperature: float,
    api_provider: str,
    api_base: str,
    api_key: str,
    context_window: int,
    tracker: TranslationRequestTracker,
    summary: TranslationRunSummary,
    persist,
    request_is_extra: bool = False,
) -> dict[int, str]:
    try:
        translated = _translate_batch_with_structured_retry(
            batch=batch,
            expected_ids=expected_ids,
            batch_index=batch_index,
            total_batches=total_batches,
            effective_prompt=effective_prompt,
            llm_model=llm_model,
            temperature=temperature,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            tracker=tracker,
            request_is_extra=request_is_extra,
        )
        persist(translated)
        return translated
    except TranslationReliabilityError as exc:
        if tracker.mode != "preview" or not exc.splittable or len(expected_ids) <= 1:
            raise
        midpoint = len(expected_ids) // 2
        left_ids = expected_ids[:midpoint]
        right_ids = expected_ids[midpoint:]
        summary.split_count += 1
        combined: dict[int, str] = {}
        for child_ids in (left_ids, right_ids):
            child = _batch_for_ids(batch, child_ids, context_window=context_window)
            translated = _translate_batch_adaptive(
                batch=child,
                expected_ids=child_ids,
                batch_index=batch_index,
                total_batches=total_batches,
                effective_prompt=effective_prompt,
                llm_model=llm_model,
                temperature=temperature,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                context_window=context_window,
                tracker=tracker,
                summary=summary,
                persist=persist,
                request_is_extra=True,
            )
            combined.update(translated)
        return combined


def _translate_batch_with_structured_retry(
    *,
    batch: dict,
    expected_ids: list[int],
    batch_index: int,
    total_batches: int,
    effective_prompt: str,
    llm_model: str,
    temperature: float,
    api_provider: str,
    api_base: str,
    api_key: str,
    tracker: TranslationRequestTracker | None = None,
    request_is_extra: bool = False,
) -> dict[int, str]:
    """Translate one batch and retry once if the model returns malformed structure."""
    last_error: RuntimeError | None = None
    batch_ids = set(expected_ids)

    for attempt in range(1, 3):
        prompt = effective_prompt
        if attempt == 2:
            print(
                f"Provider returned invalid structured output for batch "
                f"{batch_index}/{total_batches}; retrying once with stricter JSON instructions."
            )
            prompt = _build_structured_retry_prompt(effective_prompt, expected_ids)

        request_body = _build_request_body(
            batch=batch,
            effective_prompt=prompt,
            effective_model=llm_model,
            temperature=temperature,
            api_provider=api_provider,
        )
        response_text = _call_llm_api(
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            body=request_body,
            tracker=tracker,
            request_is_extra=request_is_extra or attempt > 1,
        )
        try:
            parsed = _parse_api_response(api_provider, response_text)
            translations = _extract_translations(parsed, expected_ids=expected_ids)
        except RuntimeError as exc:
            last_error = RuntimeError(
                f"Batch {batch_index}/{total_batches}: Provider returned invalid "
                f"translation JSON structure: {exc}"
            )
            if attempt == 1:
                continue
            raise TranslationReliabilityError(
                str(last_error), kind="structured_output", splittable=True
            ) from exc

        missing_ids = batch_ids - set(translations.keys())
        if missing_ids:
            last_error = RuntimeError(
                f"Batch {batch_index}/{total_batches}: missing translations for ids: "
                f"{sorted(missing_ids)}. Provider returned incomplete structured output."
            )
            if attempt == 1:
                continue
            raise TranslationReliabilityError(
                str(last_error), kind="missing_ids", splittable=True
            )

        return translations

    raise TranslationReliabilityError(
        str(last_error or f"Batch {batch_index}/{total_batches}: invalid structured output."),
        kind="structured_output",
        splittable=True,
    )


def _repair_blocking_translations(
    *, items: list[SubtitleItem], cached_translations: dict[int, str], cache_path: Path,
    target_language: str, effective_prompt: str, llm_model: str,
    translation_quality_model: str = "", temperature: float,
    api_provider: str, api_base: str, api_key: str, context_window: int,
    tracker: TranslationRequestTracker, summary: TranslationRunSummary,
    progress_callback: ProgressCallback | None,
) -> None:
    issue_positions: dict[int, tuple[str, ...]] = {}
    for position, item in enumerate(items):
        issue_types = blocking_translation_issues(item.text, item.translation, target_language)
        if issue_types:
            issue_positions[position] = issue_types
            for issue_type in issue_types:
                summary.issue_counts[issue_type] = summary.issue_counts.get(issue_type, 0) + 1

    for start, end, blocker_positions in build_repair_windows(len(items), issue_positions):
        blocker_ids = [items[position].index for position in blocker_positions]
        if tracker.budget_exhausted:
            summary.unresolved_ids.extend(blocker_ids)
            continue

        window = items[start:end]
        expected_ids = [item.index for item in window]
        before = items[max(0, start - context_window):start]
        after = items[end:end + context_window]
        repair_batch: dict = {
            "target_language": target_language,
            "requested_items": [
                {
                    "id": item.index,
                    "source_text": item.text,
                    "existing_translation": item.translation,
                }
                for item in window
            ]
        }
        if before:
            repair_batch["context_before"] = [
                {
                    "id": value.index,
                    "source_text": value.text,
                    "existing_translation": value.translation,
                }
                for value in before
            ]
        if after:
            repair_batch["context_after"] = [
                {
                    "id": value.index,
                    "source_text": value.text,
                    "existing_translation": value.translation,
                }
                for value in after
            ]
        issue_names = sorted({
            issue
            for position in blocker_positions
            for issue in issue_positions[position]
        })
        repair_prompt = (
            f"{effective_prompt}\n\nREPAIR A SUBTITLE WINDOW:\n"
            f"- The required target language code is {target_language}.\n"
            f"- Problems detected: {', '.join(issue_names)}.\n"
            "- Read the requested window as one continuous passage before translating it.\n"
            "- Detect sentences that continue across cue boundaries; do not translate fragments "
            "as independent sentences.\n"
            "- Rewrite every requested item in the target language. Every item must be non-empty "
            "and must not copy its source text.\n"
            "- You may redistribute wording across requested cues to preserve the complete meaning, "
            "but do not duplicate meaning already present in adjacent current translations.\n"
            "- Preserve each requested id and return every requested id exactly once.\n"
            "- Context items are read-only; do not return their ids.\n"
            "- Return strict JSON only."
        )
        summary.repair_windows_attempted += 1
        baseline_overlap = adjacent_translation_overlap_count(
            [item.translation for item in window]
        )
        candidate_options: dict[str, list[str]] = {}

        flash_candidate, flash_reasons = _request_repair_candidate(
            batch=repair_batch,
            prompt=repair_prompt,
            model=llm_model,
            stage="flash_initial",
            window=window,
            expected_ids=expected_ids,
            target_language=target_language,
            baseline_overlap=baseline_overlap,
            api_provider=api_provider,
            api_base=api_base,
            api_key=api_key,
            tracker=tracker,
            summary=summary,
        )
        if flash_candidate is not None:
            candidate_options["flash"] = flash_candidate
        if not translation_quality_model:
            if flash_candidate is None:
                summary.repair_windows_rejected += 1
                _increment_summary_count(
                    summary.repair_window_rejection_counts, "no_valid_candidate"
                )
                summary.unresolved_ids.extend(blocker_ids)
                continue
            selected_label, judge_reasons = "flash", ()
        else:
            if flash_candidate is None:
                correction_prompt = (
                    f"{repair_prompt}\n\nCORRECT A REJECTED ATTEMPT:\n"
                    f"- The prior attempt was rejected for: {', '.join(flash_reasons)}.\n"
                    "- Discard the prior answer and translate from source_text again.\n"
                    "- Do not repeat any rejected behavior."
                )
                corrected_candidate, _ = _request_repair_candidate(
                    batch=repair_batch,
                    prompt=correction_prompt,
                    model=llm_model,
                    stage="flash_correction",
                    window=window,
                    expected_ids=expected_ids,
                    target_language=target_language,
                    baseline_overlap=baseline_overlap,
                    api_provider=api_provider,
                    api_base=api_base,
                    api_key=api_key,
                    tracker=tracker,
                    summary=summary,
                )
                if corrected_candidate is not None:
                    candidate_options["flash_corrected"] = corrected_candidate

            quality_prompt = (
                f"{repair_prompt}\n\nQUALITY CANDIDATE:\n"
                "- Produce an independent high-quality translation from source_text.\n"
                "- Preserve semantic continuity across cue boundaries and avoid omissions."
            )
            quality_candidate, _ = _request_repair_candidate(
                batch=repair_batch,
                prompt=quality_prompt,
                model=translation_quality_model,
                stage="quality_candidate",
                window=window,
                expected_ids=expected_ids,
                target_language=target_language,
                baseline_overlap=baseline_overlap,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                summary=summary,
            )
            if quality_candidate is not None:
                candidate_options["quality"] = quality_candidate
            if summary.quality_model_unavailable:
                summary.repair_windows_rejected += 1
                _increment_summary_count(
                    summary.repair_window_rejection_counts, "quality_model_unavailable"
                )
                summary.unresolved_ids.extend(blocker_ids)
                break

            selected_label, judge_reasons = _request_repair_judgement(
                repair_batch=repair_batch,
                candidate_options=candidate_options,
                model=translation_quality_model,
                target_language=target_language,
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                tracker=tracker,
                summary=summary,
            )
        if not selected_label:
            summary.repair_windows_rejected += 1
            reason = "judge_rejected" if judge_reasons else "no_valid_candidate"
            _increment_summary_count(summary.repair_window_rejection_counts, reason)
            summary.unresolved_ids.extend(blocker_ids)
            if summary.quality_model_unavailable:
                break
            continue
        candidate_texts = candidate_options[selected_label]
        final_reasons = _candidate_rejection_reasons(
            window, candidate_texts, target_language, baseline_overlap
        )
        if final_reasons:
            summary.repair_windows_rejected += 1
            _increment_summary_count(summary.repair_window_rejection_counts, "final_guard")
            summary.unresolved_ids.extend(blocker_ids)
            continue

        updated_cache = dict(cached_translations)
        updated_cache.update(zip(expected_ids, candidate_texts, strict=True))
        try:
            _save_translation_cache(cache_path, updated_cache)
        except OSError:
            summary.repair_windows_rejected += 1
            _increment_summary_count(summary.repair_window_rejection_counts, "cache_write")
            summary.unresolved_ids.extend(blocker_ids)
            continue
        cached_translations.clear()
        cached_translations.update(updated_cache)
        for item, candidate in zip(window, candidate_texts, strict=True):
            item.translation = candidate
        summary.repaired_ids.extend(expected_ids)
        summary.repair_windows_accepted += 1
        if progress_callback:
            progress_callback({
                "phase": "repairing_translation",
                "repaired_count": len(summary.repaired_ids),
                "unresolved_count": len(summary.unresolved_ids),
                "repair_windows_accepted": summary.repair_windows_accepted,
                "repair_windows_rejected": summary.repair_windows_rejected,
            })
    if summary.quality_model_unavailable:
        summary.unresolved_ids.extend(
            items[position].index for position in issue_positions
        )
    summary.repaired_ids = sorted(set(summary.repaired_ids))
    summary.unresolved_ids = sorted(set(summary.unresolved_ids))


def _candidate_rejection_reasons(
    window: list[SubtitleItem],
    candidate_texts: list[str],
    target_language: str,
    baseline_overlap: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    issue_sets = [
        blocking_translation_issues(item.text, candidate, target_language)
        for item, candidate in zip(window, candidate_texts, strict=True)
    ]
    for issue_set in issue_sets:
        reasons.extend(issue_set)
    if adjacent_translation_overlap_count(candidate_texts) > baseline_overlap:
        reasons.append("adjacent_overlap")
    return tuple(dict.fromkeys(reasons))


def _request_repair_candidate(
    *,
    batch: dict,
    prompt: str,
    model: str,
    stage: str,
    window: list[SubtitleItem],
    expected_ids: list[int],
    target_language: str,
    baseline_overlap: int,
    api_provider: str,
    api_base: str,
    api_key: str,
    tracker: TranslationRequestTracker,
    summary: TranslationRunSummary,
) -> tuple[list[str] | None, tuple[str, ...]]:
    counter_name = {
        "flash_initial": "flash_initial_requests",
        "flash_correction": "flash_correction_requests",
        "quality_candidate": "quality_candidate_requests",
    }[stage]
    try:
        request_body = _build_request_body(
            batch=batch,
            effective_prompt=prompt,
            effective_model=model,
            temperature=REPAIR_REQUEST_TEMPERATURE,
            api_provider=api_provider,
        )
        before_requests = tracker.actual_requests
        try:
            response_text = _call_llm_api(
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                body=request_body,
                tracker=tracker,
                request_is_extra=True,
            )
        finally:
            setattr(
                summary,
                counter_name,
                getattr(summary, counter_name)
                + (tracker.actual_requests - before_requests),
            )
        parsed = _parse_api_response(api_provider, response_text)
        candidates = _extract_translations(parsed, expected_ids=expected_ids)
    except TranslationReliabilityError as exc:
        if stage == "quality_candidate" and exc.status in {401, 403, 404}:
            summary.quality_model_unavailable = True
        reasons = (f"http_{exc.status}" if exc.status else exc.kind,)
    except RuntimeError:
        reasons = ("response_error",)
    else:
        if set(candidates) != set(expected_ids):
            reasons = ("id_mismatch",)
        else:
            candidate_texts = [candidates[item.index].strip() for item in window]
            reasons = _candidate_rejection_reasons(
                window, candidate_texts, target_language, baseline_overlap
            )
            if not reasons:
                return candidate_texts, ()
            for reason in reasons:
                _increment_summary_count(summary.rejected_candidate_issue_counts, reason)
            if "adjacent_overlap" in reasons:
                summary.adjacent_overlap_rejections += 1
    _increment_summary_count(summary.candidate_stage_rejection_counts, stage)
    return None, reasons


def _request_repair_judgement(
    *,
    repair_batch: dict,
    candidate_options: dict[str, list[str]],
    model: str,
    target_language: str,
    api_provider: str,
    api_base: str,
    api_key: str,
    tracker: TranslationRequestTracker,
    summary: TranslationRunSummary,
) -> tuple[str, tuple[str, ...]]:
    if not candidate_options:
        return "", ("no_valid_candidate",)
    judge_batch = {
        "target_language": target_language,
        "requested_items": repair_batch["requested_items"],
        "context_before": repair_batch.get("context_before", []),
        "context_after": repair_batch.get("context_after", []),
        "candidate_options": [
            {
                "label": label,
                "items": [
                    {"id": item["id"], "text": text}
                    for item, text in zip(
                        repair_batch["requested_items"], texts, strict=True
                    )
                ],
            }
            for label, texts in candidate_options.items()
        ],
    }
    judge_prompt = (
        "You are a strict subtitle translation judge. Compare only the supplied candidates. "
        "Check meaning coverage, cross-cue continuity, omissions, duplication, wrong language, "
        "and readability. Never write or revise subtitle text. Return strict JSON only as "
        '{"decision":"accept","candidate":"label","issues":[]} or '
        '{"decision":"reject","candidate":"","issues":["issue_code"]}.'
    )
    try:
        before_requests = tracker.actual_requests
        try:
            response_text = _call_llm_api(
                api_provider=api_provider,
                api_base=api_base,
                api_key=api_key,
                body=_build_request_body(
                    batch=judge_batch,
                    effective_prompt=judge_prompt,
                    effective_model=model,
                    temperature=REPAIR_REQUEST_TEMPERATURE,
                    api_provider=api_provider,
                ),
                tracker=tracker,
                request_is_extra=True,
            )
        finally:
            summary.judge_requests += tracker.actual_requests - before_requests
        parsed = _parse_api_response(api_provider, response_text)
        payload = json.loads(parsed)
        decision = str(payload.get("decision") or "").strip()
        label = str(payload.get("candidate") or "").strip()
        raw_issues = payload.get("issues", [])
        issues = tuple(
            str(value).strip()
            for value in raw_issues
            if isinstance(value, str) and str(value).strip()
        ) if isinstance(raw_issues, list) else ("invalid_issues",)
        if decision == "accept" and label in candidate_options and not issues:
            return label, ()
        reasons = issues or ("judge_rejected",)
    except TranslationReliabilityError as exc:
        if exc.status in {401, 403, 404}:
            summary.quality_model_unavailable = True
        reasons = (f"http_{exc.status}" if exc.status else exc.kind,)
    except (RuntimeError, json.JSONDecodeError, TypeError, AttributeError):
        reasons = ("judge_response_error",)
    for reason in reasons:
        _increment_summary_count(summary.judge_rejection_counts, reason)
    return "", reasons


def _increment_summary_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _build_structured_retry_prompt(effective_prompt: str, expected_ids: list[int]) -> str:
    expected = ", ".join(str(tid) for tid in expected_ids)
    return (
        f"{effective_prompt}\n\n"
        "STRICT JSON RETRY:\n"
        "- Return only valid JSON, no Markdown, no comments, no trailing commas.\n"
        "- Return exactly this shape: {\"items\":[{\"id\":1,\"text\":\"...\"}]}.\n"
        f"- Include every requested id exactly once. Required ids: {expected}.\n"
        "- Do not include context ids. Do not omit empty or difficult subtitles."
    )


def _build_default_prompt(target_language: str) -> str:
    lang_name = _language_name(target_language)
    return (
        f"你是专业影视字幕翻译。把字幕翻译成自然、简洁、口语化的{lang_name}。\n"
        "要求：\n"
        "1. 保留人名、地名和专有名词的一致性。\n"
        "2. 主动联系上下文理解代词、省略、俚语、双关、前后呼应和说话人语气。\n"
        "3. 不要解释，不要扩写剧情。\n"
        "4. 每条字幕尽量短，适合屏幕阅读。\n"
        "5. 只返回指定 JSON 格式。"
    )


def _build_effective_prompt(default_prompt: str, custom_prompt: str) -> str:
    if custom_prompt.strip():
        return f"{default_prompt}\n\n用户额外要求：\n{custom_prompt.strip()}"
    return default_prompt


def _language_name(code: str) -> str:
    mapping = {
        "zh-CN": "中文",
        "zh-TW": "繁体中文",
        "en": "英文",
        "ja": "日文",
        "ko": "韩文",
        "fr": "法文",
        "de": "德文",
        "es": "西班牙文",
        "ru": "俄文",
        "pt": "葡萄牙文",
        "ar": "阿拉伯文",
        "th": "泰文",
        "vi": "越南文",
    }
    return mapping.get(code, code)


def _build_batches(
    items: list[SubtitleItem],
    batch_size: int,
    context_window: int,
) -> list[dict]:
    total = len(items)
    batches: list[dict] = []

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch_items = items[start:end]

        context_before: list[dict] = []
        context_after: list[dict] = []

        if context_window > 0:
            ctx_start = max(0, start - context_window)
            for i in range(ctx_start, start):
                context_before.append({"id": items[i].index, "text": items[i].text})

            ctx_end = min(total, end + context_window)
            for i in range(end, ctx_end):
                context_after.append({"id": items[i].index, "text": items[i].text})

        items_payload = [{"id": it.index, "text": it.text} for it in batch_items]

        batch: dict = {"items": items_payload}
        if context_before:
            batch["context_before"] = context_before
        if context_after:
            batch["context_after"] = context_after

        batches.append(batch)

    return batches


def _build_request_body(
    *,
    batch: dict,
    effective_prompt: str,
    effective_model: str,
    temperature: float,
    api_provider: str,
) -> str:
    user_content = json.dumps(batch, ensure_ascii=False)

    if api_provider == "anthropic":
        body = {
            "model": effective_model,
            "system": effective_prompt,
            "messages": [{"role": "user", "content": user_content}],
            "temperature": temperature,
            "max_tokens": 4096,
        }
    else:
        body = {
            "model": effective_model,
            "messages": [
                {"role": "system", "content": effective_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
        }

    return json.dumps(body, ensure_ascii=False)


def _call_llm_api(
    *,
    api_provider: str,
    api_base: str,
    api_key: str,
    body: str,
    tracker: TranslationRequestTracker | None = None,
    request_is_extra: bool = False,
    force_bounded_retry: bool = False,
) -> str:
    base = api_base.rstrip("/")

    if api_provider == "anthropic":
        url = f"{base}/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    data = body.encode("utf-8")

    last_error: BaseException | None = None
    for attempt in range(1, 4):
        try:
            if tracker is not None:
                tracker.before_request(extra=request_is_extra or attempt > 1)
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read().decode("utf-8")
        except http.client.IncompleteRead as exc:
            last_error = exc
            if attempt < 3:
                print(f"LLM API response was interrupted; retrying {attempt}/2...")
                time.sleep(attempt)
                continue
            break
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            safe_body = re.sub(
                r"(?i)(api[_-]?key|authorization|token|secret)[\"'\s:=]+[^\s,}\]]+",
                r"\1=[redacted]",
                error_body[:500],
            )
            if exc.code in {401, 403}:
                raise TranslationReliabilityError(
                    f"LLM API authentication failed with HTTP {exc.code}.",
                    kind="authentication",
                    status=exc.code,
                ) from exc
            if exc.code == 404:
                raise TranslationReliabilityError(
                    "LLM API endpoint or model was not found (HTTP 404).",
                    kind="not_found",
                    status=exc.code,
                ) from exc
            context_too_long = exc.code == 413 or (
                exc.code == 400
                and any(marker in safe_body.lower() for marker in (
                    "context length", "context_length", "too many tokens", "maximum context"
                ))
            )
            if context_too_long:
                raise TranslationReliabilityError(
                    f"LLM request exceeded the provider context limit (HTTP {exc.code}).",
                    kind="context_too_long",
                    splittable=True,
                    status=exc.code,
                ) from exc
            if exc.code == 429 or 500 <= exc.code <= 599:
                if not force_bounded_retry and (tracker is None or tracker.mode != "preview"):
                    raise TranslationReliabilityError(
                        f"LLM API returned HTTP {exc.code}: {safe_body}",
                        kind="rate_limited" if exc.code == 429 else "server_error",
                        status=exc.code,
                    ) from exc
                last_error = exc
                if attempt < 3:
                    retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                    try:
                        delay = min(max(float(retry_after), 0.0), 10.0)
                    except (TypeError, ValueError):
                        delay = float(attempt)
                    print(f"LLM API HTTP {exc.code}; retrying {attempt}/2...")
                    time.sleep(delay)
                    continue
                raise TranslationReliabilityError(
                    f"LLM API remained unavailable after retries (HTTP {exc.code}).",
                    kind="rate_limited" if exc.code == 429 else "server_error",
                    status=exc.code,
                ) from exc
            raise TranslationReliabilityError(
                f"LLM API returned HTTP {exc.code}: {safe_body}",
                kind="http_error",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 3:
                print(f"LLM API connection failed; retrying {attempt}/2: {exc.reason}")
                time.sleep(attempt)
                continue
            raise TranslationReliabilityError(
                f"LLM API connection failed: {exc.reason}", kind="network_error"
            ) from exc
        except OSError as exc:
            last_error = exc
            if attempt < 3:
                print(f"LLM API request error; retrying {attempt}/2: {exc}")
                time.sleep(attempt)
                continue
            raise TranslationReliabilityError(
                f"LLM API request error: {exc}", kind="network_error"
            ) from exc

    raise TranslationReliabilityError(
        "LLM API response was interrupted while reading chunked data. "
        "Try a smaller --translation-batch-size such as 5 or 3, or retry later. "
        f"Last error: {last_error}",
        kind="interrupted_response",
        splittable=True,
    )


def _parse_api_response(api_provider: str, response_text: str) -> str:
    """Extract the text content from an API response. Returns a text string
    that should contain JSON."""
    try:
        body = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse API response as JSON: {exc}") from exc

    if api_provider == "anthropic":
        content = body.get("content")
        if not isinstance(content, list):
            raise RuntimeError(
                f"Unexpected Anthropic response format: content is not a list. "
                f"Got: {json.dumps(body, ensure_ascii=False)[:300]}"
            )
        text_parts = [
            block.get("text", "") for block in content if block.get("type") == "text"
        ]
        return "\n".join(text_parts)
    else:
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise RuntimeError(
                f"Unexpected OpenAI response format: choices missing or empty. "
                f"Got: {json.dumps(body, ensure_ascii=False)[:300]}"
            )
        return choices[0].get("message", {}).get("content", "")


def _extract_translations(text: str, expected_ids: list[int] | None = None) -> dict[int, str]:
    """Parse model JSON output and return {id: translation} dict."""
    # Strip Markdown code blocks if present
    text = text.strip()
    md_pattern = r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$"
    match = re.match(md_pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        repaired_text = _strip_json_line_comments(text)
        if repaired_text != text:
            try:
                parsed = json.loads(repaired_text)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Failed to parse model translation output as JSON. "
                    f"Provider output may contain invalid comments or trailing text. "
                    f"Raw output (first 500 chars): {text[:500]}"
                ) from exc
        else:
            raise RuntimeError(
                f"Failed to parse model translation output as JSON. "
                f"Provider output was not valid JSON. "
                f"Raw output (first 500 chars): {text[:500]}"
            ) from exc

    parsed = _normalize_translation_payload(parsed)

    result: dict[int, str] = {}
    if expected_ids and all(isinstance(entry, str) for entry in parsed):
        if len(parsed) != len(expected_ids):
            raise RuntimeError(
                "Model returned a JSON string array without ids, but its length "
                f"({len(parsed)}) does not match expected item count ({len(expected_ids)}). "
                f"Parsed: {json.dumps(parsed, ensure_ascii=False)[:500]}"
            )
        return dict(zip(expected_ids, parsed, strict=True))

    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("id")
        translation = _pick_translation_text(entry)
        if isinstance(tid, str) and tid.isdigit():
            tid = int(tid)
        if isinstance(tid, int) and isinstance(translation, str):
            if tid in result:
                raise RuntimeError(f"Model returned duplicate translation id: {tid}")
            result[tid] = translation

    if not result:
        raise RuntimeError(
            f"No valid translations found in model output. "
            f"Parsed: {json.dumps(parsed, ensure_ascii=False)[:500]}"
        )

    return result


def _strip_json_line_comments(text: str) -> str:
    """Remove // comments outside JSON strings without accepting arbitrary JSON5."""
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def _normalize_translation_payload(parsed) -> list:
    """Accept common LLM wrappers around the requested translation array."""
    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        if "id" in parsed and any(
            key in parsed for key in ("translation", "text", "translated_text", "target")
        ):
            return [parsed]

        for key in ("items", "translation", "translations", "results", "data", "output"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return _normalize_translation_payload(value)

        # Some models return {"1": "译文", "2": "译文"} or {"translations": {"1": "..."}}
        numeric_items = []
        for key, value in parsed.items():
            if isinstance(key, str) and key.isdigit() and isinstance(value, str):
                numeric_items.append({"id": int(key), "translation": value})
        if numeric_items:
            return numeric_items

    raise RuntimeError(
        f"Expected translation JSON array or object wrapper, got {type(parsed).__name__}. "
        f"Parsed: {json.dumps(parsed, ensure_ascii=False)[:500]}"
    )


def _pick_translation_text(entry: dict) -> str:
    for key in ("translation", "text", "translated_text", "target"):
        value = entry.get(key)
        if isinstance(value, str):
            return value
    return ""


# ── self-test ──────────────────────────────────────────────────────────


def _self_test() -> int:
    errors: list[str] = []

    # Use unique temp directory to avoid permission issues on Windows
    temp_dir = Path("work") / f"selftest-{uuid4().hex[:12]}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Test 1: SRT parse and write round-trip (bilingual)
        sample_srt = """\
1
00:00:01,000 --> 00:00:03,000
Hello world.

2
00:00:03,500 --> 00:00:06,000
Where are you going?

3
00:00:06,500 --> 00:00:09,000
Line one.
Line two.
"""

        test_path = temp_dir / "original.srt"
        test_path.write_text(sample_srt, encoding="utf-8")

        items = read_srt(test_path)
        if len(items) != 3:
            errors.append(f"Expected 3 items, got {len(items)}")
        if items[0].text != "Hello world.":
            errors.append(f"Unexpected text for item 0: {items[0].text}")
        if items[2].text != "Line one.\nLine two.":
            errors.append(f"Multi-line text mismatch: {items[2].text!r}")

        # Test 2: Write bilingual SRT
        items[0].translation = "你好世界。"
        items[1].translation = "你要去哪？"
        items[2].translation = "第一行。\n第二行。"

        output_path = temp_dir / "bilingual.srt"
        write_srt(items, output_path)
        written = output_path.read_text(encoding="utf-8")

        if "你好世界。" not in written:
            errors.append("Bilingual output missing Chinese translation")
        if "Hello world." not in written:
            errors.append("Bilingual output missing original text")
        if "00:00:01,000 --> 00:00:03,000" not in written:
            errors.append("Bilingual output missing time line")

        # Test 3: Parse bilingual SRT back (verify the translation is preserved)
        items2 = read_srt(output_path)
        if len(items2) != 3:
            errors.append(f"Re-read items count mismatch: {len(items2)}")

        # Test 4: Markdown code block JSON parsing
        md_json_str = '```json\n[{"id": 1, "translation": "你好"}, {"id": 2, "translation": "世界"}]\n```'
        result = _extract_translations(md_json_str)
        if result.get(1) != "你好" or result.get(2) != "世界":
            errors.append(f"Markdown code block parse failed: {result}")

        # Test 5: Plain JSON array
        plain_json = '[{"id": 1, "translation": "test"}]'
        result2 = _extract_translations(plain_json)
        if result2.get(1) != "test":
            errors.append(f"Plain JSON parse failed: {result2}")

        # Test 5b: Object wrappers commonly returned by LLMs
        wrapped_json = '{"items": [{"id": "1", "translation": "wrapped test"}]}'
        result_wrapped = _extract_translations(wrapped_json)
        if result_wrapped.get(1) != "wrapped test":
            errors.append(f"Wrapped JSON parse failed: {result_wrapped}")

        singular_wrapped_json = '{"translation": [{"id": 1, "text": "singular wrapper"}]}'
        result_singular_wrapped = _extract_translations(singular_wrapped_json)
        if result_singular_wrapped.get(1) != "singular wrapper":
            errors.append(f"Singular wrapped JSON parse failed: {result_singular_wrapped}")

        keyed_json = '{"translations": {"1": "keyed test"}}'
        result_keyed = _extract_translations(keyed_json)
        if result_keyed.get(1) != "keyed test":
            errors.append(f"Keyed JSON parse failed: {result_keyed}")

        ordered_json = '["ordered one", "ordered two"]'
        result_ordered = _extract_translations(ordered_json, expected_ids=[10, 11])
        if result_ordered != {10: "ordered one", 11: "ordered two"}:
            errors.append(f"Ordered string array parse failed: {result_ordered}")

        # Test 6: OpenAI-compatible response parsing
        openai_response = json.dumps({
            "choices": [
                {
                    "message": {
                        "content": '[{"id": 1, "translation": "openai test"}]'
                    }
                }
            ]
        })
        parsed_openai = _parse_api_response("openai-compatible", openai_response)
        if "openai test" not in parsed_openai:
            errors.append(f"OpenAI response parse failed: {parsed_openai}")

        # Test 7: Anthropic Claude Messages response parsing
        anthropic_response = json.dumps({
            "content": [
                {"type": "text", "text": '[{"id": 1, "translation": "claude test"}]'}
            ]
        })
        parsed_anthropic = _parse_api_response("anthropic", anthropic_response)
        if "claude test" not in parsed_anthropic:
            errors.append(f"Anthropic response parse failed: {parsed_anthropic}")

        # Test 8: context_window > 0 — only "items" ids are returned, context ids are not
        items_ctx = [
            SubtitleItem(index=1, time_line="00:00:01,000 --> 00:00:02,000", text="One"),
            SubtitleItem(index=2, time_line="00:00:02,000 --> 00:00:03,000", text="Two"),
            SubtitleItem(index=3, time_line="00:00:03,000 --> 00:00:04,000", text="Three"),
        ]
        batches = _build_batches(items_ctx, batch_size=2, context_window=1)
        batch0 = batches[0]
        assert batch0["items"] == [{"id": 1, "text": "One"}, {"id": 2, "text": "Two"}], f"batch0 items wrong: {batch0['items']}"
        assert batch0.get("context_after") == [{"id": 3, "text": "Three"}], f"batch0 context_after wrong: {batch0.get('context_after')}"
        batch1 = batches[1]
        assert batch1["items"] == [{"id": 3, "text": "Three"}], f"batch1 items wrong: {batch1['items']}"
        assert batch1.get("context_before") == [{"id": 2, "text": "Two"}], f"batch1 context_before wrong: {batch1.get('context_before')}"

    finally:
        # Best-effort cleanup
        import shutil
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    print("self-test: all checks passed")
    return 0


# ── main ───────────────────────────────────────────────────────────────


def _cli() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Translate an SRT subtitle file using an LLM."
    )
    parser.add_argument("input", nargs="?", help="Input SRT file.")
    parser.add_argument("output", nargs="?", help="Output bilingual SRT file.")
    parser.add_argument("--api-provider", default="openai-compatible",
                        choices=["openai-compatible", "anthropic"],
                        help="API provider type.")
    parser.add_argument("--api-base", default="", help="API base URL.")
    parser.add_argument("--api-key", default="", help="API key.")
    parser.add_argument("--llm-model", default="", help="Model name.")
    parser.add_argument(
        "--translation-quality-model", default="",
        help="Optional model for preview repair candidates and judging.",
    )
    parser.add_argument("--target-language", default="zh-CN", help="Target language code.")
    parser.add_argument("--translation-batch-size", type=int, default=20, help="Batch size.")
    parser.add_argument("--translation-temperature", type=float, default=0.2, help="Temperature.")
    parser.add_argument("--translation-mode", default="bilingual",
                        choices=["bilingual", "translated"])
    parser.add_argument("--context-window", type=int, default=3, help="Context window size.")
    parser.add_argument("--translation-prompt", default="", help="Custom translation prompt.")
    parser.add_argument(
        "--translation-strategy-mode", default="standard",
        choices=[
            "standard",
            "three_pass",
            "semantic_review",
            "wenyi_review",
            "semantic_wenyi_review",
        ],
        help="Translation strategy.",
    )
    parser.add_argument(
        "--translation-scene-gap-seconds", type=float, default=30.0,
        help="Silence gap used to split translation scenes.",
    )
    parser.add_argument("--translation-max-cps-zh", type=float, default=8.0)
    parser.add_argument(
        "--translation-max-chars-per-subtitle-zh", type=int, default=36
    )
    parser.add_argument(
        "--max-http-requests",
        type=int,
        default=None,
        help="Optional hard cap for provider HTTP requests in this run.",
    )
    parser.add_argument("--self-test", action="store_true", help="Run self-test.")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    if not args.input or not args.output:
        parser.error("input and output arguments are required")
        return 1

    missing = []
    if not args.api_provider:
        missing.append("api_provider")
    if not args.api_base:
        missing.append("api_base")
    api_key = args.api_key or os.environ.get("SUBTITLE_LLM_API_KEY", "")
    if not api_key:
        missing.append("api_key (set --api-key or SUBTITLE_LLM_API_KEY env var)")
    if not args.llm_model:
        missing.append("llm_model")
    if missing:
        print(f"ERROR: Missing required parameters: {', '.join(missing)}")
        return 1

    translate_srt(
        input_path=Path(args.input),
        output_path=Path(args.output),
        api_provider=args.api_provider,
        api_base=args.api_base,
        api_key=api_key,
        llm_model=args.llm_model,
        translation_quality_model=args.translation_quality_model,
        target_language=args.target_language,
        batch_size=args.translation_batch_size,
        temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        system_prompt=args.translation_prompt,
        context_window=args.context_window,
        translation_strategy_mode=args.translation_strategy_mode,
        scene_gap_seconds=args.translation_scene_gap_seconds,
        max_cps_zh=args.translation_max_cps_zh,
        max_chars_per_subtitle_zh=args.translation_max_chars_per_subtitle_zh,
        max_http_requests=args.max_http_requests,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
