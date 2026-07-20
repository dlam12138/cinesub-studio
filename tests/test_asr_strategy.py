from __future__ import annotations

import json
from pathlib import Path

import pytest

from asr_strategy import (
    AsrCandidateReport,
    TranscriptionArtifact,
    TranscriptionCue,
    adjacent_duplicate_candidate,
    get_candidate,
    merge_retry_artifact,
    retry_windows,
    select_retry_window,
    selective_merge_retry_artifact,
    validate_artifact,
    validate_strategy_config,
    write_candidate_report,
)


def test_candidate_registry_uses_fixed_parameters_and_rejects_apply_until_promoted() -> None:
    candidate = get_candidate("vad-sensitive-v1", "dry_run", "large-v3")
    assert candidate.decode_options.vad_threshold == 0.4
    assert candidate.decode_options.vad_min_silence_duration_ms == 500
    with pytest.raises(ValueError, match="does not allow"):
        get_candidate("vad-sensitive-v1", "apply", "large-v3")
    with pytest.raises(ValueError, match="Unknown"):
        get_candidate("custom-kwargs", "dry_run", "large-v3")


def test_strategy_config_rejects_unknown_fields_and_unknown_candidate() -> None:
    assert validate_strategy_config(None) == {"mode": "off", "candidate_id": ""}
    with pytest.raises(ValueError, match="Unknown asr_strategy fields"):
        validate_strategy_config({"mode": "off", "temperature": 1})
    with pytest.raises(ValueError, match="Unknown ASR candidate"):
        validate_strategy_config({"mode": "dry_run", "candidate_id": "missing"})


def test_artifact_validation_retry_windows_and_merge() -> None:
    baseline = TranscriptionArtifact(
        cues=(
            TranscriptionCue(0, 2, "hello"),
            TranscriptionCue(2, 4, "hello", avg_logprob=-1.5),
            TranscriptionCue(4, 6, "world"),
        ),
        duration_seconds=6,
    )
    windows = retry_windows(baseline)
    assert windows == [(1.5, 4.5)]
    retry = TranscriptionArtifact(cues=(TranscriptionCue(1.5, 4.5, "replacement"),), duration_seconds=6)
    merged = merge_retry_artifact(baseline, retry, windows)
    assert [cue.text for cue in merged.cues] == ["hello", "replacement", "world"]
    assert validate_artifact(merged, 6) == []


def test_artifact_rejects_empty_non_monotonic_and_out_of_bounds() -> None:
    artifact = TranscriptionArtifact(
        cues=(TranscriptionCue(2, 3, "ok"), TranscriptionCue(1, 8, "")),
        duration_seconds=5,
    )
    errors = validate_artifact(artifact, 5)
    assert any("empty" in error for error in errors)
    assert any("monotonic" in error for error in errors)
    assert any("exceeds" in error for error in errors)


def test_candidate_report_contains_hashes_and_metrics_but_no_transcript(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    report = AsrCandidateReport(
        1, "vad-sensitive-v1", 1, "dry_run", "evaluated", "baseline", False,
        "baseline-hash", "candidate-hash", {"cue_count": 3}, {"cue_count": 4},
    )
    write_candidate_report(target, report)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["baseline_sha256"] == "baseline-hash"
    assert "text" not in target.read_text(encoding="utf-8").lower()


def test_selective_retry_accepts_only_clear_quality_improvement() -> None:
    baseline = TranscriptionArtifact(
        cues=(TranscriptionCue(0, 2, "baseline", avg_logprob=-1.4, compression_ratio=2.6),),
        duration_seconds=2,
    )
    improved = TranscriptionArtifact(
        cues=(TranscriptionCue(0, 2, "candidate", avg_logprob=-1.1, compression_ratio=2.3),),
        duration_seconds=2,
    )
    selection = select_retry_window(baseline, improved, (0, 2))
    assert selection.accepted is True
    merged, selections = selective_merge_retry_artifact(baseline, improved, [(0, 2)])
    assert merged.cues[0].text == "candidate"
    assert selections[0].reason == "accepted"


def test_selective_retry_rejects_coverage_and_duplicate_regression() -> None:
    baseline = TranscriptionArtifact(
        cues=(TranscriptionCue(0, 2, "baseline", avg_logprob=-1.4),), duration_seconds=2,
    )
    short = TranscriptionArtifact(
        cues=(TranscriptionCue(0, 1, "short", avg_logprob=-0.5),), duration_seconds=2,
    )
    assert select_retry_window(baseline, short, (0, 2)).reason == "coverage_out_of_range"
    duplicate = TranscriptionArtifact(
        cues=(
            TranscriptionCue(0, 1, "same", avg_logprob=-0.5),
            TranscriptionCue(1, 2, "same", avg_logprob=-0.5),
        ),
        duration_seconds=2,
    )
    assert select_retry_window(baseline, duplicate, (0, 2)).reason == "duplicate_regressed"


def test_selective_retry_keeps_baseline_without_clear_improvement() -> None:
    baseline = TranscriptionArtifact(
        cues=(TranscriptionCue(0, 2, "baseline", avg_logprob=-0.8, compression_ratio=1.2),),
        duration_seconds=2,
    )
    equivalent = TranscriptionArtifact(
        cues=(TranscriptionCue(0, 2, "different", avg_logprob=-0.75, compression_ratio=1.1),),
        duration_seconds=2,
    )
    merged, selections = selective_merge_retry_artifact(baseline, equivalent, [(0, 2)])
    assert merged == baseline
    assert selections[0].reason == "no_clear_improvement"


def test_adjacent_duplicate_candidate_merges_only_contiguous_high_similarity_cues() -> None:
    artifact = TranscriptionArtifact(
        cues=(
            TranscriptionCue(0, 2, "This is a repeated recognition window."),
            TranscriptionCue(2, 2.4, "This is repeated recognition window."),
            TranscriptionCue(3, 4, "A genuinely different sentence."),
        ),
        duration_seconds=4,
    )

    candidate, decisions = adjacent_duplicate_candidate(artifact)

    assert len(candidate.cues) == 2
    assert candidate.cues[0].start == 0
    assert candidate.cues[0].end == 2.4
    assert len(decisions) == 1
    assert decisions[0].first_index == 0
    assert validate_artifact(candidate, 4) == []
