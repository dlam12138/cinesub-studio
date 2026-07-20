from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest
import subtitle_translate
import web_server
from language_profile_store import resolve_language_profile_config
from pipeline_api import _build_background_command
from pipeline_cli import build_pipeline_parser
from subtitle_translate import SubtitleItem
from translation_reliability import (
    TranslationBudgetExceeded,
    TranslationReliabilityError,
    TranslationRequestTracker,
    TranslationRunSummary,
    TranslationTotalRequestLimitExceeded,
    adjacent_translation_overlap_count,
    blocking_translation_issues,
    build_repair_windows,
    normalize_reliability_config,
)


def _items() -> list[SubtitleItem]:
    return [
        SubtitleItem(1, "00:00:00,000 --> 00:00:01,000", "Bonjour"),
        SubtitleItem(2, "00:00:01,000 --> 00:00:02,000", "Bonsoir"),
    ]


def test_reliability_config_and_budget_are_bounded() -> None:
    assert normalize_reliability_config() == {"mode": "off", "max_extra_requests": 12}
    assert normalize_reliability_config("preview", max_extra_requests="3") == {
        "mode": "preview", "max_extra_requests": 3,
    }
    with pytest.raises(ValueError):
        normalize_reliability_config("apply")
    with pytest.raises(ValueError):
        normalize_reliability_config("preview", max_extra_requests=51)
    tracker = TranslationRequestTracker(mode="preview", max_extra_requests=1)
    tracker.before_request(extra=True)
    with pytest.raises(TranslationBudgetExceeded):
        tracker.before_request(extra=True)


def test_total_http_request_limit_is_hard_and_does_not_increment_past_cap() -> None:
    tracker = TranslationRequestTracker(mode="off", max_total_requests=2)
    tracker.before_request(extra=False)
    tracker.before_request(extra=True)
    assert tracker.actual_requests == 2
    assert tracker.total_request_limit_exhausted is True
    with pytest.raises(TranslationTotalRequestLimitExceeded):
        tracker.before_request(extra=False)
    assert tracker.actual_requests == 2


