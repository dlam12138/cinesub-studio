from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import validate_translation_reliability as validator
from subtitle_translate import SubtitleItem, read_srt, write_srt
from translation_reliability import TranslationRunSummary


def _bilingual_items(prefix: str, count: int = 6) -> list[SubtitleItem]:
    return [
        SubtitleItem(
            index,
            f"00:00:{index:02d},000 --> 00:00:{index:02d},900",
            f"source {index}\n{prefix} {index}",
        )
        for index in range(1, count + 1)
    ]


def test_window_ab_pack_keeps_each_window_on_one_blind_side(
    monkeypatch, tmp_path: Path,
) -> None:
    source = [
        SubtitleItem(
            index,
            f"00:00:{index:02d},000 --> 00:00:{index:02d},900",
            f"source {index}",
        )
        for index in range(1, 7)
    ]
    baseline = _bilingual_items("baseline")
    preview = _bilingual_items("preview")
    windows = [{
        "window_id": "window-1",
        "start": 1,
        "end": 4,
        "cue_ids": [2, 3, 4],
        "blocker_ids": [3],
    }]
    monkeypatch.setattr(validator, "_extract_audio", lambda *args: ["audio-window-1.wav"])

    result = validator._write_ab_pack(
        tmp_path, source, baseline, preview, "fingerprint", tmp_path / "media.mp4", windows
    )

    pack = tmp_path / "ab-review"
    a_items = {item.index: item.text for item in read_srt(pack / "A.srt")}
    b_items = {item.index: item.text for item in read_srt(pack / "B.srt")}
    answer = json.loads((pack / "answer-key.local.json").read_text(encoding="utf-8"))
    form = json.loads((pack / "review-form.local.json").read_text(encoding="utf-8"))
    preview_side = answer["window-1"]

    for cue_id in (2, 3, 4):
        selected = a_items[cue_id] if preview_side == "A" else b_items[cue_id]
        other = b_items[cue_id] if preview_side == "A" else a_items[cue_id]
        assert selected == f"preview {cue_id}"
        assert other == f"baseline {cue_id}"
    for cue_id in (1, 5, 6):
        assert a_items[cue_id] == b_items[cue_id] == f"baseline {cue_id}"
    assert form["schema_version"] == 2
    assert form["windows"][0]["cue_ids"] == [2, 3, 4]
    assert "semantic_continuity" in form["fields"]
    assert result["review_window_count"] == 1


def _write_validation_fixture(root: Path) -> tuple[Path, Path, Path]:
    source_items: list[SubtitleItem] = []
    baseline_items: list[SubtitleItem] = []
    for source_id in validator.SELECTED_SOURCE_IDS:
        seconds = source_id - validator.SELECTED_SOURCE_IDS[0]
        timeline = f"00:00:{seconds:02d},000 --> 00:00:{seconds:02d},900"
        source_text = f"texte source {source_id}"
        source_items.append(SubtitleItem(source_id, timeline, source_text))
        local_id = len(source_items)
        translation = source_text if local_id in {7, 19} else f"基线译文{local_id}"
        baseline_items.append(
            SubtitleItem(source_id, timeline, f"{source_text}\n{translation}")
        )
    source = root / "source.srt"
    baseline = root / "baseline.srt"
    media = root / "media.mp4"
    write_srt(source_items, source)
    write_srt(baseline_items, baseline)
    media.write_bytes(b"offline media")
    return source, baseline, media


