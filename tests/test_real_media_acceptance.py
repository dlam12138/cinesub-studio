from argparse import Namespace
from pathlib import Path

import pytest

from real_media_acceptance import BASE_SHA, build_run_command, build_videocr_command


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