def test_preview_cache_key_includes_quality_strategy_without_changing_off_key(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nBonjour\n", encoding="utf-8")
    common = {
        "api_provider": "openai-compatible",
        "llm_model": "flash",
        "target_language": "zh-CN",
        "translation_mode": "bilingual",
        "effective_prompt": "prompt",
    }
    legacy_off = subtitle_translate._translation_cache_path(source, **common)
    explicit_off = subtitle_translate._translation_cache_path(
        source,
        reliability_mode="off",
        translation_quality_model="quality-a",
        **common,
    )
    preview_a = subtitle_translate._translation_cache_path(
        source,
        reliability_mode="preview",
        translation_quality_model="quality-a",
        **common,
    )
    preview_b = subtitle_translate._translation_cache_path(
        source,
        reliability_mode="preview",
        translation_quality_model="quality-b",
        **common,
    )
    assert explicit_off == legacy_off
    assert preview_a != legacy_off
    assert preview_a != preview_b


@pytest.mark.parametrize(
    ("source", "translation", "expected"),
    [
        ("hello", "", "empty_translation"),
        ("hello", "Here is the translation: hello", "llm_boilerplate"),
        ("Hello!", "hello", "identical_translation"),
        ("hello", "これはテスト", "possibly_untranslated"),
    ],
)
def test_blocking_rules_cover_only_repairable_text_issues(
    source: str, translation: str, expected: str,
) -> None:
    assert expected in blocking_translation_issues(source, translation, "zh-CN")
    assert not blocking_translation_issues("Alice", "爱丽丝 Alice", "zh-CN")


def test_adjacent_overlap_detector_is_conservative() -> None:
    assert adjacent_translation_overlap_count(["我们需要更多合作", "需要更多合作"]) == 1
    assert adjacent_translation_overlap_count(["好的", "好的"]) == 0
    assert adjacent_translation_overlap_count(["第一句", "第二句"]) == 0


def test_repair_windows_merge_adjacent_blockers_and_cover_edges() -> None:
    assert build_repair_windows(5, {0: ("empty_translation",)}) == [
        (0, 2, (0,))
    ]
    assert build_repair_windows(
        5, {1: ("identical_translation",), 3: ("empty_translation",)}
    ) == [(0, 5, (1, 3))]
    with pytest.raises(ValueError):
        build_repair_windows(2, {2: ("empty_translation",)})


def test_adaptive_split_persists_first_child_before_later_failure(monkeypatch) -> None:
    calls: list[tuple[int, ...]] = []
    persisted: list[dict[int, str]] = []

    def fake_translate(**kwargs):
        ids = tuple(kwargs["expected_ids"])
        calls.append(ids)
        if ids == (1, 2):
            raise TranslationReliabilityError("bad structure", kind="structured_output", splittable=True)
        if ids == (2,):
            raise TranslationReliabilityError("still bad", kind="structured_output", splittable=True)
        return {1: "你好"}

    monkeypatch.setattr(subtitle_translate, "_translate_batch_with_structured_retry", fake_translate)
    with pytest.raises(TranslationReliabilityError):
        subtitle_translate._translate_batch_adaptive(
            batch={"items": [{"id": 1, "text": "Bonjour"}, {"id": 2, "text": "Bonsoir"}]},
            expected_ids=[1, 2], batch_index=1, total_batches=1,
            effective_prompt="", llm_model="model", temperature=0.2,
            api_provider="openai-compatible", api_base="https://example.invalid", api_key="secret",
            context_window=0,
            tracker=TranslationRequestTracker(mode="preview", max_extra_requests=5),
            summary=TranslationRunSummary(mode="preview", total_items=2),
            persist=lambda value: persisted.append(dict(value)),
        )
    assert calls == [(1, 2), (1,), (2,)]
    assert persisted == [{1: "你好"}]


def test_default_off_does_not_split(monkeypatch) -> None:
    def fail(**kwargs):
        raise TranslationReliabilityError("bad", kind="structured_output", splittable=True)

    monkeypatch.setattr(subtitle_translate, "_translate_batch_with_structured_retry", fail)
    summary = TranslationRunSummary(mode="off", total_items=2)
    with pytest.raises(TranslationReliabilityError):
        subtitle_translate._translate_batch_adaptive(
            batch={"items": [{"id": 1}, {"id": 2}]}, expected_ids=[1, 2],
            batch_index=1, total_batches=1, effective_prompt="", llm_model="m", temperature=0.2,
            api_provider="openai-compatible", api_base="", api_key="",
            context_window=0, tracker=TranslationRequestTracker(mode="off"), summary=summary,
            persist=lambda value: None,
        )
    assert summary.split_count == 0


def test_failed_preview_preserves_existing_final_output(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.srt"
    output = tmp_path / "translated.srt"
    subtitle_translate.write_srt(_items(), source)
    output.write_text("previous complete subtitle", encoding="utf-8")

    def fail(**kwargs):
        raise TranslationReliabilityError("single failed", kind="structured_output")

    monkeypatch.setattr(subtitle_translate, "_translate_batch_adaptive", fail)
    with pytest.raises(TranslationReliabilityError):
        subtitle_translate.translate_srt(
            input_path=source, output_path=output, api_provider="openai-compatible",
            api_base="https://example.invalid", api_key="secret", llm_model="model",
            target_language="zh-CN", batch_size=2, temperature=0.2,
            translation_mode="bilingual", reliability_mode="preview",
        )
    assert output.read_text(encoding="utf-8") == "previous complete subtitle"


@pytest.mark.parametrize(
    ("status", "kind", "expected_requests"),
    [(401, "authentication", 1), (429, "rate_limited", 3), (503, "server_error", 3)],
)
def test_http_errors_are_classified_and_only_preview_retries(
    monkeypatch, status: int, kind: str, expected_requests: int,
) -> None:
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid", status, "failed", {}, io.BytesIO(b'{"error":"safe"}')
        )

    monkeypatch.setattr(subtitle_translate.urllib.request, "urlopen", fail)
    monkeypatch.setattr(subtitle_translate.time, "sleep", lambda seconds: None)
    tracker = TranslationRequestTracker(mode="preview", max_extra_requests=10)
    with pytest.raises(TranslationReliabilityError) as raised:
        subtitle_translate._call_llm_api(
            api_provider="openai-compatible", api_base="https://example.invalid",
            api_key="secret", body=json.dumps({}), tracker=tracker,
        )
    assert raised.value.kind == kind
    assert tracker.actual_requests == expected_requests


def test_off_mode_429_has_no_new_bounded_retry(monkeypatch) -> None:
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            "https://example.invalid", 429, "limited", {}, io.BytesIO(b"limited")
        )

    monkeypatch.setattr(subtitle_translate.urllib.request, "urlopen", fail)
    with pytest.raises(TranslationReliabilityError):
        subtitle_translate._call_llm_api(
            api_provider="openai-compatible", api_base="https://example.invalid",
            api_key="secret", body="{}", tracker=TranslationRequestTracker(mode="off"),
        )
    assert calls == 1


