from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from provider_store import resolve_provider_config
from runtime_paths import resolve_runtime_paths
from subtitle_translate import (
    SubtitleItem,
    _atomic_write_srt,
    _build_request_body,
    _call_llm_api,
    _extract_translations,
    _parse_api_response,
    read_srt,
)
from translation_reliability import TranslationRequestTracker, blocking_translation_issues


def _seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _bounds(time_line: str) -> tuple[float, float]:
    start, end = time_line.split(" --> ", 1)
    return _seconds(start), _seconds(end)


def _overlapping_asr(cue: SubtitleItem, asr_items: list[SubtitleItem]) -> str:
    start, end = _bounds(cue.time_line)
    matches = []
    for item in asr_items:
        item_start, item_end = _bounds(item.time_line)
        if min(end, item_end) - max(start, item_start) > 0.05:
            matches.append(item.text.replace("\n", " ").strip())
    return " ".join(dict.fromkeys(value for value in matches if value))


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _prompt() -> str:
    return (
        "You clean bilingual subtitles extracted from burned-in video text.\n"
        "For every requested id, return corrected French and a coherent Simplified Chinese translation.\n"
        "Use ocr_french and asr_french as noisy evidence; fix OCR spelling, accents, punctuation and spacing.\n"
        "The burned Chinese may be shifted to a neighboring cue, so do not copy it blindly.\n"
        "Resolve sentence fragments across adjacent cues using context_before/context_after.\n"
        "Do not repeat meaning already assigned to an adjacent cue. Do not add explanations.\n"
        "Preserve every id exactly once. Each value must contain exactly two lines: French then Chinese.\n"
        'Return one strict JSON object only: {"1":"French\\nChinese","2":"French\\nChinese"}.'
    )


def _split_bilingual(value: str) -> tuple[str, str]:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if len(lines) != 2:
        raise RuntimeError("Cleaner returned an item that does not contain exactly two non-empty lines.")
    return lines[0], lines[1]


def _normalized(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())


def _merge_exact_bilingual_duplicates(items: list[SubtitleItem]) -> tuple[list[SubtitleItem], int]:
    merged: list[SubtitleItem] = []
    merged_count = 0
    for item in items:
        if merged and _normalized(merged[-1].text) == _normalized(item.text):
            start = merged[-1].time_line.split(" --> ", 1)[0]
            end = item.time_line.split(" --> ", 1)[1]
            merged[-1].time_line = f"{start} --> {end}"
            merged_count += 1
            continue
        merged.append(SubtitleItem(len(merged) + 1, item.time_line, item.text))
    return merged, merged_count


