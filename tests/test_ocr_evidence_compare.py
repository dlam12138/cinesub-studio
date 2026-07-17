from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import ocr_evidence_compare as compare
from subtitle_translate import SubtitleItem, write_srt


def _cue(index: int, start: float, end: float, text: str) -> compare.Cue:
    return compare.Cue(index, start, end, text)


def _ocr(
    index: int,
    start: float,
    end: float,
    source: str,
    target: str = "",
    stability: float | None = 0.9,
) -> compare.OcrCue:
    text = "\n".join(value for value in (source, target) if value)
    return compare.OcrCue(index, start, end, text, source, target, stability, 2)


def _write(path: Path, rows: list[tuple[str, str]]) -> None:
    write_srt(
        [SubtitleItem(index, timeline, text) for index, (timeline, text) in enumerate(rows, 1)],
        path,
    )


def test_time_alignment_supports_one_to_many_and_many_to_one():
    ocr = [_ocr(1, 0, 2, "bonjour"), _ocr(2, 3, 4, "tout le monde")]
    hypothesis = [
        _cue(1, 0, 0.9, "bon"),
        _cue(2, 1.0, 2, "jour"),
        _cue(3, 2.9, 4.1, "tout le monde"),
    ]

    groups = compare.align_by_time(ocr, hypothesis, tolerance=0.15)

    assert [(len(group.ocr), len(group.hypothesis)) for group in groups] == [(1, 2), (1, 1)]


def test_time_alignment_is_independent_of_text_and_keeps_unmatched_groups():
    ocr = [_ocr(1, 0, 1, "même texte")]
    first = [_cue(1, 0.2, 0.8, "même texte"), _cue(2, 3, 4, "extra")]
    second = [_cue(1, 0.2, 0.8, "completely different"), _cue(2, 3, 4, "changed")]

    first_shape = [
        ([cue.index for cue in group.ocr], [cue.index for cue in group.hypothesis])
        for group in compare.align_by_time(ocr, first)
    ]
    second_shape = [
        ([cue.index for cue in group.ocr], [cue.index for cue in group.hypothesis])
        for group in compare.align_by_time(ocr, second)
    ]

    assert first_shape == second_shape == [([1], [1]), ([], [2])]


def test_time_alignment_handles_shift_with_tolerance_and_total_non_overlap():
    ocr = [_ocr(1, 1.0, 2.0, "hola")]

    shifted = compare.align_by_time(ocr, [_cue(1, 2.3, 3.0, "hola")], tolerance=0.5)
    separate = compare.align_by_time(ocr, [_cue(1, 4.0, 5.0, "hola")], tolerance=0.5)

    assert [(len(group.ocr), len(group.hypothesis)) for group in shifted] == [(1, 1)]
    assert [(len(group.ocr), len(group.hypothesis)) for group in separate] == [(1, 0), (0, 1)]


