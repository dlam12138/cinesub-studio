from __future__ import annotations

import json

import pytest

import subtitle_translate
from subtitle_translate import _extract_translations, translate_srt


def _openai_response(content: str) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False)


def test_extract_translations_accepts_deepseek_json_with_line_comments():
    raw = """{
      "items": [
        {"id": 1, "text": "第一句"}, // model note that should not be emitted
        {"id": 2, "text": "第二句"}
      ]
    }"""

    assert _extract_translations(raw, expected_ids=[1, 2]) == {1: "第一句", 2: "第二句"}


def test_translate_srt_retries_once_for_incomplete_structured_output(monkeypatch, tmp_path):
    source = tmp_path / "source.srt"
    output = tmp_path / "translated.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nBonsoir.\n\n",
        encoding="utf-8",
    )

    calls: list[str] = []
    responses = [
        _openai_response('{"items":[{"id":1,"text":"你好。"}]}'),
        _openai_response('{"items":[{"id":1,"text":"你好。"},{"id":2,"text":"晚上好。"}]}'),
    ]

    def fake_call_llm_api(**kwargs):
        calls.append(kwargs["body"])
        return responses[len(calls) - 1]

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_call_llm_api)

    translate_srt(
        input_path=source,
        output_path=output,
        api_provider="openai-compatible",
        api_base="https://example.invalid",
        api_key="test-key",
        llm_model="test-model",
        target_language="zh-CN",
        batch_size=2,
        temperature=0.2,
        translation_mode="bilingual",
        context_window=0,
    )

    written = output.read_text(encoding="utf-8")
    assert len(calls) == 2
    assert "STRICT JSON RETRY" in calls[1]
    assert "你好。" in written
    assert "晚上好。" in written


def test_translate_srt_reports_provider_structured_output_after_retry(monkeypatch, tmp_path):
    source = tmp_path / "source.srt"
    output = tmp_path / "translated.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nBonsoir.\n\n",
        encoding="utf-8",
    )

    def fake_call_llm_api(**kwargs):
        return _openai_response('{"items":[{"id":1,"text":"你好。"}]}')

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", fake_call_llm_api)

    with pytest.raises(RuntimeError, match="Provider returned incomplete structured output"):
        translate_srt(
            input_path=source,
            output_path=output,
            api_provider="openai-compatible",
            api_base="https://example.invalid",
            api_key="test-key",
            llm_model="test-model",
            target_language="zh-CN",
            batch_size=2,
            temperature=0.2,
            translation_mode="bilingual",
            context_window=0,
        )

    assert not output.exists()
