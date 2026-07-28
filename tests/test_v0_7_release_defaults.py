from __future__ import annotations

from asr_runtime import quality_preset_values, resolve_quality_loop_config
from translation_strategy import normalize_translation_strategy


def test_v0_7_personal_use_quality_defaults_remain_frozen():
    balanced = quality_preset_values("balanced")
    quality = quality_preset_values("quality")
    resolved, sources = resolve_quality_loop_config(preset="balanced")

    assert "model" not in balanced
    assert balanced == {
        "word_timestamps": True,
        "resegment_subtitles": True,
        "asr_retry_mode": "dry_run",
    }
    assert quality["model"] == "large-v3"
    assert quality["asr_retry_mode"] == "dry_run"
    assert resolved["asr_retry_mode"] == "dry_run"
    assert sources["asr_retry_mode"]["source"] == "quality_preset"
    assert normalize_translation_strategy()["mode"] == "standard"


def test_unconfigured_runtime_defaults_to_small_without_quality_promotion():
    resolved, sources = resolve_quality_loop_config()

    assert "model" not in resolved
    assert resolved["resegment_subtitles"] is False
    assert resolved["asr_retry_mode"] == "off"
    assert sources["resegment_subtitles"]["source"] == "default"
