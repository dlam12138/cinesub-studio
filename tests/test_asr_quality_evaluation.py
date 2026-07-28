from __future__ import annotations

import json
from pathlib import Path

import pytest
from asr_quality_evaluation import (
    _build_time_aligned_blind_entries,
    assert_public_safe,
    build_time_aligned_review_units,
    deterministic_review_indexes,
    evaluate_campaign,
    is_formal_score_reference,
    normalize_asr_text,
    parse_srt_interval,
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


def _row(start: str, end: str, text: str) -> dict:
    return {"time": f"{start} --> {end}", "text": text}


def test_time_alignment_compares_different_cue_counts_without_truncation() -> None:
    left = [
        _row("00:00:00,000", "00:00:10,000", "alpha"),
        _row("00:00:10,000", "00:00:20,000", "beta"),
    ]
    right = [
        _row("00:00:00,000", "00:00:05,000", "one"),
        _row("00:00:05,000", "00:00:10,000", "two"),
        _row("00:00:10,000", "00:00:15,000", "three"),
        _row("00:00:15,000", "00:00:20,000", "four"),
    ]

    units = build_time_aligned_review_units(left, right)

    assert [(unit["window_start_ms"], unit["window_end_ms"]) for unit in units] == [
        (0, 15_000),
        (15_000, 20_000),
    ]
    assert units[0]["left_text"] == "alpha"
    assert units[0]["right_text"] == "one two three"
    assert units[1]["left_text"] == "beta"
    assert units[1]["right_text"] == "four"


def test_time_alignment_uses_shared_windows_for_different_boundaries() -> None:
    left = [
        _row("00:00:00,000", "00:00:12,000", "left-a"),
        _row("00:00:12,000", "00:00:30,000", "left-b"),
    ]
    right = [
        _row("00:00:00,000", "00:00:08,000", "right-a"),
        _row("00:00:08,000", "00:00:18,000", "right-b"),
        _row("00:00:18,000", "00:00:30,000", "right-c"),
    ]

    units = build_time_aligned_review_units(left, right, window_ms=10_000)

    assert [(unit["window_start_ms"], unit["window_end_ms"]) for unit in units] == [
        (0, 10_000),
        (10_000, 20_000),
        (20_000, 30_000),
    ]
    assert units[0]["left_text"] == "left-a"
    assert units[0]["right_text"] == "right-a"
    assert units[1]["right_text"] == "right-b"
    assert units[2]["left_text"] == "left-b"
    assert units[2]["right_text"] == "right-c"


def test_time_alignment_assigns_each_cue_to_only_one_window() -> None:
    left = [
        _row("00:00:00,000", "00:00:16,000", "left-unique-a"),
        _row("00:00:16,000", "00:00:31,000", "left-unique-b"),
    ]
    right = [
        _row("00:00:02,000", "00:00:20,000", "right-unique-a"),
        _row("00:00:20,000", "00:00:31,000", "right-unique-b"),
    ]

    units = build_time_aligned_review_units(left, right, window_ms=10_000)
    serialized = " ".join(
        f"{unit['left_text']} {unit['right_text']}" for unit in units
    )

    for marker in (
        "left-unique-a",
        "left-unique-b",
        "right-unique-a",
        "right-unique-b",
    ):
        assert serialized.count(marker) == 1
    assert sorted(
        index for unit in units for index in unit["left_row_indexes"]
    ) == [1, 2]
    assert sorted(
        index for unit in units for index in unit["right_row_indexes"]
    ) == [1, 2]


def test_time_alignment_keeps_a_window_with_one_empty_side() -> None:
    left = [_row("00:00:00,000", "00:00:05,000", "left-only")]
    right = [_row("00:00:20,000", "00:00:25,000", "right-only")]

    units = build_time_aligned_review_units(left, right, window_ms=10_000)

    assert units == [
        {
            "window_index": 1,
            "window_start_ms": 0,
            "window_end_ms": 10_000,
            "left_text": "left-only",
            "right_text": "",
            "left_row_indexes": [1],
            "right_row_indexes": [],
        },
        {
            "window_index": 3,
            "window_start_ms": 20_000,
            "window_end_ms": 25_000,
            "left_text": "",
            "right_text": "right-only",
            "left_row_indexes": [],
            "right_row_indexes": [1],
        },
    ]


def test_time_aligned_blind_entries_are_sha_deterministic_and_private() -> None:
    left = [
        _row(
            f"00:00:{index:02d},000",
            f"00:00:{index:02d},900",
            f"left-{index:02d}",
        )
        for index in range(30)
    ]
    right = [
        _row(
            f"00:00:{index:02d},100",
            f"00:00:{index:02d},800",
            f"right-{index:02d}",
        )
        for index in range(30)
    ]
    kwargs = {
        "sample_id": "sample-01",
        "comparison": "model",
        "candidate_label": "private-model-name",
        "window_ms": 1000,
        "control_count": 20,
    }

    first_rows, first_key = _build_time_aligned_blind_entries(
        left, right, evaluated_sha="a" * 40, **kwargs
    )
    repeated_rows, repeated_key = _build_time_aligned_blind_entries(
        left, right, evaluated_sha="a" * 40, **kwargs
    )
    changed_rows, changed_key = _build_time_aligned_blind_entries(
        left, right, evaluated_sha="b" * 40, **kwargs
    )

    assert (first_rows, first_key) == (repeated_rows, repeated_key)
    assert (first_rows, first_key) != (changed_rows, changed_key)
    assert all("-->" in row["time"] for row in first_rows)
    assert all(
        f"{row['window_start_ms']:010d}-{row['window_end_ms']:010d}"
        in row["review_id"]
        for row in first_rows
    )
    serialized = json.dumps(first_rows)
    for forbidden in (
        "private-model-name",
        "large-v3",
        "balanced",
        r"C:\\private",
        "prompt",
        "api_key",
    ):
        assert forbidden not in serialized
    assert {item["candidate_label"] for item in first_key.values()} == {
        "private-model-name"
    }


def test_parse_srt_interval_rejects_invalid_or_reversed_timestamps() -> None:
    assert parse_srt_interval(
        _row("01:02:03,004", "01:02:04,005", "text")
    ) == (3_723_004, 3_724_005)
    with pytest.raises(ValueError, match="timestamp"):
        parse_srt_interval({"time": "not-a-time"})
    with pytest.raises(ValueError, match="interval"):
        parse_srt_interval(_row("00:00:02,000", "00:00:01,000", "text"))


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
    assert "cue_index" not in review_text
    assert "window_start_ms" in review_text
    assert "window_end_ms" in review_text
    assert str(tmp_path) not in json.dumps(summary)