def test_asr_signals_normalize_accents_punctuation_and_detect_duplicates():
    ocr = [_ocr(1, 0, 1, "Écoutez !"), _ocr(2, 1.1, 2, "señor")]
    hypothesis = [
        _cue(1, 0, 1, "écoutez"),
        _cue(2, 1.1, 1.5, "señor"),
        _cue(3, 1.5, 2, "señor"),
    ]

    metrics, _ = compare.calculate_asr_signals(ocr, hypothesis, language="es", tolerance=0.1)

    assert metrics["ocr_source_disagreement_rate"] < 0.5
    assert metrics["duplicate_cue_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert metrics["language_script_anomaly_rate"] == 0


def test_low_stability_cues_do_not_enter_comparable_asr_metrics():
    ocr = [_ocr(1, 0, 1, "bonjour", stability=0.5)]

    metrics, _ = compare.calculate_asr_signals(
        ocr, [_cue(1, 0, 1, "bonjour")], language="fr", tolerance=0.1
    )

    assert metrics["high_stability_ocr_coverage"] == 0
    assert metrics["ocr_source_disagreement_rate"] is None
    assert metrics["high_stability_reference_characters"] == 0


def test_missing_sidecar_marks_every_ocr_cue_low_confidence(tmp_path):
    srt = tmp_path / "ocr.srt"
    _write(srt, [("00:00:00,000 --> 00:00:01,000", "bonjour\n你好")])

    cues, warnings = compare.load_ocr_cues(srt)

    assert cues[0].stability is None
    assert warnings and "low confidence" in warnings[0]


def test_translation_signals_find_empty_shifted_and_duplicate_outputs():
    ocr = [
        _ocr(1, 0, 1, "bonjour", "你好"),
        _ocr(2, 1, 2, "le monde", "世界"),
    ]
    source = [_cue(1, 0, 1, "bonjour"), _cue(2, 1, 2, "le monde")]
    translation = [_cue(1, 0, 1, ""), _cue(2, 1, 1.5, "世界"), _cue(3, 1.5, 2, "世界")]

    metrics, details = compare.calculate_translation_signals(
        ocr, source, translation, tolerance=0.05
    )

    assert metrics["translation_issue_counts"]["empty_translation"] == 1
    assert metrics["translation_issue_counts"]["ocr_target_without_translation"] == 1
    assert metrics["duplicate_cue_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert {item["ocr_index"] for item in details} == {1, 2}


def test_bilingual_translation_input_keeps_only_chinese_target_lines():
    items = [SubtitleItem(1, "00:00:00,000 --> 00:00:01,000", "bonjour\n你好")]

    cues = compare._to_target_cues(items)

    assert cues[0].text == "你好"


def _output(asr: dict, blockers: int = 0) -> dict:
    return {
        "asr_signals": asr,
        "translation_signals": {"translation_blocking_issue_count": blockers},
    }


def _asr_metrics(disagreement: float | None, coverage: float = 0.8) -> dict:
    return {
        "high_stability_ocr_coverage": coverage,
        "ocr_source_disagreement_rate": disagreement,
        "ocr_text_without_asr_coverage_rate": 0.1,
        "asr_without_ocr_coverage_rate": 0.1,
        "duplicate_cue_rate": 0.01,
    }


def test_weak_screen_has_only_three_allowed_decisions_and_never_allows_apply():
    insufficient = compare._candidate_decision(
        _output(_asr_metrics(0.5)), _output(_asr_metrics(0.3, coverage=0.5))
    )
    rejected = compare._candidate_decision(
        _output(_asr_metrics(0.5)), _output(_asr_metrics(0.46))
    )
    eligible = compare._candidate_decision(
        _output(_asr_metrics(0.5), blockers=2), _output(_asr_metrics(0.4), blockers=1)
    )

    assert insufficient["decision"] == "insufficient_evidence"
    assert rejected["decision"] == "rejected_by_weak_screen"
    assert eligible["decision"] == "eligible_for_gold_benchmark"
    assert eligible["apply_allowed"] is False


def test_manifest_rejects_paths_outside_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.srt"
    outside.write_text("x", encoding="utf-8")
    manifest = project / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "samples": [{
            "id": "sample",
            "language": "fr",
            "ocr_srt": str(outside.resolve()),
            "baseline": {"source_srt": str(outside.resolve())},
        }],
    }), encoding="utf-8")

    with pytest.raises(compare.OcrEvidenceError, match="inside the project root"):
        compare.load_manifest(manifest, project)


def _fixture_project(tmp_path: Path, *, candidate: bool = True) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    _write(project / "ocr.srt", [
        ("00:00:00,000 --> 00:00:01,000", "bonjour\n你好"),
        ("00:00:01,000 --> 00:00:02,000", "le monde\n世界"),
    ])
    _write(project / "baseline-source.srt", [
        ("00:00:00,000 --> 00:00:01,000", "bon jour"),
        ("00:00:01,000 --> 00:00:02,000", "le monde"),
    ])
    _write(project / "baseline-zh.srt", [
        ("00:00:00,000 --> 00:00:01,000", "您好"),
        ("00:00:01,000 --> 00:00:02,000", "世界"),
    ])
    _write(project / "candidate-source.srt", [
        ("00:00:00,000 --> 00:00:01,000", "bonjour"),
        ("00:00:01,000 --> 00:00:02,000", "le monde"),
    ])
    _write(project / "candidate-zh.srt", [
        ("00:00:00,000 --> 00:00:01,000", "你好"),
        ("00:00:01,000 --> 00:00:02,000", "世界"),
    ])
    (project / "sidecar.json").write_text(json.dumps({
        "schema_version": 1,
        "cues": [
            {"index": 1, "stability": 0.9, "sampled_frame_count": 2},
            {"index": 2, "stability": 0.9, "sampled_frame_count": 2},
        ],
    }), encoding="utf-8")
    candidates = [{
        "id": "candidate-v1",
        "source_srt": "candidate-source.srt",
        "translation_srt": "candidate-zh.srt",
    }] if candidate else []
    manifest = project / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "samples": [{
            "id": "sample-fr",
            "language": "fr",
            "tags": ["interview"],
            "ocr_srt": "ocr.srt",
            "ocr_sidecar": "sidecar.json",
            "baseline": {
                "source_srt": "baseline-source.srt",
                "translation_srt": "baseline-zh.srt",
            },
            "candidates": candidates,
        }],
    }), encoding="utf-8")
    return project, manifest


