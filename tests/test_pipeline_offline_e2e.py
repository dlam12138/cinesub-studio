from __future__ import annotations

import json
from pathlib import Path

import batch_worker
import pytest
import subtitle_translate
from pipeline_stages import StageResult
from task_state import (
    TaskState,
    apply_retry_failed_plan,
    prepare_retry_failed_tasks,
    set_state_root_provider,
)
from pipeline_reliability import task_identity
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

    calls = {"extract": 0, "transcribe": 0, "translate": 0, "quality": 0}

    def fake_extract(context, **kwargs):
        calls["extract"] += 1
        audio = context.work_dir / f"{context.task_id}.16k.wav"
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

    def fake_translate_api(**kwargs):
        return _openai_response(kwargs["body"])

    real_translate_stage = batch_worker.translate_stage

    def fake_translate_stage(*args, **kwargs):
        calls["translate"] += 1
        return real_translate_stage(*args, **kwargs)

    def fake_quality_check(context, *, report_path, **kwargs):
        calls["quality"] += 1
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"status": "pass", "issues": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        return StageResult("quality_checking", "completed", (report_path,))

    monkeypatch.setattr(batch_worker, "extract_audio_stage", fake_extract)
    monkeypatch.setattr(batch_worker, "transcribe_stage", fake_transcribe)
    monkeypatch.setattr(batch_worker, "translate_stage", fake_translate_stage)
    monkeypatch.setattr(batch_worker, "quality_check_stage", fake_quality_check)
    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_translate_api)

    def make_pipeline(*, mode: str = "bilingual", **overrides) -> batch_worker.BatchPipeline:
        config = {
            "input_dir": roots["input"],
            "output_dir": roots["output"],
            "work_dir": roots["work"],
            "model_dir": roots["models"],
            "model": "offline-stub",
            "device": "cpu",
            "local_files_only": True,
            "asr_mode": "fixed",
            "language": "fr",
            "translate": True,
            "api_provider": "openai-compatible",
            "api_base": "https://offline.invalid",
            "api_key": "offline-test-only",
            "llm_model": "offline-llm-stub",
            "target_language": "zh-CN",
            "translation_mode": mode,
            "translation_reliability_mode": "off",
            "move_completed": False,
        }
        config.update(overrides)
        return batch_worker.BatchPipeline(batch_worker.BatchConfig(**config))

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
    task_id = task_identity(media, roots["input"])[0]
    state = TaskState.load(roots["states"] / f"{task_id}.state.json")
    events = [
        json.loads(line)
        for line in roots["events"].read_text(encoding="utf-8").splitlines()
    ]

    assert source.stat().st_size > 0
    assert translated.stat().st_size > 0
    if mode == "bilingual":
        translated_only = (
            roots["output"]
            / "zh"
            / f"{media.stem}.offline-stub.translated.zh-CN.srt"
        )
        assert translated_only.stat().st_size > 0
        assert state is not None
        assert Path(state.translated_srt) == translated_only.resolve()
        assert Path(state.bilingual_srt) == translated.resolve()
    assert report.stat().st_size > 0
    assert state is not None and state.status == "completed" and state.stage == "completed"
    assert {event["stage"] for event in events if event["event"] == "completed"} >= {
        "extracting_audio", "transcribing", "translating", "quality_checking",
    }
    assert calls == {"extract": 1, "transcribe": 1, "translate": 1, "quality": 1}


def test_offline_pipeline_failure_reuses_intermediate_outputs_on_retry(
    offline_pipeline, monkeypatch,
) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "离线恢复.mp4"
    media.write_bytes(b"offline media fixture")

    def fail_translate_api(**kwargs):
        raise TranslationReliabilityError("offline injected failure", kind="network_error")

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fail_translate_api)
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
    assert retry_plan.selected_task_ids == [task_identity(media, roots["input"])[0]]
    assert retry_plan.untouched_count == 1
    assert TaskState.load(retry_plan.selected_tasks[0].state_path()).status == "failed"
    apply_retry_failed_plan(retry_plan, run_id="retry-run")

    def recover_translate_api(**kwargs):
        return _openai_response(kwargs["body"])

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", recover_translate_api)
    second = make_pipeline().run()
    recovered = TaskState.load(
        roots["states"] / f"{task_identity(media, roots['input'])[0]}.state.json"
    )

    assert second["completed"] == 1
    assert recovered is not None and recovered.status == "completed"
    assert calls == {"extract": 1, "transcribe": 1, "translate": 2, "quality": 1}
    assert (roots["output"] / "reports" / f"{media.stem}.offline-stub.quality_report.json").is_file()


