from dataclasses import dataclass
from pathlib import Path

import pytest

from wenyi_subtitle_strategy import (
    WENYI_STRATEGY_VERSION,
    assert_immutable,
    cache_fingerprint,
    detect_cross_line_windows,
    immutable_records,
    merge_profile_glossary,
    relevant_glossary,
    resolve_model_tier,
    stable_sample,
    run_wenyi_review,
)
from wenyi_vendor import prompts
from wenyi_vendor.json_parser import parse_json_loose
from wenyi_vendor.validation import (
    validate_items,
    validate_judgment,
)
from translation_reliability import (
    TranslationRequestTracker,
    TranslationRunSummary,
    TranslationTotalRequestLimitExceeded,
)
from subtitle_translate import translate_srt


@dataclass
class Cue:
    index: int
    time_line: str
    text: str
    translation: str = ""


def cues(*texts: str) -> list[Cue]:
    return [
        Cue(index + 1, f"00:00:0{index},000 --> 00:00:0{index + 1},000", text)
        for index, text in enumerate(texts)
    ]


def test_pinned_version_and_model_tiers_require_pro():
    assert "v0.3.2" in WENYI_STRATEGY_VERSION
    assert resolve_model_tier("fast", flash_model="flash", pro_model="pro") == "flash"
    assert resolve_model_tier("cheap", flash_model="flash", pro_model="pro") == "pro"
    assert resolve_model_tier("strong", flash_model="flash", pro_model="pro") == "pro"
    with pytest.raises(ValueError, match="translation_quality_model"):
        resolve_model_tier("strong", flash_model="flash", pro_model="")


def test_loose_json_parser_preserves_structured_content():
    assert parse_json_loose('prefix ```json\n{"items":[{"id":1}]}\n``` suffix') == {
        "items": [{"id": 1}]
    }
    assert parse_json_loose('note {"id":1,"translation":"He said "go" now"} tail') == {
        "id": 1,
        "translation": 'He said "go" now',
    }


def test_immutable_records_reject_id_time_or_source_change():
    items = cues("Hello", "world.")
    baseline = immutable_records(items)
    items[0].translation = "你好"
    assert_immutable(baseline, items)
    items[1].time_line = "00:00:09,000 --> 00:00:10,000"
    with pytest.raises(RuntimeError, match="timing"):
        assert_immutable(baseline, items)


def test_cross_line_detection_english_french_and_normal_sentence():
    english = cues("I asked him to", "do it because", "we had no choice.")
    french = cues("Je lui ai demandé de", "le faire puisque", "nous étions prêts.")
    french_elision = cues(
        "Ils ne peuvent pas demander des explications",
        "puisqu'ils n'ont pas de langue pour le faire.",
    )
    normal = cues("This is complete.", "Another sentence.")
    unfinished_only = cues("This heading has no punctuation", "A fresh sentence starts")
    assert any(window["ids"] == [1, 2] for window in detect_cross_line_windows(english))
    assert any("cross_reference" in window["reasons"] for window in detect_cross_line_windows(french))
    assert detect_cross_line_windows(french_elision)
    assert detect_cross_line_windows(normal) == []
    assert detect_cross_line_windows(unfinished_only) == []


def test_cross_line_windows_are_reviewed_once_per_translation_batch(
    monkeypatch, tmp_path
):
    import subtitle_translate as transport

    items = cues(
        "I asked him to",
        "do it because",
        "we could",
        "but did not.",
    )
    cross_payloads: list[dict] = []

    def request_json(**kwargs):
        stage = kwargs["stage"]
        if stage == "wenyi_analysis":
            return {
                "video_summary": "test",
                "scene_summaries": [],
                "discourse_style": "explanation",
                "typed_glossary": [],
                "asr_warnings": [],
            }
        if stage == "wenyi_translation":
            return {
                "items": [
                    {"id": row["id"], "translation": f"译文{row['id']}"}
                    for row in kwargs["batch"]["items"]
                ]
            }
        if stage == "wenyi_review":
            return {"issues": [], "terms": []}
        if stage == "wenyi_cross_line":
            cross_payloads.append(kwargs["batch"])
            return {"issues": [], "terms": []}
        if stage == "wenyi_consistency":
            return {"groups": []}
        raise AssertionError(stage)

    monkeypatch.setattr(transport, "_request_json_object_stage", request_json)
    monkeypatch.setattr(transport, "_save_translation_cache", lambda *args: None)
    summary = TranslationRunSummary(
        mode="off", total_items=len(items), strategy_mode="wenyi_review"
    )
    run_wenyi_review(
        items=items,
        cached_translations={},
        translation_cache_path=tmp_path / "translation.json",
        output_path=tmp_path / "movie.srt",
        target_language="zh-CN",
        profile_prompt="profile",
        profile_glossary=[],
        flash_model="flash",
        pro_model="pro",
        batch_size=20,
        temperature=0.2,
        api_provider="openai-compatible",
        api_base="http://x",
        api_key="secret",
        context_window=3,
        scene_gap_seconds=30,
        max_cps_zh=8,
        max_chars_per_subtitle_zh=36,
        tracker=TranslationRequestTracker(mode="off"),
        summary=summary,
    )
    assert len(cross_payloads) == 1
    assert len(cross_payloads[0]["windows"]) >= 2


