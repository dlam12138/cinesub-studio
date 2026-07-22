from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import re
import subprocess
import sys
import time
from pathlib import Path

from asr_runtime import resolve_quality_loop_config
from asr_model_locator import locate_asr_model, validate_model_directory
from ffmpeg_locator import find_ffmpeg
from runtime_paths import resolve_runtime_paths


BASE_SHA = "ff2f48b754687346410c850ecdf628045056de8c"
VIDEOCR_RELEASE = {
    "version": "v1.5.1",
    "source_sha": "ab4599dd8d55978ee2b29169c9e1b40dd0bae316",
    "repository": "https://github.com/timminator/VideOCR",
    "release_url": "https://github.com/timminator/VideOCR/releases/tag/v1.5.1",
    "windows_gpu_cuda12_9": {
        "asset": "videocr-cli-GPU-v1.5.1-CUDA-12.9.7z",
        "size": 1_589_507_616,
        "sha256": "1561e77e4747a897ae2c4bd19e308572effe71f8ea33db01a33a8a0a97ff7cec",
    },
    "windows_cpu": {
        "asset": "videocr-cli-CPU-v1.5.1.7z",
        "size": 471_804_781,
        "sha256": "4380a364667ead5d7f0bdf2933b68108bcdc9f039f4e8917d5707b479056c315",
    },
}
RUN_PROFILES = {
    "speed": ("small", "speed", None),
    "balanced": ("small", "balanced", None),
    "large-control": ("large-v3", "quality", "off"),
    "quality": ("large-v3", "quality", None),
}
CAMPAIGN_PROFILES = tuple(RUN_PROFILES)
CAMPAIGN_SAMPLES = (
    {"sample_id": "sample-01", "description": "french-low-volume", "asr_mode": "fixed", "language": "fr"},
    {"sample_id": "sample-02", "description": "french-near-field", "asr_mode": "auto", "language": None},
    {"sample_id": "sample-03", "description": "french-far-field", "asr_mode": "fixed", "language": "fr"},
    {"sample_id": "sample-04", "description": "english-dialogue", "asr_mode": "auto", "language": None},
    {"sample_id": "sample-05", "description": "mandarin-dialogue", "asr_mode": "fixed", "language": "zh"},
    {"sample_id": "sample-06", "description": "bilingual-switching", "asr_mode": "multilingual", "language": None},
)
MULTILINGUAL_CONTROL_SAMPLE_ID = "sample-01"
CONFIG_DIFF_WHITELIST = ("asr_retry_mode",)
PAIR_INVARIANT_FIELDS = (
    "model",
    "quality_preset",
    "asr_mode",
    "language",
    "beam_size",
    "vad_filter",
    "word_timestamps",
    "resegment_subtitles",
    "asr_hotword_prompt_sha256",
    "device",
    "compute_type",
    "local_files_only",
    "input_sha256",
)


