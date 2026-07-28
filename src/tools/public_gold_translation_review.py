from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
from pathlib import Path
from typing import Any

REVIEW_VERSION = "public-gold-translation-review-v1"
EXPECTED_SAMPLE_IDS = tuple(f"sample-{number:02d}" for number in range(1, 7))
FLUENCY_FIELDS = (
    "review_id", "sample_id", "context_before_zh", "option_a_zh", "option_b_zh",
    "context_after_zh", "preference", "fluency_error", "readability_error",
    "continuity_error", "notes",
)
FIDELITY_FIELDS = (
    "review_id", "sample_id", "context_before_fr", "source_fr", "context_after_fr",
    "option_a_zh", "option_b_zh", "preference", "omission", "addition",
    "mistranslation", "terminology", "pronoun", "continuity", "severity", "notes",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_srt(path: Path) -> list[dict[str, Any]]:
    rows = []
    text = path.read_text(encoding="utf-8-sig").strip()
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            raise ValueError(f"Invalid translation review SRT: {path.name}")
        rows.append({
            "id": int(lines[0]),
            "time": lines[1],
            "text": "\n".join(lines[2:]).strip(),
        })
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _resolve(base: Path, value: object) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        raise FileNotFoundError("Translation review input artifact is unavailable")
    return path.resolve()


def build_review_handoff(
    manifest_path: Path,
    output_dir: Path,
    *,
    max_units: int = 120,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evaluated_sha = str(manifest.get("evaluated_sha") or "").strip()
    if not evaluated_sha:
        raise ValueError("Translation review manifest requires evaluated_sha")
    samples = {
        str(row.get("id") or row.get("sample_id")): row
        for row in manifest.get("samples", [])
    }
    if tuple(sorted(samples)) != EXPECTED_SAMPLE_IDS:
        raise ValueError("Translation review requires exactly sample-01 through sample-06")
    base = manifest_path.resolve().parent
    units = []
    source_hashes = {}
    for sample_id in EXPECTED_SAMPLE_IDS:
        sample = samples[sample_id]
        source_path = _resolve(base, sample.get("source_srt"))
        standard_path = _resolve(base, sample.get("standard_srt"))
        three_pass_path = _resolve(base, sample.get("three_pass_srt"))
        source = _parse_srt(source_path)
        standard = _parse_srt(standard_path)
        three_pass = _parse_srt(three_pass_path)
        ids = [row["id"] for row in source]
        if (
            ids != [row["id"] for row in standard]
            or ids != [row["id"] for row in three_pass]
        ):
            raise ValueError(f"{sample_id} strategies do not preserve source cue IDs")
        source_hashes[sample_id] = _sha256_file(source_path)
        for index, (source_row, standard_row, three_row) in enumerate(
            zip(source, standard, three_pass, strict=True)
        ):
            units.append({
                "sample_id": sample_id,
                "cue_id": source_row["id"],
                "source": source_row["text"],
                "standard": standard_row["text"],
                "three_pass": three_row["text"],
                "before_fr": source[index - 1]["text"] if index else "",
                "after_fr": source[index + 1]["text"] if index + 1 < len(source) else "",
                "before_standard": standard[index - 1]["text"] if index else "",
                "after_standard": standard[index + 1]["text"] if index + 1 < len(standard) else "",
                "before_three": three_pass[index - 1]["text"] if index else "",
                "after_three": three_pass[index + 1]["text"] if index + 1 < len(three_pass) else "",
            })
    if not units:
        raise ValueError("Translation review contains no cues")
    seed_material = (
        f"{evaluated_sha}:{REVIEW_VERSION}:"
        + ":".join(source_hashes[sample_id] for sample_id in EXPECTED_SAMPLE_IDS)
    )
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    selected = list(range(len(units)))
    if len(selected) > max_units:
        selected = sorted(random.Random(seed).sample(selected, max_units))
    fluency_rows = []
    fidelity_rows = []
    answer_key = {}
    for index in selected:
        unit = units[index]
        review_id = f"{unit['sample_id']}-cue-{unit['cue_id']:05d}"
        swap = bool(hashlib.sha256(f"{seed}:{review_id}".encode()).digest()[0] & 1)
        option_a = unit["three_pass"] if swap else unit["standard"]
        option_b = unit["standard"] if swap else unit["three_pass"]
        before_a = unit["before_three"] if swap else unit["before_standard"]
        before_b = unit["before_standard"] if swap else unit["before_three"]
        after_a = unit["after_three"] if swap else unit["after_standard"]
        after_b = unit["after_standard"] if swap else unit["after_three"]
        fluency_rows.append({
            "review_id": review_id,
            "sample_id": unit["sample_id"],
            "context_before_zh": f"A: {before_a}\nB: {before_b}",
            "option_a_zh": option_a,
            "option_b_zh": option_b,
            "context_after_zh": f"A: {after_a}\nB: {after_b}",
            "preference": "",
            "fluency_error": "",
            "readability_error": "",
            "continuity_error": "",
            "notes": "",
        })
        fidelity_rows.append({
            "review_id": review_id,
            "sample_id": unit["sample_id"],
            "context_before_fr": unit["before_fr"],
            "source_fr": unit["source"],
            "context_after_fr": unit["after_fr"],
            "option_a_zh": option_a,
            "option_b_zh": option_b,
            "preference": "",
            "omission": "",
            "addition": "",
            "mistranslation": "",
            "terminology": "",
            "pronoun": "",
            "continuity": "",
            "severity": "",
            "notes": "",
        })
        answer_key[review_id] = {
            "A": "three_pass" if swap else "standard",
            "B": "standard" if swap else "three_pass",
        }
    output_dir = output_dir.resolve()
    handoff_dir = output_dir / "review_handoff"
    _write_tsv(
        handoff_dir / "translation-fluency-blind.local.tsv",
        FLUENCY_FIELDS,
        fluency_rows,
    )
    _write_tsv(
        handoff_dir / "translation-fidelity-assisted.local.tsv",
        FIDELITY_FIELDS,
        fidelity_rows,
    )
    instructions = (
        "Stage 3A Public Gold translation review\n\n"
        "Review option A and B without attempting to infer their strategy identity.\n"
        "Use A, B, or TIE in preference. Mark applicable error columns with 1.\n"
        "For fidelity, use severity none/minor/major/severe. Preserve review_id and sample_id.\n"
        "Reviewer type: llm_assisted_bilingual_review.\n"
        "This package does not establish professional human validation and cannot unlock Release Prep.\n"
    )
    (handoff_dir / "REVIEW_INSTRUCTIONS.txt").write_text(instructions, encoding="utf-8")
    key_payload = {
        "schema_version": 1,
        "review_version": REVIEW_VERSION,
        "evaluated_sha": evaluated_sha,
        "seed_hex": f"{seed:016x}",
        "source_srt_sha256": source_hashes,
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "temperature": manifest.get("temperature"),
        "language_profile": manifest.get("language_profile"),
        "glossary_sha256": manifest.get("glossary_sha256"),
        "context_window": manifest.get("context_window"),
        "answer_key": answer_key,
    }
    _write_json(output_dir / "translation-review-answer-key.local.json", key_payload)
    summary = {
        "schema_version": 1,
        "review_version": REVIEW_VERSION,
        "reviewer_type": "llm_assisted_bilingual_review",
        "evaluated_sha": evaluated_sha,
        "review_unit_count": len(selected),
        "sample_count": 6,
        "handoff_files": [
            "translation-fluency-blind.local.tsv",
            "translation-fidelity-assisted.local.tsv",
            "REVIEW_INSTRUCTIONS.txt",
        ],
        "answer_key_excluded_from_handoff": True,
        "human_fidelity_gate": "not_completed",
        "allows_release_prep": False,
    }
    _write_json(output_dir / "translation-review-plan.local.json", summary)
    return summary


def score_assisted_review(
    completed_tsv: Path,
    answer_key_path: Path,
    output: Path,
) -> dict[str, Any]:
    key = json.loads(answer_key_path.read_text(encoding="utf-8"))
    answer_key = key.get("answer_key", {})
    with completed_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    wins = losses = ties = invalid = 0
    error_counts = {
        name: {"standard": 0, "three_pass": 0}
        for name in (
            "omission", "addition", "mistranslation", "terminology",
            "pronoun", "continuity",
        )
    }
    severe = {"standard": 0, "three_pass": 0}
    for row in rows:
        review_id = str(row.get("review_id") or "")
        mapping = answer_key.get(review_id)
        preference = str(row.get("preference") or "").strip().upper()
        if not mapping or preference not in {"A", "B", "TIE"}:
            invalid += 1
            continue
        if preference == "TIE":
            ties += 1
        elif mapping[preference] == "three_pass":
            wins += 1
        else:
            losses += 1
        if preference in {"A", "B"}:
            strategy = mapping[preference]
            for name in error_counts:
                error_counts[name][strategy] += str(row.get(name) or "").strip() in {
                    "1", "true", "yes", "x"
                }
            severe[strategy] += str(row.get("severity") or "").strip().casefold() == "severe"
    non_ties = wins + losses
    preference_rate = wins / non_ties if non_ties else 0.0
    decision = "inconclusive"
    if invalid == 0 and non_ties:
        if preference_rate >= 0.60:
            decision = "promote_for_human_confirmation"
        elif losses > wins:
            decision = "keep_standard"
    payload = {
        "schema_version": 1,
        "reviewer_type": "llm_assisted_bilingual_review",
        "review_unit_count": len(rows),
        "three_pass_wins": wins,
        "three_pass_losses": losses,
        "ties": ties,
        "invalid_or_incomplete": invalid,
        "three_pass_non_tie_preference_rate": round(preference_rate, 8),
        "error_counts": error_counts,
        "severe_counts": severe,
        "assisted_review_decision": decision,
        "human_fidelity_gate": "not_completed",
        "allows_production_default_translation_change": False,
        "allows_release_prep": False,
    }
    _write_json(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or score public-gold translation review.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--max-units", type=int, default=120)
    score = subparsers.add_parser("score")
    score.add_argument("--completed-tsv", type=Path, required=True)
    score.add_argument("--answer-key", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "build":
        payload = build_review_handoff(
            args.manifest,
            args.output_dir,
            max_units=args.max_units,
        )
    else:
        payload = score_assisted_review(
            args.completed_tsv,
            args.answer_key,
            args.output,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
