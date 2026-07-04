from __future__ import annotations

from pathlib import Path

import pytest

import mixed_language_asr_evidence as evidence


def _sample(index: int, language: str, probability: float | None, error: str = "") -> dict:
    return {
        "sample_index": index,
        "start_seconds": float(index),
        "end_seconds": float(index + 1),
        "detected_language": language,
        "language_probability": probability,
        "text_preview": f"text {index}",
        "segment_count": 1,
        "error": error,
    }


def test_sample_window_planning_for_short_media():
    windows = evidence.plan_sample_windows(
        duration_seconds=20,
        sample_count=8,
        sample_seconds=30,
    )

    assert windows == [evidence.SampleWindow(index=1, start_seconds=0.0, end_seconds=20.0)]


def test_sample_window_planning_for_long_media():
    windows = evidence.plan_sample_windows(
        duration_seconds=300,
        sample_count=3,
        sample_seconds=30,
    )

    assert windows == [
        evidence.SampleWindow(index=1, start_seconds=0.0, end_seconds=30.0),
        evidence.SampleWindow(index=2, start_seconds=135.0, end_seconds=165.0),
        evidence.SampleWindow(index=3, start_seconds=270.0, end_seconds=300.0),
    ]


def test_mixed_language_summary_classification():
    summary = evidence.classify_summary([
        _sample(1, "en", 0.91),
        _sample(2, "fr", 0.88),
        _sample(3, "en", 0.82),
    ])

    assert summary["distinct_detected_languages"] == ["en", "fr"]
    assert summary["dominant_language"] == "en"
    assert summary["mixed_language_likelihood"] == "likely"
    assert summary["low_confidence_count"] == 0


def test_low_confidence_classification_is_possible_not_likely():
    summary = evidence.classify_summary([
        _sample(1, "en", 0.94),
        _sample(2, "fr", 0.31),
    ])

    assert summary["mixed_language_likelihood"] == "possible"
    assert summary["low_confidence_count"] == 1


def test_single_language_summary_classification():
    summary = evidence.classify_summary([
        _sample(1, "ja", 0.92),
        _sample(2, "ja", 0.81),
    ])

    assert summary["mixed_language_likelihood"] == "none"
    assert summary["dominant_language"] == "ja"


def test_json_and_markdown_report_generation(tmp_path):
    input_path = tmp_path / "movie.mp4"
    input_path.write_text("placeholder", encoding="utf-8")
    report = evidence.build_report(
        input_path=input_path,
        model_name="small",
        device="cpu",
        compute_type="int8",
        allow_model_download=False,
        sample_count=2,
        sample_seconds=30,
        ffmpeg_path=str(tmp_path / "ffmpeg.exe"),
        ffprobe_path=str(tmp_path / "ffprobe.exe"),
        duration_seconds=90,
        samples=[_sample(1, "en", 0.9), _sample(2, "fr", 0.8)],
        device_warnings=[],
    )

    paths = evidence.write_reports(report, tmp_path / "reports", input_path)
    json_path = Path(paths["json_path"])
    markdown_path = Path(paths["markdown_path"])

    assert json_path.name.endswith(".asr_evidence.json")
    assert markdown_path.name.endswith(".asr_evidence.md")
    assert '"report_type": "mixed_language_asr_evidence"' in json_path.read_text(encoding="utf-8")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Mixed-Language ASR Evidence" in markdown
    assert "This is sampled evidence only." in markdown


def test_local_only_model_missing_fails_cleanly():
    class MissingModel:
        def __init__(self, *args, **kwargs):
            raise OSError("huggingface cache miss")

    with pytest.raises(evidence.ModelUnavailable, match="Model not found locally"):
        evidence.create_whisper_model(
            model_name="small",
            device="cpu",
            compute_type="int8",
            local_files_only=True,
            model_class=MissingModel,
        )


def test_allow_model_download_passes_local_files_only_false():
    calls = {}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            calls.update(kwargs)

    evidence.create_whisper_model(
        model_name="small",
        device="cpu",
        compute_type="int8",
        local_files_only=False,
        model_class=FakeModel,
    )

    assert calls["local_files_only"] is False