def test_repair_accepts_only_clean_candidate_and_writes_cache(monkeypatch, tmp_path: Path) -> None:
    items = _items()
    items[0].translation = "Here is the translation: hello"
    items[1].translation = "晚上好"
    calls = 0

    def respond(**kwargs):
        nonlocal calls
        calls += 1
        request = json.loads(kwargs["body"])
        payload = json.loads(request["messages"][1]["content"])
        prompt = request["messages"][0]["content"]
        assert "one continuous passage" in prompt
        assert "Every item must be non-empty" in prompt
        assert "do not duplicate meaning" in prompt
        assert request["temperature"] == 0.0
        assert payload["target_language"] == "zh-CN"
        assert payload["requested_items"] == [
            {
                "id": 1,
                "source_text": "Bonjour",
                "existing_translation": "Here is the translation: hello",
            },
            {
                "id": 2,
                "source_text": "Bonsoir",
                "existing_translation": "晚上好",
            },
        ]
        return json.dumps({"choices": [{"message": {"content": json.dumps({
            "items": [{"id": 1, "text": "你好"}, {"id": 2, "text": "晚上好"}]
        }, ensure_ascii=False)}}]})

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", respond)
    cache_path = tmp_path / "cache.json"
    summary = TranslationRunSummary(mode="preview", total_items=2)
    subtitle_translate._repair_blocking_translations(
        items=items, cached_translations={1: items[0].translation, 2: items[1].translation},
        cache_path=cache_path, target_language="zh-CN", effective_prompt="",
        llm_model="model", temperature=0.2, api_provider="openai-compatible",
        api_base="https://example.invalid", api_key="secret", context_window=1,
        tracker=TranslationRequestTracker(mode="preview", max_extra_requests=2),
        summary=summary, progress_callback=None,
    )
    assert calls == 1
    assert items[0].translation == "你好"
    assert summary.repaired_ids == [1, 2]
    assert summary.unresolved_ids == []
    assert summary.repair_windows_attempted == 1
    assert summary.repair_windows_accepted == 1
    assert json.loads(cache_path.read_text(encoding="utf-8"))["translations"]["1"] == "你好"


def test_repair_attempts_each_id_once_and_keeps_failed_text(monkeypatch, tmp_path: Path) -> None:
    items = _items()
    items[0].translation = "Bonjour"
    items[1].translation = "晚上好"
    calls = 0

    def respond(**kwargs):
        nonlocal calls
        calls += 1
        return json.dumps({"choices": [{"message": {"content": '{"items":[]}'}}]})

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", respond)
    summary = TranslationRunSummary(mode="preview", total_items=2)
    subtitle_translate._repair_blocking_translations(
        items=items, cached_translations={1: "Bonjour", 2: "晚上好"},
        cache_path=tmp_path / "cache.json", target_language="zh-CN", effective_prompt="",
        llm_model="model", temperature=0.2, api_provider="openai-compatible",
        api_base="https://example.invalid", api_key="secret", context_window=0,
        tracker=TranslationRequestTracker(mode="preview", max_extra_requests=2),
        summary=summary, progress_callback=None,
    )
    assert calls == 1
    assert items[0].translation == "Bonjour"
    assert summary.unresolved_ids == [1]
    assert summary.repair_windows_rejected == 1


