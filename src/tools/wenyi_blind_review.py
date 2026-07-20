"""Create and score the fixed 151-cue WenYi promotion blind review.

This tool is offline. It never invokes an LLM and requires an explicit
authorized=true manifest so paid regression runs remain a separate decision.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


TARGET_COUNT = 151
SEED = "wenyi-review-v032-promotion-151"
RISK_CATEGORIES = {
    "adopted_repair", "adopted_shortening", "unresolved_budget",
    "consistency_definite", "consistency_variant", "cross_line_issue",
    "asr_warning",
}


def _srt(path: Path) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        item_id = int(lines[0])
        result[item_id] = {"time": lines[1], "text": "\n".join(lines[2:]).strip()}
    return result


def _target_text(source_text: str, rendered_text: str) -> str:
    normalized_source = source_text.strip()
    normalized_rendered = rendered_text.strip()
    if normalized_rendered == normalized_source:
        return ""
    prefix = f"{normalized_source}\n"
    if normalized_rendered.startswith(prefix):
        return normalized_rendered[len(prefix):].strip()
    return normalized_rendered


def _atomic_tsv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, payload: Any) -> None:
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


def build(manifest_path: Path, output_path: Path, key_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("authorized") is not True:
        raise ValueError("manifest must explicitly set authorized=true")
    base = manifest_path.parent
    pool: dict[tuple[str, int], dict] = {}
    risk_keys: set[tuple[str, int]] = set()
    for sample in manifest.get("samples", []):
        sample_id = str(sample["id"])
        source = _srt((base / sample["source_srt"]).resolve())
        baseline = _srt((base / sample["baseline_srt"]).resolve())
        candidate = _srt((base / sample["candidate_srt"]).resolve())
        if list(source) != list(baseline) or list(source) != list(candidate):
            raise ValueError(f"{sample_id}: ids differ between source/baseline/candidate")
        if any(
            source[item_id]["time"] != candidate[item_id]["time"]
            or source[item_id]["time"] != baseline[item_id]["time"]
            for item_id in source
        ):
            raise ValueError(f"{sample_id}: baseline or candidate time line changed")
        ordered_ids = list(source)
        source_position = {
            item_id: position for position, item_id in enumerate(ordered_ids)
        }
        report = json.loads((base / sample["report"]).read_text(encoding="utf-8"))
        category_by_id: dict[int, set[str]] = {}
        for item_id in sample.get("required_ids", []):
            normalized_id = int(item_id)
            if normalized_id not in source:
                raise ValueError(
                    f"{sample_id}: required id {normalized_id} is unavailable"
                )
            category_by_id.setdefault(normalized_id, set()).add(
                "known_regression"
            )
            risk_keys.add((sample_id, normalized_id))
        for row in report.get("review_items", []):
            category = str(row.get("category") or "")
            item_id = int(row.get("id"))
            category_by_id.setdefault(item_id, set()).add(category)
            if category in RISK_CATEGORIES:
                risk_keys.add((sample_id, item_id))
        for item_id in source:
            position = source_position[item_id]
            categories = sorted(category_by_id.get(item_id, set()))
            pool[(sample_id, item_id)] = {
                "sample_id": sample_id,
                "cue_id": item_id,
                "category": ",".join(categories) or "unchanged",
                "time": source[item_id]["time"],
                "source": source[item_id]["text"],
                "context_before": "\n".join(
                    source[context_id]["text"]
                    for context_id in ordered_ids[max(0, position - 2):position]
                ),
                "context_after": "\n".join(
                    source[context_id]["text"]
                    for context_id in ordered_ids[position + 1:position + 3]
                ),
                "baseline": _target_text(
                    source[item_id]["text"], baseline[item_id]["text"]
                ),
                "candidate": _target_text(
                    source[item_id]["text"], candidate[item_id]["text"]
                ),
            }
            if not pool[(sample_id, item_id)]["baseline"]:
                raise ValueError(f"{sample_id}:{item_id}: baseline translation is empty")
            if not pool[(sample_id, item_id)]["candidate"]:
                raise ValueError(f"{sample_id}:{item_id}: candidate translation is empty")
    if len(risk_keys) > TARGET_COUNT:
        raise ValueError(
            f"risk item count {len(risk_keys)} exceeds the fixed {TARGET_COUNT}-cue review"
        )
    remaining = [key for key in pool if key not in risk_keys]
    remaining.sort(key=lambda key: hashlib.sha256(
        f"{SEED}:{key[0]}:{key[1]}".encode("utf-8")
    ).hexdigest())
    selected = sorted(risk_keys) + remaining[:TARGET_COUNT - len(risk_keys)]
    if len(selected) != TARGET_COUNT:
        raise ValueError(f"only {len(selected)} eligible cues; {TARGET_COUNT} required")
    rows: list[dict] = []
    keys: list[dict] = []
    for sample_id, item_id in selected:
        row = pool[(sample_id, item_id)]
        candidate_is_a = bool(hashlib.sha256(
            f"{SEED}:ab:{sample_id}:{item_id}".encode("utf-8")
        ).digest()[0] & 1)
        rows.append({
            "sample_id": sample_id,
            "cue_id": item_id,
            "category": row["category"],
            "time": row["time"],
            "source": row["source"],
            "context_before": row["context_before"],
            "context_after": row["context_after"],
            "option_a": row["candidate"] if candidate_is_a else row["baseline"],
            "option_b": row["baseline"] if candidate_is_a else row["candidate"],
            "fidelity_choice": "",
            "naturalness_choice": "",
            "severe_error": "",
            "notes": "",
        })
        keys.append({
            "sample_id": sample_id,
            "cue_id": item_id,
            "candidate_option": "A" if candidate_is_a else "B",
        })
    fields = list(rows[0])
    _atomic_tsv(output_path, rows, fields)
    _atomic_json(key_path, {
        "schema_version": 1, "seed": SEED, "count": TARGET_COUNT, "keys": keys
    })
    return {
        "count": len(rows),
        "risk_count": len(risk_keys),
        "random_fill_count": len(rows) - len(risk_keys),
        "review": str(output_path),
        "answer_key": str(key_path),
    }


def score(review_path: Path, key_path: Path) -> dict:
    key = json.loads(key_path.read_text(encoding="utf-8"))
    answers = {
        (str(row["sample_id"]), int(row["cue_id"])): row["candidate_option"]
        for row in key.get("keys", [])
    }
    wins = losses = ties = natural_wins = natural_losses = natural_ties = 0
    severe_errors = 0
    severe_items: list[dict[str, Any]] = []
    categories: dict[str, dict[str, int]] = {}
    per_sample: dict[str, dict[str, int]] = {}
    strictly_blind = {"wins": 0, "losses": 0, "ties": 0}
    reviewed = 0
    with review_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            choice = str(row.get("fidelity_choice") or "").strip().upper()
            if choice not in {"A", "B", "TIE"}:
                continue
            reviewed += 1
            expected = answers[(str(row["sample_id"]), int(row["cue_id"]))]
            category_names = str(row.get("category") or "unchanged").split(",")
            outcome = "ties" if choice == "TIE" else (
                "wins" if choice == expected else "losses"
            )
            if outcome == "wins":
                wins += 1
            elif outcome == "losses":
                losses += 1
            else:
                ties += 1
            for category in category_names:
                values = categories.setdefault(
                    category, {"wins": 0, "losses": 0, "ties": 0}
                )
                values[outcome] += 1
            sample_values = per_sample.setdefault(
                str(row["sample_id"]), {"wins": 0, "losses": 0, "ties": 0}
            )
            sample_values[outcome] += 1
            if "known_regression" not in category_names:
                strictly_blind[outcome] += 1
            natural = str(row.get("naturalness_choice") or "").strip().upper()
            if natural == "TIE":
                natural_ties += 1
            elif natural in {"A", "B"}:
                if natural == expected:
                    natural_wins += 1
                else:
                    natural_losses += 1
            severe = str(row.get("severe_error") or "").strip().upper()
            if severe in {expected, "BOTH"}:
                severe_errors += 1
                severe_items.append({
                    "sample_id": str(row["sample_id"]),
                    "cue_id": int(row["cue_id"]),
                    "category": str(row.get("category") or "unchanged"),
                    "candidate_option": expected,
                    "notes": str(row.get("notes") or ""),
                })
    non_ties = wins + losses
    fidelity_rate = wins / non_ties if non_ties else 0.0
    natural_non_ties = natural_wins + natural_losses
    naturalness_rate = (
        natural_wins / natural_non_ties if natural_non_ties else 0.0
    )
    strict_non_ties = strictly_blind["wins"] + strictly_blind["losses"]
    strict_rate = (
        strictly_blind["wins"] / strict_non_ties if strict_non_ties else 0.0
    )
    no_category_regression = all(
        values["wins"] >= values["losses"] for values in categories.values()
    )
    return {
        "reviewed": reviewed,
        "candidate_fidelity_wins": wins,
        "candidate_fidelity_losses": losses,
        "fidelity_ties": ties,
        "candidate_fidelity_preference_rate": round(fidelity_rate, 6),
        "candidate_naturalness_wins": natural_wins,
        "candidate_naturalness_losses": natural_losses,
        "naturalness_ties": natural_ties,
        "candidate_naturalness_preference_rate": round(naturalness_rate, 6),
        "severe_error_count": severe_errors,
        "severe_error_items": severe_items,
        "categories": categories,
        "per_sample": per_sample,
        "known_regressions_excluded": {
            **strictly_blind,
            "candidate_fidelity_preference_rate": round(strict_rate, 6),
        },
        "no_category_fidelity_regression": no_category_regression,
        "promotion_gate_passed": (
            reviewed == TARGET_COUNT
            and fidelity_rate >= 0.60
            and severe_errors == 0
            and no_category_regression
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("manifest", type=Path)
    build_parser.add_argument("review", type=Path)
    build_parser.add_argument("key", type=Path)
    score_parser = commands.add_parser("score")
    score_parser.add_argument("review", type=Path)
    score_parser.add_argument("key", type=Path)
    score_parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.manifest, args.review, args.key)
    else:
        result = score(args.review, args.key)
        if args.output:
            _atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