def _private_acceptance_path(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    private_root = (
        resolve_runtime_paths().project_root
        / "acceptance"
        / "v0.7.1-real-media-private"
    ).resolve()
    try:
        resolved.relative_to(private_root)
    except ValueError as exc:
        raise ValueError(f"Private acceptance artifact must stay under {private_root}") from exc
    return resolved


def _videocr_executable_path(path: str | Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if absolute.name.casefold() != "videocr-cli.exe" or not absolute.is_file():
        raise FileNotFoundError(f"VideOCR CLI executable is unavailable: {absolute}")
    return absolute


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def resolve_acceptance_profile(profile: str) -> dict:
    if profile not in RUN_PROFILES:
        raise ValueError(f"Unknown acceptance profile: {profile}")
    model, preset, retry_override = RUN_PROFILES[profile]
    explicit = (
        {"asr_retry_mode": retry_override}
        if retry_override is not None
        else {}
    )
    loop, _sources = resolve_quality_loop_config(
        explicit=explicit,
        preset=preset,
    )
    resolved = {"model": model, **loop}
    if profile == "quality" and resolved["asr_retry_mode"] != "dry_run":
        raise RuntimeError("The quality acceptance profile must resolve to dry_run.")
    if profile == "large-control":
        quality = resolve_acceptance_profile("quality")
        differences = sorted(
            key for key in set(quality) | set(resolved)
            if quality.get(key) != resolved.get(key)
        )
        if differences != list(CONFIG_DIFF_WHITELIST):
            raise RuntimeError(
                "large-control must resolve quality and override only asr_retry_mode."
            )
        if resolved["asr_retry_mode"] != "off":
            raise RuntimeError("large-control must disable ASR retry.")
    return resolved


def build_campaign_contract(evaluated_sha: str) -> dict:
    evaluated_sha = str(evaluated_sha or "").strip()
    if not evaluated_sha:
        raise ValueError("evaluated_sha is required")
    scenarios = [
        {**sample, "scenario_id": f'{sample["sample_id"]}-primary', "role": "primary"}
        for sample in CAMPAIGN_SAMPLES
    ]
    control = next(
        sample for sample in CAMPAIGN_SAMPLES
        if sample["sample_id"] == MULTILINGUAL_CONTROL_SAMPLE_ID
    )
    scenarios.append({
        **control,
        "scenario_id": f'{control["sample_id"]}-multilingual-control',
        "description": f'{control["description"]}-multilingual-control',
        "role": "single-language-multilingual-control",
        "asr_mode": "multilingual",
        "language": None,
    })
    runs = []
    for scenario in scenarios:
        for profile in CAMPAIGN_PROFILES:
            runs.append({
                "run_id": f'{scenario["scenario_id"]}-{profile}',
                "sample_id": scenario["sample_id"],
                "scenario_id": scenario["scenario_id"],
                "role": scenario["role"],
                "description": scenario["description"],
                "profile": profile,
                "asr_mode": scenario["asr_mode"],
                "language": scenario["language"],
                "expected_profile_config": resolve_acceptance_profile(profile),
            })
    if len(runs) != 28 or len({row["run_id"] for row in runs}) != 28:
        raise AssertionError("The ASR baseline campaign must contain exactly 28 unique runs.")
    return {
        "schema_version": 1,
        "campaign": "asr-baseline-hardening-v1",
        "evaluated_sha": evaluated_sha,
        "local_files_only": True,
        "private_evidence_required": True,
        "run_count": len(runs),
        "runs": runs,
    }


def deterministic_review_indexes(
    *,
    sample_id: str,
    evaluated_sha: str,
    cue_count: int,
    suspicious_indexes: list[int] | tuple[int, ...],
    reuse_from: dict | None = None,
) -> dict:
    if cue_count < 1:
        raise ValueError("cue_count must be positive")
    suspicious = sorted({int(index) for index in suspicious_indexes})
    if any(index < 1 or index > cue_count for index in suspicious):
        raise ValueError("suspicious cue index is outside the available cue range")
    ordinary = [index for index in range(1, cue_count + 1) if index not in suspicious]
    if len(ordinary) < 20:
        raise ValueError("At least 20 ordinary cues are required for review sampling")

    reused_from_sha = None
    if reuse_from is not None:
        if reuse_from.get("sample_id") != sample_id:
            raise ValueError("Reused review indexes belong to a different sample")
        selected = [int(index) for index in reuse_from.get("ordinary_cue_indexes", [])]
        if len(selected) != 20 or len(set(selected)) != 20:
            raise ValueError("Reused review record must contain 20 unique ordinary cues")
        if any(index < 1 or index > cue_count for index in selected):
            raise ValueError("Reused review index is outside the current cue range")
        reused_from_sha = str(reuse_from.get("evaluated_sha") or "")
        seed_hex = str(reuse_from.get("seed_hex") or "")
        seed_integer = reuse_from.get("seed_integer")
    else:
        full_digest = hashlib.sha256(f"{sample_id}{evaluated_sha}".encode("utf-8")).hexdigest()
        seed_hex = full_digest[:16]
        seed_integer = int(seed_hex, 16)
        selected = sorted(random.Random(seed_integer).sample(ordinary, 20))

    return {
        "schema_version": 1,
        "sample_id": sample_id,
        "evaluated_sha": evaluated_sha,
        "seed_contract": "SHA256(sample_id + evaluated_sha)",
        "seed_hex": seed_hex,
        "seed_integer": seed_integer,
        "reused_from_evaluated_sha": reused_from_sha,
        "cue_count": cue_count,
        "suspicious_cue_indexes": suspicious,
        "ordinary_cue_indexes": selected,
    }


def model_fingerprint(path: Path) -> dict:
    files = []
    for name in ("config.json", "model.bin", "tokenizer.json", "tokenizer.model", "vocabulary.json", "vocabulary.txt"):
        candidate = path / name
        if candidate.is_file():
            stat = candidate.stat()
            files.append({
                "name": name,
                "size": stat.st_size,
                "sha256": sha256_file(candidate),
                "mtime_ns": stat.st_mtime_ns,
            })
    return {
        "revision": path.name if path.parent.name == "snapshots" else "flat",
        "files": files,
    }


def preflight_models(args: argparse.Namespace) -> dict:
    from transcribe import create_asr_session

    model_dir = Path(args.model_dir).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for model_name in args.models:
        location = locate_asr_model(model_name, model_dir)
        if not location.available:
            raise RuntimeError(
                f"Model preflight failed for {model_name}: {location.source} {location.error}"
            )
        model_path = Path(location.local_path)
        valid, missing = validate_model_directory(model_path)
        if not valid:
            raise RuntimeError(f"Model preflight failed for {model_name}: {missing}")
        started = time.perf_counter()
        session = create_asr_session(
            model_name=model_name,
            model_dir=model_dir,
            device=args.device,
            compute_type=args.compute_type,
            local_files_only=True,
        )
        load_seconds = time.perf_counter() - started
        rows.append({
            "model": model_name,
            "source": location.source,
            "revision": location.revision or "flat",
            "device": session.device,
            "compute_type": session.compute_type,
            "load_seconds": round(load_seconds, 6),
            "fingerprint": model_fingerprint(model_path),
        })
        del session
        gc.collect()
    payload = {
        "schema_version": 1,
        "local_files_only": True,
        "ctranslate2_version": _version("ctranslate2"),
        "models": rows,
    }
    _write_json(output_path, payload)
    return payload


def environment_fingerprint(args: argparse.Namespace) -> dict:
    paths = resolve_runtime_paths()
    ffmpeg = find_ffmpeg(paths.project_root)
    payload = {
        "schema_version": 1,
        "base_sha": BASE_SHA,
        "implementation_sha": args.implementation_sha,
        "acceptance_runner_sha": args.acceptance_runner_sha,
        "evaluated_sha": _git_sha(paths.project_root),
        "python": platform.python_version(),
        "packages": {
            name: _version(name)
            for name in ("faster-whisper", "ctranslate2", "av", "numpy")
        },
        "ffmpeg": _command_line([str(ffmpeg), "-version"]) if ffmpeg else "missing",
        "ffprobe": _command_line([str(Path(ffmpeg).with_name("ffprobe.exe")), "-version"]) if ffmpeg else "missing",
        "gpu": _command_line([
            "nvidia-smi", "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader",
        ]),
        "cuda_runtime": _cuda_runtime_version(),
        "ocr": json.loads(Path(args.ocr_preflight).read_text(encoding="utf-8")),
        "models": json.loads(Path(args.model_preflight).read_text(encoding="utf-8")),
    }
    _write_json(Path(args.output), payload)
    return payload


def preflight_videocr(args: argparse.Namespace) -> dict:
    executable = _videocr_executable_path(args.executable)
    output_path = _private_acceptance_path(args.output)
    if not executable.is_file() or executable.stat().st_size <= 0:
        raise FileNotFoundError(f"VideOCR CLI executable is unavailable: {executable}")
    help_result = subprocess.run(
        [str(executable), "-h"],
        cwd=executable.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout,
    )
    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    required_flags = (
        "--video_path",
        "--output",
        "--ocr_engine",
        "--lang",
        "--time_start",
        "--time_end",
        "--crop_x",
        "--crop_y",
        "--crop_width",
        "--crop_height",
        "--use_gpu",
    )
    missing_flags = [flag for flag in required_flags if flag not in help_text]
    if help_result.returncode != 0 or missing_flags:
        raise RuntimeError(
            "VideOCR CLI help preflight failed: "
            f"exit={help_result.returncode}, missing_flags={missing_flags}"
        )
    archive = _private_acceptance_path(args.archive) if args.archive else None
    archive_record = None
    if archive:
        if not archive.is_file():
            raise FileNotFoundError(f"VideOCR release archive is unavailable: {archive}")
        archive_record = {
            "name": archive.name,
            "size": archive.stat().st_size,
            "sha256": sha256_file(archive),
        }
        expected = VIDEOCR_RELEASE[args.release_asset]
        if archive_record["size"] != expected["size"] or archive_record["sha256"] != expected["sha256"]:
            raise RuntimeError("VideOCR release archive does not match the frozen GitHub asset.")
    payload = {
        "schema_version": 1,
        "tool": "VideOCR CLI",
        "version": VIDEOCR_RELEASE["version"],
        "source_sha": VIDEOCR_RELEASE["source_sha"],
        "repository": VIDEOCR_RELEASE["repository"],
        "release_url": VIDEOCR_RELEASE["release_url"],
        "executable": {
            "name": executable.name,
            "size": executable.stat().st_size,
            "sha256": sha256_file(executable),
        },
        "release_asset": args.release_asset,
        "archive": archive_record,
        "archive_verified": archive_record is not None,
        "ocr_engine": "paddleocr",
        "language": "fr",
        "cloud_ocr_allowed": False,
        "help_contract_verified": True,
    }
    _write_json(output_path, payload)
    return payload


def build_videocr_command(args: argparse.Namespace) -> list[str]:
    if args.ocr_engine != "paddleocr":
        raise ValueError("Real-media acceptance only permits local PaddleOCR.")
    crop_values = (args.crop_x, args.crop_y, args.crop_width, args.crop_height)
    if any(value < 0 for value in crop_values[:2]) or any(value <= 0 for value in crop_values[2:]):
        raise ValueError("VideOCR crop coordinates and dimensions are invalid.")
    command = [
        str(_videocr_executable_path(args.executable)),
        "--video_path", str(Path(args.input).resolve()),
        "--output", str(_private_acceptance_path(args.output)),
        "--ocr_engine", "paddleocr",
        "--lang", args.language,
        "--time_start", args.time_start,
        "--time_end", args.time_end,
        "--crop_x", str(args.crop_x),
        "--crop_y", str(args.crop_y),
        "--crop_width", str(args.crop_width),
        "--crop_height", str(args.crop_height),
        "--conf_threshold", str(args.conf_threshold),
        "--sim_threshold", str(args.sim_threshold),
        "--max_merge_gap", str(args.max_merge_gap),
        "--frames_to_skip", str(args.frames_to_skip),
        "--use_gpu", str(bool(args.use_gpu)).lower(),
        "--post_processing", "true",
    ]
    return command


def run_videocr(args: argparse.Namespace) -> dict:
    executable = _videocr_executable_path(args.executable)
    output_path = _private_acceptance_path(args.output)
    private_dir = _private_acceptance_path(args.private_dir)
    private_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_videocr_command(args)
    runtime_root = (
        Path(os.path.abspath(args.runtime_root))
        if getattr(args, "runtime_root", "")
        else private_dir
    )
    runtime_dir = runtime_root / f"{args.sample_id}.videocr-runtime"
    local_app_data = runtime_dir / "local-app-data"
    roaming_app_data = runtime_dir / "roaming-app-data"
    temporary_dir = runtime_dir / "temp"
    paddle_cache = runtime_dir / "paddle-cache"
    for directory in (local_app_data, roaming_app_data, temporary_dir, paddle_cache):
        directory.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "LOCALAPPDATA": str(local_app_data),
        "APPDATA": str(roaming_app_data),
        "TEMP": str(temporary_dir),
        "TMP": str(temporary_dir),
        "PADDLE_PDX_CACHE_HOME": str(paddle_cache),
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
    })
    stdout_path = private_dir / f"{args.sample_id}.videocr.stdout.local.log"
    stderr_path = private_dir / f"{args.sample_id}.videocr.stderr.local.log"
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            command,
            cwd=executable.parent,
            stdout=stdout,
            stderr=stderr,
            text=True,
            env=env,
            timeout=args.timeout,
        )
    elapsed = time.perf_counter() - started
    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError(
            f"VideOCR acceptance run failed: exit={result.returncode}; see private logs."
        )
    payload = {
        "schema_version": 1,
        "sample_id": args.sample_id,
        "tool": "VideOCR CLI",
        "version": VIDEOCR_RELEASE["version"],
        "ocr_engine": "paddleocr",
        "language": args.language,
        "use_gpu": bool(args.use_gpu),
        "time_window": [args.time_start, args.time_end],
        "crop": {
            "x": args.crop_x,
            "y": args.crop_y,
            "width": args.crop_width,
            "height": args.crop_height,
        },
        "elapsed_seconds": round(elapsed, 6),
        "input_sha256": sha256_file(Path(args.input).resolve()),
        "output_sha256": sha256_file(output_path),
        "output_bytes": output_path.stat().st_size,
        "weak_evidence_only": True,
        "runtime_isolated": True,
    }
    _write_json(private_dir / f"{args.sample_id}.videocr.run.local.json", payload)
    return payload


