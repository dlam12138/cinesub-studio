from argparse import Namespace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from real_media_acceptance import (
    BASE_SHA,
    build_campaign_contract,
    build_run_command,
    build_videocr_command,
    compare_quality_control,
    deterministic_review_indexes,
    environment_fingerprint,
    main,
    normalize_run_id,
    resolve_acceptance_profile,
    run_campaign,
    run_profile,
    run_videocr,
    validate_campaign_reports,
)


def test_acceptance_runner_builds_isolated_local_only_quality_control(tmp_path: Path) -> None:
    args = Namespace(
        profile="large-control",
        input=str(tmp_path / "sample.mp4"),
        model_dir=str(tmp_path / "models"),
        output_dir=str(tmp_path / "output"),
        work_dir=str(tmp_path / "work"),
        device="cuda",
        compute_type="float16",
        language="fr",
        hotword_prompt="",
    )

    command = build_run_command(args)

    assert BASE_SHA == "ff2f48b754687346410c850ecdf628045056de8c"
    assert command[0]
    assert "--local-files-only" in command
    assert command[command.index("--model") + 1] == "large-v3"
    assert command[command.index("--quality-preset") + 1] == "quality"
    assert command[command.index("--asr-retry-mode") + 1] == "off"
    assert command[command.index("--device") + 1] == "cuda"


