from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import secrets
import subprocess
import time
from pathlib import Path

from ffmpeg_locator import find_ffmpeg
from provider_store import resolve_provider_config
from runtime_paths import resolve_runtime_paths
from subtitle_translate import (
    SubtitleItem,
    _build_default_prompt,
    _build_effective_prompt,
    _save_translation_cache,
    _translation_cache_path,
    read_srt,
    translate_srt,
    write_srt,
)
from translation_reliability import (
    REPAIR_STRATEGY_VERSION,
    TranslationReliabilityError,
    adjacent_translation_overlap_count,
    blocking_translation_issues,
    build_repair_windows,
)

SAMPLE_ID = "french-short-blockers-v1"
SELECTED_SOURCE_IDS = tuple(range(58, 70)) + tuple(range(72, 84))
TARGET_ISSUES = {"empty_translation", "llm_boilerplate", "identical_translation", "possibly_untranslated"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _translation_text(source: str, bilingual: str) -> str:
    prefix = f"{source}\n"
    return bilingual[len(prefix):].strip() if bilingual.startswith(prefix) else bilingual.strip()


def _issue_map(source_items: list[SubtitleItem], translated_items: list[SubtitleItem]) -> dict[int, tuple[str, ...]]:
    translated_by_id = {item.index: item for item in translated_items}
    result: dict[int, tuple[str, ...]] = {}
    for source in source_items:
        translated = translated_by_id[source.index]
        target = _translation_text(source.text, translated.text)
        issues = blocking_translation_issues(source.text, target, "zh-CN")
        if issues:
            result[source.index] = issues
    return result


def _translation_values(
    source_items: list[SubtitleItem],
    translated_items: list[SubtitleItem],
) -> list[str]:
    translated_by_id = {item.index: item for item in translated_items}
    return [
        _translation_text(source.text, translated_by_id[source.index].text)
        for source in source_items
    ]


def _repair_window_descriptors(
    source_items: list[SubtitleItem],
    issues: dict[int, tuple[str, ...]],
) -> list[dict]:
    issue_positions = {
        position: issues[item.index]
        for position, item in enumerate(source_items)
        if item.index in issues
    }
    descriptors: list[dict] = []
    for number, (start, end, blocker_positions) in enumerate(
        build_repair_windows(len(source_items), issue_positions),
        start=1,
    ):
        descriptors.append({
            "window_id": f"window-{number}",
            "start": start,
            "end": end,
            "cue_ids": [item.index for item in source_items[start:end]],
            "blocker_ids": [source_items[position].index for position in blocker_positions],
        })
    return descriptors


def _prepare_sample(source_path: Path, baseline_path: Path, run_dir: Path) -> tuple[Path, list[SubtitleItem], list[SubtitleItem]]:
    source_by_id = {item.index: item for item in read_srt(source_path)}
    baseline_by_id = {item.index: item for item in read_srt(baseline_path)}
    missing = [value for value in SELECTED_SOURCE_IDS if value not in source_by_id or value not in baseline_by_id]
    if missing:
        raise RuntimeError(f"Validation sample is missing {len(missing)} required cue ids.")
    source_items: list[SubtitleItem] = []
    baseline_items: list[SubtitleItem] = []
    local_mapping: dict[str, int] = {}
    for new_id, source_id in enumerate(SELECTED_SOURCE_IDS, start=1):
        source = source_by_id[source_id]
        baseline = baseline_by_id[source_id]
        source_items.append(SubtitleItem(new_id, source.time_line, source.text))
        baseline_items.append(SubtitleItem(new_id, source.time_line, baseline.text))
        local_mapping[str(new_id)] = source_id
    sample_path = run_dir / "sample.source.srt"
    write_srt(source_items, sample_path)
    (run_dir / "cue-map.local.json").write_text(
        json.dumps(local_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return sample_path, source_items, baseline_items


def _validate_structure(source: list[SubtitleItem], output: list[SubtitleItem]) -> None:
    if len(source) != len(output):
        raise RuntimeError("Preview output changed the cue count.")
    for expected, actual in zip(source, output, strict=False):
        if expected.index != actual.index or expected.time_line != actual.time_line:
            raise RuntimeError("Preview output changed cue ids or the timeline.")


def _extract_audio(media_path: Path, source_items: list[SubtitleItem], destination: Path) -> list[str]:
    ffmpeg = find_ffmpeg(resolve_runtime_paths().project_root)
    if not ffmpeg or not media_path.is_file():
        return []
    windows = ((source_items[0], source_items[11]), (source_items[12], source_items[-1]))
    outputs: list[str] = []
    for number, (first, last) in enumerate(windows, start=1):
        start = first.time_line.split(" --> ", 1)[0].replace(",", ".")
        end = last.time_line.split(" --> ", 1)[1].replace(",", ".")
        output = destination / f"audio-window-{number}.wav"
        result = subprocess.run(
            [ffmpeg, "-y", "-ss", start, "-to", end, "-i", str(media_path),
             "-vn", "-ac", "1", "-ar", "16000", str(output)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return []
        outputs.append(output.name)
    return outputs


def _write_ab_pack(
    run_dir: Path, source: list[SubtitleItem], baseline: list[SubtitleItem], preview: list[SubtitleItem],
    fingerprint: str, media_path: Path, windows: list[dict],
) -> dict:
    pack_dir = run_dir / "ab-review"
    pack_dir.mkdir(parents=True, exist_ok=True)
    a_items: list[SubtitleItem] = []
    b_items: list[SubtitleItem] = []
    answer_key: dict[str, str] = {}
    label_by_id: dict[int, str] = {}
    for window in windows:
        window_id = window["window_id"]
        preview_is_a = (
            int(hashlib.sha256(f"{fingerprint}:{window_id}".encode()).hexdigest(), 16) % 2 == 0
        )
        label = "A" if preview_is_a else "B"
        answer_key[window_id] = label
        for cue_id in window["cue_ids"]:
            label_by_id[cue_id] = label
    for source_item, baseline_item, preview_item in zip(source, baseline, preview, strict=True):
        baseline_text = _translation_text(source_item.text, baseline_item.text)
        preview_text = _translation_text(source_item.text, preview_item.text)
        label = label_by_id.get(source_item.index)
        if label == "A":
            a_text, b_text = preview_text, baseline_text
        elif label == "B":
            a_text, b_text = baseline_text, preview_text
        else:
            a_text = b_text = baseline_text
        a_items.append(SubtitleItem(source_item.index, source_item.time_line, a_text))
        b_items.append(SubtitleItem(source_item.index, source_item.time_line, b_text))
    write_srt(a_items, pack_dir / "A.srt")
    write_srt(b_items, pack_dir / "B.srt")
    review_form = {
        "schema_version": 2,
        "sample_id": SAMPLE_ID,
        "status": "pending_user_review",
        "fields": [
            "missing", "duplicate", "wrong_language", "semantic_continuity",
            "timeline", "readability", "preference",
        ],
        "windows": [
            {
                "window_id": window["window_id"],
                "cue_ids": window["cue_ids"],
                "blocker_ids": window["blocker_ids"],
                "A": {},
                "B": {},
                "preference": "",
            }
            for window in windows
        ],
    }
    (pack_dir / "review-form.local.json").write_text(
        json.dumps(review_form, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pack_dir / "answer-key.local.json").write_text(
        json.dumps(answer_key, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    audio_files = _extract_audio(media_path, source, pack_dir)
    return {
        "cue_count": len(source),
        "review_window_count": len(windows),
        "audio_window_count": len(audio_files),
        "status": "pending_user_review",
    }


def _round_passed(round_result: dict, expected_window_count: int) -> bool:
    summary = round_result.get("summary", {})
    return (
        round_result.get("complete") is True
        and round_result.get("cue_count_unchanged") is True
        and round_result.get("timeline_unchanged") is True
        and round_result.get("blocking_issue_count") == 0
        and round_result.get("adjacent_overlap_delta", 1) <= 0
        and summary.get("repair_windows_attempted") == expected_window_count
        and summary.get("repair_windows_accepted") == expected_window_count
        and summary.get("repair_windows_rejected") == 0
        and summary.get("judge_requests", 0) >= expected_window_count
        and summary.get("quality_model_unavailable") is False
        and summary.get("unresolved_count") == 0
        and summary.get("budget_exhausted") is False
    )


def _quality_model(args: argparse.Namespace, provider: dict) -> str:
    return (
        str(getattr(args, "quality_model", "") or "").strip()
        or str(provider.get("translation_quality_model") or "").strip()
        or str(provider.get("llm_model") or "").strip()
    )


def run(args: argparse.Namespace) -> dict:
    paths = resolve_runtime_paths()
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = paths.project_root / "work" / "translation-reliability-validation" / f"{timestamp}-{secrets.token_hex(3)}"
    run_dir.mkdir(parents=True, exist_ok=False)
    source_path = paths.project_root / args.source
    baseline_path = paths.project_root / args.baseline
    media_path = paths.project_root / args.media
    sample_path, source_items, baseline_items = _prepare_sample(source_path, baseline_path, run_dir)
    provider = resolve_provider_config(args.provider)
    if not provider.get("api_key"):
        raise RuntimeError("The selected Provider has no API key.")
    fingerprint_payload = {
        "schema_version": 3,
        "sample_id": SAMPLE_ID,
        "source_sha256": _sha256(source_path),
        "baseline_sha256": _sha256(baseline_path),
        "selected_ids": SELECTED_SOURCE_IDS,
        "provider": args.provider,
        "model": provider.get("llm_model", ""),
        "quality_model": _quality_model(args, provider),
        "mode": "preview",
        "repair_strategy": REPAIR_STRATEGY_VERSION,
        "max_extra_requests": args.max_extra_requests,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    baseline_issues = _issue_map(source_items, baseline_items)
    repair_windows = _repair_window_descriptors(source_items, baseline_issues)
    baseline_overlap_count = adjacent_translation_overlap_count(
        _translation_values(source_items, baseline_items)
    )
    rounds: list[dict] = []
    preview_outputs: list[list[SubtitleItem]] = []
    total_requests = 0
    for round_number in (1, 2):
        remaining_http_budget = args.max_http_requests - total_requests
        if remaining_http_budget <= 0:
            rounds.append({
                "round": round_number,
                "complete": False,
                "failure_category": "TranslationBudgetExceeded",
                "failure_kind": "http_budget_exhausted",
                "failure_status": None,
            })
            break
        round_extra_limit = min(args.max_extra_requests, remaining_http_budget)
        round_source = run_dir / f"round-{round_number}.source.srt"
        write_srt(source_items, round_source)
        output = run_dir / f"round-{round_number}.preview.srt"
        effective_prompt = _build_effective_prompt(_build_default_prompt("zh-CN"), "")
        cache_path = _translation_cache_path(
            round_source,
            api_provider=provider["api_provider"],
            llm_model=provider["llm_model"],
            target_language="zh-CN",
            translation_mode="bilingual",
            effective_prompt=effective_prompt,
            reliability_mode="preview",
            translation_quality_model=_quality_model(args, provider),
        )
        baseline_cache = {
            source.index: _translation_text(source.text, baseline.text)
            for source, baseline in zip(source_items, baseline_items, strict=True)
        }
        _save_translation_cache(cache_path, baseline_cache)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                summary = translate_srt(
                    input_path=round_source, output_path=output,
                    api_provider=provider["api_provider"], api_base=provider["api_base"],
                    api_key=provider["api_key"], llm_model=provider["llm_model"],
                    translation_quality_model=(
                        _quality_model(args, provider)
                    ),
                    target_language="zh-CN", batch_size=len(source_items), temperature=0.2,
                    translation_mode="bilingual", context_window=3,
                    reliability_mode="preview", max_extra_requests=round_extra_limit,
                )
        except Exception as exc:
            rounds.append({
                "round": round_number,
                "complete": False,
                "failure_category": type(exc).__name__,
                "failure_kind": (
                    exc.kind if isinstance(exc, TranslationReliabilityError) else ""
                ),
                "failure_status": (
                    exc.status if isinstance(exc, TranslationReliabilityError) else None
                ),
            })
            break
        total_requests += summary.actual_requests
        output_items = read_srt(output)
        _validate_structure(source_items, output_items)
        output_issues = _issue_map(source_items, output_items)
        output_overlap_count = adjacent_translation_overlap_count(
            _translation_values(source_items, output_items)
        )
        preview_outputs.append(output_items)
        rounds.append({
            "round": round_number,
            "output_sha256": _sha256(output),
            "blocking_issue_count": sum(len(value) for value in output_issues.values()),
            "blocking_cue_count": len(output_issues),
            "adjacent_overlap_count": output_overlap_count,
            "adjacent_overlap_delta": output_overlap_count - baseline_overlap_count,
            "authorized_extra_request_limit": round_extra_limit,
            "summary": summary.safe_summary(),
            "complete": True,
            "cue_count_unchanged": True,
            "timeline_unchanged": True,
        })
        if summary.quality_model_unavailable:
            break
    baseline_count = sum(len(value) for value in baseline_issues.values())
    round_blockers = [
        item.get("blocking_issue_count", baseline_count)
        for item in rounds
    ]
    fixed_ratio = 1.0 if baseline_count == 0 else min(
        (baseline_count - value) / baseline_count for value in round_blockers
    )
    automatic_pass = (
        len(rounds) == 2
        and total_requests <= args.max_http_requests
        and fixed_ratio >= 0.8
        and all(
            _round_passed(item, len(repair_windows))
            for item in rounds
        )
    )
    ab_pack = _write_ab_pack(
        run_dir, source_items, baseline_items, preview_outputs[-1], fingerprint, media_path,
        repair_windows,
    ) if automatic_pass else {
        "status": "not_generated",
        "cue_count": 0,
        "review_window_count": 0,
        "audio_window_count": 0,
    }
    report = {
        "schema_version": 3,
        "sample_id": SAMPLE_ID,
        "sample_fingerprint": fingerprint,
        "selected_cue_count": len(source_items),
        "selected_id_hash": hashlib.sha256(
            json.dumps(SELECTED_SOURCE_IDS).encode("ascii")
        ).hexdigest(),
        "provider_id": args.provider,
        "model_id": provider["llm_model"],
        "quality_model_id": (
            _quality_model(args, provider)
        ),
        "repair_strategy": REPAIR_STRATEGY_VERSION,
        "baseline_blocking_issue_count": baseline_count,
        "baseline_adjacent_overlap_count": baseline_overlap_count,
        "repair_window_count": len(repair_windows),
        "rounds": rounds,
        "total_http_requests": total_requests,
        "authorized_http_request_limit": args.max_http_requests,
        "http_budget_exceeded": total_requests > args.max_http_requests,
        "target_issue_fixed_ratio": round(fixed_ratio, 4),
        "automatic_gate": "pass" if automatic_pass else "no_go",
        "human_gate": "pending" if automatic_pass else "not_started",
        "promotion_decision": "pending_human_review" if automatic_pass else "no_go",
        "production_default": "off",
        "ab_review": ab_pack,
        "contains_subtitle_text": False,
        "contains_api_key": False,
        "contains_absolute_paths": False,
    }
    (run_dir / "validation-report.public.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded translation reliability preview validation.")
    parser.add_argument("--provider", default="deepseek-main")
    parser.add_argument("--source", default="output/source/fr_short.small.srt")
    parser.add_argument("--baseline", default="output/bilingual/fr_short.small.bilingual.zh-CN.srt")
    parser.add_argument("--media", default="archive/fr_short.mp4")
    parser.add_argument("--quality-model", default="deepseek-v4-pro")
    parser.add_argument("--max-http-requests", type=int, default=40, choices=range(1, 41))
    parser.add_argument("--max-extra-requests", type=int, default=16, choices=range(0, 17))
    args = parser.parse_args()
    return 0 if run(args)["automatic_gate"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