def build_run_command(args: argparse.Namespace) -> list[str]:
    model, preset, retry_override = RUN_PROFILES[args.profile]
    command = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve().parents[1] / "core" / "transcribe.py"),
        str(Path(args.input).resolve()),
        "--model", model,
        "--model-dir", str(Path(args.model_dir).resolve()),
        "--output-dir", str(Path(args.output_dir).resolve()),
        "--work-dir", str(Path(args.work_dir).resolve()),
        "--device", args.device,
        "--compute-type", args.compute_type,
        "--quality-preset", preset,
        "--local-files-only",
    ]
    if retry_override:
        command += ["--asr-retry-mode", retry_override]
    asr_mode = str(getattr(args, "asr_mode", "") or "").strip()
    language = str(getattr(args, "language", "") or "").strip()
    if not asr_mode:
        asr_mode = "fixed" if language else "auto"
    command += ["--asr-mode", asr_mode]
    if asr_mode == "fixed":
        if not language:
            raise ValueError("fixed acceptance runs require a language")
        command += ["--language", language]
    elif language:
        raise ValueError(f"{asr_mode} acceptance runs do not accept a language")
    if args.hotword_prompt:
        command += ["--asr-hotword-prompt", args.hotword_prompt]
    return command


def normalize_run_id(args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "run_id", "") or "").strip()
    if explicit:
        return explicit
    sample_id = str(getattr(args, "sample_id", "") or "sample").strip()
    scenario_id = str(
        getattr(args, "scenario_id", "") or f"{sample_id}-primary"
    ).strip()
    profile = str(getattr(args, "profile", "") or "run").strip()
    derived = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        f"{scenario_id}-{profile}",
    ).strip(".-_")
    return derived[:160] or "acceptance-run"


