from __future__ import annotations

import hashlib
import json

import pytest
from translation_quality_benchmark import (
    REVIEW_DIMENSIONS,
    build_blind_review,
    evaluate_manifest,
    score_blind_review,
)


def _srt(text: str, *, time: str = "00:00:01,000 --> 00:00:02,000") -> str:
    return f"1\n{time}\n{text}\n"


def _manifest(tmp_path, *, authorized: bool = True):
    source = tmp_path / "source.srt"
    source.write_text(_srt("Bonjour."), encoding="utf-8")
    (tmp_path / "reference.srt").write_text(_srt("你好。"), encoding="utf-8")
    (tmp_path / "standard.srt").write_text(_srt("您好。"), encoding="utf-8")
    (tmp_path / "three.srt").write_text(_srt("你好。"), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "authorized": authorized,
        "evaluated_sha": "abc123",
        "samples": [{
            "id": "sample-01",
            "category": "dialogue_continuity",
            "source_srt": "source.srt",
            "source_srt_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "reference_srt": "reference.srt",
            "standard_srt": "standard.srt",
            "three_pass_srt": "three.srt",
        }],
    }), encoding="utf-8")
    return manifest


def test_translation_benchmark_requires_authorization_and_structure(tmp_path) -> None:
    with pytest.raises(ValueError, match="authorized=true"):
        evaluate_manifest(_manifest(tmp_path, authorized=False))

    report = evaluate_manifest(_manifest(tmp_path))
    assert report["summary"]["automatic_gate_passed"] is True
    assert report["summary"]["promotion_gate_passed"] is False
    assert (
        report["samples"][0]["three_pass"]["metrics"]["char_bigram_f1"]
        > report["samples"][0]["standard"]["metrics"]["char_bigram_f1"]
    )


def test_source_hash_and_timeline_drift_invalidate_campaign(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    (tmp_path / "source.srt").write_text(_srt("Changed"), encoding="utf-8")
    with pytest.raises(ValueError, match="source SRT hash changed"):
        evaluate_manifest(manifest)

    manifest = _manifest(tmp_path)
    (tmp_path / "three.srt").write_text(
        _srt("你好。", time="00:00:02,000 --> 00:00:03,000"),
        encoding="utf-8",
    )
    report = evaluate_manifest(manifest)
    assert report["summary"]["automatic_gate_passed"] is False


def test_blind_review_is_deterministic_and_hides_strategy_names(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    first_path, first_key = tmp_path / "review-1.json", tmp_path / "key-1.json"
    second_path, second_key = tmp_path / "review-2.json", tmp_path / "key-2.json"
    first = build_blind_review(manifest, first_path, first_key)
    second = build_blind_review(manifest, second_path, second_key)

    assert first == second
    serialized = json.dumps(first, ensure_ascii=False)
    assert "three_pass" not in serialized
    assert "standard_srt" not in serialized
    assert "_answer_key" not in first
    assert read_json(first_key) == read_json(second_key)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_human_review_and_category_gate_are_required(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    automatic = evaluate_manifest(manifest)
    review_path, key_path = tmp_path / "review.json", tmp_path / "key.json"
    review = build_blind_review(manifest, review_path, key_path)
    candidate = read_json(key_path)["answers"]["sample-01-00001"]["candidate"]
    for dimension in REVIEW_DIMENSIONS:
        review["samples"][0][dimension] = candidate
    review_path.write_text(json.dumps(review), encoding="utf-8")

    without_automatic = score_blind_review(review_path, key_path)
    assert without_automatic["promotion_gate_passed"] is False
    scored = score_blind_review(review_path, key_path, automatic)
    assert scored["candidate_preference_rate"] == 1.0
    assert scored["promotion_gate_passed"] is True

    opposite = "B" if candidate == "A" else "A"
    review["samples"][0]["fidelity"] = opposite
    review["samples"][0]["severity"] = "major"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    regressed = score_blind_review(review_path, key_path, automatic)
    assert regressed["promotion_gate_passed"] is False