def run(args: argparse.Namespace) -> dict:
    paths = resolve_runtime_paths()
    input_path = (paths.project_root / args.input).resolve()
    asr_path = (paths.project_root / args.asr).resolve()
    output_path = (paths.project_root / args.output).resolve()
    checkpoint = output_path.with_suffix(".progress.local.json")
    provider = resolve_provider_config(args.provider)
    if not provider.get("api_key"):
        raise RuntimeError("The selected Provider has no API key.")
    input_items = read_srt(input_path)
    asr_items = read_srt(asr_path)
    evidence = [
        {
            "id": item.index,
            "ocr_french": item.text.splitlines()[0] if item.text.splitlines() else "",
            "ocr_chinese": item.text.splitlines()[1] if len(item.text.splitlines()) > 1 else "",
            "asr_french": _overlapping_asr(item, asr_items),
        }
        for item in input_items
    ]
    tracker = TranslationRequestTracker(
        mode="preview", max_extra_requests=args.max_extra_requests
    )
    completed: dict[int, str] = {}
    previous_requests = 0
    if args.resume and checkpoint.is_file():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        completed = {
            int(key): str(value) for key, value in saved.get("translations", {}).items()
        }
        previous_requests = int(saved.get("request_count", 0))
    pending = [item for item in evidence if item["id"] not in completed]
    evidence_position = {item["id"]: position for position, item in enumerate(evidence)}
    network_pending = [] if args.fallback_ocr else pending
    for offset in range(0, len(network_pending), args.batch_size):
        current = network_pending[offset:offset + args.batch_size]
        first_position = evidence_position[current[0]["id"]]
        last_position = evidence_position[current[-1]["id"]]
        before = evidence[max(0, first_position - 2):first_position]
        after = evidence[last_position + 1:last_position + 3]
        batch = {"items": current, "context_before": before, "context_after": after}
        body = _build_request_body(
            batch=batch,
            effective_prompt=_prompt(),
            effective_model=provider["llm_model"],
            temperature=0.0,
            api_provider=provider["api_provider"],
        )
        if provider["api_provider"] == "openai-compatible":
            request_payload = json.loads(body)
            request_payload["response_format"] = {"type": "json_object"}
            body = json.dumps(request_payload, ensure_ascii=False)
        response = _call_llm_api(
            api_provider=provider["api_provider"],
            api_base=provider["api_base"],
            api_key=provider["api_key"],
            body=body,
            tracker=tracker,
        )
        parsed = _parse_api_response(provider["api_provider"], response)
        expected_ids = [item["id"] for item in current]
        translated = _extract_translations(parsed, expected_ids=expected_ids)
        if set(translated) != set(expected_ids):
            raise RuntimeError("Cleaner omitted or added subtitle ids.")
        for cue_id, value in translated.items():
            _split_bilingual(value)
            completed[cue_id] = value
        _atomic_json(checkpoint, {
            "completed_ids": sorted(completed),
            "request_count": previous_requests + tracker.actual_requests,
            "translations": {str(key): value for key, value in sorted(completed.items())},
        })
    llm_cleaned_count = len(completed)
    if args.fallback_ocr:
        for item in pending:
            french = str(item.get("ocr_french") or item.get("asr_french") or "").strip()
            chinese = str(item.get("ocr_chinese") or "").strip()
            if not french or not chinese:
                raise RuntimeError(f"OCR fallback is incomplete for cue {item['id']}.")
            completed[item["id"]] = f"{french}\n{chinese}"
    if len(completed) != len(input_items):
        raise RuntimeError("Cleaner did not complete every subtitle cue.")
    output_items: list[SubtitleItem] = []
    blockers = 0
    consecutive_duplicates = 0
    previous_chinese = ""
    for item in input_items:
        french, chinese = _split_bilingual(completed[item.index])
        blockers += len(blocking_translation_issues(french, chinese, "zh-CN"))
        normalized_chinese = _normalized(chinese)
        if previous_chinese and normalized_chinese == previous_chinese:
            consecutive_duplicates += 1
        previous_chinese = normalized_chinese
        output_items.append(SubtitleItem(item.index, item.time_line, f"{french}\n{chinese}"))
    output_items, merged_duplicate_count = _merge_exact_bilingual_duplicates(output_items)
    _atomic_write_srt(output_items, output_path)
    reparsed = read_srt(output_path)
    structure_ok = len(reparsed) == len(output_items) and all(
        expected.index == actual.index and expected.time_line == actual.time_line
        for expected, actual in zip(output_items, reparsed, strict=True)
    )
    summary = {
        "source_type": "automatically_cleaned_burned_subtitle_ocr",
        "provider_id": args.provider,
        "model_id": provider["llm_model"],
        "source_cue_count": len(input_items),
        "cue_count": len(output_items),
        "merged_exact_duplicate_count": merged_duplicate_count,
        "http_requests": previous_requests + tracker.actual_requests,
        "resume_http_requests": tracker.actual_requests,
        "known_timed_out_requests": args.known_timed_out_requests,
        "llm_cleaned_count": llm_cleaned_count,
        "ocr_fallback_count": len(input_items) - llm_cleaned_count,
        "structure_ok": structure_ok,
        "blocking_issue_count": blockers,
        "premerge_consecutive_duplicate_count": consecutive_duplicates,
        "final_consecutive_duplicate_count": 0,
        "manual_review_performed": False,
        "manual_review_recommended": True,
        "caveat": (
            "This is an automated best-effort hybrid. Remaining OCR fallback cues preserve burned text "
            "and may retain OCR errors or source subtitle misalignment."
        ),
        "output_file": output_path.name,
    }
    _atomic_json(output_path.with_suffix(".summary.json"), summary)
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatically clean burned bilingual OCR subtitles.")
    parser.add_argument(
        "--input",
        default="work/bilibili-subtitles/BV1mJ2rBCEe8/BV1mJ2rBCEe8.burned-bilingual.ocr.srt",
    )
    parser.add_argument("--asr", default="output/source/fr_short.small.srt")
    parser.add_argument(
        "--output",
        default="work/bilibili-subtitles/BV1mJ2rBCEe8/BV1mJ2rBCEe8.bilingual.auto-clean.srt",
    )
    parser.add_argument("--provider", default="deepseek-main")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--known-timed-out-requests", type=int, default=0)
    parser.add_argument("--max-extra-requests", type=int, default=1, choices=range(0, 2))
    parser.add_argument("--fallback-ocr", action="store_true")
    summary = run(parser.parse_args())
    return 0 if summary["structure_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