def test_run_defaults_to_zero_network_calls_and_writes_sanitized_reports(tmp_path, monkeypatch):
    project, manifest = _fixture_project(tmp_path)
    monkeypatch.setattr(compare, "resolve_runtime_paths", lambda: SimpleNamespace(project_root=project))
    monkeypatch.setattr(
        compare,
        "_call_llm_api",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network call must not happen")),
    )
    args = Namespace(
        manifest=str(manifest.relative_to(project)),
        output_dir="output/reports/ocr_evidence",
        run_id="test-run",
        tolerance=0.5,
        llm_judge="off",
        provider="",
        max_llm_cues=0,
    )

    report = compare.run(args)

    output = project / "output/reports/ocr_evidence/test-run"
    public_text = (output / "summary.json").read_text(encoding="utf-8")
    assert report["llm_judge"]["actual_requests"] == 0
    assert output.joinpath("summary.md").is_file()
    assert output.joinpath("details.local.json").is_file()
    assert output.joinpath("review_needed.srt").is_file()
    assert str(project) not in public_text
    assert "bonjour" not in public_text
    assert not list(output.glob("*.tmp"))
    assert report["aggregates"]["by_tag"]["interview"]["sample_count"] == 1
    assert "candidate-v1" in report["aggregates"]["by_candidate"]
    candidate = report["samples"][0]["outputs"][1]
    assert candidate["weak_screen"]["apply_allowed"] is False


def test_llm_judge_randomizes_labels_uses_neighbors_and_caches(tmp_path, monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(compare, "resolve_provider_config", lambda provider_id: {
        "api_key": "secret-value",
        "api_provider": "openai-compatible",
        "api_base": "https://example.invalid/v1",
        "llm_model": "judge-model",
    })

    def fake_call(**kwargs):
        kwargs["tracker"].before_request(extra=kwargs["request_is_extra"])
        body = json.loads(kwargs["body"])
        captured.append(json.loads(body["messages"][1]["content"]))
        return json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "preference": "A", "confidence": 0.8, "categories": ["omission"], "reason": "clear"
            })}}]
        })

    monkeypatch.setattr(compare, "_call_llm_api", fake_call)
    details = [
        {"ocr_index": 1, "source": "avant", "ocr_target": "之前", "translation": "此前"},
        {"ocr_index": 2, "source": "bonjour", "ocr_target": "你好", "translation": "您好"},
        {"ocr_index": 3, "source": "après", "ocr_target": "之后", "translation": "后来"},
    ]
    candidate_details = [
        {"ocr_index": 2, "source": "bonjour", "ocr_target": "你好", "translation": "你好"},
    ]
    samples = [{
        "id": "sample",
        "details": {
            "baseline": {"translation": details},
            "candidate": {"translation": candidate_details},
        },
    }]
    cache = tmp_path / "judge-cache.json"

    judgments, summary, warnings = compare._run_llm_judges(
        samples=samples, provider_id="provider", maximum=1, cache_path=cache
    )
    second, second_summary, _ = compare._run_llm_judges(
        samples=samples, provider_id="provider", maximum=1, cache_path=cache
    )

    assert not warnings
    assert len(judgments) == 1
    assert summary["actual_requests"] == 1
    assert second_summary["actual_requests"] == 0
    assert second_summary["cache_hits"] == 1
    assert second == judgments
    assert captured[0]["previous"]["ocr_index"] == 1
    assert captured[0]["next"]["ocr_index"] == 3
    assert {captured[0]["current"]["translation_A"], captured[0]["current"]["translation_B"]} == {"您好", "你好"}
    assert "secret-value" not in cache.read_text(encoding="utf-8")


def test_uncertain_mode_requires_explicit_provider_and_positive_budget(tmp_path, monkeypatch):
    project, manifest = _fixture_project(tmp_path, candidate=False)
    monkeypatch.setattr(compare, "resolve_runtime_paths", lambda: SimpleNamespace(project_root=project))
    args = Namespace(
        manifest=str(manifest.relative_to(project)),
        output_dir="output/reports/ocr_evidence",
        run_id="invalid",
        tolerance=0.5,
        llm_judge="uncertain",
        provider="",
        max_llm_cues=0,
    )

    with pytest.raises(compare.OcrEvidenceError, match="requires --provider"):
        compare.run(args)
