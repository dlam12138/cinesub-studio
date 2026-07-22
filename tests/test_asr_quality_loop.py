from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import batch_worker
import pipeline_config
import transcribe
from asr_retry import build_retry_report, merge_retry_artifact, plan_retry_windows, select_retry_window
from asr_runtime import (
    TranscriptionArtifact,
    TranscriptionCue,
    TranscriptionWord,
    deduplicate_boundary_cues,
    resolve_quality_loop_config,
)
from subtitle_resegment import SubtitleResegmenter


def test_quality_preset_priority_and_explicit_override() -> None:
    loop, sources = resolve_quality_loop_config(
        explicit={"resegment_subtitles": False, "model": "small"},
        preset="quality",
        profile_asr={"word_timestamps": False, "asr_retry_mode": "off"},
    )

    assert loop["model"] == "small"
    assert sources["model"]["source"] == "explicit_request"
    assert loop["word_timestamps"] is True
    assert sources["word_timestamps"]["source"] == "quality_preset"
    assert loop["resegment_subtitles"] is False
    assert sources["resegment_subtitles"]["source"] == "explicit_request"
    assert loop["asr_retry_mode"] == "apply"


def test_pipeline_quality_preset_keeps_explicit_model() -> None:
    args = SimpleNamespace(
        provider=None,
        no_translate=True,
        language_profile=None,
        asr_mode=None,
        language=None,
        quality_preset="quality",
        word_timestamps=None,
        resegment_subtitles=None,
        asr_retry_mode=None,
        asr_hotword_prompt="",
        model="small",
        device=None,
        compute_type=None,
        no_vad=False,
        beam_size=None,
        target_language=None,
        translation_reliability_mode=None,
        translation_max_extra_requests=None,
        translation_strategy_mode=None,
        translation_scene_gap_seconds=None,
        api_provider=None,
        api_base=None,
        api_key=None,
        llm_model=None,
        translation_quality_model=None,
        translation_prompt=None,
        subtitle_formats=None,
        ass_style_id=None,
    )

    values, _messages = pipeline_config.resolve_cli_config(
        args,
        ["--quality-preset", "quality", "--model", "small"],
    )

    assert values["model"] == "small"
    assert values["word_timestamps"] is True
    assert values["resegment_subtitles"] is True
    assert values["asr_retry_mode"] == "apply"
    assert values["effective_asr_config"]["model"] == {
        "value": "small",
        "source": "explicit_request",
    }


def test_pipeline_signature_includes_v07_quality_loop_fields(tmp_path: Path) -> None:
    base = batch_worker.BatchConfig(
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
        model_dir=tmp_path / "models",
        model="small",
        word_timestamps=False,
        resegment_subtitles=False,
        asr_retry_mode="off",
    )
    changed = batch_worker.BatchConfig(
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
        model_dir=tmp_path / "models",
        model="small",
        quality_preset="balanced",
        word_timestamps=True,
        resegment_subtitles=True,
        asr_retry_mode="dry_run",
        asr_hotword_prompt="DTS",
    )
    no_hotword = batch_worker.BatchConfig(
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
        model_dir=tmp_path / "models",
        model="small",
        quality_preset="balanced",
        word_timestamps=True,
        resegment_subtitles=True,
        asr_retry_mode="dry_run",
        asr_hotword_prompt="",
    )

    assert base.asr_signature() != changed.asr_signature()
    assert no_hotword.asr_signature() != changed.asr_signature()


def test_deduplicate_boundary_cues_preserves_words() -> None:
    word = TranscriptionWord(1.0, 1.5, "hello", 0.9)
    low = TranscriptionCue(1.0, 2.0, "hello", avg_logprob=-1.0)
    high = TranscriptionCue(1.1, 2.1, "hello", words=(word,), avg_logprob=-0.1)

    deduped, removed = deduplicate_boundary_cues((low, high))

    assert removed == 1
    assert deduped[0].words == (word,)


def test_segment_conversion_offsets_cue_and_word_times() -> None:
    word = SimpleNamespace(start=0.1, end=0.3, word="Bonjour", probability=0.8)
    segment = SimpleNamespace(
        start=1.0,
        end=2.0,
        text="Bonjour",
        words=[word],
        avg_logprob=-0.2,
    )

    cues = transcribe._segments_to_cues([segment], offset=45.0)

    assert cues[0].start == pytest.approx(46.0)
    assert cues[0].end == pytest.approx(47.0)
    assert cues[0].words[0].start == pytest.approx(45.1)
    assert cues[0].words[0].end == pytest.approx(45.3)