def test_single_run_cli_keeps_run_id_optional(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run_profile(args):
        captured["run_id"] = normalize_run_id(args)
        return {}

    monkeypatch.setattr("real_media_acceptance.run_profile", fake_run_profile)
    monkeypatch.setattr(
        "real_media_acceptance.sys.argv",
        [
            "real_media_acceptance.py",
            "run",
            "--input", str(tmp_path / "sample.mp4"),
            "--sample-id", "sample-02",
            "--profile", "balanced",
            "--model-dir", str(tmp_path / "models"),
            "--output-dir", str(tmp_path / "output"),
            "--work-dir", str(tmp_path / "work"),
            "--private-dir", str(tmp_path / "private"),
        ],
    )

    assert main() == 0
    assert captured["run_id"] == "sample-02-primary-balanced"


def test_run_profile_uses_derived_run_id_for_files_and_report(
    monkeypatch, tmp_path: Path
) -> None:
    input_path = tmp_path / "sample.mp4"
    output_dir = tmp_path / "output"
    private_dir = tmp_path / "private"
    input_path.write_bytes(b"media")
    output_dir.mkdir()
    (output_dir / "sample.small.srt").write_text("fixture", encoding="utf-8")
    (output_dir / "sample.small.lang.json").write_text(json.dumps({
        "model": "small",
        "quality_preset": "balanced",
        "asr_mode": "auto",
        "forced_language": None,
        "beam_size": 5,
        "vad_filter": True,
        "word_timestamps": True,
        "resegment_summary": {"enabled": True},
        "asr_retry": {"mode": "dry_run"},
        "effective_asr_config": {
            "asr_hotword_prompt": {"value": ""},
        },
        "device": "cpu",
        "compute_type": "int8",
        "local_files_only": True,
    }), encoding="utf-8")

    class CompletedProcess:
        pid = 123

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(
        "real_media_acceptance.subprocess.Popen",
        lambda *_args, **_kwargs: CompletedProcess(),
    )
    payload = run_profile(Namespace(
        input=str(input_path),
        sample_id="sample-02",
        profile="balanced",
        asr_mode="auto",
        language="",
        model_dir=str(tmp_path / "models"),
        output_dir=str(output_dir),
        work_dir=str(tmp_path / "work"),
        private_dir=str(private_dir),
        device="cpu",
        compute_type="int8",
        hotword_prompt="",
        input_duration=0.0,
    ))

    assert payload["run_id"] == "sample-02-primary-balanced"
    assert (private_dir / "sample-02-primary-balanced.run.local.json").is_file()


def test_campaign_contract_fixes_six_primary_modes_and_exactly_28_runs() -> None:
    contract = build_campaign_contract("evaluated-sha")

    assert contract["run_count"] == 28
    assert contract["local_files_only"] is True
    assert all(
        run["expected_profile_config"]["asr_retry_mode"] != "apply"
        for run in contract["runs"]
    )
    assert len({run["run_id"] for run in contract["runs"]}) == 28
    primary = {
        run["sample_id"]: (run["asr_mode"], run["language"])
        for run in contract["runs"]
        if run["role"] == "primary" and run["profile"] == "speed"
    }
    assert primary == {
        "sample-01": ("fixed", "fr"),
        "sample-02": ("auto", None),
        "sample-03": ("fixed", "fr"),
        "sample-04": ("auto", None),
        "sample-05": ("fixed", "zh"),
        "sample-06": ("multilingual", None),
    }
    control = [
        run for run in contract["runs"]
        if run["role"] == "single-language-multilingual-control"
    ]
    assert len(control) == 4
    assert {run["profile"] for run in control} == {
        "speed", "balanced", "large-control", "quality"
    }
    assert {(run["sample_id"], run["asr_mode"], run["language"]) for run in control} == {
        ("sample-01", "multilingual", None)
    }


def test_quality_control_resolves_quality_then_only_overrides_retry() -> None:
    quality = resolve_acceptance_profile("quality")
    control = resolve_acceptance_profile("large-control")

    assert quality["asr_retry_mode"] == "dry_run"
    assert control["asr_retry_mode"] == "off"
    assert sorted(key for key in quality if quality[key] != control[key]) == [
        "asr_retry_mode"
    ]


@pytest.mark.parametrize("mode", ["auto", "multilingual"])
def test_acceptance_command_supports_unforced_language_modes(
    tmp_path: Path, mode: str
) -> None:
    args = Namespace(
        profile="quality",
        input=str(tmp_path / "sample.mp4"),
        model_dir=str(tmp_path / "models"),
        output_dir=str(tmp_path / "output"),
        work_dir=str(tmp_path / "work"),
        device="cuda",
        compute_type="float16",
        asr_mode=mode,
        language="",
        hotword_prompt="",
    )

    command = build_run_command(args)

    assert command[command.index("--asr-mode") + 1] == mode
    assert "--language" not in command
    assert "--local-files-only" in command


def _campaign_report(planned: dict) -> dict:
    profile = planned["expected_profile_config"]
    config = {
        "model": profile["model"],
        "quality_preset": profile["quality_preset"],
        "asr_mode": planned["asr_mode"],
        "language": planned["language"],
        "beam_size": 5,
        "vad_filter": True,
        "word_timestamps": profile["word_timestamps"],
        "resegment_subtitles": profile["resegment_subtitles"],
        "asr_retry_mode": profile["asr_retry_mode"],
        "asr_hotword_prompt_sha256": "empty-hotword-hash",
        "device": "cuda",
        "compute_type": "float16",
        "local_files_only": True,
        "input_sha256": f'{planned["sample_id"]}-input-hash',
    }
    return {
        "run_id": planned["run_id"],
        "sample_id": planned["sample_id"],
        "scenario_id": planned["scenario_id"],
        "profile": planned["profile"],
        "input_sha256": config["input_sha256"],
        "effective_config": config,
        "decode_config_sha256": f'{planned["scenario_id"]}-decode-hash',
        "runtime_config_sha256": "runtime-hash",
        "output_srt_sha256": f'{planned["scenario_id"]}-srt-hash',
    }


def test_campaign_validation_asserts_all_seven_quality_control_pairs() -> None:
    contract = build_campaign_contract("evaluated-sha")
    reports = [_campaign_report(planned) for planned in contract["runs"]]

    summary = validate_campaign_reports(contract, reports)

    assert summary["status"] == "pass"
    assert summary["run_count"] == 28
    assert summary["comparison_count"] == 7
    assert all(
        row["effective_config_diff"] == ["asr_retry_mode"]
        and row["decode_config_hash_match"]
        and row["runtime_config_hash_match"]
        and row["input_hash_match"]
        and row["output_srt_hash_match"]
        for row in summary["comparisons"]
    )


@pytest.mark.parametrize(
    ("field", "unexpected"),
    [
        ("asr_mode", "fixed"),
        ("language", "en"),
        ("model", "large-v3"),
        ("quality_preset", "quality"),
        ("word_timestamps", True),
        ("resegment_subtitles", True),
        ("asr_retry_mode", "dry_run"),
    ],
)
def test_campaign_validation_rejects_each_run_contract_drift(
    field: str, unexpected: object
) -> None:
    contract = build_campaign_contract("evaluated-sha")
    reports = [_campaign_report(planned) for planned in contract["runs"]]
    target = next(
        report for report in reports
        if report["run_id"] == "sample-04-primary-speed"
    )
    target["effective_config"][field] = unexpected

    with pytest.raises(RuntimeError, match=f"planned {field}"):
        validate_campaign_reports(contract, reports)


def test_quality_control_comparison_rejects_decode_drift() -> None:
    contract = build_campaign_contract("evaluated-sha")
    planned = [
        row for row in contract["runs"]
        if row["scenario_id"] == "sample-01-primary"
        and row["profile"] in {"large-control", "quality"}
    ]
    reports = {row["profile"]: _campaign_report(row) for row in planned}
    reports["quality"]["effective_config"]["beam_size"] = 6

    with pytest.raises(RuntimeError, match="contracts differ"):
        compare_quality_control(reports["large-control"], reports["quality"])


def test_review_sampling_is_deterministic_and_remediation_reuses_indexes() -> None:
    first = deterministic_review_indexes(
        sample_id="sample-04",
        evaluated_sha="sha-a",
        cue_count=80,
        suspicious_indexes=[1, 3, 5],
    )
    repeated = deterministic_review_indexes(
        sample_id="sample-04",
        evaluated_sha="sha-a",
        cue_count=80,
        suspicious_indexes=[1, 3, 5],
    )
    remediation = deterministic_review_indexes(
        sample_id="sample-04",
        evaluated_sha="sha-b",
        cue_count=80,
        suspicious_indexes=[1, 3, 5, first["ordinary_cue_indexes"][0]],
        reuse_from=first,
    )

    assert len(first["ordinary_cue_indexes"]) == 20
    assert first["ordinary_cue_indexes"] == repeated["ordinary_cue_indexes"]
    assert remediation["ordinary_cue_indexes"] == first["ordinary_cue_indexes"]
    assert remediation["reused_from_evaluated_sha"] == "sha-a"
    assert remediation["seed_integer"] == first["seed_integer"]
    assert not set(first["ordinary_cue_indexes"]) & {1, 3, 5}


def test_campaign_runner_dispatches_exact_plan_without_media_processing(
    tmp_path: Path, monkeypatch
) -> None:
    private_root = tmp_path / "acceptance" / "v0.7.1-real-media-private"
    samples = {}
    for number in range(1, 7):
        sample_id = f"sample-{number:02d}"
        media = private_root / "media" / f"{sample_id}.mp4"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"private-fixture")
        samples[sample_id] = {"input": str(media), "duration_seconds": 240}
    manifest = private_root / "campaign.local.json"
    manifest.write_text(json.dumps({"samples": samples}), encoding="utf-8")
    calls = []

    monkeypatch.setattr(
        "real_media_acceptance.resolve_runtime_paths",
        lambda: Namespace(project_root=tmp_path),
    )
    monkeypatch.setattr("real_media_acceptance._git_sha", lambda _root: "evaluated-sha")

    def fake_run_profile(args):
        calls.append(args)
        planned = next(
            row for row in build_campaign_contract("evaluated-sha")["runs"]
            if row["run_id"] == args.run_id
        )
        return _campaign_report(planned)

    monkeypatch.setattr("real_media_acceptance.run_profile", fake_run_profile)
    summary = run_campaign(Namespace(
        manifest=str(manifest),
        evaluated_sha="evaluated-sha",
        model_dir=str(tmp_path / "models"),
        private_dir=str(private_root / "campaign"),
        device="cuda",
        compute_type="float16",
    ))

    assert summary["run_count"] == 28
    assert len(calls) == 28
    assert all(call.hotword_prompt == "" for call in calls)
    assert all("v0.7.1-real-media-private" in call.private_dir for call in calls)


