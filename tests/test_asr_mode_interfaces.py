from __future__ import annotations

import pytest

from language_profile_store import _with_profile_defaults
from pipeline_api import _build_background_command
from pipeline_cli import build_pipeline_parser
from pipeline_config import resolve_cli_config
from web_server import _parse_asr_request_payload


def test_language_profile_migrates_legacy_source_language() -> None:
    fixed = _with_profile_defaults({
        "source_language": "fr",
        "asr": {"recognizer": "funasr-sensevoice", "aligner": "whisperx"},
    })
    assert fixed["asr_mode"] == "fixed"
    assert fixed["source_language"] == "fr"
    assert fixed["asr"]["language"] == "fr"
    assert "recognizer" not in fixed["asr"]
    assert "aligner" not in fixed["asr"]

    automatic = _with_profile_defaults({
        "source_language": "auto",
        "asr": {},
    })
    assert automatic["asr_mode"] == "auto"
    assert automatic["asr"]["language"] is None


def test_web_asr_payload_supports_three_modes_and_legacy_language() -> None:
    assert _parse_asr_request_payload({}) == {"asr_mode": "", "language": ""}
    assert _parse_asr_request_payload({"language": "fr"}) == {
        "asr_mode": "fixed",
        "language": "fr",
    }
    assert _parse_asr_request_payload({"asr_mode": "multilingual"}) == {
        "asr_mode": "multilingual",
        "language": "",
    }
    with pytest.raises(ValueError, match="requires"):
        _parse_asr_request_payload({"asr_mode": "fixed"})


def test_pipeline_cli_explicit_language_overrides_auto_profile(monkeypatch) -> None:
    import language_profile_store

    monkeypatch.setattr(
        language_profile_store,
        "resolve_language_profile_config",
        lambda _profile: {
            "profile_id": "auto-profile",
            "asr_mode": "auto",
            "source_language": "auto",
            "asr": {"whisper_model": "small"},
        },
    )
    args = build_pipeline_parser().parse_args(
        ["--language-profile", "auto-profile", "--language", "fr"]
    )
    values, _messages = resolve_cli_config(
        args, ["--language-profile", "--language"]
    )
    assert values["asr_mode"] == "fixed"
    assert values["language"] == "fr"


def test_pipeline_background_command_passes_only_public_asr_flags() -> None:
    command = _build_background_command(
        action="run",
        provider_id="",
        language_profile_id="",
        input_dir="input",
        model="small",
        device="cpu",
        compute_type="int8",
        translate_enabled=False,
        asr_mode="multilingual",
        language="",
        local_files_only=True,
        subtitle_formats=["srt"],
        ass_style_id="clean-cn",
    )
    assert command[command.index("--asr-mode") + 1] == "multilingual"
    assert "--language" not in command
    assert "--asr-recognizer" not in command
    assert "--segment-asr-routing" not in command