def test_total_request_limit_propagates_instead_of_falling_back(
    monkeypatch, tmp_path
):
    import subtitle_translate as transport

    def request_json(**kwargs):
        raise TranslationTotalRequestLimitExceeded(1)

    monkeypatch.setattr(transport, "_request_json_object_stage", request_json)
    items = cues("Bonjour.")
    with pytest.raises(TranslationTotalRequestLimitExceeded):
        run_wenyi_review(
            items=items,
            cached_translations={},
            translation_cache_path=tmp_path / "translation.json",
            output_path=tmp_path / "movie.srt",
            target_language="zh-CN",
            profile_prompt="profile",
            profile_glossary=[],
            flash_model="flash",
            pro_model="pro",
            batch_size=20,
            temperature=0.2,
            api_provider="openai-compatible",
            api_base="http://x",
            api_key="secret",
            context_window=3,
            scene_gap_seconds=30,
            max_cps_zh=8,
            max_chars_per_subtitle_zh=36,
            tracker=TranslationRequestTracker(mode="off", max_total_requests=1),
            summary=TranslationRunSummary(
                mode="off", total_items=1, strategy_mode="wenyi_review"
            ),
        )


def test_profile_glossary_wins_and_common_words_are_not_promoted():
    dynamic = [
        {
            "source": "Alice", "target": "爱丽丝", "type": "person",
            "confidence": "high", "evidence_ids": [1],
        },
        {
            "source": "bottle", "target": "奶瓶", "type": "common_word",
            "confidence": "high", "evidence_ids": [2],
        },
    ]
    merged = merge_profile_glossary(
        [{"source": "Alice", "target": "艾丽斯", "type": "person"}],
        [],
        dynamic,
    )
    assert relevant_glossary(merged, "Alice arrived.")[0]["target"] == "艾丽斯"
    assert all(row["source"] != "bottle" for row in merged)


def test_id_alignment_and_six_field_shortening_gate():
    assert validate_items(
        {"items": [{"id": 2, "translation": "乙"}, {"id": 1, "translation": "甲"}]},
        [1, 2],
    ) == {2: "乙", 1: "甲"}
    with pytest.raises(RuntimeError, match="exactly"):
        validate_items({"items": [{"id": 1, "translation": "甲"}]}, [1, 2])
    good = {
        "id": 1, "choice": "B", "confidence": "high",
        "facts": True, "negation": True, "numbers": True, "entities": True,
        "references": True, "logic": True,
    }
    assert validate_judgment(good, 1, equivalence=True)["choice"] == "B"
    rejected = validate_judgment(
        {**good, "negation": False}, 1, equivalence=True
    )
    assert rejected["negation"] is False
    with pytest.raises(RuntimeError, match="must be boolean"):
        validate_judgment({**good, "negation": "false"}, 1, equivalence=True)


def test_prompt_order_and_cache_fingerprint_include_context_models():
    rendered = prompts.translation_prompt(
        target_language="zh-CN",
        profile="P",
        video_summary="V",
        scene_summary="S",
        discourse_style="dialogue",
        glossary=[],
        recent=[],
        context_before=[],
        context_after=[],
    )
    labels = [
        "【Language Profile】", "【全片概览】", "【本场摘要】",
        "【相关术语】", "【最近译文】", "【只读前文】",
    ]
    assert [rendered.index(label) for label in labels] == sorted(
        rendered.index(label) for label in labels
    )
    records = immutable_records(cues("x"))
    first = cache_fingerprint(
        records=records, profile_glossary=[], profile_prompt="p",
        flash_model="f", pro_model="p", target_language="zh", context_window=3,
    )
    second = cache_fingerprint(
        records=records, profile_glossary=[], profile_prompt="p",
        flash_model="f", pro_model="p2", target_language="zh", context_window=3,
    )
    assert first != second
    assert stable_sample(range(1, 30), "task", 12) == stable_sample(
        range(1, 30), "task", 12
    )
    assert len(stable_sample(range(1, 30), "task", 12)) == 12


