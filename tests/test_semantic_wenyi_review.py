from dataclasses import dataclass
import json

from translation_reliability import TranslationRequestTracker, TranslationRunSummary
from wenyi_subtitle_strategy import run_wenyi_review


@dataclass
class Cue:
    index: int
    time_line: str
    text: str
    translation: str = ""


def _cue(item_id: int, source: str) -> Cue:
    return Cue(
        item_id,
        f"00:00:0{item_id - 1},000 --> 00:00:0{item_id},000",
        source,
    )


def test_hybrid_skips_flash_and_requires_strict_proof(monkeypatch, tmp_path):
    import subtitle_translate as transport

    items = [_cue(1, "He did not go."), _cue(2, "Broken source fragment.")]
    baseline = {1: "他去了。", 2: "原有译文。"}
    calls: list[str] = []

    def request_json(**kwargs):
        stage = kwargs["stage"]
        calls.append(stage)
        if stage == "wenyi_analysis":
            return {
                "video_summary": "test",
                "scene_summaries": [],
                "discourse_style": "explanation",
                "typed_glossary": [],
                "asr_warnings": [{
                    "id": 2,
                    "category": "grammatically_impossible",
                    "detail": "broken",
                    "confidence": "high",
                }],
            }
        if stage == "wenyi_review":
            return {
                "issues": [
                    {
                        "id": 1,
                        "type": "missing",
                        "detail": "negation",
                        "confidence": "high",
                    },
                    {
                        "id": 2,
                        "type": "mistranslation",
                        "detail": "source uncertain",
                        "confidence": "high",
                    },
                ],
                "terms": [],
            }
        if stage == "wenyi_repair":
            assert kwargs["batch"]["items"][0]["id"] == 1
            return {"items": [{"id": 1, "translation": "他没去。"}]}
        if stage == "wenyi_judge":
            candidate = "他没去。"
            choice = "A" if kwargs["batch"]["option_a"] == candidate else "B"
            return {
                "id": 1,
                "choice": choice,
                "confidence": "high",
                "facts": True,
                "negation": True,
                "numbers": True,
                "entities": True,
                "references": True,
                "logic": True,
                "issue_resolved": True,
                "no_new_error": True,
                "reason": "restores negation",
            }
        if stage == "wenyi_consistency":
            return {"groups": []}
        raise AssertionError(stage)

    monkeypatch.setattr(transport, "_request_json_object_stage", request_json)
    monkeypatch.setattr(transport, "_save_translation_cache", lambda *args: None)
    summary = TranslationRunSummary(
        mode="off",
        total_items=2,
        strategy_mode="semantic_wenyi_review",
    )
    output = tmp_path / "movie.srt"
    run_wenyi_review(
        items=items,
        cached_translations={},
        translation_cache_path=tmp_path / "translation.json",
        output_path=output,
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
        strategy_mode="semantic_wenyi_review",
        baseline_translations=baseline,
        source_warning_ids={2},
        semantic_baseline_report={"strategy_mode": "semantic_review"},
    )

    assert "wenyi_translation" not in calls
    assert calls.count("wenyi_repair") == 1
    assert items[0].translation == "他没去。"
    assert items[1].translation == baseline[2]
    report_path = output.with_name(
        "movie.semantic_wenyi_review_report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["strategy_mode"] == "semantic_wenyi_review"
    assert report["semantic_baseline"] == {"1": baseline[1], "2": baseline[2]}
    assert report["source_warning_ids"] == [2]


def test_hybrid_keeps_baseline_when_any_strict_field_is_false(
    monkeypatch, tmp_path
):
    import subtitle_translate as transport

    items = [_cue(1, "He did not go.")]

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
        if stage == "wenyi_review":
            return {
                "issues": [{
                    "id": 1,
                    "type": "missing",
                    "detail": "negation",
                    "confidence": "high",
                }],
                "terms": [],
            }
        if stage == "wenyi_repair":
            return {"items": [{"id": 1, "translation": "他没去。"}]}
        if stage == "wenyi_judge":
            candidate = "他没去。"
            return {
                "id": 1,
                "choice": (
                    "A" if kwargs["batch"]["option_a"] == candidate else "B"
                ),
                "confidence": "high",
                "facts": True,
                "negation": True,
                "numbers": True,
                "entities": True,
                "references": True,
                "logic": True,
                "issue_resolved": True,
                "no_new_error": False,
                "reason": "introduced another error",
            }
        if stage == "wenyi_consistency":
            return {"groups": []}
        raise AssertionError(stage)

    monkeypatch.setattr(transport, "_request_json_object_stage", request_json)
    monkeypatch.setattr(transport, "_save_translation_cache", lambda *args: None)
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
        summary=TranslationRunSummary(
            mode="off",
            total_items=1,
            strategy_mode="semantic_wenyi_review",
        ),
        strategy_mode="semantic_wenyi_review",
        baseline_translations={1: "他去了。"},
    )
    assert items[0].translation == "他去了。"


def test_translate_srt_runs_semantic_baseline_before_hybrid_challenger(
    monkeypatch, tmp_path
):
    import subtitle_translate as transport
    import wenyi_subtitle_strategy

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nHe stayed.\n",
        encoding="utf-8",
    )
    output = tmp_path / "output.srt"
    calls: list[tuple] = []

    def semantic_stage(**kwargs):
        calls.append(("semantic", kwargs["cache_path"]))
        kwargs["items"][0].translation = "他留下了。"
        transport._atomic_write_json(
            transport._semantic_review_report_path(kwargs["output_path"]),
            {
                "strategy_mode": "semantic_review",
                "video_analysis": {"suspected_asr_errors": []},
            },
        )

    def hybrid_stage(**kwargs):
        calls.append(
            (
                "hybrid",
                kwargs["translation_cache_path"],
                kwargs["strategy_mode"],
                kwargs["baseline_translations"],
            )
        )
        kwargs["items"][0].translation = "他留下了。"

    monkeypatch.setattr(
        transport, "_translate_semantic_review", semantic_stage
    )
    monkeypatch.setattr(
        wenyi_subtitle_strategy, "run_wenyi_review", hybrid_stage
    )
    summary = transport.translate_srt(
        input_path=source,
        output_path=output,
        api_provider="openai-compatible",
        api_base="https://invalid.example",
        api_key="never-used",
        llm_model="flash",
        translation_quality_model="pro",
        target_language="zh-CN",
        batch_size=10,
        temperature=0.2,
        translation_mode="bilingual",
        translation_strategy_mode="semantic_wenyi_review",
    )

    assert [row[0] for row in calls] == ["semantic", "hybrid"]
    assert calls[0][1] != calls[1][1]
    assert calls[1][2] == "semantic_wenyi_review"
    assert calls[1][3] == {1: "他留下了。"}
    assert summary.strategy_mode == "semantic_wenyi_review"
    assert "他留下了。" in output.read_text(encoding="utf-8")