def test_subtitle_resegmenter_conserves_text_and_falls_back_without_words() -> None:
    words = (
        TranscriptionWord(0.0, 0.4, "Hello", 0.9),
        TranscriptionWord(0.5, 0.8, "world.", 0.9),
        TranscriptionWord(1.1, 1.4, "Next", 0.9),
        TranscriptionWord(1.5, 1.8, "line", 0.9),
    )
    artifact = TranscriptionArtifact(
        cues=(TranscriptionCue(0.0, 2.0, "Hello world. Next line", words=words),),
        duration_seconds=2.0,
    )

    result = SubtitleResegmenter(max_units=4).resegment(artifact, enabled=True)

    assert result.summary["applied"] is True
    assert "".join(cue.text for cue in result.artifact.cues).replace(" ", "") == "Helloworld.Nextline"
    assert sum(len(cue.words) for cue in result.artifact.cues) == len(words)

    fallback = SubtitleResegmenter().resegment(
        TranscriptionArtifact(cues=(TranscriptionCue(0.0, 1.0, "plain"),)),
        enabled=True,
    )
    assert fallback.artifact.cues[0].text == "plain"
    assert fallback.summary["fallback_reason"] == "no_word_timestamps"


def test_subtitle_resegmenter_preserves_zero_duration_text_tokens_in_order() -> None:
    words = (
        TranscriptionWord(0.0, 0.4, " Je", 0.9),
        TranscriptionWord(0.5, 0.8, " c", 0.9),
        TranscriptionWord(0.8, 0.8, "'est", 0.9),
        TranscriptionWord(0.9, 1.2, " vrai.", 0.9),
    )
    artifact = TranscriptionArtifact(
        cues=(TranscriptionCue(0.0, 1.2, " Je c'est vrai.", words=words),),
        duration_seconds=1.2,
    )

    result = SubtitleResegmenter(max_units=4).resegment(artifact, enabled=True)

    assert result.summary["applied"] is True
    assert "".join(cue.text for cue in result.artifact.cues).replace(" ", "") == "Jec'estvrai."
    assert tuple(word for cue in result.artifact.cues for word in cue.words) == words


def test_retry_budget_transaction_and_report_omit_transcripts() -> None:
    baseline = TranscriptionArtifact(
        cues=(
            TranscriptionCue(0.0, 1.0, "repeat", avg_logprob=-1.5, compression_ratio=2.6),
            TranscriptionCue(1.0, 2.0, "repeat", avg_logprob=-1.5, compression_ratio=2.6),
            TranscriptionCue(3.0, 4.0, "tail", avg_logprob=-0.1),
        ),
        duration_seconds=40.0,
    )
    windows, skipped = plan_retry_windows(baseline, max_duration_ratio=0.05)
    assert len(windows) <= 1
    assert skipped

    candidate = TranscriptionArtifact(
        cues=(TranscriptionCue(0.0, 2.0, "repeat fixed", avg_logprob=-0.5, compression_ratio=1.1),),
        duration_seconds=40.0,
    )
    selection = select_retry_window(baseline, candidate, (0.0, 2.2))
    report = build_retry_report("apply", [selection])
    report_text = str(report)

    assert "repeat fixed" not in report_text
    assert "baseline_text_hash" in report_text
    if selection.accepted:
        merged = merge_retry_artifact(baseline, candidate, [(0.0, 2.2)])
        assert [cue.text for cue in merged.cues] == ["repeat fixed", "tail"]


def test_retry_hard_rejects_empty_candidate() -> None:
    baseline = TranscriptionArtifact(
        cues=(TranscriptionCue(0.0, 1.0, "spoken", avg_logprob=-1.0),),
        duration_seconds=1.0,
    )
    candidate = TranscriptionArtifact(cues=(), duration_seconds=1.0)

    selection = select_retry_window(baseline, candidate, (0.0, 1.0))

    assert selection.accepted is False
    assert "empty_candidate" in selection.reasons


def test_asr_review_message_reports_apply_and_dry_run_truthfully() -> None:
    accepted = transcribe._retry_summary_message(
        warning=True,
        mode="apply",
        report={"accepted_window_count": 2, "executed_window_count": 3},
    )
    dry_run = transcribe._retry_summary_message(
        warning=True,
        mode="dry_run",
        report={"accepted_window_count": 0, "executed_window_count": 3},
    )

    assert "2" in accepted
    assert "已事务式接受" in accepted
    assert "dry-run" in dry_run
    assert "未改写输出" in dry_run
