from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import segment_asr_prototype as proto


def test_manual_window_parsing():
    windows = proto.parse_manual_windows(["00:05:00-00:07:00"], duration_seconds=600)

    assert windows == [proto.SampleWindow(index=1, start_seconds=300.0, end_seconds=420.0)]


def test_invalid_manual_window_rejects_reversed_range():
    with pytest.raises(ValueError, match="after start"):
        proto.parse_manual_windows(["00:07:00-00:05:00"], duration_seconds=600)


def test_invalid_manual_window_rejects_out_of_range():
    with pytest.raises(ValueError, match="exceeds media duration"):
        proto.parse_manual_windows(["00:09:00-00:11:00"], duration_seconds=600)


def test_manual_windows_override_automatic_planning():
    windows = proto.plan_windows(
        duration_seconds=600,
        manual_windows=["00:01:00-00:02:00"],
        samples=3,
        sample_every_seconds=180,
        window_seconds=60,
    )

    assert windows == [proto.SampleWindow(index=1, start_seconds=60.0, end_seconds=120.0)]


def test_sample_every_seconds_takes_precedence_over_samples():
    windows = proto.plan_windows(
        duration_seconds=500,
        manual_windows=[],
        samples=2,
        sample_every_seconds=180,
        window_seconds=60,
    )

    assert windows == [
        proto.SampleWindow(index=1, start_seconds=0.0, end_seconds=60.0),
        proto.SampleWindow(index=2, start_seconds=180.0, end_seconds=240.0),
        proto.SampleWindow(index=3, start_seconds=360.0, end_seconds=420.0),
    ]


def test_samples_use_window_seconds_for_uniform_windows():
    windows = proto.plan_windows(
        duration_seconds=300,
        manual_windows=[],
        samples=3,
        sample_every_seconds=None,
        window_seconds=60,
    )

    assert windows == [
        proto.SampleWindow(index=1, start_seconds=0.0, end_seconds=60.0),
        proto.SampleWindow(index=2, start_seconds=120.0, end_seconds=180.0),
        proto.SampleWindow(index=3, start_seconds=240.0, end_seconds=300.0),
    ]


def test_forced_language_modes_pass_requested_languages():
    calls = []

    class FakeModel:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs["language"])
            language = kwargs["language"] or "en"
            return [SimpleNamespace(text=f"text for {language}")], SimpleNamespace(
                language=language,
                language_probability=0.75,
            )

    results = proto.analyze_window_modes(FakeModel(), Path("sample.wav"))

    assert calls == [None, "fr", "en"]
    assert [result["requested_language"] for result in results] == [None, "fr", "en"]
    assert [result["mode"] for result in results] == ["auto", "forced-fr", "forced-en"]
    assert all(result["segment_count"] == 1 for result in results)


def test_report_contains_mode_fields_and_forced_language_warning(tmp_path):
    input_path = tmp_path / "movie.wav"
    input_path.write_text("placeholder", encoding="utf-8")
    windows = [
        {
            "window_index": 1,
            "start_seconds": 0.0,
            "end_seconds": 60.0,
            "results": [
                {
                    "mode": "forced-fr",
                    "requested_language": "fr",
                    "detected_language": "fr",
                    "language_probability": 0.9,
                    "segment_count": 2,
                    "text_preview": "bonjour",
                    "error": "",
                }
            ],
        }
    ]

    report = proto.build_report(
        input_path=input_path,
        model_name="small",
        device="cpu",
        compute_type="int8",
        allow_model_download=False,
        duration_seconds=120,
        manual_windows=[],
        samples=1,
        sample_every_seconds=None,
        window_seconds=60,
        ffmpeg_path=str(tmp_path / "ffmpeg.exe"),
        ffprobe_path=str(tmp_path / "ffprobe.exe"),
        windows=windows,
        device_warnings=[],
    )
    paths = proto.write_reports(report, tmp_path / "reports", input_path)

    json_path = Path(paths["json_path"])
    markdown_path = Path(paths["markdown_path"])
    assert json_path.name.endswith(".asr_segment_prototype.json")
    assert markdown_path.name.endswith(".asr_segment_prototype.md")

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "requested_language" in markdown
    assert "detected_language" in markdown
    assert "language_probability" in markdown
    assert "segment_count" in markdown
    assert "Forced-language rows are transcription comparison modes" in markdown
    assert "forced-fr" in markdown
    assert "fr" in markdown

    assert report["windows"][0]["results"][0]["requested_language"] == "fr"
    assert report["windows"][0]["results"][0]["detected_language"] == "fr"
    assert report["windows"][0]["results"][0]["language_probability"] == 0.9


def test_local_only_model_missing_fails_cleanly():
    class MissingModel:
        def __init__(self, *args, **kwargs):
            raise OSError("huggingface cache miss")

    with pytest.raises(proto.ModelUnavailable, match="Model not found locally"):
        proto.create_whisper_model(
            model_name="small",
            device="cpu",
            compute_type="int8",
            local_files_only=True,
            model_class=MissingModel,
        )
