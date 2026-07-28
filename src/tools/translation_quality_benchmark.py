from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from encoding_utils import read_json, write_json

SCHEMA_VERSION = 2
CAMPAIGN_VERSION = "stage3-translation-quality-v1"
REVIEW_DIMENSIONS = (
    "fidelity", "fluency", "terminology", "pronoun", "continuity",
    "character_voice", "subtitle_readability",
)


def _parse_srt(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError(f"SRT is empty: {path.name}")
    rows: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = block.strip().splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            raise ValueError(f"Invalid SRT block: {path.name}")
        try:
            item_id = int(lines[0].strip())
        except ValueError as exc:
            raise ValueError(f"Invalid SRT cue ID: {path.name}") from exc
        rows.append({
            "id": item_id,
            "time": lines[1].strip(),
            "text": "\n".join(lines[2:]).strip(),
        })
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError(f"SRT contains duplicate cue IDs: {path.name}")
    return rows


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _char_ngram_f1(candidate: str, reference: str, n: int = 2) -> float:
    def counts(value: str) -> Counter[str]:
        compact = _compact(value)
        return Counter(compact[index:index + n] for index in range(max(0, len(compact) - n + 1)))

    left, right = counts(candidate), counts(reference)
    overlap = sum((left & right).values())
    left_total, right_total = sum(left.values()), sum(right.values())
    if not left_total and not right_total:
        return 1.0
    if not left_total or not right_total:
        return 0.0
    precision, recall = overlap / left_total, overlap / right_total
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_manifest(manifest_path: Path) -> tuple[dict[str, Any], Path]:
    manifest = read_json(manifest_path)
    if manifest.get("authorized") is not True:
        raise ValueError("benchmark manifest must set authorized=true")
    if not manifest.get("samples"):
        raise ValueError("benchmark manifest must contain samples")
    return manifest, manifest_path.parent


def _structure(candidate: list[dict[str, Any]], expected: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [row["id"] for row in candidate]
    expected_ids = [row["id"] for row in expected]
    by_id = {row["id"]: row for row in candidate}
    exact_ids = ids == expected_ids
    time_mismatches = sum(
        item["id"] not in by_id or by_id[item["id"]]["time"] != item["time"]
        for item in expected
    )
    empty_count = sum(not row["text"].strip() for row in candidate)
    extra_count = len(set(ids) - set(expected_ids))
    missing_count = len(set(expected_ids) - set(ids))
    suspicious_wrapper_count = sum(
        bool(re.search(r"```|^\s*(?:json|markdown)\s*[:：]|作为(?:ai|人工智能)", row["text"], re.I))
        for row in candidate
    )
    passed = (
        exact_ids and not time_mismatches and not empty_count and not extra_count
        and not missing_count and not suspicious_wrapper_count
    )
    return {
        "ids_exact": exact_ids,
        "time_mismatch_count": time_mismatches,
        "empty_count": empty_count,
        "extra_count": extra_count,
        "missing_count": missing_count,
        "suspicious_wrapper_count": suspicious_wrapper_count,
        "passed": passed,
    }


def _automatic_metrics(candidate: list[str], reference: list[str]) -> dict[str, float]:
    try:
        from sacrebleu.metrics import BLEU, CHRF
    except ImportError as exc:
        raise RuntimeError("SacreBLEU is required for translation evaluation") from exc
    char_scores = [
        _char_ngram_f1(left, right)
        for left, right in zip(candidate, reference, strict=True)
    ]
    return {
        "char_bigram_f1": round(sum(char_scores) / len(char_scores), 6) if char_scores else 0.0,
        "chrf": round(float(CHRF().corpus_score(candidate, [reference]).score), 6),
        "bleu": round(float(BLEU(effective_order=True).corpus_score(candidate, [reference]).score), 6),
    }


def evaluate_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest, base = _validate_manifest(manifest_path)
    samples: list[dict[str, Any]] = []
    for sample in manifest["samples"]:
        sample_id = str(sample.get("id") or "").strip()
        source_path = (base / sample["source_srt"]).resolve()
        source_sha = _sha256(source_path)
        frozen_sha = str(sample.get("source_srt_sha256") or "")
        if frozen_sha and source_sha != frozen_sha:
            raise ValueError(f"{sample_id} source SRT hash changed; benchmark is invalid")
        source = _parse_srt(source_path)
        reference = _parse_srt((base / sample["reference_srt"]).resolve())
        reference_structure = _structure(reference, source)
        if not reference_structure["passed"]:
            raise ValueError(f"{sample_id} reference structure does not match frozen source")
        expected_ids = [row["id"] for row in reference]
        reference_by_id = {row["id"]: row for row in reference}
        result = {
            "id": sample_id,
            "category": str(sample.get("category") or "uncategorized"),
            "source_srt_sha256": source_sha,
            "source_srt_hash_prefix": source_sha[:12],
            "cue_count": len(source),
        }
        for label in ("standard", "three_pass"):
            rows = _parse_srt((base / sample[f"{label}_srt"]).resolve())
            structure = _structure(rows, reference)
            by_id = {row["id"]: row for row in rows}
            candidates = [by_id[item_id]["text"] for item_id in expected_ids if item_id in by_id]
            references = [reference_by_id[item_id]["text"] for item_id in expected_ids if item_id in by_id]
            result[label] = {
                "structure": structure,
                "metrics": _automatic_metrics(candidates, references),
            }
        samples.append(result)
    automatic_gate = bool(samples) and all(
        row[label]["structure"]["passed"]
        for row in samples for label in ("standard", "three_pass")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_version": CAMPAIGN_VERSION,
        "sample_count": len(samples),
        "samples": samples,
        "summary": {
            "automatic_gate_passed": automatic_gate,
            "promotion_gate_passed": False,
            "promotion_note": (
                "Automatic metrics are supporting evidence only; promotion requires "
                "completed blinded human review and category no-regression."
            ),
        },
    }


def build_blind_review(
    manifest_path: Path,
    output_path: Path,
    key_path: Path | None = None,
) -> dict[str, Any]:
    manifest, base = _validate_manifest(manifest_path)
    key_path = key_path or output_path.with_suffix(".key.local.json")
    review_rows: list[dict[str, Any]] = []
    answer_key: dict[str, Any] = {}
    evaluated_sha = str(manifest.get("evaluated_sha") or "")
    for sample in manifest["samples"]:
        sample_id = str(sample["id"])
        standard = _parse_srt((base / sample["standard_srt"]).resolve())
        three_pass = _parse_srt((base / sample["three_pass_srt"]).resolve())
        if [row["id"] for row in standard] != [row["id"] for row in three_pass]:
            raise ValueError(f"{sample_id} candidate cue IDs differ")
        for left, right in zip(standard, three_pass, strict=True):
            review_id = f"{sample_id}-{left['id']:05d}"
            seed_material = f"{evaluated_sha}:{CAMPAIGN_VERSION}:{review_id}"
            swap = bool(random.Random(seed_material).getrandbits(1))
            option_a, option_b = ((right["text"], left["text"]) if swap else (left["text"], right["text"]))
            answer_key[review_id] = {"candidate": "A" if swap else "B"}
            review_rows.append({
                "review_id": review_id,
                "sample_id": sample_id,
                "cue_id": left["id"],
                "category": str(sample.get("category") or "uncategorized"),
                "option_a": option_a,
                "option_b": option_b,
                **{dimension: "" for dimension in REVIEW_DIMENSIONS},
                "severity": "",
                "error_category": "",
                "notes": "",
            })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "campaign_version": CAMPAIGN_VERSION,
        "blind": True,
        "dimensions": list(REVIEW_DIMENSIONS),
        "samples": review_rows,
        "instructions": "Choose A, B, or TIE independently for each dimension.",
    }
    key = {
        "schema_version": SCHEMA_VERSION,
        "candidate_label": "three_pass",
        "answers": answer_key,
    }
    write_json(output_path, payload)
    write_json(key_path, key)
    return payload


def score_blind_review(
    path: Path,
    key_path: Path,
    automatic_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = read_json(path)
    key = read_json(key_path).get("answers", {})
    wins = losses = ties = reviewed = 0
    categories: dict[str, dict[str, int]] = {}
    dimensions = {name: {"wins": 0, "losses": 0, "ties": 0} for name in REVIEW_DIMENSIONS}
    severe_fidelity_losses = 0
    for row in payload.get("samples", []):
        review_id = str(row.get("review_id"))
        candidate = key.get(review_id, {}).get("candidate")
        row_preferences = [
            str(row.get(dimension) or "").strip().upper()
            for dimension in REVIEW_DIMENSIONS
        ]
        valid = [value for value in row_preferences if value in {"A", "B", "TIE"}]
        if not valid:
            continue
        reviewed += 1
        category = str(row.get("category") or "uncategorized")
        category_row = categories.setdefault(category, {"wins": 0, "losses": 0, "ties": 0})
        overall = valid[0]
        if overall == "TIE":
            ties += 1
            category_row["ties"] += 1
        elif overall == candidate:
            wins += 1
            category_row["wins"] += 1
        else:
            losses += 1
            category_row["losses"] += 1
        for dimension, preference in zip(
            REVIEW_DIMENSIONS, row_preferences, strict=True
        ):
            if preference not in {"A", "B", "TIE"}:
                continue
            outcome = "ties" if preference == "TIE" else ("wins" if preference == candidate else "losses")
            dimensions[dimension][outcome] += 1
            if (
                dimension == "fidelity" and outcome == "losses"
                and str(row.get("severity") or "").casefold() == "major"
            ):
                severe_fidelity_losses += 1
    preference_rate = wins / (wins + losses) if wins + losses else 0.0
    no_category_regression = bool(categories) and all(
        values["wins"] >= values["losses"] for values in categories.values()
    )
    automatic_gate = bool(
        automatic_report
        and automatic_report.get("summary", {}).get("automatic_gate_passed")
    )
    promotion = (
        reviewed > 0 and preference_rate >= 0.60 and no_category_regression
        and severe_fidelity_losses == 0 and automatic_gate
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "reviewed": reviewed,
        "candidate_wins": wins,
        "candidate_losses": losses,
        "ties": ties,
        "candidate_preference_rate": round(preference_rate, 6),
        "categories": categories,
        "dimensions": dimensions,
        "severe_fidelity_loss_count": severe_fidelity_losses,
        "automatic_gate_passed": automatic_gate,
        "promotion_gate_passed": promotion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate authorized subtitle translation benchmarks.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("manifest", type=Path)
    evaluate.add_argument("--output", type=Path)
    blind = subparsers.add_parser("blind")
    blind.add_argument("manifest", type=Path)
    blind.add_argument("output", type=Path)
    blind.add_argument("--key", type=Path, required=True)
    score = subparsers.add_parser("score-blind")
    score.add_argument("review", type=Path)
    score.add_argument("--key", type=Path, required=True)
    score.add_argument("--automatic-report", type=Path, required=True)
    score.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "evaluate":
        result = evaluate_manifest(args.manifest)
    elif args.action == "blind":
        result = build_blind_review(args.manifest, args.output, args.key)
    else:
        result = score_blind_review(
            args.review, args.key, read_json(args.automatic_report)
        )
    output = getattr(args, "output", None)
    if output and args.action != "blind":
        write_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