def test_videocr_command_is_local_scoped_and_explicit(tmp_path: Path, monkeypatch) -> None:
    private_root = tmp_path / "acceptance" / "v0.7.1-real-media-private"
    executable = private_root / "tools" / "videocr-cli.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    output = private_root / "evidence" / "sample-01.fr.srt"
    monkeypatch.setattr(
        "real_media_acceptance.resolve_runtime_paths",
        lambda: Namespace(project_root=tmp_path),
    )
    args = Namespace(
        executable=str(executable),
        input=str(tmp_path / "sample.mp4"),
        output=str(output),
        ocr_engine="paddleocr",
        language="fr",
        time_start="05:00",
        time_end="08:00",
        crop_x=100,
        crop_y=700,
        crop_width=1720,
        crop_height=220,
        conf_threshold=75,
        sim_threshold=80,
        max_merge_gap=0.09,
        frames_to_skip=0,
        use_gpu=True,
    )

    command = build_videocr_command(args)

    assert command[command.index("--ocr_engine") + 1] == "paddleocr"
    assert command[command.index("--lang") + 1] == "fr"
    assert command[command.index("--use_gpu") + 1] == "true"
    assert command[command.index("--post_processing") + 1] == "true"
    assert command[command.index("--crop_height") + 1] == "220"
    assert "google_lens" not in command


