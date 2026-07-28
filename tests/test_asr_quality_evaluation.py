from __future__ import annotations

import json
from pathlib import Path

import pytest
from asr_quality_evaluation import (
    assert_public_safe,
    deterministic_review_indexes,
    evaluate_campaign,
    is_formal_score_reference,
    normalize_asr_text,
    validate_reference_manifest,
)
from real_media_acceptance import build_campaign_contract


def _manifest(
    reference_type: str = "gold_verbatim",
    *,
    campaign_scope: str = "",
) -> dict:
    return {
        "authorized": True,
        "campaign_scope": campaign_scope,
        "samples": [
            {
                "id": f"sample-{number:02d}",
                "source_group_id": f"film-{((number - 1) // 2) + 1:02d}",
                "authorized": True,
                "reference_type": reference_type,
                "reference_language": "fr",
                "reference_path": f"reference-{number:02d}.srt",
            }
            for number in range(1, 7)
        ],
    }


def test_normalization_v1_is_deterministic_and_symmetric() -> None:
    source = "  L’ÉTAT—c'est  42…  "
    assert normalize_asr_text(source) == "l'état c'est 42"
    assert normalize_asr_text(source) == normalize_asr_text(source)


def test_reference_manifest_requires_exact_authorized_samples_and_types() -> None:
    samples = validate_reference_manifest(_manifest())
    assert tuple(sorted(samples)) == tuple(f"sample-{number:02d}" for number in range(1, 7))
    invalid = _manifest("production_subtitle")
    invalid["samples"][0]["authorized"] = False
    with pytest.raises(ValueError, match="authorized=true"):
        validate_reference_manifest(invalid)
    invalid = _manifest("not-gold")
    with pytest.raises(ValueError, match="invalid reference_type"):
        validate_reference_manifest(invalid)
    assert is_formal_score_reference("gold_verbatim") is True
    assert is_formal_score_reference("production_subtitle") is False
    assert is_formal_score_reference("ocr_weak") is False
    stage3a = _manifest(campaign_scope="stage3a_french_film")
    assert len({
        sample["source_group_id"]
        for sample in validate_reference_manifest(stage3a).values()
    }) == 3
    stage3a["samples"][4]["source_group_id"] = "film-01"
    stage3a["samples"][5]["source_group_id"] = "film-01"
    with pytest.raises(ValueError, match="three distinct source groups"):
        validate_reference_manifest(stage3a)


def test_review_indexes_are_sha_campaign_deterministic() -> None:
    first = deterministic_review_indexes(
        evaluated_sha="abc",
        sample_id="sample-01",
        candidate_count=100,
        suspicious_indexes=[2, 8],
    )
    repeated = deterministic_review_indexes(
        evaluated_sha="abc",
        sample_id="sample-01",
        candidate_count=100,
        suspicious_indexes=[2, 8],
    )
    changed = deterministic_review_indexes(
        evaluated_sha="def",
        sample_id="sample-01",
        candidate_count=100,
        suspicious_indexes=[2, 8],
    )
    assert first == repeated
    assert first != changed
    assert {2, 8}.issubset(first)


def test_public_report_rejects_paths_text_and_secret_fields() -> None:
    safe = {"sample_id": "sample-01", "wer": 0.1, "hash_prefix": "abcdef123456"}
    assert_public_safe(safe)
    for payload in (
        {"source_text": "private words"},
        {"reference_path": "relative.srt"},
        {"value": r"C:\private\sample.srt"},
        {"api_key_masked": "***"},
    ):
        with pytest.raises(ValueError):
            assert_public_safe(payload)
    assert "private words" not in json.dumps(safe)


def _srt(path: Path, text: str) -> None:
    path.write_text(
        f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n",
        encoding="utf-8",
    )


def test_evaluator_scores_only_gold_and_emits_private_blind_artifacts(tmp_path) -> None:
    evaluated_sha = "a" * 40
    contract = build_campaign_contract(evaluated_sha)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    references = tmp_path / "references"
    references.mkdir()
    manifest_samples = []
    for number in range(1, 7):
        sample_id = f"sample-{number:02d}"
        reference = references / f"{sample_id}.srt"
        _srt(reference, "bonjour")
        manifest_samples.append({
            "id": sample_id,
            "authorized": True,
            "reference_type": "gold_verbatim" if number == 1 else "production_subtitle",
            "reference_language": "fr",
            "reference_path": reference.name,
        })
    manifest_path = references / "manifest.json"
    manifest_path.write_text(
        json.dumps({"authorized": True, "samples": manifest_samples}),
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    for planned in contract["runs"]:
        artifact_dir = tmp_path / "artifacts" / planned["run_id"]
        artifact_dir.mkdir(parents=True)
        srt = artifact_dir / "candidate.srt"
        review = artifact_dir / "candidate.asr_review.json"
        _srt(srt, "bonjour")
        review.write_text(
            json.dumps({"asr_retry_report": {
                "planned_window_count": 0,
                "executed_window_count": 0,
                "accepted_window_count": 0,
                "windows": [],
            }}),
            encoding="utf-8",
        )
        report = {
            "run_id": planned["run_id"],
            "sample_id": planned["sample_id"],
            "scenario_id": planned["scenario_id"],
            "profile": planned["profile"],
            "evaluated_sha": evaluated_sha,
            "end_to_end_seconds": 1.0,
            "asr_retry_report": {
                "planned_window_count": 0,
                "executed_window_count": 0,
                "accepted_window_count": 0,
                "windows": [],
            },
            "artifacts": {
                "output_srt": str(srt),
                "asr_review": str(review),
            },
        }
        if planned["run_id"] == "sample-01-primary-quality":
            review.unlink()
        (reports_dir / f"{planned['run_id']}.run.local.json").write_text(
            json.dumps(report),
            encoding="utf-8",
        )
    output_dir = tmp_path / "evaluation"
    summary = evaluate_campaign(
        contract_path, reports_dir, manifest_path, output_dir
    )
    assert summary["run_count"] == 28
    assert summary["gold_sample_count"] == 1
    error_rows = (
        output_dir / "asr-quality-error-alignment.local.csv"
    ).read_text(encoding="utf-8-sig").splitlines()
    assert len(error_rows) == 5
    review_text = (
        output_dir / "asr-quality-blind-review.local.tsv"
    ).read_text(encoding="utf-8-sig")
    assert "large-v3" not in review_text
    assert "balanced" not in review_text
    assert str(tmp_path) not in json.dumps(summary)
