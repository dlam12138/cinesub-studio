from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

from encoding_utils import read_json, write_json


SCHEMA_VERSION = 1


def _parse_srt(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    rows: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            item_id = int(lines[0].strip())
        except ValueError:
            continue
        rows.append({
            "id": item_id,
            "time": lines[1].strip(),
            "text": "\n".join(lines[2:]).strip(),
        })
    return rows


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _char_ngram_f1(candidate: str, reference: str, n: int = 2) -> float:
    def counts(value: str) -> dict[str, int]:
        compact = _compact(value)
        result: dict[str, int] = {}
        for index in range(max(0, len(compact) - n + 1)):
            token = compact[index:index + n]
            result[token] = result.get(token, 0) + 1
        return result

    left, right = counts(candidate), counts(reference)
    overlap = sum(min(value, right.get(key, 0)) for key, value in left.items())
    left_total, right_total = sum(left.values()), sum(right.values())
    if not left_total and not right_total:
        return 1.0
    if not left_total or not right_total:
        return 0.0
    precision, recall = overlap / left_total, overlap / right_total
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def evaluate_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    if not manifest.get("authorized"):
        raise ValueError("benchmark manifest must set authorized=true")
    base = manifest_path.parent
    samples: list[dict[str, Any]] = []
    for sample in manifest.get("samples", []):
        sample_id = str(sample.get("id") or "").strip()
        reference = _parse_srt((base / sample["reference_srt"]).resolve())
        standard = _parse_srt((base / sample["standard_srt"]).resolve())
        three_pass = _parse_srt((base / sample["three_pass_srt"]).resolve())
        expected_ids = [row["id"] for row in reference]
        result = {"id": sample_id, "category": sample.get("category", "")}
        for label, rows in (("standard", standard), ("three_pass", three_pass)):
            ids = [row["id"] for row in rows]
            by_id = {row["id"]: row for row in rows}
            scores = [
                _char_ngram_f1(by_id[item["id"]]["text"], item["text"])
                for item in reference if item["id"] in by_id
            ]
            result[label] = {
                "ids_exact": ids == expected_ids,
                "empty_count": sum(not row["text"].strip() for row in rows),
                "time_mismatch_count": sum(
                    by_id[item["id"]]["time"] != item["time"]
                    for item in reference if item["id"] in by_id
                ),
                "char_bigram_f1": round(sum(scores) / len(scores), 6) if scores else 0.0,
            }
        samples.append(result)
    standard = [row["standard"]["char_bigram_f1"] for row in samples]
    three_pass = [row["three_pass"]["char_bigram_f1"] for row in samples]
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest": str(manifest_path.resolve()),
        "sample_count": len(samples),
        "samples": samples,
        "summary": {
            "standard_char_bigram_f1": round(sum(standard) / len(standard), 6) if standard else 0.0,
            "three_pass_char_bigram_f1": round(sum(three_pass) / len(three_pass), 6) if three_pass else 0.0,
            "automatic_gate_passed": bool(samples) and all(
                row["three_pass"]["ids_exact"]
                and not row["three_pass"]["empty_count"]
                and not row["three_pass"]["time_mismatch_count"]
                for row in samples
            ),
            "promotion_note": "Automatic scores are supporting evidence only; promotion also requires blinded human review.",
        },
    }


def build_blind_review(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    if not manifest.get("authorized"):
        raise ValueError("benchmark manifest must set authorized=true")
    base = manifest_path.parent
    seed = int(hashlib.sha256(str(manifest_path.resolve()).encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    review_rows = []
    answer_key = {}
    for sample in manifest.get("samples", []):
        sample_id = str(sample["id"])
        standard = (base / sample["standard_srt"]).read_text(encoding="utf-8-sig")
        three_pass = (base / sample["three_pass_srt"]).read_text(encoding="utf-8-sig")
        swap = bool(rng.getrandbits(1))
        option_a, option_b = ((three_pass, standard) if swap else (standard, three_pass))
        answer_key[sample_id] = {"three_pass": "A" if swap else "B"}
        review_rows.append({
            "id": sample_id,
            "category": sample.get("category", ""),
            "option_a_srt": option_a,
            "option_b_srt": option_b,
            "preference": "",
            "notes": "",
        })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "blind": True,
        "samples": review_rows,
        "_answer_key": answer_key,
        "instructions": "Reviewers choose A, B, or TIE without inspecting _answer_key. Remove _answer_key before sharing externally.",
    }
    write_json(output_path, payload)
    return payload


def score_blind_review(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    key = payload.get("_answer_key", {})
    wins = losses = ties = reviewed = 0
    categories: dict[str, dict[str, int]] = {}
    for row in payload.get("samples", []):
        preference = str(row.get("preference") or "").strip().upper()
        if preference not in {"A", "B", "TIE"}:
            continue
        reviewed += 1
        category = str(row.get("category") or "uncategorized")
        category_row = categories.setdefault(category, {"wins": 0, "losses": 0, "ties": 0})
        if preference == "TIE":
            ties += 1
            category_row["ties"] += 1
        elif preference == key.get(str(row.get("id")), {}).get("three_pass"):
            wins += 1
            category_row["wins"] += 1
        else:
            losses += 1
            category_row["losses"] += 1
    preference_rate = wins / (wins + losses) if wins + losses else 0.0
    no_category_regression = all(
        values["wins"] >= values["losses"] for values in categories.values()
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "reviewed": reviewed,
        "three_pass_wins": wins,
        "three_pass_losses": losses,
        "ties": ties,
        "three_pass_preference_rate": round(preference_rate, 6),
        "categories": categories,
        "promotion_gate_passed": reviewed > 0 and preference_rate >= 0.60 and no_category_regression,
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
    score = subparsers.add_parser("score-blind")
    score.add_argument("review", type=Path)
    score.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.action == "evaluate":
        result = evaluate_manifest(args.manifest)
    elif args.action == "blind":
        result = build_blind_review(args.manifest, args.output)
    else:
        result = score_blind_review(args.review)
    output = getattr(args, "output", None)
    if output and args.action != "blind":
        write_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