def test_offline_pipeline_second_run_skips_with_stage_signatures(offline_pipeline) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "stable.mp4"
    media.write_bytes(b"offline media fixture")

    first = make_pipeline().run()
    second = make_pipeline().run()

    assert first["completed"] == 1
    assert second == {"total": 1, "completed": 0, "failed": 0, "skipped": 1}
    assert calls == {"extract": 1, "transcribe": 1, "translate": 1, "quality": 1}
    state = TaskState.load(
        roots["states"] / f"{task_identity(media, roots['input'])[0]}.state.json"
    )
    assert state is not None
    assert state.stage_build_signatures.get("input")
    assert state.stage_build_signatures.get("final_output")
    assert state.artifact_fingerprints.get("final_output", {}).get("sha256")


def test_missing_final_output_fingerprint_rebuilds_only_finalization(offline_pipeline) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "final-contract.mp4"
    media.write_bytes(b"offline media fixture")
    pipeline = make_pipeline()
    assert pipeline.run()["completed"] == 1
    state_path = roots["states"] / f"{task_identity(media, roots['input'])[0]}.state.json"
    state = TaskState.load(state_path)
    assert state is not None
    state.artifact_fingerprints.pop("final_output")
    state.stage_build_signatures.pop("final_output")
    state.save()

    plan = batch_worker.build_pipeline_plan(pipeline.config)

    assert plan.tasks[0].category == "rebuild"
    assert plan.tasks[0].rebuild_from == "final_output"
    assert make_pipeline().run()["completed"] == 1
    assert calls == {"extract": 1, "transcribe": 1, "translate": 1, "quality": 1}


@pytest.mark.parametrize(
    "override",
    [
        {"translation_batch_size": 8},
        {"translation_temperature": 0.0},
        {"context_window": 1},
        {"translation_max_extra_requests": 3},
        {"translation_scene_gap_seconds": 7.5},
    ],
)
def test_translation_signature_knobs_rebuild_from_translation(
    offline_pipeline, override: dict,
) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "translation-knob.mp4"
    media.write_bytes(b"offline media fixture")

    assert make_pipeline().run()["completed"] == 1
    changed = make_pipeline(**override)
    plan = batch_worker.build_pipeline_plan(changed.config)

    assert plan.tasks[0].category == "rebuild"
    assert plan.tasks[0].rebuild_from == "translation"
    assert changed.run()["completed"] == 1
    assert calls == {"extract": 1, "transcribe": 1, "translate": 2, "quality": 2}


def test_quality_only_profile_threshold_rebuilds_from_quality(
    offline_pipeline,
) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "quality-only.mp4"
    media.write_bytes(b"offline media fixture")
    baseline_profile = {
        "quality_thresholds": {"language_probability_warning": 0.80},
        "translation_style": "film style",
        "glossary": [{"source": "Jean", "target": "让", "note": ""}],
    }
    changed_profile = {
        **baseline_profile,
        "quality_thresholds": {"language_probability_warning": 0.95},
    }

    assert make_pipeline(lang_profile_config=baseline_profile).run()["completed"] == 1
    changed = make_pipeline(lang_profile_config=changed_profile)
    plan = batch_worker.build_pipeline_plan(changed.config)

    assert plan.tasks[0].category == "rebuild"
    assert plan.tasks[0].rebuild_from == "quality"
    assert changed.run()["completed"] == 1
    assert calls == {"extract": 1, "transcribe": 1, "translate": 1, "quality": 2}


def test_translation_length_threshold_still_rebuilds_from_translation(
    offline_pipeline,
) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "translation-length-threshold.mp4"
    media.write_bytes(b"offline media fixture")
    baseline_profile = {
        "quality_thresholds": {
            "language_probability_warning": 0.80,
            "max_cps_zh": 8,
            "max_chars_per_subtitle_zh": 36,
        },
    }
    changed_profile = {
        "quality_thresholds": {
            "language_probability_warning": 0.80,
            "max_cps_zh": 6,
            "max_chars_per_subtitle_zh": 36,
        },
    }

    assert make_pipeline(lang_profile_config=baseline_profile).run()["completed"] == 1
    changed = make_pipeline(lang_profile_config=changed_profile)
    plan = batch_worker.build_pipeline_plan(changed.config)

    assert plan.tasks[0].category == "rebuild"
    assert plan.tasks[0].rebuild_from == "translation"
    assert changed.run()["completed"] == 1
    assert calls == {"extract": 1, "transcribe": 1, "translate": 2, "quality": 2}


def test_reserved_ass_request_does_not_invalidate_final_output(
    offline_pipeline,
) -> None:
    roots, calls, make_pipeline = offline_pipeline
    media = roots["input"] / "reserved-ass.mp4"
    media.write_bytes(b"offline media fixture")

    assert make_pipeline(subtitle_formats=["srt"]).run()["completed"] == 1
    changed = make_pipeline(subtitle_formats=["srt", "ass"])
    plan = batch_worker.build_pipeline_plan(changed.config)

    assert plan.tasks[0].category == "skip"
    assert plan.tasks[0].rebuild_from == ""
    assert changed.run() == {"total": 1, "completed": 0, "failed": 0, "skipped": 1}
    assert calls == {"extract": 1, "transcribe": 1, "translate": 1, "quality": 1}
