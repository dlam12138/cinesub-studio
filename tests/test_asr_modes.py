from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import transcribe
from asr_runtime import (
    TranscriptionArtifact,
    TranscriptionCue,
    deduplicate_boundary_cues,
    normalize_asr_request,
    plan_vad_blocks,
)


def test_asr_request_normalization_and_legacy_language() -> None:
    assert normalize_asr_request(None, None) == ("auto", None)
    assert normalize_asr_request(None, "fr") == ("fixed", "fr")
    assert normalize_asr_request("fixed", "zh") == ("fixed", "zh")
    assert normalize_asr_request("multilingual", None) == ("multilingual", None)
    with pytest.raises(ValueError, match="requires"):
        normalize_asr_request("fixed", None)
    with pytest.raises(ValueError, match="does not accept"):
        normalize_asr_request("auto", "en")


def test_vad_blocks_close_on_long_silence_and_keep_short_overlap() -> None:
    sr = 16_000
    spans = [
        {"start": 2 * sr, "end": 20 * sr},
        {"start": 21 * sr, "end": 42 * sr},
        {"start": 55 * sr, "end": 70 * sr},
    ]
    blocks = plan_vad_blocks(spans, audio_samples=80 * sr)
    assert len(blocks) == 2
    assert blocks[0].start == pytest.approx(1.2)
    assert blocks[0].end == pytest.approx(42.8)
    assert blocks[1].start == pytest.approx(54.2)
    assert blocks[1].end == pytest.approx(70.8)


def test_vad_blocks_return_empty_for_silence_and_limit_long_chunks() -> None:
    sr = 16_000
    assert plan_vad_blocks([], audio_samples=120 * sr) == []
    spans = [
        {"start": 0, "end": 30 * sr},
        {"start": 31 * sr, "end": 59 * sr},
        {"start": 60 * sr, "end": 88 * sr},
    ]
    blocks = plan_vad_blocks(spans, audio_samples=90 * sr)
    assert blocks
    assert all(block.end - block.start <= 60.001 for block in blocks)


def test_boundary_dedup_prefers_confidence_but_keeps_other_language() -> None:
    duplicate_low = TranscriptionCue(10.0, 12.0, "Hello, world!", avg_logprob=-1.2)
    duplicate_high = TranscriptionCue(11.4, 12.5, "hello world", avg_logprob=-0.3)
    chinese = TranscriptionCue(11.7, 13.0, "你好，世界", avg_logprob=-0.1)
    merged, removed = deduplicate_boundary_cues(
        [duplicate_low, duplicate_high, chinese]
    )
    assert removed == 1
    assert len(merged) == 2
    assert merged[0].text == "hello world"
    assert merged[1].text == "你好，世界"


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, _audio, **kwargs):
        self.calls.append(kwargs)
        segment = SimpleNamespace(
            start=0.0,
            end=1.5,
            text="Bonjour",
            avg_logprob=-0.2,
            compression_ratio=1.1,
            no_speech_prob=0.01,
        )
        info = SimpleNamespace(
            language=kwargs.get("language") or "fr",
            language_probability=0.98,
            duration=1.5,
        )
        return [segment], info


def _session(model: _FakeModel, model_dir: Path) -> transcribe.AsrSession:
    return transcribe.AsrSession(
        model=model,
        model_name="small",
        device="cpu",
        compute_type="int8",
        model_dir=model_dir,
        local_files_only=True,
    )


@pytest.mark.parametrize(
    ("mode", "language", "expected_language"),
    [("auto", None, None), ("fixed", "fr", "fr")],
)
def test_whole_audio_modes_call_model_once(
    tmp_path: Path, mode: str, language: str | None, expected_language: str | None
) -> None:
    model = _FakeModel()
    srt = tmp_path / f"{mode}.srt"
    transcribe.transcribe_to_srt(
        audio_path=tmp_path / "audio.wav",
        srt_path=srt,
        model_name="small",
        model_dir=tmp_path,
        device="cpu",
        compute_type="int8",
        asr_mode=mode,
        language=language,
        beam_size=5,
        vad_filter=True,
        local_files_only=True,
        session=_session(model, tmp_path),
    )
    assert len(model.calls) == 1
    assert model.calls[0]["language"] == expected_language
    report = json.loads(srt.with_suffix(".lang.json").read_text(encoding="utf-8"))
    assert report["asr_mode"] == mode