def run_profile(args: argparse.Namespace) -> dict:
    run_id = normalize_run_id(args)
    private_dir = Path(args.private_dir).resolve()
    private_dir.mkdir(parents=True, exist_ok=True)
    command = build_run_command(args)
    env = os.environ.copy()
    env["PYTHONPATH"] = ";".join(str(Path(__file__).resolve().parents[1] / part) for part in ("core", "pipeline", "config", "web", "tools"))
    stdout_path = private_dir / f"{run_id}.stdout.local.log"
    stderr_path = private_dir / f"{run_id}.stderr.local.log"
    gpu_samples = []
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True, env=env)
        while process.poll() is None:
            gpu_samples.extend(_gpu_process_samples(process.pid, time.perf_counter() - started))
            time.sleep(0.5)
        returncode = process.wait()
    elapsed = time.perf_counter() - started
    if returncode != 0:
        raise RuntimeError(f"Acceptance run {run_id} failed with exit code {returncode}")
    input_path = Path(args.input).resolve()
    model = RUN_PROFILES[args.profile][0]
    lang_path = Path(args.output_dir).resolve() / f"{input_path.stem}.{model}.lang.json"
    language_report = json.loads(lang_path.read_text(encoding="utf-8"))
    srt_path = Path(args.output_dir).resolve() / f"{input_path.stem}.{model}.srt"
    if not srt_path.is_file() or srt_path.stat().st_size <= 0:
        raise RuntimeError(f"Acceptance run {run_id} did not produce a non-empty SRT")
    input_sha256 = sha256_file(input_path)
    effective_config = _effective_config_snapshot(
        language_report,
        input_sha256=input_sha256,
    )
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "sample_id": args.sample_id,
        "scenario_id": getattr(args, "scenario_id", f"{args.sample_id}-primary"),
        "profile": args.profile,
        "evaluated_sha": getattr(args, "evaluated_sha", ""),
        "input_sha256": input_sha256,
        "input_duration_seconds": args.input_duration,
        "returncode": returncode,
        "end_to_end_seconds": round(elapsed, 6),
        "device": language_report.get("device"),
        "compute_type": language_report.get("compute_type"),
        "phase_timings": language_report.get("phase_timings", {}),
        "asr_retry_report": language_report.get("asr_retry_report", {}),
        "resegment_summary": language_report.get("resegment_summary", {}),
        "word_timing_count": language_report.get("word_timing_count", 0),
        "effective_config": effective_config,
        "effective_config_sha256": _sha256_json(effective_config),
        "decode_config_sha256": _decode_config_sha256(effective_config),
        "runtime_config_sha256": _runtime_config_sha256(effective_config),
        "output_srt_sha256": sha256_file(srt_path),
        "gpu_samples": gpu_samples,
    }
    _write_json(private_dir / f"{run_id}.run.local.json", payload)
    return payload


