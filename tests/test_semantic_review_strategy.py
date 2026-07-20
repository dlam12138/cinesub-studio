from __future__ import annotations

import json

import pytest

import job_api
import semantic_review_strategy
import subtitle_translate


def _openai(content: dict) -> str:
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]},
        ensure_ascii=False,
    )


def _source(path) -> None:
    path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n"
        "puisqu'ils n'ont pas de langue pour le faire.\n\n"
        "2\n00:00:03,500 --> 00:00:06,000\n"
        "Ces témoignages de fans parlent du biberon.\n\n",
        encoding="utf-8",
    )


def _kwargs(source, output) -> dict:
    return {
        "input_path": source,
        "output_path": output,
        "api_provider": "openai-compatible",
        "api_base": "https://example.invalid",
        "api_key": "test-key",
        "llm_model": "flash-model",
        "translation_quality_model": "quality-model",
        "target_language": "zh-CN",
        "batch_size": 10,
        "temperature": 0.2,
        "translation_mode": "bilingual",
        "context_window": 3,
        "translation_strategy_mode": "semantic_review",
        "scene_gap_seconds": 30,
        "max_cps_zh": 20,
        "max_chars_per_subtitle_zh": 36,
    }


def _analysis(scene: bool) -> dict:
    payload = {
        "speakers": [],
        "typed_glossary": [{
            "source": "biberon",
            "target": "奶瓶",
            "type": "common_term",
            "confidence": "high",
            "note": "ordinary noun",
            "evidence_ids": [2],
        }],
        "suspected_asr_errors": [],
    }
    payload["scene_summary" if scene else "video_summary"] = "讨论语言与粉丝来信。"
    return payload


def test_semantic_review_repairs_only_proven_issue_and_resumes(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "source.srt"
    output = tmp_path / "semantic.srt"
    second = tmp_path / "semantic-second.srt"
    _source(source)
    calls: list[str] = []

    def fake_call(**kwargs):
        kwargs["tracker"].before_request(extra=kwargs.get("request_is_extra", False))
        body = json.loads(kwargs["body"])
        system = body["messages"][0]["content"]
        user = json.loads(body["messages"][1]["content"])
        calls.append(system)
        if "complete-video ASR transcript" in system:
            return _openai(_analysis(scene=True))
        if "Synthesize the supplied scene analyses" in system:
            return _openai(_analysis(scene=False))
        if "CONTEXTUAL INITIAL TRANSLATION" in system:
            return _openai({"items": [
                {"id": 1, "translation": "因为他们没有语言能力。"},
                {"id": 2, "translation": "这些粉丝来信谈到了奶瓶。"},
            ]})
        if "conservative fidelity reviewer" in system:
            return _openai({"items": [
                {"id": 1, "issues": [{
                    "type": "mistranslation",
                    "severity": "severe",
                    "confidence": "high",
                    "detail": "把不能用语言完成该动作泛化为没有语言能力",
                    "evidence": "原文限定为 pour le faire",
                    "suggestion": "因为他们不会用语言提问。",
                }]},
                {"id": 2, "issues": []},
            ]})
        if "TARGETED FIDELITY REPAIR" in system:
            return _openai({"items": [
                {"id": 1, "translation": "因为他们不会用语言提问。"},
            ]})
        if "Blindly compare two subtitle translations" in system:
            choice = "A" if "提问" in user["option_a"] else "B"
            return _openai({
                "id": 1,
                "choice": choice,
                "confidence": "high",
                "reason": "该选项保留了动作限定。",
            })
        if "Audit the complete final subtitle translation" in system:
            return _openai({"issues": [{
                "id": 2,
                "type": "terminology",
                "detail": "仅报告测试问题，不允许改写。",
                "related_ids": [],
            }]})
        raise AssertionError(system)

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_call)
    summary = subtitle_translate.translate_srt(**_kwargs(source, output))

    assert len(calls) == 7
    assert summary.semantic_analysis_requests == 2
    assert summary.semantic_review_requests == 1
    assert summary.semantic_repair_requests == 1
    assert summary.semantic_judge_requests == 1
    assert summary.semantic_consistency_requests == 1
    assert summary.semantic_repair_accepted_ids == [1]
    written = output.read_text(encoding="utf-8")
    assert "因为他们不会用语言提问。" in written
    assert "这些粉丝来信谈到了奶瓶。" in written
    review = output.with_name(f"{output.stem}.review_needed.srt")
    assert "仅报告测试问题" not in written
    assert "Ces témoignages de fans" in review.read_text(encoding="utf-8")
    report = json.loads(
        output.with_name(
            f"{output.stem}.semantic_review_report.json"
        ).read_text(encoding="utf-8")
    )
    assert report["final_sources"]["1"] == "repair"
    assert report["final_sources"]["2"] == "initial"
    assert report["consistency_issues"][0]["id"] == 2

    monkeypatch.setattr(
        subtitle_translate,
        "_call_llm_api",
        lambda **_kwargs: pytest.fail("semantic stage cache should be reused"),
    )
    resumed = subtitle_translate.translate_srt(**_kwargs(source, second))
    assert resumed.cache_hits == 2
    assert resumed.semantic_cached_batches == 1
    assert second.read_text(encoding="utf-8") == written


def test_semantic_analysis_failure_does_not_overwrite_complete_output(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "source.srt"
    output = tmp_path / "semantic.srt"
    _source(source)
    output.write_text("existing complete subtitle", encoding="utf-8")
    monkeypatch.setattr(
        subtitle_translate,
        "_call_llm_api",
        lambda **_kwargs: _openai({"scene_summary": "missing arrays"}),
    )
    with pytest.raises(RuntimeError, match="semantic_analysis_scene"):
        subtitle_translate.translate_srt(**_kwargs(source, output))
    assert output.read_text(encoding="utf-8") == "existing complete subtitle"


def test_semantic_validators_and_deterministic_formatting_are_strict() -> None:
    review = semantic_review_strategy.validate_semantic_review(
        {"items": [{"id": 1, "issues": []}]}, [1]
    )
    assert review == {1: []}
    with pytest.raises(RuntimeError, match="exactly match"):
        semantic_review_strategy.validate_semantic_review(
            {"items": [{"id": 1, "issues": []}]}, [1, 2]
        )
    assert semantic_review_strategy.deterministic_subtitle_format(
        "你好！！  世界", max_line_chars=4
    ).replace("\n", "") == "你好！ 世界"
    mapping = semantic_review_strategy.deterministic_ab_mapping("batch", 7)
    assert set(mapping.values()) == {"A", "B"}


def test_web_progress_reports_semantic_stages(monkeypatch) -> None:
    monkeypatch.setitem(
        job_api.JOBS,
        "semantic-job",
        {"status": "running", "stage": "transcribing", "progress": 30},
    )
    job_api._infer_stage_from_logs(
        "semantic-job",
        ["Translation stage: semantic_judge (id=1)"],
    )
    assert job_api.JOBS["semantic-job"]["stage"] == "semantic_judgment"
    assert job_api.JOBS["semantic-job"]["progress"] == 78