def test_orchestrator_adopts_only_blind_judged_repair_and_reuses_cache(
    monkeypatch, tmp_path
):
    import subtitle_translate as transport

    items = [
        Cue(1, "00:00:00,000 --> 00:00:04,000", "He did not go."),
        Cue(2, "00:00:04,000 --> 00:00:08,000", "She stayed."),
    ]
    calls: list[str] = []

    def request_json(**kwargs):
        stage = kwargs["stage"]
        calls.append(stage)
        if stage == "wenyi_analysis":
            return {
                "video_summary": "test",
                "scene_summaries": [{"scene_index": 1, "summary": "scene"}],
                "discourse_style": "dialogue",
                "typed_glossary": [],
                "asr_warnings": [],
            }
        if stage == "wenyi_review":
            return {
                "issues": [{
                    "id": 1, "type": "missing", "detail": "negation",
                    "confidence": "high",
                }],
                "terms": [],
            }
        if stage == "wenyi_translation":
            return {
                "items": [
                    {
                        "id": row["id"],
                        "translation": "他去了" if row["id"] == 1 else "她留下了",
                    }
                    for row in kwargs["batch"]["items"]
                ]
            }
        if stage == "wenyi_repair":
            return {"items": [{"id": 1, "translation": "他没去"}]}
        if stage == "wenyi_judge":
            candidate = "他没去"
            choice = "A" if kwargs["batch"]["option_a"] == candidate else "B"
            return {"id": 1, "choice": choice, "confidence": "high", "reason": "faithful"}
        if stage == "wenyi_consistency":
            return {"groups": []}
        raise AssertionError(stage)

    monkeypatch.setattr(transport, "_request_json_object_stage", request_json)
    monkeypatch.setattr(transport, "_save_translation_cache", lambda *args: None)
    cache = tmp_path / "translation.json"
    output = tmp_path / "movie.srt"
    summary = TranslationRunSummary(mode="off", total_items=2, strategy_mode="wenyi_review")
    run_wenyi_review(
        items=items, cached_translations={}, translation_cache_path=cache,
        output_path=output, target_language="zh-CN", profile_prompt="profile",
        profile_glossary=[], flash_model="flash", pro_model="pro", batch_size=20,
        temperature=0.2, api_provider="openai-compatible", api_base="http://x",
        api_key="secret", context_window=3, scene_gap_seconds=30,
        max_cps_zh=8, max_chars_per_subtitle_zh=36,
        tracker=TranslationRequestTracker(mode="off", max_extra_requests=0),
        summary=summary,
    )
    assert items[0].translation == "他没去"
    assert summary.wenyi_repair_accepted_ids == [1]
    assert output.with_name("movie.wenyi_review_report.json").is_file()

    calls.clear()
    cached_items = [
        Cue(1, "00:00:00,000 --> 00:00:04,000", "He did not go."),
        Cue(2, "00:00:04,000 --> 00:00:08,000", "She stayed."),
    ]
    cached_summary = TranslationRunSummary(
        mode="off", total_items=2, strategy_mode="wenyi_review"
    )
    run_wenyi_review(
        items=cached_items, cached_translations={}, translation_cache_path=cache,
        output_path=output, target_language="zh-CN", profile_prompt="profile",
        profile_glossary=[], flash_model="flash", pro_model="pro", batch_size=20,
        temperature=0.2, api_provider="openai-compatible", api_base="http://x",
        api_key="secret", context_window=3, scene_gap_seconds=30,
        max_cps_zh=8, max_chars_per_subtitle_zh=36,
        tracker=TranslationRequestTracker(mode="off", max_extra_requests=0),
        summary=cached_summary,
    )
    assert calls == []
    assert cached_items[0].translation == "他没去"
    assert cached_summary.wenyi_cached_batches == 1


def test_translate_srt_fails_before_transport_when_pro_is_missing(tmp_path):
    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello.\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="translation_quality_model"):
        translate_srt(
            input_path=source,
            output_path=tmp_path / "out.srt",
            api_provider="openai-compatible",
            api_base="https://invalid.example",
            api_key="never-used",
            llm_model="flash",
            translation_quality_model="",
            target_language="zh-CN",
            batch_size=10,
            temperature=0.2,
            translation_mode="bilingual",
            translation_strategy_mode="wenyi_review",
        )
