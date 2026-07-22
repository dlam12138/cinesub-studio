from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

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
    if args.language:
        command += ["--asr-mode", "fixed", "--language", args.language]
    if args.hotword_prompt:
        command += ["--asr-hotword-prompt", args.hotword_prompt]
    return command


def run_profile(args: argparse.Namespace) -> dict:
    private_dir = Path(args.private_dir).resolve()
    private_dir.mkdir(parents=True, exist_ok=True)
    command = build_run_command(args)
    env = os.environ.copy()
    env["PYTHONPATH"] = ";".join(str(Path(__file__).resolve().parents[1] / part) for part in ("core", "pipeline", "config", "web", "tools"))
    stdout_path = private_dir / f"{args.run_id}.stdout.local.log"
    stderr_path = private_dir / f"{args.run_id}.stderr.local.log"
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
        raise RuntimeError(f"Acceptance run {args.run_id} failed with exit code {returncode}")
    input_path = Path(args.input).resolve()
    model = RUN_PROFILES[args.profile][0]
    lang_path = Path(args.output_dir).resolve() / f"{input_path.stem}.{model}.lang.json"
    language_report = json.loads(lang_path.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 1,
        "run_id": args.run_id,
        "sample_id": args.sample_id,
        "profile": args.profile,
        "input_sha256": sha256_file(input_path),
        "input_duration_seconds": args.input_duration,
        "returncode": returncode,
        "end_to_end_seconds": round(elapsed, 6),
        "device": language_report.get("device"),
        "compute_type": language_report.get("compute_type"),
        "phase_timings": language_report.get("phase_timings", {}),
        "asr_retry_report": language_report.get("asr_retry_report", {}),
        "resegment_summary": language_report.get("resegment_summary", {}),
        "word_timing_count": language_report.get("word_timing_count", 0),
        "gpu_samples": gpu_samples,
    }
    _write_json(private_dir / f"{args.run_id}.run.local.json", payload)
    return payload


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
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--profile", choices=tuple(RUN_PROFILES), required=True)
    run_parser.add_argument("--model-dir", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--work-dir", required=True)
    run_parser.add_argument("--private-dir", required=True)
    run_parser.add_argument("--device", default="cuda")
    run_parser.add_argument("--compute-type", default="float16")
    run_parser.add_argument("--language", default="fr")
    run_parser.add_argument("--hotword-prompt", default="")
    run_parser.add_argument("--input-duration", type=float, default=0.0)

    args = parser.parse_args()
    if args.action == "model-preflight":
        preflight_models(args)
    elif args.action == "fingerprint":
        environment_fingerprint(args)
    elif args.action == "ocr-preflight":
        preflight_videocr(args)
    elif args.action == "ocr-run":
        run_videocr(args)
    else:
        run_profile(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