def test_videocr_command_rejects_cloud_ocr(tmp_path: Path, monkeypatch) -> None:
    private_root = tmp_path / "acceptance" / "v0.7.1-real-media-private"
    monkeypatch.setattr(
        "real_media_acceptance.resolve_runtime_paths",
        lambda: Namespace(project_root=tmp_path),
    )
    args = Namespace(
        executable=str(private_root / "videocr-cli.exe"),
        input=str(tmp_path / "sample.mp4"),
        output=str(private_root / "sample.srt"),
        ocr_engine="google_lens",
        language="fr",
        time_start="00:00",
        time_end="01:00",
        crop_x=0,
        crop_y=0,
        crop_width=100,
        crop_height=50,
        conf_threshold=75,
        sim_threshold=80,
        max_merge_gap=0.09,
        frames_to_skip=0,
        use_gpu=False,
    )

    with pytest.raises(ValueError, match="local PaddleOCR"):
        build_videocr_command(args)


def test_videocr_executable_may_be_user_supplied_but_output_stays_private(
    tmp_path: Path, monkeypatch
) -> None:
    private_root = tmp_path / "acceptance" / "v0.7.1-real-media-private"
    executable = tmp_path / "external" / "videocr-cli.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(
        "real_media_acceptance.resolve_runtime_paths",
        lambda: Namespace(project_root=tmp_path),
    )
    args = Namespace(
        executable=str(executable),
        input=str(tmp_path / "sample.mp4"),
        output=str(private_root / "sample.srt"),
        ocr_engine="paddleocr",
        language="fr",
        time_start="00:00",
        time_end="01:00",
        crop_x=0,
        crop_y=10,
        crop_width=100,
        crop_height=50,
        conf_threshold=75,
        sim_threshold=80,
        max_merge_gap=0.09,
        frames_to_skip=0,
        use_gpu=False,
    )

    command = build_videocr_command(args)

    assert command[0] == str(executable.resolve())

    args.output = str(tmp_path / "public.srt")
    with pytest.raises(ValueError, match="Private acceptance artifact"):
        build_videocr_command(args)


