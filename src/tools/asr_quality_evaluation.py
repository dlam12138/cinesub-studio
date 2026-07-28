from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

from encoding_utils import read_json, write_json

SCHEMA_VERSION = 1
CAMPAIGN_VERSION = "stage3-asr-quality-v1"
NORMALIZATION_VERSION = "asr-normalization-v1"
PUBLIC_GOLD_SCOPE = "stage3a_public_gold"
PUBLIC_GOLD_METRIC_VERSION = "public-gold-asr-metrics-v1"
EXPECTED_SAMPLE_IDS = tuple(f"sample-{number:02d}" for number in range(1, 7))
REFERENCE_TYPES = {"gold_verbatim", "production_subtitle", "ocr_weak"}
SRT_TIMESTAMP_RE = re.compile(
    r"^\s*(\d+):(\d+):(\d+),(\d+)\s+-->\s+"
    r"(\d+):(\d+):(\d+),(\d+)\s*$"
)
PUBLIC_FORBIDDEN_KEYS = {
    "path", "transcript", "prompt", "api_key", "authorization",
    "source_srt", "reference_srt", "candidate_srt", "input",
}


def normalize_asr_text(value: str) -> str:
    """Normalize reference and candidate copies without altering source artifacts."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.translate(str.maketrans({
        "’": "'", "‘": "'", "ʼ": "'", "‐": "-", "‑": "-", "‒": "-",
        "–": "-", "—": "-", "―": "-",
    }))
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)
    text = re.sub(r"[^\w\s']", " ", text, flags=re.UNICODE)
    text = re.sub(r"(?<!\w)'|'(?!\w)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_formal_score_reference(reference_type: str) -> bool:
    return reference_type == "gold_verbatim"


def _parse_srt(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8-sig").strip()
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = block.strip().splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            raise ValueError(f"Invalid SRT block in private artifact: {path.name}")
        rows.append({
            "id": int(lines[0].strip()),
            "time": lines[1].strip(),
            "text": "\n".join(lines[2:]).strip(),
        })
    return rows


def _joined_text(rows: list[dict[str, Any]]) -> str:
    return " ".join(row["text"] for row in rows)


def parse_srt_interval(row: dict[str, Any]) -> tuple[int, int]:
    match = SRT_TIMESTAMP_RE.match(str(row.get("time") or ""))
    if not match:
        raise ValueError("Invalid SRT timestamp")
    values = [int(value) for value in match.groups()]
    start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
    end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
    if end < start:
        raise ValueError("Invalid SRT interval")
    return start, end


def _format_srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(int(milliseconds), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_time_aligned_review_units(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    *,
    window_ms: int = 15_000,
) -> list[dict[str, Any]]:
    if window_ms < 1:
        raise ValueError("Review window must be at least 1 millisecond")
    indexed = {
        "left": [
            (index, row, parse_srt_interval(row))
            for index, row in enumerate(left_rows, start=1)
        ],
        "right": [
            (index, row, parse_srt_interval(row))
            for index, row in enumerate(right_rows, start=1)
        ],
    }
    intervals = [
        interval
        for side in indexed.values()
        for _, _, interval in side
    ]
    if not intervals:
        return []
    timeline_start = min(start for start, _ in intervals)
    timeline_end = max(end for _, end in intervals)
    if timeline_end <= timeline_start:
        timeline_end = timeline_start + 1
    duration = max(timeline_end - timeline_start, 1)
    window_count = (duration + window_ms - 1) // window_ms
    units = [
        {
            "window_index": index + 1,
            "window_start_ms": timeline_start + index * window_ms,
            "window_end_ms": min(
                timeline_start + (index + 1) * window_ms,
                timeline_end,
            ),
            "left_text": "",
            "right_text": "",
            "left_row_indexes": [],
            "right_row_indexes": [],
        }
        for index in range(window_count)
    ]
    for side, rows in indexed.items():
        assigned: list[list[tuple[int, int, str]]] = [
            [] for _ in range(window_count)
        ]
        for row_index, row, (start, end) in rows:
            midpoint = start + (end - start) // 2
            window_index = min(
                max((midpoint - timeline_start) // window_ms, 0),
                window_count - 1,
            )
            assigned[window_index].append(
                (start, row_index, str(row.get("text") or "").strip())
            )
        for index, members in enumerate(assigned):
            members.sort(key=lambda item: (item[0], item[1]))
            units[index][f"{side}_text"] = " ".join(
                text for _, _, text in members if text
            )
            units[index][f"{side}_row_indexes"] = [
                row_index for _, row_index, _ in members
            ]
    return [
        unit for unit in units
        if unit["left_text"] or unit["right_text"]
    ]


def _build_time_aligned_blind_entries(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    *,
    evaluated_sha: str,
    sample_id: str,
    comparison: str,
    candidate_label: str,
    window_ms: int = 15_000,
    control_count: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    units = build_time_aligned_review_units(
        left_rows,
        right_rows,
        window_ms=window_ms,
    )
    selected = deterministic_review_indexes(
        evaluated_sha=evaluated_sha,
        sample_id=f"{sample_id}:{comparison}",
        candidate_count=len(units),
        suspicious_indexes=[],
        control_count=control_count,
    )
    rows: list[dict[str, Any]] = []
    key: dict[str, Any] = {}
    for selected_index in selected:
        unit = units[selected_index - 1]
        start = unit["window_start_ms"]
        end = unit["window_end_ms"]
        review_id = f"{sample_id}-{comparison}-{start:010d}-{end:010d}"
        seed = hashlib.sha256(
            (
                f"{evaluated_sha}:{sample_id}:{comparison}:"
                f"{start}:{end}:{CAMPAIGN_VERSION}"
            ).encode()
        ).digest()
        swap = bool(seed[0] & 1)
        left_text = unit["left_text"]
        right_text = unit["right_text"]
        option_a, option_b = (
            (right_text, left_text) if swap else (left_text, right_text)
        )
        rows.append({
            "review_id": review_id,
            "sample_id": sample_id,
            "window_index": unit["window_index"],
            "window_start_ms": start,
            "window_end_ms": end,
            "time": f"{_format_srt_time(start)} --> {_format_srt_time(end)}",
            "category": comparison,
            "option_a": option_a,
            "option_b": option_b,
            "preference": "",
            "error_category": "",
            "severity": "",
            "notes": "",
        })
        key[review_id] = {
            "candidate": "A" if swap else "B",
            "candidate_label": candidate_label,
        }
    return rows, key


def validate_reference_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if manifest.get("authorized") is not True:
        raise ValueError("ASR reference manifest must set authorized=true")
    raw_samples = manifest.get("samples", [])
    if isinstance(raw_samples, dict):
        samples = {str(key): value for key, value in raw_samples.items()}
    else:
        samples = {str(row.get("id") or row.get("sample_id")): row for row in raw_samples}
    if tuple(sorted(samples)) != EXPECTED_SAMPLE_IDS:
        raise ValueError("ASR reference manifest must define exactly sample-01 through sample-06")
    campaign_scope = str(manifest.get("campaign_scope") or "")
    source_groups = set()
    for sample_id, sample in samples.items():
        if sample.get("authorized") is not True:
            raise ValueError(f"{sample_id} must explicitly set authorized=true")
        reference_type = sample.get("reference_type")
        if reference_type not in REFERENCE_TYPES:
            raise ValueError(f"{sample_id} has an invalid reference_type")
        reference_language = str(
            sample.get("reference_language") or sample.get("language") or ""
        ).strip()
        if not reference_language:
            raise ValueError(f"{sample_id} must define reference_language")
        if (
            reference_type != "ocr_weak"
            and not str(sample.get("reference_path") or "").strip()
        ):
            raise ValueError(f"{sample_id} must define reference_path")
        if campaign_scope in {"stage3a_french_film", PUBLIC_GOLD_SCOPE}:
            if reference_language != "fr":
                raise ValueError(f"{sample_id} must be French in the Stage 3A scope")
            if campaign_scope == PUBLIC_GOLD_SCOPE and reference_type != "gold_verbatim":
                raise ValueError(
                    f"{sample_id} must use gold_verbatim in the public-gold scope"
                )
            source_group_id = str(sample.get("source_group_id") or "").strip()
            if not source_group_id:
                raise ValueError(f"{sample_id} must define source_group_id")
            source_groups.add(source_group_id)
    if (
        campaign_scope in {"stage3a_french_film", PUBLIC_GOLD_SCOPE}
        and len(source_groups) < 3
    ):
        raise ValueError("Stage 3A requires at least three distinct source groups")
    return samples


def deterministic_review_indexes(
    *,
    evaluated_sha: str,
    sample_id: str,
    candidate_count: int,
    suspicious_indexes: list[int],
    control_count: int = 20,
) -> list[int]:
    if candidate_count < 1:
        return []
    suspicious = sorted({
        int(index) for index in suspicious_indexes
        if 1 <= int(index) <= candidate_count
    })
    ordinary = [index for index in range(1, candidate_count + 1) if index not in suspicious]
    seed_material = f"{evaluated_sha}:{sample_id}:{CAMPAIGN_VERSION}"
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    selected = random.Random(seed).sample(ordinary, min(control_count, len(ordinary)))
    return sorted(set(suspicious + selected))


def _artifact_path(report: dict[str, Any], field: str) -> Path:
    value = str(report.get("artifacts", {}).get(field) or "")
    if not value:
        raise ValueError(f"Campaign report is missing private artifact {field}")
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(f"Private campaign artifact is unavailable: {field}")
    return path


def _retry_report(report: dict[str, Any]) -> dict[str, Any]:
    value = str(report.get("artifacts", {}).get("asr_review") or "")
    if value:
        path = Path(value)
        if path.is_file():
            payload = read_json(path).get("asr_retry_report", {})
            if isinstance(payload, dict):
                return payload
    payload = report.get("asr_retry_report", {})
    if not isinstance(payload, dict):
        raise ValueError("Campaign report contains an invalid ASR retry report")
    return payload


def _jiwer_metrics(reference: str, candidate: str) -> dict[str, Any]:
    try:
        import jiwer
    except ImportError as exc:
        raise RuntimeError("JiWER is required for gold_verbatim ASR evaluation") from exc
    word = jiwer.process_words(reference, candidate)
    char = jiwer.process_characters(reference, candidate)
    return {
        "wer": round(float(word.wer), 8),
        "cer": round(float(char.cer), 8),
        "substitutions": int(word.substitutions),
        "insertions": int(word.insertions),
        "deletions": int(word.deletions),
        "reference_words": int(word.hits + word.substitutions + word.deletions),
        "candidate_words": len(candidate.split()),
        "character_substitutions": int(char.substitutions),
        "character_insertions": int(char.insertions),
        "character_deletions": int(char.deletions),
        "reference_characters": int(char.hits + char.substitutions + char.deletions),
    }


def _segmentation_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    zero_duration = overlap = long_cues = high_cps = 0
    gap_seconds = 0.0
    previous_end = -1
    for row in rows:
        start, end = parse_srt_interval(row)
        zero_duration += end <= start
        overlap += start < previous_end
        if previous_end >= 0 and start > previous_end:
            gap_seconds += (start - previous_end) / 1000
        previous_end = max(previous_end, end)
        long_cues += max((len(line) for line in row["text"].splitlines()), default=0) > 42
        duration = max((end - start) / 1000, 0.001)
        high_cps += len(_joined_text([row]).replace(" ", "")) / duration > 20
    return {
        "cue_count": len(rows),
        "zero_duration_count": zero_duration,
        "overlap_count": overlap,
        "gap_seconds": round(gap_seconds, 3),
        "over_20_cps_count": high_cps,
        "over_42_character_line_count": long_cues,
        "joined_normalized_sha256": hashlib.sha256(
            normalize_asr_text(_joined_text(rows)).encode("utf-8")
        ).hexdigest(),
    }


def _load_reports(reports_dir: Path, expected_count: int) -> list[dict[str, Any]]:
    reports = [
        read_json(path) for path in sorted(reports_dir.glob("*.run.local.json"))
    ]
    if (
        len(reports) != expected_count
        or len({row.get("run_id") for row in reports}) != expected_count
    ):
        raise ValueError(
            f"ASR evaluation requires exactly {expected_count} unique campaign reports"
        )
    return reports


def _public_projection(detail: dict[str, Any]) -> dict[str, Any]:
    raw_groups = sorted({row["source_group_id"] for row in detail["samples"]})
    group_aliases = {
        source_group_id: f"source-group-{index:02d}"
        for index, source_group_id in enumerate(raw_groups, start=1)
    }
    public_samples = []
    for row in detail["samples"]:
        public_samples.append({
            "sample_id": row["sample_id"],
            "reference_type": row["reference_type"],
            "reference_language": row["reference_language"],
            "source_group_id": group_aliases[row["source_group_id"]],
            "duration_seconds": row.get("duration_seconds"),
            "profiles": {
                name: {
                    (
                        "joined_normalized_hash_prefix"
                        if key == "joined_normalized_sha256" else key
                    ): (value[:12] if key == "joined_normalized_sha256" else value)
                    for key, value in profile.items()
                    if key not in {"normalized_reference", "normalized_candidate"}
                }
                for name, profile in row["profiles"].items()
            },
            "retry": row["retry"],
        })
    group_rows = []
    for source_group_id in sorted({row["source_group_id"] for row in public_samples}):
        members = [row for row in public_samples if row["source_group_id"] == source_group_id]
        group_rows.append({
            "source_group_id": source_group_id,
            "sample_ids": [row["sample_id"] for row in members],
            "sample_count": len(members),
            "gold_sample_count": sum(
                row["reference_type"] == "gold_verbatim" for row in members
            ),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_version": CAMPAIGN_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "evaluated_sha": detail["evaluated_sha"],
        "run_count": detail["run_count"],
        "gold_sample_count": detail["gold_sample_count"],
        "dataset": detail.get("dataset"),
        "dataset_version": detail.get("dataset_version"),
        "dataset_license": detail.get("dataset_license"),
        "dataset_citation": detail.get("dataset_citation"),
        "source_page": detail.get("source_page"),
        "archive_hash_prefix": detail.get("archive_hash_prefix"),
        "dataset_snapshot_hash_prefix": detail.get("dataset_snapshot_hash_prefix"),
        "samples": public_samples,
        "source_groups": group_rows,
        "model_comparison": detail.get("model_comparison"),
        "resegment_comparison": detail.get("resegment_comparison"),
        "retry_decision": detail.get("retry_decision"),
        "formal_score_reference_types": ["gold_verbatim"],
        "excluded_from_formal_score": ["production_subtitle", "ocr_weak"],
    }


def assert_public_safe(payload: Any) -> None:
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).casefold()
                if (
                    lowered.endswith("_text")
                    or any(token in lowered for token in PUBLIC_FORBIDDEN_KEYS)
                ):
                    raise ValueError(f"Public ASR report contains forbidden field: {key}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            if (
                not re.match(r"^https?://", value, flags=re.IGNORECASE)
                and re.search(r"(?:[A-Za-z]:[\\/]|/Users/|/home/|\\\\)", value)
            ):
                raise ValueError("Public ASR report contains a private path")
    visit(payload)


def _corpus_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reference_words = sum(int(row["reference_words"]) for row in rows)
    word_errors = sum(
        int(row[key]) for row in rows
        for key in ("substitutions", "insertions", "deletions")
    )
    reference_characters = sum(int(row["reference_characters"]) for row in rows)
    character_errors = sum(
        int(row[key]) for row in rows
        for key in (
            "character_substitutions",
            "character_insertions",
            "character_deletions",
        )
    )
    return {
        "wer": round(word_errors / max(reference_words, 1), 8),
        "cer": round(character_errors / max(reference_characters, 1), 8),
        "substitutions": sum(int(row["substitutions"]) for row in rows),
        "insertions": sum(int(row["insertions"]) for row in rows),
        "deletions": sum(int(row["deletions"]) for row in rows),
        "reference_words": reference_words,
        "empty_output_count": sum(bool(row.get("empty_output")) for row in rows),
        "repeated_phrase_count": sum(bool(row.get("repeated_phrase")) for row in rows),
        "hallucination_like_insertion_count": sum(
            bool(row.get("hallucination_like_insertion")) for row in rows
        ),
        "end_to_end_seconds": round(
            sum(float(row.get("end_to_end_seconds") or 0) for row in rows),
            6,
        ),
        "peak_gpu_memory_mib": max(
            (int(row.get("peak_gpu_memory_mib") or 0) for row in rows),
            default=0,
        ),
    }


def _candidate_text_for_interval(
    candidate_rows: list[dict[str, Any]],
    start: int,
    end: int,
) -> str:
    selected = []
    for row in candidate_rows:
        row_start, row_end = parse_srt_interval(row)
        midpoint = row_start + (row_end - row_start) // 2
        if start <= midpoint < end:
            selected.append((row_start, str(row.get("text") or "")))
    return " ".join(text for _, text in sorted(selected))


def _has_repeated_phrase(text: str) -> bool:
    words = normalize_asr_text(text).split()
    for width in (3, 4, 5):
        for index in range(0, len(words) - width * 2 + 1):
            if words[index:index + width] == words[index + width:index + width * 2]:
                return True
    return False


def _clip_error_rows(
    *,
    sample_id: str,
    profile: str,
    reference_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for clip_index, reference_row in enumerate(reference_rows, start=1):
        start, end = parse_srt_interval(reference_row)
        reference = normalize_asr_text(str(reference_row.get("text") or ""))
        candidate = normalize_asr_text(
            _candidate_text_for_interval(candidate_rows, start, end)
        )
        metrics = _jiwer_metrics(reference, candidate)
        rows.append({
            "sample_id": sample_id,
            "clip_index": clip_index,
            "profile": profile,
            **metrics,
            "empty_output": not bool(candidate),
            "repeated_phrase": _has_repeated_phrase(candidate),
            "hallucination_like_insertion": (
                metrics["insertions"] >= 3
                and metrics["insertions"] / max(metrics["reference_words"], 1) >= 0.2
            ),
        })
    return rows


def _manifest_duration(sample: dict[str, Any]) -> float | None:
    start = sample.get("start")
    end = sample.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    return round(float(end) - float(start), 6)


def paired_bootstrap(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
    *,
    seed_material: str,
    resamples: int = 10_000,
) -> dict[str, Any]:
    left = {
        (row["sample_id"], int(row["clip_index"])): row for row in left_rows
    }
    right = {
        (row["sample_id"], int(row["clip_index"])): row for row in right_rows
    }
    if not left or set(left) != set(right):
        raise ValueError("Paired bootstrap requires identical non-empty clip units")
    keys = sorted(left)

    def wer(rows: list[dict[str, Any]]) -> float:
        errors = sum(
            int(row["substitutions"]) + int(row["insertions"]) + int(row["deletions"])
            for row in rows
        )
        words = sum(int(row["reference_words"]) for row in rows)
        return errors / max(words, 1)

    observed_deltas = {
        key: (
            (
                int(left[key]["substitutions"])
                + int(left[key]["insertions"])
                + int(left[key]["deletions"])
            ) / max(int(left[key]["reference_words"]), 1)
            - (
                int(right[key]["substitutions"])
                + int(right[key]["insertions"])
                + int(right[key]["deletions"])
            ) / max(int(right[key]["reference_words"]), 1)
        )
        for key in keys
    }
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed)
    deltas = []
    for _ in range(resamples):
        selected = [keys[generator.randrange(len(keys))] for _ in keys]
        deltas.append(
            wer([left[key] for key in selected])
            - wer([right[key] for key in selected])
        )
    deltas.sort()
    lower = deltas[math.floor((resamples - 1) * 0.025)]
    upper = deltas[math.ceil((resamples - 1) * 0.975)]
    return {
        "metric_version": PUBLIC_GOLD_METRIC_VERSION,
        "resamples": resamples,
        "seed_hex": f"{seed:016x}",
        "mean_delta_wer": round(sum(deltas) / len(deltas), 8),
        "ci95_lower": round(lower, 8),
        "ci95_upper": round(upper, 8),
        "large_v3_winning_clip_count": sum(value > 0 for value in observed_deltas.values()),
        "small_winning_clip_count": sum(value < 0 for value in observed_deltas.values()),
        "tie_clip_count": sum(value == 0 for value in observed_deltas.values()),
        "clip_count": len(keys),
    }


def evaluate_campaign(
    contract_path: Path,
    reports_dir: Path,
    reference_manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    contract = read_json(contract_path)
    expected_count = int(contract.get("run_count") or 0)
    reports = _load_reports(reports_dir, expected_count)
    manifest = read_json(reference_manifest_path)
    samples = validate_reference_manifest(manifest)
    evaluated_sha = str(contract.get("evaluated_sha") or "")
    expected_run_ids = {row.get("run_id") for row in contract.get("runs", [])}
    actual_run_ids = {row.get("run_id") for row in reports}
    if len(expected_run_ids) != expected_count or actual_run_ids != expected_run_ids:
        raise ValueError(
            f"Campaign reports do not match the frozen {expected_count}-run contract"
        )
    if not evaluated_sha or any(row.get("evaluated_sha") != evaluated_sha for row in reports):
        raise ValueError("Campaign report evaluated SHA mismatch")
    by_sample: dict[str, dict[str, dict[str, Any]]] = {}
    for report in reports:
        if report.get("scenario_id", "").endswith("multilingual-control"):
            continue
        by_sample.setdefault(report["sample_id"], {})[report["profile"]] = report

    detail_rows = []
    error_rows: list[dict[str, Any]] = []
    clip_error_rows: list[dict[str, Any]] = []
    blind_rows: list[dict[str, Any]] = []
    blind_key: dict[str, Any] = {}
    base = reference_manifest_path.parent
    for sample_id in EXPECTED_SAMPLE_IDS:
        sample = samples[sample_id]
        reference_path = str(sample.get("reference_path") or "").strip()
        reference_rows = (
            _parse_srt((base / reference_path).resolve())
            if reference_path else []
        )
        normalized_reference = normalize_asr_text(_joined_text(reference_rows))
        profiles: dict[str, Any] = {}
        for profile, report in sorted(by_sample[sample_id].items()):
            candidate_rows = _parse_srt(_artifact_path(report, "output_srt"))
            normalized_candidate = normalize_asr_text(_joined_text(candidate_rows))
            metrics = {
                **_segmentation_metrics(candidate_rows),
                "empty_output": not bool(normalized_candidate),
                "end_to_end_seconds": report.get("end_to_end_seconds"),
                "peak_gpu_memory_mib": max(
                    (
                        int(row.get("used_memory_mib") or 0)
                        for row in report.get("gpu_samples", [])
                    ),
                    default=0,
                ),
                "repeated_phrase": _has_repeated_phrase(normalized_candidate),
            }
            if is_formal_score_reference(sample["reference_type"]):
                metrics.update(_jiwer_metrics(normalized_reference, normalized_candidate))
                metrics["hallucination_like_insertion"] = (
                    metrics["insertions"] >= 3
                    and metrics["insertions"] / max(metrics["reference_words"], 1) >= 0.2
                )
                error_rows.append({
                    "sample_id": sample_id,
                    "profile": profile,
                    **{key: metrics[key] for key in (
                        "wer", "cer", "substitutions", "insertions", "deletions"
                    )},
                })
                clip_error_rows.extend(_clip_error_rows(
                    sample_id=sample_id,
                    profile=profile,
                    reference_rows=reference_rows,
                    candidate_rows=candidate_rows,
                ))
            profiles[profile] = metrics

        quality = by_sample[sample_id]["quality"]
        retry_report = _retry_report(quality)
        retry_windows = []
        for window_index, window in enumerate(retry_report.get("windows", []), start=1):
            retry_windows.append({
                "window_index": window_index,
                "accepted": bool(window.get("accepted")),
                "skipped": bool(window.get("skipped")),
                "reasons": list(window.get("reasons", [])),
                "baseline_metrics": window.get("baseline_metrics", {}),
                "candidate_metrics": window.get("candidate_metrics", {}),
                "metric_deltas": window.get("metric_deltas", {}),
                "baseline_hash_prefix": str(window.get("baseline_text_hash") or "")[:12],
                "candidate_hash_prefix": str(window.get("candidate_text_hash") or "")[:12],
            })

        comparisons = (
            (
                "model",
                (
                    "small-quality-control"
                    if contract.get("campaign_scope") == PUBLIC_GOLD_SCOPE
                    else "speed"
                ),
                "large-control",
                "large-v3",
            ),
            (
                "resegment",
                (
                    "small-balanced-no-resegment"
                    if contract.get("campaign_scope") == PUBLIC_GOLD_SCOPE
                    else "speed"
                ),
                "balanced",
                "balanced",
            ),
        )
        for comparison, left_profile, right_profile, candidate_label in comparisons:
            left_rows = _parse_srt(_artifact_path(by_sample[sample_id][left_profile], "output_srt"))
            right_rows = _parse_srt(_artifact_path(by_sample[sample_id][right_profile], "output_srt"))
            comparison_rows, comparison_key = _build_time_aligned_blind_entries(
                left_rows,
                right_rows,
                evaluated_sha=evaluated_sha,
                sample_id=sample_id,
                comparison=comparison,
                candidate_label=candidate_label,
            )
            blind_rows.extend(comparison_rows)
            blind_key.update(comparison_key)
        detail_rows.append({
            "sample_id": sample_id,
            "reference_type": sample["reference_type"],
            "reference_language": str(
                sample.get("reference_language") or sample.get("language") or ""
            ),
            "source_group_id": str(sample.get("source_group_id") or sample_id),
            "duration_seconds": _manifest_duration(sample),
            "profiles": profiles,
            "retry": {
                "planned_window_count": retry_report.get("planned_window_count", 0),
                "executed_window_count": retry_report.get("executed_window_count", 0),
                "accepted_window_count": retry_report.get("accepted_window_count", 0),
                "windows": retry_windows,
                "human_text_blind_review_available": False,
                "unavailable_reason": (
                    "The production retry audit intentionally stores hashes and metrics, "
                    "not baseline/candidate transcript text."
                ),
            },
        })

    model_comparison = None
    resegment_comparison = None
    retry_decision = None
    if contract.get("campaign_scope") == PUBLIC_GOLD_SCOPE:
        small_clips = [
            row for row in clip_error_rows
            if row["profile"] == "small-quality-control"
        ]
        large_clips = [
            row for row in clip_error_rows
            if row["profile"] == "large-control"
        ]
        small_corpus = _corpus_metrics(small_clips)
        large_corpus = _corpus_metrics(large_clips)
        for corpus, profile in (
            (small_corpus, "small-quality-control"),
            (large_corpus, "large-control"),
        ):
            corpus["end_to_end_seconds"] = round(sum(
                float(detail["profiles"][profile]["end_to_end_seconds"] or 0)
                for detail in detail_rows
            ), 6)
            corpus["peak_gpu_memory_mib"] = max(
                int(detail["profiles"][profile]["peak_gpu_memory_mib"] or 0)
                for detail in detail_rows
            )
        bootstrap = paired_bootstrap(
            small_clips,
            large_clips,
            seed_material=(
                f"{evaluated_sha}:"
                f"{manifest.get('archive_local_download_sha256', '') or manifest.get('dataset_snapshot_sha256', '')}:"
                f"{PUBLIC_GOLD_METRIC_VERSION}"
            ),
        )
        sample_regressions = []
        non_inferior = 0
        severe_regression = False
        for detail in detail_rows:
            small_wer = detail["profiles"]["small-quality-control"]["wer"]
            large_wer = detail["profiles"]["large-control"]["wer"]
            delta = round(small_wer - large_wer, 8)
            non_inferior += large_wer <= small_wer
            severe_regression |= large_wer - small_wer > 0.05
            sample_regressions.append({
                "sample_id": detail["sample_id"],
                "small_wer": small_wer,
                "large_v3_wer": large_wer,
                "delta_wer": delta,
            })
        relative_improvement = (
            (small_corpus["wer"] - large_corpus["wer"])
            / max(small_corpus["wer"], 1e-12)
        )
        promote = all((
            bootstrap["ci95_lower"] > 0,
            relative_improvement >= 0.05,
            non_inferior >= 4,
            not severe_regression,
            large_corpus["insertions"] <= small_corpus["insertions"],
            large_corpus["hallucination_like_insertion_count"]
            <= small_corpus["hallucination_like_insertion_count"],
            large_corpus["empty_output_count"] <= small_corpus["empty_output_count"],
        ))
        model_comparison = {
            "left_profile": "small-quality-control",
            "right_profile": "large-control",
            "small": small_corpus,
            "large_v3": large_corpus,
            "relative_wer_improvement": round(relative_improvement, 8),
            "bootstrap": bootstrap,
            "sample_non_inferior_count": non_inferior,
            "severe_regression": severe_regression,
            "per_sample": sample_regressions,
            "decision": "promote" if promote else "keep_current_model_split",
        }
        resegment_samples = []
        for detail in detail_rows:
            left = detail["profiles"]["small-balanced-no-resegment"]
            right = detail["profiles"]["balanced"]
            resegment_samples.append({
                "sample_id": detail["sample_id"],
                "text_preserved": (
                    left["joined_normalized_sha256"]
                    == right["joined_normalized_sha256"]
                ),
                "without_resegment": {
                    key: left[key] for key in (
                        "cue_count", "zero_duration_count", "overlap_count",
                        "gap_seconds", "over_20_cps_count",
                        "over_42_character_line_count",
                    )
                },
                "with_resegment": {
                    key: right[key] for key in (
                        "cue_count", "zero_duration_count", "overlap_count",
                        "gap_seconds", "over_20_cps_count",
                        "over_42_character_line_count",
                    )
                },
            })
        resegment_comparison = {
            "left_profile": "small-balanced-no-resegment",
            "right_profile": "balanced",
            "all_text_preserved": all(row["text_preserved"] for row in resegment_samples),
            "structural_safety_only": True,
            "natural_film_readability_requires_private_blind_review": True,
            "samples": resegment_samples,
            "decision": (
                "keep_pending_private_film_review"
                if all(row["text_preserved"] for row in resegment_samples)
                else "reject"
            ),
        }
        accepted = sum(
            int(detail["retry"]["accepted_window_count"]) for detail in detail_rows
        )
        retry_decision = {
            "planned_window_count": sum(
                int(detail["retry"]["planned_window_count"]) for detail in detail_rows
            ),
            "executed_window_count": sum(
                int(detail["retry"]["executed_window_count"]) for detail in detail_rows
            ),
            "accepted_window_count": accepted,
            "decision": "keep_dry_run" if accepted == 0 else "private_apply_confirmation",
        }

    detail = {
        "schema_version": SCHEMA_VERSION,
        "campaign_version": CAMPAIGN_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "evaluated_sha": evaluated_sha,
        "run_count": len(reports),
        "dataset": manifest.get("dataset"),
        "dataset_version": manifest.get("dataset_version"),
        "dataset_license": manifest.get("dataset_license"),
        "dataset_citation": manifest.get("dataset_citation"),
        "source_page": manifest.get("source_page"),
        "archive_hash_prefix": str(
            manifest.get("archive_local_download_sha256")
            or manifest.get("archive_hash_prefix")
            or ""
        )[:12],
        "dataset_snapshot_hash_prefix": str(
            manifest.get("dataset_snapshot_sha256") or ""
        )[:12],
        "gold_sample_count": sum(
            row["reference_type"] == "gold_verbatim" for row in detail_rows
        ),
        "samples": detail_rows,
        "model_comparison": model_comparison,
        "resegment_comparison": resegment_comparison,
        "retry_decision": retry_decision,
    }
    public = _public_projection(detail)
    assert_public_safe(public)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "asr-quality-detail.local.json", detail)
    write_json(output_dir / "asr-quality-summary.local.json", public)
    write_json(output_dir / "asr-quality-review-key.local.json", blind_key)
    _write_csv(output_dir / "asr-quality-per-sample.local.csv", _flatten_samples(public))
    _write_csv(output_dir / "asr-quality-error-alignment.local.csv", error_rows)
    _write_csv(output_dir / "asr-quality-clip-errors.local.csv", clip_error_rows)
    _write_tsv(output_dir / "asr-quality-blind-review.local.tsv", blind_rows)
    return public


def _flatten_samples(public: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for sample in public["samples"]:
        for profile, metrics in sample["profiles"].items():
            rows.append({
                "sample_id": sample["sample_id"],
                "reference_type": sample["reference_type"],
                "profile": profile,
                **metrics,
            })
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row}) if rows else ["sample_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["review_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate existing private ASR campaign artifacts.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--contract", type=Path, required=True)
    evaluate.add_argument("--reports-dir", type=Path, required=True)
    evaluate.add_argument("--reference-manifest", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_campaign(
        args.contract, args.reports_dir, args.reference_manifest, args.output_dir
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