def run_campaign(args: argparse.Namespace) -> dict:
    private_dir = _private_acceptance_path(args.private_dir)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    samples = manifest.get("samples", {})
    expected_sample_ids = {sample["sample_id"] for sample in CAMPAIGN_SAMPLES}
    if set(samples) != expected_sample_ids:
        raise ValueError("Campaign manifest must define exactly sample-01 through sample-06")
    current_sha = _git_sha(resolve_runtime_paths().project_root)
    if current_sha != args.evaluated_sha:
        raise RuntimeError(
            f"Campaign evaluated SHA mismatch: expected {args.evaluated_sha}, current {current_sha}"
        )

    contract = build_campaign_contract(args.evaluated_sha)
    private_dir.mkdir(parents=True, exist_ok=True)
    _write_json(private_dir / "campaign.contract.local.json", contract)
    reports = []
    for planned in contract["runs"]:
        sample = samples[planned["sample_id"]]
        input_path = Path(sample.get("input", "")).expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(
                f'Private campaign input is unavailable for {planned["sample_id"]}'
            )
        run_root = private_dir / "runs" / planned["run_id"]
        report = run_profile(argparse.Namespace(
            input=str(input_path),
            sample_id=planned["sample_id"],
            scenario_id=planned["scenario_id"],
            run_id=planned["run_id"],
            evaluated_sha=args.evaluated_sha,
            profile=planned["profile"],
            asr_mode=planned["asr_mode"],
            language=planned["language"] or "",
            model_dir=args.model_dir,
            output_dir=str(run_root / "output"),
            work_dir=str(run_root / "work"),
            private_dir=str(private_dir / "reports"),
            device=args.device,
            compute_type=args.compute_type,
            hotword_prompt="",
            input_duration=float(sample.get("duration_seconds", 0.0)),
        ))
        reports.append(report)
    summary = validate_campaign_reports(contract, reports)
    _write_json(private_dir / "campaign.summary.local.json", summary)
    return summary


