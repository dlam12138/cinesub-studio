from __future__ import annotations

import json
from pathlib import Path

import batch_worker
import pytest
import subtitle_translate
from pipeline_stages import StageResult
from task_state import TaskState, prepare_retry_failed_tasks, set_state_root_provider
from translation_reliability import TranslationReliabilityError


def _openai_response(body: str) -> str:
    request = json.loads(body)
    payload = json.loads(request["messages"][1]["content"])
    translations = {
        int(item["id"]): f"译文{int(item['id'])}"
        for item in payload["items"]
    }
    content = json.dumps({
        "items": [
            {"id": item_id, "text": text}
            for item_id, text in translations.items()
        ]
    }, ensure_ascii=False)
    return json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False)


@pytest.fixture
def offline_pipeline(tmp_path: Path, monkeypatch):
    roots = {
        "input": tmp_path / "input",
        "output": tmp_path / "output",
        "work": tmp_path / "work",
        "states": tmp_path / "work" / "states",
        "models": tmp_path / "models",
        "archive": tmp_path / "archive",
        "failed": tmp_path / "failed",
        "events": tmp_path / "logs" / "pipeline.events.jsonl",
    }
    for path in roots.values():
        if path.suffix:
            continue
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", roots["states"])
    monkeypatch.setattr(batch_worker, "DIR_ARCHIVE", roots["archive"])
    monkeypatch.setattr(batch_worker, "DIR_FAILED", roots["failed"])
    monkeypatch.setattr(batch_worker, "STAGE_EVENT_LOG", roots["events"])
    set_state_root_provider(lambda: roots["states"])

    calls = {"extract": 0, "transcribe": 0}

    def fake_extract(context, **kwargs):
        calls["extract"] += 1
        audio = context.work_dir / f"{context.input_path.stem}.16k.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"offline wav fixture")
        return StageResult("extracting_audio", "completed", (audio,))

    def fake_transcribe(context, *, srt_path, **kwargs):
        calls["transcribe"] += 1
        subtitle_translate.write_srt(
            [
                subtitle_translate.SubtitleItem(
                    1, "00:00:00,000 --> 00:00:01,500", "Bonjour tout le monde"
                ),
                subtitle_translate.SubtitleItem(
                    2, "00:00:01,500 --> 00:00:03,000", "Nous devons coopérer"
                ),
            ],
            srt_path,
        )
        language = {
            "source_language": "fr",
            "language_probability": 0.99,
            "forced_language": "fr",
        }
        srt_path.with_suffix(".lang.json").write_text(
            json.dumps(language, ensure_ascii=False), encoding="utf-8"
        )
        return StageResult(
            "transcribing",
            "completed",
            (srt_path, srt_path.with_suffix(".lang.json")),
            {"language_detection": language},
        )

    monkeypatch.setattr(batch_worker, "extract_audio_stage", fake_extract)
    monkeypatch.setattr(batch_worker, "transcribe_stage", fake_transcribe)
    monkeypatch.setattr(subtitle_translate, "_call_llm_api", lambda **kwargs: _openai_response(kwargs["body"]))

    def make_pipeline(*, mode: str = "bilingual") -> batch_worker.BatchPipeline:
        return batch_worker.BatchPipeline(batch_worker.BatchConfig(
            input_dir=roots["input"],
            output_dir=roots["output"],
            work_dir=roots["work"],
            model_dir=roots["models"],
            model="offline-stub",
            device="cpu",
            local_files_only=True,
            asr_mode="fixed",
            language="fr",
            translate=True,
            api_provider="openai-compatible",
            api_base="https://offline.invalid",
            api_key="offline-test-only",
            llm_model="offline-llm-stub",
            target_language="zh-CN",
            translation_mode=mode,
            translation_reliability_mode="off",
            move_completed=False,
        ))

    yield roots, calls, make_pipeline
    set_state_root_provider(lambda: batch_worker.DIR_WORK_STATES)


@pytest.mark.parametrize(
    ("mode", "artifact_dir", "artifact_marker"),
    [
        ("translated", "zh", ".translated.zh-CN.srt"),
        ("bilingual", "bilingual", ".bilingual.zh-CN.srt"),
    ],
)
def test_offline_pipeline_generates_complete_artifact_sets(
    offline_pipeline, mode: str, artifact_dir: str, artifact_marker: str,
) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / f"离线-{mode}.mp4"
    media.write_bytes(b"offline media fixture")

    result = make_pipeline(mode=mode).run()

    assert result == {"total": 1, "completed": 1, "failed": 0, "skipped": 0}
    source = roots["output"] / "source" / f"{media.stem}.offline-stub.srt"
    translated = next((roots["output"] / artifact_dir).glob(f"{media.stem}*{artifact_marker}"))
    report = roots["output"] / "reports" / f"{media.stem}.offline-stub.quality_report.json"
    state = TaskState.load(roots["states"] / f"{media.stem}.state.json")
    events = [
        json.loads(line)
        for line in roots["events"].read_text(encoding="utf-8").splitlines()
    ]

    assert source.stat().st_size > 0
    assert translated.stat().st_size > 0
    assert report.stat().st_size > 0
    assert state is not None and state.status == "completed" and state.stage == "completed"
    assert {event["stage"] for event in events if event["event"] == "completed"} >= {
        "extracting_audio", "transcribing", "translating", "quality_checking",
    }
    assert calls == {"extract": 1, "transcribe": 1}


def test_offline_pipeline_failure_reuses_intermediate_outputs_on_retry(
    offline_pipeline, monkeypatch,
) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "离线恢复.mp4"
    media.write_bytes(b"offline media fixture")

    monkeypatch.setattr(
        subtitle_translate,
        "_call_llm_api",
        lambda **kwargs: (_ for _ in ()).throw(
            TranslationReliabilityError("offline injected failure", kind="network_error")
        ),
    )
    first = make_pipeline().run()
    assert first["failed"] == 1

    completed = TaskState(
        "already-done.mp4",
        str(roots["input"] / "already-done.mp4"),
        status="completed",
        stage="completed",
    )
    completed.save()
    retry_plan = prepare_retry_failed_tasks(sorted(roots["states"].glob("*.state.json")))
    assert retry_plan.selected_task_ids == [media.name]
    assert retry_plan.untouched_count == 1

    monkeypatch.setattr(
        subtitle_translate,
        "_call_llm_api",
        lambda **kwargs: _openai_response(kwargs["body"]),
    )
    second = make_pipeline().run()
    recovered = TaskState.load(roots["states"] / f"{media.stem}.state.json")

    assert second["completed"] == 1
    assert recovered is not None and recovered.status == "completed"
    assert calls == {"extract": 1, "transcribe": 1}
    assert (roots["output"] / "reports" / f"{media.stem}.offline-stub.quality_report.json").is_file()
