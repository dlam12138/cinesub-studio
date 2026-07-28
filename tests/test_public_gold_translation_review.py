from __future__ import annotations

import csv
import json
from pathlib import Path

from public_gold_translation_review import (
    build_review_handoff,
    score_assisted_review,
)


def _srt(path: Path, prefix: str) -> None:
    blocks = []
    for index in range(1, 4):
        blocks.append(
            f"{index}\n00:00:0{index - 1},000 --> 00:00:0{index},000\n"
            f"{prefix} {index}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _manifest(tmp_path: Path) -> Path:
    samples = []
    for number in range(1, 7):
        sample_id = f"sample-{number:02d}"
        source = tmp_path / f"{sample_id}.fr.srt"
        standard = tmp_path / f"{sample_id}.standard.srt"
        three = tmp_path / f"{sample_id}.three.srt"
        _srt(source, "source français")
        _srt(standard, "标准译文")
        _srt(three, "三步译文")
        samples.append({
            "id": sample_id,
            "source_srt": source.name,
            "standard_srt": standard.name,
            "three_pass_srt": three.name,
        })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "evaluated_sha": "a" * 40,
        "provider": "private-provider",
        "model": "private-model",
        "temperature": 0,
        "language_profile": "fr-zh",
        "glossary_sha256": "b" * 64,
        "context_window": 3,
        "samples": samples,
    }), encoding="utf-8")
    return manifest


def test_translation_handoff_is_deterministic_blind_and_separates_key(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = build_review_handoff(manifest, first_dir, max_units=12)
    repeated = build_review_handoff(manifest, second_dir, max_units=12)

    assert first == repeated
    assert first["review_unit_count"] == 12
    assert first["reviewer_type"] == "llm_assisted_bilingual_review"
    assert first["answer_key_excluded_from_handoff"] is True
    assert first["allows_release_prep"] is False
    handoff = first_dir / "review_handoff"
    assert sorted(path.name for path in handoff.iterdir()) == [
        "REVIEW_INSTRUCTIONS.txt",
        "translation-fidelity-assisted.local.tsv",
        "translation-fluency-blind.local.tsv",
    ]
    serialized = "\n".join(
        path.read_text(encoding="utf-8-sig") for path in handoff.iterdir()
    )
    for forbidden in (
        "three_pass", "standard.srt", "private-provider", "private-model",
        str(tmp_path), "prompt",
    ):
        assert forbidden not in serialized
    first_fidelity = (
        handoff / "translation-fidelity-assisted.local.tsv"
    ).read_text(encoding="utf-8-sig")
    second_fidelity = (
        second_dir / "review_handoff" / "translation-fidelity-assisted.local.tsv"
    ).read_text(encoding="utf-8-sig")
    assert first_fidelity == second_fidelity
    with (
        handoff / "translation-fidelity-assisted.local.tsv"
    ).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len({row["review_id"] for row in rows}) == len(rows)
    assert all(row["context_before_fr"] is not None for row in rows)


def test_assisted_score_cannot_unlock_human_or_release_gate(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    output_dir = tmp_path / "review"
    build_review_handoff(manifest, output_dir, max_units=12)
    completed = output_dir / "review_handoff" / "translation-fidelity-assisted.local.tsv"
    with completed.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    key = json.loads(
        (output_dir / "translation-review-answer-key.local.json").read_text(
            encoding="utf-8"
        )
    )["answer_key"]
    for row in rows:
        row["preference"] = next(
            option for option, strategy in key[row["review_id"]].items()
            if strategy == "three_pass"
        )
        row["severity"] = "none"
    with completed.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    score = score_assisted_review(
        completed,
        output_dir / "translation-review-answer-key.local.json",
        output_dir / "score.json",
    )

    assert score["assisted_review_decision"] == "promote_for_human_confirmation"
    assert score["human_fidelity_gate"] == "not_completed"
    assert score["allows_production_default_translation_change"] is False
    assert score["allows_release_prep"] is False
