from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline_stages
from pipeline_stages import StageError, TaskContext
from stage_event_log import write_stage_event


def _context(tmp_path: Path, name: str = "电影 样本.wav") -> TaskContext:
    source = tmp_path / name
    source.write_bytes(b"source")
    work = tmp_path / "work"
    output = tmp_path / "output"
    work.mkdir()
    output.mkdir()
    return TaskContext(name, source, work, output)


def test_extract_stage_reuses_non_empty_audio(tmp_path: Path) -> None:
    context = _context(tmp_path)
    audio = context.work_dir / f"{context.input_path.stem}.16k.wav"
    audio.write_bytes(b"wav")
    result = pipeline_stages.extract_audio_stage(context, project_root=tmp_path)
    assert result.reused is True
    assert result.outputs == (audio,)


def test_extract_stage_reports_subprocess_failure(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        pipeline_stages,
        "run_text",
        lambda *args, **kwargs: SimpleNamespace(returncode=7, stderr="decoder failed"),
    )
    with pytest.raises(StageError) as caught:
        pipeline_stages.extract_audio_stage(context, project_root=tmp_path, ffmpeg_path="ffmpeg")
    assert caught.value.stage == "extracting_audio"
    assert caught.value.returncode == 7


def test_transcribe_stage_rejects_empty_output(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    audio = context.work_dir / "audio.wav"
    audio.write_bytes(b"wav")
    target = context.output_dir / "source.srt"
    fake = types.ModuleType("transcribe")

    def transcribe_to_srt(**kwargs):
        kwargs["srt_path"].write_bytes(b"")
        return {"source_language": "fr"}

    fake.transcribe_to_srt = transcribe_to_srt
    monkeypatch.setitem(sys.modules, "transcribe", fake)
    config = SimpleNamespace(
        lang_profile_config={}, language_profile_id="", language_profile_name="", model="small",
        model_dir=tmp_path, device="cpu", compute_type="int8", language=None, beam_size=5,
        vad_filter=True, local_files_only=True,
    )
    with pytest.raises(StageError, match="non-empty SRT"):
        pipeline_stages.transcribe_stage(context, audio_path=audio, srt_path=target, config=config)


def test_translate_and_quality_stages_return_structured_results(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    source = context.output_dir / "source.srt"
    translated = context.output_dir / "translated.srt"
    report = context.output_dir / "quality.json"
    source.write_text("source", encoding="utf-8")

    translation = types.ModuleType("subtitle_translate")
    translation.translate_srt = lambda **kwargs: kwargs["output_path"].write_text("translated", encoding="utf-8")
    quality = types.ModuleType("quality_checker")
    quality.run_quality_check = lambda **kwargs: report.write_text("{}", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "subtitle_translate", translation)
    monkeypatch.setitem(sys.modules, "quality_checker", quality)
    config = SimpleNamespace(
        api_provider="openai-compatible", api_base="", api_key="secret", llm_model="model",
        target_language="zh-CN", translation_batch_size=20, translation_temperature=0.2,
        translation_mode="translated", context_window=3, lang_profile_config={},
    )
    translated_result = pipeline_stages.translate_stage(
        context, source_srt=source, output_path=translated, config=config, effective_prompt=""
    )
    quality_result = pipeline_stages.quality_check_stage(
        context, source_srt=source, translated_srt=translated, report_path=report, config=config
    )
    assert translated_result.stage == "translating"
    assert quality_result.stage == "quality_checking"


def test_terminology_consistency_reports_missing_profile_terms(tmp_path: Path) -> None:
    source = tmp_path / "source.srt"
    translated = tmp_path / "translated.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nAlice\n", encoding="utf-8")
    translated.write_text("1\n00:00:01,000 --> 00:00:02,000\n她\n", encoding="utf-8")
    result = pipeline_stages._terminology_consistency(
        source, translated, [{"source": "Alice", "target": "爱丽丝"}]
    )
    assert result["status"] == "warning"
    assert result["missing_terms"][0]["expected_target"] == "爱丽丝"


def test_archive_stage_avoids_existing_destination(tmp_path: Path) -> None:
    context = _context(tmp_path)
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / context.input_path.name).write_bytes(b"old")
    result = pipeline_stages.archive_stage(context, archive_dir=archive)
    assert result.outputs[0].name != context.input_path.name
    assert result.outputs[0].read_bytes() == b"source"


def test_stage_event_log_redacts_secrets_and_absolute_paths(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    write_stage_event(
        target,
        task_id="movie.mkv",
        stage="translating",
        event="failed",
        status="failed",
        summary=r"api_key=super-secret C:\Users\tester\movie.srt",
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "super-secret" not in payload["summary"]
    assert "Users" not in payload["summary"]
