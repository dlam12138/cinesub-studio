from __future__ import annotations

import json

from translation_quality_benchmark import (
    build_blind_review,
    evaluate_manifest,
    score_blind_review,
)


def _srt(text: str) -> str:
    return f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n"


def test_translation_benchmark_evaluates_structure_and_blind_gate(tmp_path) -> None:
    (tmp_path / "reference.srt").write_text(_srt("你好。"), encoding="utf-8")
    (tmp_path / "standard.srt").write_text(_srt("您好。"), encoding="utf-8")
    (tmp_path / "three.srt").write_text(_srt("你好。"), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({
            "authorized": True,
            "samples": [{
                "id": "sample",
                "category": "en-zh",
                "reference_srt": "reference.srt",
                "standard_srt": "standard.srt",
                "three_pass_srt": "three.srt",
            }],
        }),
        encoding="utf-8",
    )
    report = evaluate_manifest(manifest)
    assert report["summary"]["automatic_gate_passed"] is True
    assert (
        report["summary"]["three_pass_char_bigram_f1"]
        > report["summary"]["standard_char_bigram_f1"]
    )

    review_path = tmp_path / "review.json"
    review = build_blind_review(manifest, review_path)
    correct = review["_answer_key"]["sample"]["three_pass"]
    review["samples"][0]["preference"] = correct
    review_path.write_text(json.dumps(review), encoding="utf-8")
    scored = score_blind_review(review_path)
    assert scored["promotion_gate_passed"] is True
