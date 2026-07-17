from __future__ import annotations

import hashlib
import http.client
import json
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
) -> Path:
    try:
        stat = input_path.stat()
        input_sig = f"{input_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
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
    tracker = TranslationRequestTracker(**reliability)

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
    )
    cached_translations = _load_translation_cache(cache_path)
    summary = TranslationRunSummary(
        mode=reliability["mode"],
        total_items=total_items,
        cache_hits=sum(1 for item in items if item.index in cached_translations),
    )
    if cached_translations:
        print(f"Loaded {len(cached_translations)} cached translation(s): {cache_path}")
        for item in items:
            if item.index in cached_translations:
                item.translation = cached_translations[item.index]

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
    summary.review_required = bool(summary.unresolved_ids)
    print(f"Translation done: {output_path}")
    return summary


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
                if tracker is None or tracker.mode != "preview":
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
        translation_quality_model=args.translation_quality_model or args.llm_model,
        target_language=args.target_language,
        batch_size=args.translation_batch_size,
        temperature=args.translation_temperature,
        translation_mode=args.translation_mode,
        system_prompt=args.translation_prompt,
        context_window=args.context_window,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
