from __future__ import annotations

import json
from pathlib import Path

from semantic_review_regression import build_report


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_build_report_keeps_structure_and_blinds_options(tmp_path: Path) -> None:
    sample_id = "sample"
    root = tmp_path / "regression"
    sample = root / sample_id
    source = "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n"
    _write(sample / "asr" / f"{sample_id}.large-v3.srt", source)
    translation = sample / "translation"
    _write(
        translation / f"{sample_id}.semantic-review.flash-pro.bilingual.zh-CN.srt",
        "1\n00:00:00,000 --> 00:00:02,000\nHello\n你好\n\n",
    )
    _write(
        translation / f"{sample_id}.standard.flash.bilingual.zh-CN.srt",
        "1\n00:00:00,000 --> 00:00:02,000\nHello\n您好\n\n",
    )
    _write(
        translation
        / f"{sample_id}.semantic-review.flash-pro.bilingual.zh-CN.semantic_review_report.json",
        json.dumps({
            "issue_counts": {"mistranslation": 1},
            "final_sources": {"1": "repair"},
            "budget_violation_ids": [],
            "consistency_issues": [],
            "unresolved_ids": [],
            "prompt_version": "test",
        }),
    )

    output = tmp_path / "report"
    report = build_report(root, [sample_id], output)

    assert report["totals"]["all_structurally_valid"] is True
    assert report["totals"]["repair_adopted_count"] == 1
    blind = (output / "blind-semantic-vs-standard.tsv").read_text(
        encoding="utf-8-sig"
    )
    assert "你好" in blind
    assert "您好" in blind
    key = json.loads(
        (output / "blind-semantic-vs-standard.key.json").read_text(encoding="utf-8")
    )
    assert key[0]["semantic_option"] in {"A", "B"}
