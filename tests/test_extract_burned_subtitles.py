from argparse import Namespace

import pytest

from extract_burned_subtitles import (
    OcrCue,
    _consensus_text,
    _merge_consecutive,
    _observation_stability,
    _parse_sampling_offsets,
    _sample_offsets,
    _select_language_tag,
    _temporal_stability,
    _uniform_sampling_cues,
    run,
)
from subtitle_translate import SubtitleItem


def test_uniform_sampling_uses_fixed_intervals_and_preserves_final_fraction():
    timeline = [SubtitleItem(1, "00:00:02,000 --> 00:00:05,250", "example")]

    cues = _uniform_sampling_cues(timeline, 2.0)

    assert [cue.time_line for cue in cues] == [
        "00:00:00,000 --> 00:00:02,000",
        "00:00:02,000 --> 00:00:04,000",
        "00:00:04,000 --> 00:00:05,250",
    ]


@pytest.mark.parametrize(
    ("sample_interval", "crop_height", "message"),
    [
        (0.0, 170, "sample_interval"),
        (1.0, 0, "crop_height"),
    ],
)
def test_run_rejects_invalid_sampling_arguments_before_touching_files(
    sample_interval, crop_height, message
):
    args = Namespace(sample_interval=sample_interval, crop_height=crop_height)

    with pytest.raises(ValueError, match=message):
        run(args)


def test_merged_ocr_cues_retain_frame_evidence_for_stability_sidecar():
    cues = [
        OcrCue(1, "00:00:00,000 --> 00:00:01,000", "Bonjour", "你好", [1], ["Bonjour"], ["你好"]),
        OcrCue(2, "00:00:01,000 --> 00:00:02,000", "Bonjour!", "你好", [2], ["Bonjour!"], ["你好"]),
    ]

    merged = _merge_consecutive(cues)

    assert len(merged) == 1
    assert merged[0].sampled_frame_ids == [1, 2]
    assert _observation_stability(merged[0].french, merged[0].french_observations) > 0.9


def test_single_ocr_observation_is_presence_not_high_stability():
    assert _observation_stability("hola", ["hola"]) == 0.5
    assert _observation_stability("", []) is None


def test_multiframe_offsets_and_consensus_are_deterministic():
    assert _sample_offsets(3) == [0.25, 0.5, 0.75]
    assert _parse_sampling_offsets("0.2,0.5,0.8") == [0.2, 0.5, 0.8]
    assert _consensus_text(["Bonjour", "Bon jour", "Bonjour"]) == "Bonjour"


def test_temporal_stability_uses_text_presence_position_and_persistence():
    boxes = [
        {"left": 10, "top": 20, "width": 100, "height": 20},
        {"left": 11, "top": 20, "width": 100, "height": 20},
        {"left": 10, "top": 21, "width": 100, "height": 20},
    ]

    score = _temporal_stability("Bonjour", ["Bonjour", "Bonjour", "Bonjour"], boxes)

    assert score is not None
    assert score >= 0.95


def test_ocr_language_selection_uses_enumerated_tags_without_default_fallback():
    available = ["en-US", "fr-FR", "zh-Hans-CN"]
    assert _select_language_tag(available, "fr-FR") == "fr-FR"
    assert _select_language_tag(available, "zh-Hans", hans=True) == "zh-Hans-CN"
    with pytest.raises(RuntimeError, match="unavailable"):
        _select_language_tag(available, "de-DE")


@pytest.mark.parametrize("value", ["0", "0.5,0.5", "0.8,0.2", "abc"])
def test_sampling_offsets_reject_invalid_values(value):
    with pytest.raises(ValueError, match="sampling_offsets"):
        _parse_sampling_offsets(value)