def _effective_value(report: dict, field: str, fallback: object = None) -> object:
    row = report.get("effective_asr_config", {}).get(field, {})
    return row.get("value", fallback) if isinstance(row, dict) else fallback


def _effective_config_snapshot(language_report: dict, *, input_sha256: str) -> dict:
    retry = language_report.get("asr_retry", {})
    resegment = language_report.get("resegment_summary", {})
    hotword = _effective_value(language_report, "asr_hotword_prompt", "")
    return {
        "model": language_report.get("model"),
        "quality_preset": language_report.get("quality_preset"),
        "asr_mode": language_report.get("asr_mode"),
        "language": language_report.get("forced_language"),
        "beam_size": language_report.get("beam_size"),
        "vad_filter": language_report.get("vad_filter"),
        "word_timestamps": bool(language_report.get("word_timestamps")),
        "resegment_subtitles": bool(resegment.get("enabled")),
        "asr_retry_mode": retry.get("mode"),
        "asr_hotword_prompt_sha256": _sha256_text(hotword),
        "device": language_report.get("device"),
        "compute_type": language_report.get("compute_type"),
        "local_files_only": language_report.get("local_files_only") is True,
        "input_sha256": input_sha256,
    }


def _decode_config_sha256(config: dict) -> str:
    fields = (
        "model", "asr_mode", "language", "beam_size", "vad_filter",
        "word_timestamps", "resegment_subtitles", "asr_hotword_prompt_sha256",
    )
    return _sha256_json({field: config.get(field) for field in fields})


def _runtime_config_sha256(config: dict) -> str:
    fields = ("device", "compute_type", "local_files_only")
    return _sha256_json({field: config.get(field) for field in fields})


def compare_quality_control(control: dict, quality: dict) -> dict:
    if control.get("profile") != "large-control" or quality.get("profile") != "quality":
        raise ValueError("Comparison requires large-control and quality reports")
    if control.get("scenario_id") != quality.get("scenario_id"):
        raise ValueError("Comparison reports belong to different scenarios")
    left = control.get("effective_config", {})
    right = quality.get("effective_config", {})
    differences = sorted(
        key for key in set(left) | set(right)
        if left.get(key) != right.get(key)
    )
    assertions = {
        "effective_config_diff": differences,
        "effective_config_diff_whitelist": list(CONFIG_DIFF_WHITELIST),
        "effective_config_diff_valid": differences == list(CONFIG_DIFF_WHITELIST),
        "invariant_fields_match": all(left.get(key) == right.get(key) for key in PAIR_INVARIANT_FIELDS),
        "decode_config_hash_match": control.get("decode_config_sha256") == quality.get("decode_config_sha256"),
        "runtime_config_hash_match": control.get("runtime_config_sha256") == quality.get("runtime_config_sha256"),
        "input_hash_match": control.get("input_sha256") == quality.get("input_sha256"),
        "output_srt_hash_match": control.get("output_srt_sha256") == quality.get("output_srt_sha256"),
        "control_retry_off": left.get("asr_retry_mode") == "off",
        "quality_retry_dry_run": right.get("asr_retry_mode") == "dry_run",
    }
    if not all(value for key, value in assertions.items() if key.endswith("_match") or key.endswith("_valid") or key in {"control_retry_off", "quality_retry_dry_run"}):
        raise RuntimeError("quality and large-control acceptance contracts differ")
    return assertions


