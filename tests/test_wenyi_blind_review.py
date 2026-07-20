import csv
import json
from pathlib import Path

from wenyi_blind_review import build, score


def write_srt(path: Path, prefix: str, count: int = 160) -> None:
    blocks = []
    for item_id in range(1, count + 1):
        blocks.append(
            f"{item_id}\n00:00:00,000 --> 00:00:01,000\n{prefix}{item_id}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def test_builds_exactly_151_and_scores_two_dimensions(tmp_path):
    source = tmp_path / "source.srt"
    baseline = tmp_path / "baseline.srt"
    candidate = tmp_path / "candidate.srt"
    write_srt(source, "source")
    write_srt(baseline, "base")
    write_srt(candidate, "new")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "review_items": [
            {"category": "adopted_repair", "id": item_id}
            for item_id in range(1, 6)
        ]
    }), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "authorized": True,
        "samples": [{
            "id": "movie", "source_srt": "source.srt",
            "baseline_srt": "baseline.srt", "candidate_srt": "candidate.srt",
            "report": "report.json", "required_ids": [6],
        }],
    }), encoding="utf-8")
    review, key = tmp_path / "review.tsv", tmp_path / "key.json"
    result = build(manifest, review, key)
    assert result["count"] == 151
    assert result["risk_count"] == 6
    keys = {
        (row["sample_id"], str(row["cue_id"])): row["candidate_option"]
        for row in json.loads(key.read_text(encoding="utf-8"))["keys"]
    }
    with review.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fields = list(rows[0])
    for row in rows:
        expected = keys[(row["sample_id"], row["cue_id"])]
        row["fidelity_choice"] = expected
        row["naturalness_choice"] = "TIE"
        row["severe_error"] = "NONE"
    with review.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    scored = score(review, key)
    assert scored["reviewed"] == 151
    assert scored["candidate_fidelity_preference_rate"] == 1.0
    assert scored["promotion_gate_passed"] is True
    assert rows[0]["context_after"]
