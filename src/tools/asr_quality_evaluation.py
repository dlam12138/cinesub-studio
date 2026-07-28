from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

from encoding_utils import read_json, write_json

SCHEMA_VERSION = 1
CAMPAIGN_VERSION = "stage3-asr-quality-v1"
NORMALIZATION_VERSION = "asr-normalization-v1"
EXPECTED_SAMPLE_IDS = tuple(f"sample-{number:02d}" for number in range(1, 7))
REFERENCE_TYPES = {"gold_verbatim", "production_subtitle", "ocr_weak"}
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
        if campaign_scope == "stage3a_french_film":
            if reference_language != "fr":
                raise ValueError(f"{sample_id} must be French in the Stage 3A scope")
            source_group_id = str(sample.get("source_group_id") or "").strip()
            if not source_group_id:
                raise ValueError(f"{sample_id} must define source_group_id")
            source_groups.add(source_group_id)
    if campaign_scope == "stage3a_french_film" and len(source_groups) < 3:
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
    }


def _segmentation_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    zero_duration = overlap = long_cues = high_cps = 0
    gap_seconds = 0.0
    previous_end = -1
    for row in rows:
        match = re.match(
            r"(\d+):(\d+):(\d+),(\d+)\s+-->\s+(\d+):(\d+):(\d+),(\d+)",
            row["time"],
        )
        if not match:
            raise ValueError("Invalid SRT timestamp")
        values = [int(value) for value in match.groups()]
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
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


def _load_reports(reports_dir: Path) -> list[dict[str, Any]]:
    reports = [
        read_json(path) for path in sorted(reports_dir.glob("*.run.local.json"))
    ]
    if len(reports) != 28 or len({row.get("run_id") for row in reports}) != 28:
        raise ValueError("ASR evaluation requires exactly 28 unique campaign reports")
    return reports


def _public_projection(detail: dict[str, Any]) -> dict[str, Any]:
    public_samples = []
    for row in detail["samples"]:
        public_samples.append({
            "sample_id": row["sample_id"],
            "reference_type": row["reference_type"],
            "reference_language": row["reference_language"],
            "source_group_id": row["source_group_id"],
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
        "samples": public_samples,
        "source_groups": group_rows,
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
            if re.search(r"(?:[A-Za-z]:[\\/]|/Users/|/home/|\\\\)", value):
                raise ValueError("Public ASR report contains a private path")
    visit(payload)


def evaluate_campaign(
    contract_path: Path,
    reports_dir: Path,
    reference_manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    contract = read_json(contract_path)
    reports = _load_reports(reports_dir)
    samples = validate_reference_manifest(read_json(reference_manifest_path))
    evaluated_sha = str(contract.get("evaluated_sha") or "")
    expected_run_ids = {row.get("run_id") for row in contract.get("runs", [])}
    actual_run_ids = {row.get("run_id") for row in reports}
    if len(expected_run_ids) != 28 or actual_run_ids != expected_run_ids:
        raise ValueError("Campaign reports do not match the frozen 28-run contract")
    if not evaluated_sha or any(row.get("evaluated_sha") != evaluated_sha for row in reports):
        raise ValueError("Campaign report evaluated SHA mismatch")
    by_sample: dict[str, dict[str, dict[str, Any]]] = {}
    for report in reports:
        if report.get("scenario_id", "").endswith("multilingual-control"):
            continue
        by_sample.setdefault(report["sample_id"], {})[report["profile"]] = report

    detail_rows = []
    error_rows: list[dict[str, Any]] = []
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
            }
            if is_formal_score_reference(sample["reference_type"]):
                metrics.update(_jiwer_metrics(normalized_reference, normalized_candidate))
                error_rows.append({
                    "sample_id": sample_id,
                    "profile": profile,
                    **{key: metrics[key] for key in (
                        "wer", "cer", "substitutions", "insertions", "deletions"
                    )},
                })
            profiles[profile] = metrics

        quality = by_sample[sample_id]["quality"]
        retry_report = read_json(_artifact_path(quality, "asr_review")).get(
            "asr_retry_report", {}
        )
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
            ("model", "speed", "large-control", "large-v3"),
            ("resegment", "speed", "balanced", "balanced"),
        )
        for comparison, left_profile, right_profile, candidate_label in comparisons:
            left_rows = _parse_srt(_artifact_path(by_sample[sample_id][left_profile], "output_srt"))
            right_rows = _parse_srt(_artifact_path(by_sample[sample_id][right_profile], "output_srt"))
            count = min(len(left_rows), len(right_rows))
            selected = deterministic_review_indexes(
                evaluated_sha=evaluated_sha,
                sample_id=f"{sample_id}:{comparison}",
                candidate_count=count,
                suspicious_indexes=[],
            )
            for index in selected:
                seed = hashlib.sha256(
                    f"{evaluated_sha}:{sample_id}:{comparison}:{index}:{CAMPAIGN_VERSION}".encode()
                ).digest()
                swap = bool(seed[0] & 1)
                left, right = left_rows[index - 1], right_rows[index - 1]
                option_a, option_b = ((right, left) if swap else (left, right))
                review_id = f"{sample_id}-{comparison}-{index:04d}"
                blind_rows.append({
                    "review_id": review_id,
                    "sample_id": sample_id,
                    "cue_index": index,
                    "time": left["time"],
                    "category": comparison,
                    "option_a": option_a["text"],
                    "option_b": option_b["text"],
                    "preference": "",
                    "error_category": "",
                    "severity": "",
                    "notes": "",
                })
                blind_key[review_id] = {
                    "candidate": "A" if swap else "B",
                    "candidate_label": candidate_label,
                }
        detail_rows.append({
            "sample_id": sample_id,
            "reference_type": sample["reference_type"],
            "reference_language": str(
                sample.get("reference_language") or sample.get("language") or ""
            ),
            "source_group_id": str(sample.get("source_group_id") or sample_id),
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

    detail = {
        "schema_version": SCHEMA_VERSION,
        "campaign_version": CAMPAIGN_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "evaluated_sha": evaluated_sha,
        "run_count": len(reports),
        "gold_sample_count": sum(
            row["reference_type"] == "gold_verbatim" for row in detail_rows
        ),
        "samples": detail_rows,
    }
    public = _public_projection(detail)
    assert_public_safe(public)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "asr-quality-detail.local.json", detail)
    write_json(output_dir / "asr-quality-summary.local.json", public)
    write_json(output_dir / "asr-quality-review-key.local.json", blind_key)
    _write_csv(output_dir / "asr-quality-per-sample.local.csv", _flatten_samples(public))
    _write_csv(output_dir / "asr-quality-error-alignment.local.csv", error_rows)
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
