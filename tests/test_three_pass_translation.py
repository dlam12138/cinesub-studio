from __future__ import annotations

import json

import pytest

import subtitle_translate
import job_api


def _openai(content: dict) -> str:
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]},
        ensure_ascii=False,
    )


def _source(path) -> None:
    path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello, Alice.\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\nGood evening.\n\n",
        encoding="utf-8",
    )


def _kwargs(source, output) -> dict:
    return {
        "input_path": source,
        "output_path": output,
        "api_provider": "openai-compatible",
        "api_base": "https://example.invalid",
        "api_key": "test-key",
        "llm_model": "fast-model",
        "translation_quality_model": "quality-model",
        "target_language": "zh-CN",
        "batch_size": 10,
        "temperature": 0.2,
        "translation_mode": "bilingual",
        "context_window": 1,
        "translation_strategy_mode": "three_pass",
        "scene_gap_seconds": 30,
    }


def test_three_pass_preserves_ids_times_and_resumes_from_stage_cache(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "source.srt"
    output = tmp_path / "translated.srt"
    second = tmp_path / "translated-second.srt"
    _source(source)
    responses = [
        _openai({
            "items": [
                {"id": 1, "translation": "你好，爱丽丝。"},
                {"id": 2, "translation": "晚上好。"},
            ]
        }),
        _openai({
            "issues": [
                {"id": 1, "issues": []},
                {"id": 2, "issues": ["语气可更自然"]},
            ],
            "scene_summary": "Alice is greeted in the evening.",
        }),
        _openai({
            "items": [
                {"id": 1, "translation": "你好，爱丽丝。"},
                {"id": 2, "translation": "晚上好。"},
            ],
            "scene_summary": "Alice is greeted in the evening.",
        }),
    ]
    calls: list[dict] = []

    def fake_call(**kwargs):
        kwargs["tracker"].before_request(extra=kwargs.get("request_is_extra", False))
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_call)
    summary = subtitle_translate.translate_srt(**_kwargs(source, output))
    assert len(calls) == 3
    assert summary.initial_pass_requests == 1
    assert summary.reflection_pass_requests == 1
    assert summary.final_pass_requests == 1
    written = output.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:02,000" in written
    assert "00:00:02,500 --> 00:00:04,000" in written
    assert "你好，爱丽丝。" in written

    monkeypatch.setattr(
        subtitle_translate,
        "_call_llm_api",
        lambda **_kwargs: pytest.fail("completed three-pass cache should be reused"),
    )
    resumed = subtitle_translate.translate_srt(**_kwargs(source, second))
    assert resumed.cache_hits == 2
    assert resumed.three_pass_cached_batches == 1
    assert second.read_text(encoding="utf-8") == written


def test_three_pass_failure_keeps_existing_complete_output(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.srt"
    output = tmp_path / "translated.srt"
    _source(source)
    output.write_text("existing complete subtitle", encoding="utf-8")
    responses = [
        _openai({
            "items": [
                {"id": 1, "translation": "你好，爱丽丝。"},
                {"id": 2, "translation": "晚上好。"},
            ]
        }),
        _openai({"issues": [{"id": 1, "issues": []}]}),
        _openai({"issues": [{"id": 1, "issues": []}]}),
    ]
    calls = 0

    def fake_call(**_kwargs):
        nonlocal calls
        value = responses[calls]
        calls += 1
        return value

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_call)
    with pytest.raises(RuntimeError, match="reflection"):
        subtitle_translate.translate_srt(**_kwargs(source, output))
    assert output.read_text(encoding="utf-8") == "existing complete subtitle"


def test_three_pass_retries_final_when_ids_do_not_match(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.srt"
    output = tmp_path / "translated.srt"
    _source(source)
    responses = [
        _openai({"items": [
            {"id": 1, "translation": "你好，爱丽丝。"},
            {"id": 2, "translation": "晚上好。"},
        ]}),
        _openai({
            "issues": [{"id": 1, "issues": []}, {"id": 2, "issues": []}],
            "scene_summary": "A greeting.",
        }),
        _openai({"items": [{"id": 1, "translation": "你好，爱丽丝。"}]}),
        _openai({"items": [
            {"id": 1, "translation": "你好，爱丽丝。"},
            {"id": 2, "translation": "晚上好。"},
        ], "scene_summary": "A greeting."}),
    ]
    calls = 0

    def fake_call(**kwargs):
        nonlocal calls
        kwargs["tracker"].before_request(extra=kwargs.get("request_is_extra", False))
        value = responses[calls]
        calls += 1
        return value

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_call)
    summary = subtitle_translate.translate_srt(**_kwargs(source, output))
    assert calls == 4
    assert summary.final_pass_requests == 2
    assert "晚上好。" in output.read_text(encoding="utf-8")


def test_three_pass_enforces_no_issue_stability_and_compresses_over_budget(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "budget-source.srt"
    output = tmp_path / "budget-translated.srt"
    _source(source)
    responses = [
        _openai({"items": [
            {"id": 1, "translation": "你好，爱丽丝。"},
            {"id": 2, "translation": "晚上好。"},
        ]}),
        _openai({
            "issues": [
                {"id": 1, "issues": ["超出字符预算"]},
                {"id": 2, "issues": []},
            ],
            "scene_summary": "A greeting.",
        }),
        _openai({"items": [
            {"id": 1, "translation": "你好，亲爱的爱丽丝。"},
            {"id": 2, "translation": "祝你晚上愉快。"},
        ], "scene_summary": "A greeting."}),
        _openai({"items": [
            {"id": 1, "translation": "你好Alice"},
        ]}),
    ]
    calls = 0

    def fake_call(**kwargs):
        nonlocal calls
        kwargs["tracker"].before_request(extra=kwargs.get("request_is_extra", False))
        value = responses[calls]
        calls += 1
        return value

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_call)
    kwargs = _kwargs(source, output)
    kwargs["max_cps_zh"] = 4
    kwargs["max_chars_per_subtitle_zh"] = 10
    summary = subtitle_translate.translate_srt(**kwargs)

    assert calls == 4
    assert summary.compression_pass_requests == 1
    written = output.read_text(encoding="utf-8")
    assert "你好Alice" in written
    assert "祝你晚上愉快" not in written
    assert "晚上好。" in written


def test_web_progress_prefers_latest_three_pass_stage(monkeypatch) -> None:
    monkeypatch.setitem(
        job_api.JOBS,
        "three-pass-job",
        {"status": "running", "stage": "transcribing", "progress": 30},
    )
    job_api._infer_stage_from_logs(
        "three-pass-job",
        [
            "Translation stage: initial (1/1)",
            "Translation stage: reflection (1/1)",
            "Translation stage: final (1/1)",
        ],
    )
    assert job_api.JOBS["three-pass-job"]["stage"] == "translation_final"
    assert job_api.JOBS["three-pass-job"]["progress"] == 80