def _run_with_fake_llm(
    monkeypatch,
    tmp_path: Path,
    *,
    duplicate_output: bool,
    max_http_requests: int = 20,
    quality_model_unavailable: bool = False,
) -> dict:
    source, baseline, media = _write_validation_fixture(tmp_path)
    monkeypatch.setattr(
        validator,
        "resolve_runtime_paths",
        lambda: SimpleNamespace(project_root=tmp_path),
    )
    monkeypatch.setattr(
        validator,
        "resolve_provider_config",
        lambda provider_id: {
            "api_provider": "openai-compatible",
            "api_base": "https://offline.invalid",
            "api_key": "test-secret",
            "llm_model": "offline-model",
        },
    )
    monkeypatch.setattr(
        validator,
        "_extract_audio",
        lambda *args: ["audio-window-1.wav", "audio-window-2.wav"],
    )

    def fake_translate_srt(**kwargs):
        assert kwargs["translation_quality_model"] == "offline-model"
        items = read_srt(kwargs["input_path"])
        output = []
        for item in items:
            translation = "重复内容文本" if duplicate_output else f"优化译文{item.index}"
            output.append(
                SubtitleItem(item.index, item.time_line, f"{item.text}\n{translation}")
            )
        write_srt(output, kwargs["output_path"])
        return TranslationRunSummary(
            mode="preview",
            total_items=len(items),
            cache_hits=len(items),
            actual_requests=2 if quality_model_unavailable else 6,
            extra_requests=2 if quality_model_unavailable else 6,
            repaired_ids=[6, 7, 8, 18, 19, 20],
            repair_windows_attempted=2,
            repair_windows_accepted=0 if quality_model_unavailable else 2,
            repair_windows_rejected=1 if quality_model_unavailable else 0,
            flash_initial_requests=2,
            quality_candidate_requests=1 if quality_model_unavailable else 2,
            judge_requests=0 if quality_model_unavailable else 2,
            quality_model_unavailable=quality_model_unavailable,
            unresolved_ids=[7, 19] if quality_model_unavailable else [],
        )

    monkeypatch.setattr(validator, "translate_srt", fake_translate_srt)
    args = argparse.Namespace(
        provider="deepseek-main",
        source=source.name,
        baseline=baseline.name,
        media=media.name,
        max_http_requests=max_http_requests,
        max_extra_requests=8,
    )
    return validator.run(args)


def test_schema_v2_fake_llm_pass_generates_pending_window_review(
    monkeypatch, tmp_path: Path,
) -> None:
    report = _run_with_fake_llm(monkeypatch, tmp_path, duplicate_output=False)

    assert report["schema_version"] == 3
    assert report["repair_strategy"] == "window-v3-quality-chain"
    assert report["quality_model_id"] == "offline-model"
    assert report["repair_window_count"] == 2
    assert report["automatic_gate"] == "pass"
    assert report["human_gate"] == "pending"
    assert report["promotion_decision"] == "pending_human_review"
    assert report["production_default"] == "off"
    assert report["total_http_requests"] == 12
    assert all(item["adjacent_overlap_delta"] <= 0 for item in report["rounds"])
    assert report["ab_review"]["review_window_count"] == 2
    serialized = json.dumps(report, ensure_ascii=False)
    assert "test-secret" not in serialized
    assert str(tmp_path) not in serialized
    assert "texte source" not in serialized


def test_schema_v2_fake_llm_overlap_regression_is_no_go(
    monkeypatch, tmp_path: Path,
) -> None:
    report = _run_with_fake_llm(monkeypatch, tmp_path, duplicate_output=True)

    assert report["automatic_gate"] == "no_go"
    assert report["human_gate"] == "not_started"
    assert report["promotion_decision"] == "no_go"
    assert any(item["adjacent_overlap_delta"] > 0 for item in report["rounds"])
    assert report["ab_review"]["status"] == "not_generated"


def test_schema_v2_fake_llm_http_budget_excess_is_saved_as_no_go(
    monkeypatch, tmp_path: Path,
) -> None:
    report = _run_with_fake_llm(
        monkeypatch,
        tmp_path,
        duplicate_output=False,
        max_http_requests=3,
    )

    assert report["total_http_requests"] == 6
    assert report["authorized_http_request_limit"] == 3
    assert report["http_budget_exceeded"] is True
    assert report["automatic_gate"] == "no_go"
    assert report["promotion_decision"] == "no_go"


def test_schema_v3_stops_after_quality_model_unavailable(
    monkeypatch, tmp_path: Path,
) -> None:
    report = _run_with_fake_llm(
        monkeypatch,
        tmp_path,
        duplicate_output=False,
        quality_model_unavailable=True,
    )

    assert len(report["rounds"]) == 1
    assert report["rounds"][0]["summary"]["quality_model_unavailable"] is True
    assert report["automatic_gate"] == "no_go"
    assert report["human_gate"] == "not_started"
    assert report["ab_review"]["status"] == "not_generated"


@pytest.mark.parametrize(
    "round_result",
    [
        {"complete": False},
        {
            "complete": True,
            "cue_count_unchanged": True,
            "timeline_unchanged": True,
            "blocking_issue_count": 0,
            "adjacent_overlap_delta": 0,
            "summary": {
                "repair_windows_attempted": 2,
                "repair_windows_accepted": 1,
                "repair_windows_rejected": 1,
                "unresolved_count": 1,
                "budget_exhausted": False,
            },
        },
    ],
)
def test_round_gate_rejects_incomplete_or_rejected_windows(round_result: dict) -> None:
    assert validator._round_passed(round_result, 2) is False