def validate_campaign_reports(contract: dict, reports: list[dict]) -> dict:
    expected = {row["run_id"]: row for row in contract.get("runs", [])}
    actual = {row.get("run_id"): row for row in reports}
    if len(expected) != 28 or set(actual) != set(expected) or len(actual) != len(reports):
        raise RuntimeError("Campaign evidence must contain exactly the planned 28 unique runs")
    for run_id, report in actual.items():
        planned = expected[run_id]
        for field in ("sample_id", "scenario_id", "profile"):
            if report.get(field) != planned[field]:
                raise RuntimeError(f"Campaign report {run_id} does not match planned {field}")
        effective = report.get("effective_config", {})
        if effective.get("asr_mode") != planned["asr_mode"]:
            raise RuntimeError(f"Campaign report {run_id} does not match planned asr_mode")
        if effective.get("language") != planned["language"]:
            raise RuntimeError(f"Campaign report {run_id} does not match planned language")
        expected_profile = planned["expected_profile_config"]
        for field in (
            "model",
            "quality_preset",
            "word_timestamps",
            "resegment_subtitles",
            "asr_retry_mode",
        ):
            if effective.get(field) != expected_profile[field]:
                raise RuntimeError(
                    f"Campaign report {run_id} does not match planned {field}"
                )
        if effective.get("local_files_only") is not True:
            raise RuntimeError(f"Campaign report {run_id} was not local-files-only")
    comparisons = []
    scenario_ids = sorted({row["scenario_id"] for row in expected.values()})
    for scenario_id in scenario_ids:
        by_profile = {
            report["profile"]: report for report in reports
            if report["scenario_id"] == scenario_id
        }
        comparisons.append({
            "scenario_id": scenario_id,
            **compare_quality_control(by_profile["large-control"], by_profile["quality"]),
        })
    return {
        "schema_version": 1,
        "campaign": contract.get("campaign"),
        "evaluated_sha": contract.get("evaluated_sha"),
        "run_count": len(reports),
        "comparison_count": len(comparisons),
        "status": "pass",
        "comparisons": comparisons,
    }


