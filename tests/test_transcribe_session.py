from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from transcribe import AsrSession, transcribe_to_srt


class _FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, _audio: str, **_kwargs):
        self.calls += 1
        segments = [
            SimpleNamespace(
                start=0.0, end=1.0, text=" hello ", avg_logprob=-0.2,
                compression_ratio=1.1, no_speech_prob=0.0,
            )
        ]
        return iter(segments), SimpleNamespace(language="en", language_probability=0.9, duration=1.0)


def test_transcribe_reuses_supplied_session_without_loading_model(tmp_path: Path) -> None:
    model = _FakeModel()
    session = AsrSession(model, "small", "cpu", "int8", tmp_path, True)
    for index in range(2):
        artifacts = []
        transcribe_to_srt(
            audio_path=tmp_path / "audio.wav", srt_path=tmp_path / f"out-{index}.srt",
            model_name="small", model_dir=tmp_path, device="cpu", compute_type="int8",
            language=None, beam_size=5, vad_filter=True, local_files_only=True,
            artifact_out=artifacts, session=session,
        )
        assert len(artifacts) == 1
    assert model.calls == 2


def test_transcribe_rejects_mismatched_session(tmp_path: Path) -> None:
    session = AsrSession(_FakeModel(), "small", "cpu", "int8", tmp_path, True)
    try:
        transcribe_to_srt(
            audio_path=tmp_path / "audio.wav", srt_path=tmp_path / "out.srt",
            model_name="large-v3", model_dir=tmp_path, device="cpu", compute_type="int8",
            language=None, beam_size=5, vad_filter=True, local_files_only=True, session=session,
        )
    except ValueError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("mismatched session was accepted")