def test_multilingual_reuses_one_model_and_reports_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _FakeModel()
    created: list[object] = []

    def fake_create(**_kwargs):
        created.append(object())
        return _session(model, tmp_path)

    def fake_multilingual(**kwargs):
        assert kwargs["model"] is model
        kwargs["model"].transcribe([0], language=None, beam_size=5, vad_filter=False)
        kwargs["model"].transcribe([0], language=None, beam_size=5, vad_filter=False)
        cues = (
            TranscriptionCue(1.0, 2.0, "Bonjour", avg_logprob=-0.2),
            TranscriptionCue(46.0, 47.0, "Hello", avg_logprob=-0.1),
        )
        blocks = [
            {
                "index": 1,
                "start": 0.0,
                "end": 45.8,
                "language": "fr",
                "language_probability": 0.97,
                "cue_count": 1,
                "review_recommended": False,
            },
            {
                "index": 2,
                "start": 45.0,
                "end": 60.0,
                "language": "en",
                "language_probability": 0.96,
                "cue_count": 1,
                "review_recommended": False,
            },
        ]
        return TranscriptionArtifact(cues=cues, language="multilingual"), blocks

    monkeypatch.setattr(transcribe, "create_asr_session", fake_create)
    monkeypatch.setattr(transcribe, "_transcribe_multilingual", fake_multilingual)
    srt = tmp_path / "multilingual.srt"
    transcribe.transcribe_to_srt(
        audio_path=tmp_path / "already-extracted.wav",
        srt_path=srt,
        model_name="small",
        model_dir=tmp_path,
        device="cpu",
        compute_type="int8",
        asr_mode="multilingual",
        language=None,
        beam_size=5,
        vad_filter=True,
        local_files_only=True,
    )
    assert len(created) == 1
    assert len(model.calls) == 2
    assert all(call["language"] is None for call in model.calls)
    report = json.loads(srt.with_suffix(".lang.json").read_text(encoding="utf-8"))
    assert report["distinct_languages"] == ["en", "fr"]
    assert report["block_count"] == 2


def test_multilingual_fails_clearly_when_vad_finds_no_speech(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_module = types.ModuleType("faster_whisper.audio")
    audio_module.decode_audio = lambda *_args, **_kwargs: [0.0] * 16_000
    vad_module = types.ModuleType("faster_whisper.vad")
    vad_module.VadOptions = lambda **kwargs: kwargs
    vad_module.get_speech_timestamps = lambda *_args, **_kwargs: []
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad_module)
    with pytest.raises(RuntimeError, match="未检测到可转写语音"):
        transcribe._transcribe_multilingual(
            model=_FakeModel(),
            audio_path=tmp_path / "silent.wav",
            beam_size=5,
            options=transcribe.AsrDecodeOptions(),
            backend_version="test",
        )


def test_multilingual_does_not_skip_a_failed_speech_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_module = types.ModuleType("faster_whisper.audio")
    audio_module.decode_audio = lambda *_args, **_kwargs: [0.0] * 32_000
    vad_module = types.ModuleType("faster_whisper.vad")
    vad_module.VadOptions = lambda **kwargs: kwargs
    vad_module.get_speech_timestamps = lambda *_args, **_kwargs: [
        {"start": 0, "end": 16_000}
    ]
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_module)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad_module)
    model = SimpleNamespace(
        transcribe=lambda *_args, **_kwargs: (
            [],
            SimpleNamespace(language="fr", language_probability=0.9),
        )
    )
    with pytest.raises(RuntimeError, match="第 1 个语音块失败"):
        transcribe._transcribe_multilingual(
            model=model,
            audio_path=tmp_path / "speech.wav",
            beam_size=5,
            options=transcribe.AsrDecodeOptions(),
            backend_version="test",
        )