def test_window_repair_rejects_new_adjacent_duplication_atomically(
    monkeypatch, tmp_path: Path,
) -> None:
    items = [
        SubtitleItem(1, "00:00:00,000 --> 00:00:01,000", "Face aux technologies"),
        SubtitleItem(2, "00:00:01,000 --> 00:00:02,000", "au climat"),
        SubtitleItem(3, "00:00:02,000 --> 00:00:03,000", "il faut coopérer"),
    ]
    items[0].translation = "面对技术变革"
    items[1].translation = "面对气候变化"
    items[2].translation = "il faut coopérer"
    original = [item.translation for item in items]
    cache = {item.index: item.translation for item in items}

    def respond(**kwargs):
        content = json.dumps({
            "items": [
                {"id": 2, "text": "需要更多合作"},
                {"id": 3, "text": "我们需要更多合作"},
            ]
        }, ensure_ascii=False)
        return json.dumps({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", respond)
    summary = TranslationRunSummary(mode="preview", total_items=3)
    subtitle_translate._repair_blocking_translations(
        items=items, cached_translations=cache, cache_path=tmp_path / "cache.json",
        target_language="zh-CN", effective_prompt="", llm_model="model", temperature=0.2,
        api_provider="openai-compatible", api_base="https://example.invalid", api_key="secret",
        context_window=1, tracker=TranslationRequestTracker(mode="preview", max_extra_requests=2),
        summary=summary, progress_callback=None,
    )
    assert [item.translation for item in items] == original
    assert cache == dict(zip((1, 2, 3), original, strict=True))
    assert summary.repair_windows_rejected == 1
    assert summary.adjacent_overlap_rejections == 1
    assert summary.unresolved_ids == [3]


def test_window_repair_keeps_items_and_cache_when_atomic_cache_write_fails(
    monkeypatch, tmp_path: Path,
) -> None:
    items = _items()
    items[0].translation = "Bonjour"
    items[1].translation = "晚上好"
    cache = {1: "Bonjour", 2: "晚上好"}

    def respond(**kwargs):
        content = '{"items":[{"id":1,"text":"你好"},{"id":2,"text":"晚上好"}]}'
        return json.dumps({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", respond)
    monkeypatch.setattr(
        subtitle_translate,
        "_save_translation_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    summary = TranslationRunSummary(mode="preview", total_items=2)
    subtitle_translate._repair_blocking_translations(
        items=items, cached_translations=cache, cache_path=tmp_path / "cache.json",
        target_language="zh-CN", effective_prompt="", llm_model="model", temperature=0.2,
        api_provider="openai-compatible", api_base="https://example.invalid", api_key="secret",
        context_window=1, tracker=TranslationRequestTracker(mode="preview", max_extra_requests=2),
        summary=summary, progress_callback=None,
    )
    assert items[0].translation == "Bonjour"
    assert cache == {1: "Bonjour", 2: "晚上好"}
    assert summary.unresolved_ids == [1]
    assert summary.repair_windows_rejected == 1


@pytest.mark.parametrize(
    "content",
    [
        '{"items":[{"id":1,"text":"你好"}]}',
        '{"items":[{"id":1,"text":"你好"},{"id":2,"text":"晚上好"},{"id":99,"text":"额外"}]}',
        '{"items":[{"id":1,"text":"你好"},{"id":1,"text":"重复"},{"id":2,"text":"晚上好"}]}',
    ],
)
def test_window_repair_requires_exact_ids(monkeypatch, tmp_path: Path, content: str) -> None:
    items = _items()
    items[0].translation = "Bonjour"
    items[1].translation = "晚上好"
    monkeypatch.setattr(
        subtitle_translate,
        "_call_llm_api",
        lambda **kwargs: json.dumps({"choices": [{"message": {"content": content}}]}),
    )
    summary = TranslationRunSummary(mode="preview", total_items=2)
    subtitle_translate._repair_blocking_translations(
        items=items, cached_translations={1: "Bonjour", 2: "晚上好"},
        cache_path=tmp_path / "cache.json", target_language="zh-CN", effective_prompt="",
        llm_model="model", temperature=0.2, api_provider="openai-compatible",
        api_base="https://example.invalid", api_key="secret", context_window=1,
        tracker=TranslationRequestTracker(mode="preview", max_extra_requests=2),
        summary=summary, progress_callback=None,
    )
    assert items[0].translation == "Bonjour"
    assert summary.unresolved_ids == [1]
    assert summary.repair_windows_rejected == 1


def test_window_repair_budget_zero_marks_blocker_without_request(
    monkeypatch, tmp_path: Path,
) -> None:
    items = _items()
    items[0].translation = "Bonjour"
    items[1].translation = "晚上好"
    monkeypatch.setattr(
        subtitle_translate,
        "_call_llm_api",
        lambda **kwargs: pytest.fail("budget-exhausted repair must not call the LLM"),
    )
    summary = TranslationRunSummary(mode="preview", total_items=2)
    subtitle_translate._repair_blocking_translations(
        items=items, cached_translations={1: "Bonjour", 2: "晚上好"},
        cache_path=tmp_path / "cache.json", target_language="zh-CN", effective_prompt="",
        llm_model="model", temperature=0.2, api_provider="openai-compatible",
        api_base="https://example.invalid", api_key="secret", context_window=1,
        tracker=TranslationRequestTracker(mode="preview", max_extra_requests=0),
        summary=summary, progress_callback=None,
    )
    assert summary.unresolved_ids == [1]
    assert summary.repair_windows_attempted == 0


def _openai_response(payload: dict) -> str:
    return json.dumps({
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    })


def _run_quality_chain(
    monkeypatch,
    tmp_path: Path,
    responses,
    *,
    max_extra_requests: int = 8,
) -> tuple[list[SubtitleItem], TranslationRunSummary, list[str]]:
    items = _items()
    items[0].translation = "Bonjour"
    items[1].translation = "晚上好"
    models: list[str] = []
    iterator = iter(responses)

    def respond(**kwargs):
        body = json.loads(kwargs["body"])
        models.append(body["model"])
        kwargs["tracker"].before_request(extra=kwargs.get("request_is_extra", False))
        value = next(iterator)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(subtitle_translate, "_call_llm_api", respond)
    summary = TranslationRunSummary(mode="preview", total_items=2)
    subtitle_translate._repair_blocking_translations(
        items=items,
        cached_translations={1: "Bonjour", 2: "晚上好"},
        cache_path=tmp_path / "cache.json",
        target_language="zh-CN",
        effective_prompt="",
        llm_model="flash-model",
        translation_quality_model="quality-model",
        temperature=0.2,
        api_provider="openai-compatible",
        api_base="https://example.invalid",
        api_key="secret",
        context_window=1,
        tracker=TranslationRequestTracker(
            mode="preview", max_extra_requests=max_extra_requests
        ),
        summary=summary,
        progress_callback=None,
    )
    return items, summary, models


def test_quality_chain_selects_quality_candidate_after_clean_flash(
    monkeypatch, tmp_path: Path,
) -> None:
    items, summary, models = _run_quality_chain(
        monkeypatch,
        tmp_path,
        [
            _openai_response({"items": [{"id": 1, "text": "你好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response({"items": [{"id": 1, "text": "您好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response({"decision": "accept", "candidate": "quality", "issues": []}),
        ],
    )
    assert [item.translation for item in items] == ["您好", "晚上好"]
    assert models == ["flash-model", "quality-model", "quality-model"]
    assert summary.flash_initial_requests == 1
    assert summary.quality_candidate_requests == 1
    assert summary.judge_requests == 1
    assert summary.repair_windows_accepted == 1


def test_quality_chain_corrects_source_echo_before_judgement(
    monkeypatch, tmp_path: Path,
) -> None:
    items, summary, _ = _run_quality_chain(
        monkeypatch,
        tmp_path,
        [
            _openai_response({"items": [{"id": 1, "text": "Bonjour"}, {"id": 2, "text": "Bonsoir"}]}),
            _openai_response({"items": [{"id": 1, "text": "你好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response({"items": [{"id": 1, "text": "您好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response({
                "decision": "accept", "candidate": "flash_corrected", "issues": []
            }),
        ],
    )
    assert items[0].translation == "你好"
    assert summary.flash_correction_requests == 1
    assert summary.candidate_stage_rejection_counts == {"flash_initial": 1}
    assert summary.rejected_candidate_issue_counts["identical_translation"] >= 1


def test_quality_chain_uses_quality_candidate_when_flash_attempts_fail(
    monkeypatch, tmp_path: Path,
) -> None:
    items, summary, _ = _run_quality_chain(
        monkeypatch,
        tmp_path,
        [
            _openai_response({"items": []}),
            _openai_response({"items": []}),
            _openai_response({"items": [{"id": 1, "text": "您好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response({"decision": "accept", "candidate": "quality", "issues": []}),
        ],
    )
    assert items[0].translation == "您好"
    assert summary.flash_correction_requests == 1
    assert summary.repair_windows_accepted == 1


@pytest.mark.parametrize(
    "judge_payload",
    [
        {"decision": "reject", "candidate": "", "issues": ["semantic_omission"]},
        {"unexpected": True},
    ],
)
def test_quality_chain_rejects_window_when_judge_does_not_accept(
    monkeypatch, tmp_path: Path, judge_payload: dict,
) -> None:
    items, summary, _ = _run_quality_chain(
        monkeypatch,
        tmp_path,
        [
            _openai_response({"items": [{"id": 1, "text": "你好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response({"items": [{"id": 1, "text": "您好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response(judge_payload),
        ],
    )
    assert items[0].translation == "Bonjour"
    assert summary.repair_windows_rejected == 1
    assert summary.unresolved_ids == [1]
    assert summary.judge_rejection_counts


def test_quality_chain_budget_exhaustion_never_accepts_unjudged_candidate(
    monkeypatch, tmp_path: Path,
) -> None:
    items, summary, _ = _run_quality_chain(
        monkeypatch,
        tmp_path,
        [
            _openai_response({"items": [{"id": 1, "text": "你好"}, {"id": 2, "text": "晚上好"}]}),
            _openai_response({"items": [{"id": 1, "text": "您好"}, {"id": 2, "text": "晚上好"}]}),
        ],
        max_extra_requests=2,
    )
    assert items[0].translation == "Bonjour"
    assert summary.repair_windows_rejected == 1
    assert summary.judge_rejection_counts["budget_exhausted"] == 1


def test_quality_chain_stops_when_quality_model_is_unavailable(
    monkeypatch, tmp_path: Path,
) -> None:
    unavailable = TranslationReliabilityError(
        "not found", kind="http_error", status=404
    )
    items, summary, models = _run_quality_chain(
        monkeypatch,
        tmp_path,
        [
            _openai_response({"items": [{"id": 1, "text": "你好"}, {"id": 2, "text": "晚上好"}]}),
            unavailable,
        ],
    )
    assert items[0].translation == "Bonjour"
    assert models == ["flash-model", "quality-model"]
    assert summary.quality_model_unavailable is True
    assert summary.judge_requests == 0
    assert summary.repair_window_rejection_counts["quality_model_unavailable"] == 1


def test_safe_summary_exposes_counts_without_cue_ids() -> None:
    summary = TranslationRunSummary(
        mode="preview",
        total_items=3,
        repaired_ids=[1, 2],
        unresolved_ids=[3],
        repair_windows_attempted=2,
        repair_windows_accepted=1,
        repair_windows_rejected=1,
    )
    safe = summary.safe_summary()
    assert safe["repaired_count"] == 2
    assert safe["unresolved_count"] == 1
    assert safe["repair_windows_accepted"] == 1
    assert "repaired_ids" not in safe
    assert "unresolved_ids" not in safe


def test_corrupt_cache_is_ignored(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    cache.write_text('{"translations": [', encoding="utf-8")
    assert subtitle_translate._load_translation_cache(cache) == {}
    cache.write_text('{"translations": []}', encoding="utf-8")
    assert subtitle_translate._load_translation_cache(cache) == {}


def test_language_profile_defaults_reliability_to_off() -> None:
    resolved = resolve_language_profile_config("auto-detect")
    assert resolved["translation_reliability"] == {
        "mode": "off", "max_extra_requests": 12,
    }


def test_pipeline_cli_accepts_preview_settings() -> None:
    args = build_pipeline_parser().parse_args([
        "--translation-reliability-mode", "preview",
        "--translation-max-extra-requests", "7",
        "--translation-quality-model", "quality-model",
    ])
    assert args.translation_reliability_mode == "preview"
    assert args.translation_max_extra_requests == 7
    assert args.translation_quality_model == "quality-model"


def test_web_pipeline_command_forwards_explicit_reliability(monkeypatch) -> None:
    monkeypatch.setattr("pipeline_api._active_provider_id", lambda: "")
    monkeypatch.setattr("pipeline_api._active_language_profile_id", lambda: "")
    command = _build_background_command(
        action="run", provider_id="", language_profile_id="", input_dir="input",
        model="small", device="auto", compute_type="", translate_enabled=True,
        language="", local_files_only=True, subtitle_formats=["srt"], ass_style_id="default",
        translation_reliability_mode="preview", translation_max_extra_requests=6,
    )
    assert command[command.index("--translation-reliability-mode") + 1] == "preview"
    assert command[command.index("--translation-max-extra-requests") + 1] == "6"


def test_web_payload_omission_preserves_profile_and_invalid_mode_is_rejected() -> None:
    assert web_server._parse_translation_reliability_payload({}) == {}
    assert web_server._parse_translation_reliability_payload({
        "translation_reliability_mode": "preview",
        "translation_max_extra_requests": 5,
    }) == {
        "translation_reliability_mode": "preview",
        "translation_max_extra_requests": 5,
    }
    with pytest.raises(ValueError):
        web_server._parse_translation_reliability_payload({
            "translation_reliability_mode": "apply",
        })
