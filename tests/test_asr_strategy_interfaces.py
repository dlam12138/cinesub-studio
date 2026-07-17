from __future__ import annotations

from pipeline_cli import build_pipeline_parser
from pipeline_config import resolve_cli_config
from pipeline_api import _build_background_command
from web_server import _parse_asr_strategy_payload


def test_pipeline_cli_overrides_profile_strategy(monkeypatch) -> None:
    import language_profile_store

    monkeypatch.setattr(
        language_profile_store,
        "resolve_language_profile_config",
        lambda _profile: {
            "asr": {"whisper_model": "large-v3"},
            "asr_strategy": {"mode": "dry_run", "candidate_id": "vad-balanced-v1"},
        },
    )
    args = build_pipeline_parser().parse_args(
        ["--language-profile", "profile", "--asr-candidate-id", "vad-sensitive-v1"]
    )
    values, _messages = resolve_cli_config(args, ["--language-profile", "--asr-candidate-id"])
    assert values["asr_strategy"] == {
        "mode": "dry_run",
        "candidate_id": "vad-sensitive-v1",
    }


def test_web_strategy_validation_returns_fixed_registered_candidate() -> None:
    assert _parse_asr_strategy_payload(
        {"asr_experiment_mode": "dry_run", "asr_candidate_id": "decode-repeat-guard-v1"},
        "large-v3",
    ) == {"mode": "dry_run", "candidate_id": "decode-repeat-guard-v1"}


def test_pipeline_background_command_passes_strategy_flags() -> None:
    command = _build_background_command(
        action="run", provider_id="", language_profile_id="", input_dir="input",
        model="large-v3", device="cuda", compute_type="float16", translate_enabled=False,
        language="", local_files_only=True, subtitle_formats=["srt"], ass_style_id="clean-cn",
        asr_experiment_mode="dry_run", asr_candidate_id="vad-sensitive-v1",
    )
    assert command[command.index("--asr-experiment-mode") + 1] == "dry_run"
    assert command[command.index("--asr-candidate-id") + 1] == "vad-sensitive-v1"