def test_environment_fingerprint_embeds_ocr_and_model_preflights(
    tmp_path: Path, monkeypatch
) -> None:
    ocr_path = tmp_path / "ocr.json"
    model_path = tmp_path / "models.json"
    ocr_path.write_text('{"tool":"VideOCR CLI"}', encoding="utf-8")
    model_path.write_text('{"models":[{"model":"small"}]}', encoding="utf-8")
    monkeypatch.setattr(
        "real_media_acceptance.resolve_runtime_paths",
        lambda: Namespace(project_root=tmp_path),
    )
    monkeypatch.setattr("real_media_acceptance.find_ffmpeg", lambda _root: None)
    monkeypatch.setattr("real_media_acceptance._git_sha", lambda _root: "evaluated")
    monkeypatch.setattr("real_media_acceptance._command_line", lambda _command: "unavailable")
    monkeypatch.setattr("real_media_acceptance._cuda_runtime_version", lambda: "12.9")
    args = Namespace(
        implementation_sha="implementation",
        acceptance_runner_sha="runner",
        ocr_preflight=str(ocr_path),
        model_preflight=str(model_path),
        output=str(tmp_path / "fingerprint.json"),
    )

    payload = environment_fingerprint(args)

    assert payload["ocr"]["tool"] == "VideOCR CLI"
    assert payload["models"]["models"][0]["model"] == "small"


def test_videocr_run_redirects_user_and_cache_directories(tmp_path: Path, monkeypatch) -> None:
    private_root = tmp_path / "acceptance" / "v0.7.1-real-media-private"
    executable = tmp_path / "external" / "videocr-cli.exe"
    input_path = tmp_path / "sample.mp4"
    output_path = private_root / "evidence" / "sample.srt"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    input_path.write_bytes(b"media")
    monkeypatch.setattr(
        "real_media_acceptance.resolve_runtime_paths",
        lambda: Namespace(project_root=tmp_path),
    )
    captured = {}

    def fake_run(_command, **kwargs):
        captured.update(kwargs["env"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nBonjour\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("real_media_acceptance.subprocess.run", fake_run)
    args = Namespace(
        executable=str(executable),
        input=str(input_path),
        output=str(output_path),
        private_dir=str(private_root / "audit"),
        runtime_root=str(tmp_path / "ascii-runtime"),
        sample_id="sample-01",
        ocr_engine="paddleocr",
        language="fr",
        time_start="00:00",
        time_end="01:00",
        crop_x=0,
        crop_y=10,
        crop_width=100,
        crop_height=50,
        conf_threshold=75,
        sim_threshold=80,
        max_merge_gap=0.09,
        frames_to_skip=0,
        use_gpu=False,
        timeout=30.0,
    )

    run_videocr(args)

    assert str(tmp_path / "ascii-runtime") in captured["LOCALAPPDATA"]
    assert str(tmp_path / "ascii-runtime") in captured["APPDATA"]
    assert str(tmp_path / "ascii-runtime") in captured["TEMP"]
    assert str(tmp_path / "ascii-runtime") in captured["PADDLE_PDX_CACHE_HOME"]


def test_videocr_executable_path_preserves_junction_style_input(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "ascii-link" / "videocr-cli.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(
        "real_media_acceptance.resolve_runtime_paths",
        lambda: Namespace(project_root=tmp_path),
    )
    args = Namespace(
        executable=str(executable),
        input=str(tmp_path / "sample.mp4"),
        output=str(tmp_path / "acceptance" / "v0.7.1-real-media-private" / "sample.srt"),
        ocr_engine="paddleocr",
        language="fr",
        time_start="00:00",
        time_end="01:00",
        crop_x=0,
        crop_y=0,
        crop_width=100,
        crop_height=50,
        conf_threshold=75,
        sim_threshold=80,
        max_merge_gap=0.09,
        frames_to_skip=0,
        use_gpu=True,
    )

    assert build_videocr_command(args)[0] == str(executable.absolute())