def _gpu_process_samples(pid: int, elapsed: float) -> list[dict]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_memory",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) == 2 and values[0] == str(pid):
            rows.append({"elapsed_seconds": round(elapsed, 3), "used_memory_mib": int(values[1])})
    return rows


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _git_sha(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _command_line(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _cuda_runtime_version() -> str:
    try:
        output = subprocess.check_output(
            ["nvidia-smi"], text=True, encoding="utf-8", errors="replace"
        )
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    marker = "CUDA Version:"
    if marker not in output:
        return "unavailable"
    return output.split(marker, 1)[1].split()[0]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run private v0.7.1 real-media acceptance checks.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    model_parser = subparsers.add_parser("model-preflight")
    model_parser.add_argument("--model-dir", required=True)
    model_parser.add_argument("--models", nargs="+", default=["small", "large-v3"])
    model_parser.add_argument("--device", default="cuda")
    model_parser.add_argument("--compute-type", default="float16")
    model_parser.add_argument("--output", required=True)

    fingerprint_parser = subparsers.add_parser("fingerprint")
    fingerprint_parser.add_argument("--implementation-sha", required=True)
    fingerprint_parser.add_argument("--acceptance-runner-sha", required=True)
    fingerprint_parser.add_argument("--ocr-preflight", required=True)
    fingerprint_parser.add_argument("--model-preflight", required=True)
    fingerprint_parser.add_argument("--output", required=True)

    ocr_preflight_parser = subparsers.add_parser("ocr-preflight")
    ocr_preflight_parser.add_argument("--executable", required=True)
    ocr_preflight_parser.add_argument("--archive", default="")
    ocr_preflight_parser.add_argument(
        "--release-asset",
        choices=("windows_gpu_cuda12_9", "windows_cpu"),
        default="windows_gpu_cuda12_9",
    )
    ocr_preflight_parser.add_argument("--timeout", type=float, default=120.0)
    ocr_preflight_parser.add_argument("--output", required=True)

    ocr_parser = subparsers.add_parser("ocr-run")
    ocr_parser.add_argument("--executable", required=True)
    ocr_parser.add_argument("--input", required=True)
    ocr_parser.add_argument("--output", required=True)
    ocr_parser.add_argument("--private-dir", required=True)
    ocr_parser.add_argument(
        "--runtime-root",
        default="",
        help="Optional private ASCII-only runtime root for third-party OCR temp/cache files.",
    )
    ocr_parser.add_argument("--sample-id", required=True)
    ocr_parser.add_argument("--ocr-engine", default="paddleocr")
    ocr_parser.add_argument("--language", default="fr")
    ocr_parser.add_argument("--time-start", required=True)
    ocr_parser.add_argument("--time-end", required=True)
    ocr_parser.add_argument("--crop-x", type=int, required=True)
    ocr_parser.add_argument("--crop-y", type=int, required=True)
    ocr_parser.add_argument("--crop-width", type=int, required=True)
    ocr_parser.add_argument("--crop-height", type=int, required=True)
    ocr_parser.add_argument("--conf-threshold", type=int, default=75)
    ocr_parser.add_argument("--sim-threshold", type=int, default=80)
    ocr_parser.add_argument("--max-merge-gap", type=float, default=0.09)
    ocr_parser.add_argument("--frames-to-skip", type=int, default=0)
    ocr_parser.add_argument("--use-gpu", action=argparse.BooleanOptionalAction, default=True)
    ocr_parser.add_argument("--timeout", type=float, default=7200.0)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input", required=True)
    run_parser.add_argument("--sample-id", required=True)
    run_parser.add_argument("--run-id", default="")
    run_parser.add_argument("--profile", choices=tuple(RUN_PROFILES), required=True)
    run_parser.add_argument("--model-dir", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--work-dir", required=True)
    run_parser.add_argument("--private-dir", required=True)
    run_parser.add_argument("--device", default="cuda")
    run_parser.add_argument("--compute-type", default="float16")
    run_parser.add_argument("--asr-mode", choices=("auto", "fixed", "multilingual"), default="")
    run_parser.add_argument("--language", default="")
    run_parser.add_argument("--hotword-prompt", default="")
    run_parser.add_argument("--input-duration", type=float, default=0.0)

    campaign_parser = subparsers.add_parser("campaign-plan")
    campaign_parser.add_argument("--evaluated-sha", required=True)
    campaign_parser.add_argument("--output", required=True)

    review_parser = subparsers.add_parser("review-sample")
    review_parser.add_argument("--sample-id", required=True)
    review_parser.add_argument("--evaluated-sha", required=True)
    review_parser.add_argument("--cue-count", type=int, required=True)
    review_parser.add_argument("--suspicious-index", type=int, action="append", default=[])
    review_parser.add_argument("--reuse-from", default="")
    review_parser.add_argument("--output", required=True)

    validate_parser = subparsers.add_parser("campaign-validate")
    validate_parser.add_argument("--contract", required=True)
    validate_parser.add_argument("--reports-dir", required=True)
    validate_parser.add_argument("--output", required=True)

    campaign_run_parser = subparsers.add_parser("campaign-run")
    campaign_run_parser.add_argument("--manifest", required=True)
    campaign_run_parser.add_argument("--evaluated-sha", required=True)
    campaign_run_parser.add_argument("--model-dir", required=True)
    campaign_run_parser.add_argument("--private-dir", required=True)
    campaign_run_parser.add_argument("--device", default="cuda")
    campaign_run_parser.add_argument("--compute-type", default="float16")

    args = parser.parse_args()
    if args.action == "model-preflight":
        preflight_models(args)
    elif args.action == "fingerprint":
        environment_fingerprint(args)
    elif args.action == "ocr-preflight":
        preflight_videocr(args)
    elif args.action == "ocr-run":
        run_videocr(args)
    elif args.action == "run":
        run_profile(args)
    elif args.action == "campaign-plan":
        _write_json(Path(args.output), build_campaign_contract(args.evaluated_sha))
    elif args.action == "campaign-run":
        run_campaign(args)
    elif args.action == "review-sample":
        reuse = (
            json.loads(Path(args.reuse_from).read_text(encoding="utf-8"))
            if args.reuse_from else None
        )
        payload = deterministic_review_indexes(
            sample_id=args.sample_id,
            evaluated_sha=args.evaluated_sha,
            cue_count=args.cue_count,
            suspicious_indexes=args.suspicious_index,
            reuse_from=reuse,
        )
        _write_json(Path(args.output), payload)
    else:
        contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        reports = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(Path(args.reports_dir).glob("*.run.local.json"))
        ]
        _write_json(Path(args.output), validate_campaign_reports(contract, reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
