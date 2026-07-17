"""Reproducible, local-only ASR benchmark runner for stage three.

The public runner validates a local manifest, prepares one normalized WAV per
sample, executes each ASR configuration in an isolated worker process, and
writes transcript-free JSON/Markdown metrics.  The hidden worker entry point
exists only so process memory and GPU memory can be sampled without changing
the production transcription function.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
import wave
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


_src = Path(__file__).resolve().parents[1]
for _sub in ("core", "pipeline", "config", "web", "tools"):
    _subpath = str(_src / _sub)
    if _subpath not in sys.path:
        sys.path.insert(0, _subpath)

from encoding_utils import read_json, read_text, write_json, write_text
from code_switch_metrics import calculate_code_switch_metrics, load_language_annotations
from ffmpeg_locator import find_ffmpeg
from runtime_env import choose_device
from runtime_paths import resolve_runtime_paths


PATHS = resolve_runtime_paths(Path(__file__).resolve())
PROJECT_ROOT = PATHS.project_root
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "reports" / "asr_benchmark"
DEFAULT_TMP_DIR = PROJECT_ROOT / ".tmp" / "asr-benchmark"
REPORT_TYPE = "asr_benchmark"
SCHEMA_VERSION = 1
SUPPORTED_LANGUAGES = {"fr", "en", "zh", "mixed"}
MODEL_REPOSITORIES = {
    "small": "models--Systran--faster-whisper-small",
    "large-v3": "models--Systran--faster-whisper-large-v3",
}
SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*\S+")
CHECKPOINT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str


class BenchmarkError(RuntimeError):
    """Controlled benchmark/configuration failure."""


def normalize_for_cer(text: str) -> str:
    """Normalize text for character error rate without leaking tokenization policy."""
    text = re.sub(r"(?i)\[UNK\]", "", text)
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char
        for char in normalized
        if not char.isspace()
        and not unicodedata.category(char).startswith(("P", "S", "C"))
    )


def normalize_for_wer(text: str) -> list[str]:
    text = re.sub(r"(?i)\[UNK\]", " ", text)
    normalized = unicodedata.normalize("NFKC", text).casefold()
    cleaned = "".join(
        " " if unicodedata.category(char).startswith(("P", "S", "C")) else char
        for char in normalized
    )
    return cleaned.split()


def levenshtein_distance(reference: Sequence[Any], hypothesis: Sequence[Any]) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for ref_index, ref_item in enumerate(reference, start=1):
        current = [ref_index]
        for hyp_index, hyp_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hyp_index] + 1,
                    previous[hyp_index - 1] + (ref_item != hyp_item),
                )
            )
        previous = current
    return previous[-1]


def error_rate(reference: Sequence[Any], hypothesis: Sequence[Any]) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return levenshtein_distance(reference, hypothesis) / len(reference)


def parse_srt(path: Path) -> list[Cue]:
    raw = read_text(path, user_input=True).strip()
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            index = int(lines[0])
            start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
            start = _parse_timestamp(start_text)
            end = _parse_timestamp(end_text)
        except (ValueError, TypeError):
            continue
        cues.append(Cue(index=index, start=start, end=end, text=" ".join(lines[2:])))
    return cues


def _parse_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", value)
    if not match:
        raise ValueError(value)
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _interval_iou(left: Cue, right: Cue) -> float:
    intersection = max(0.0, min(left.end, right.end) - max(left.start, right.start))
    union = max(left.end, right.end) - min(left.start, right.start)
    return intersection / union if union > 0 else 0.0


def _timing_matches(reference: list[Cue], hypothesis: list[Cue], tolerance: float) -> list[tuple[Cue, Cue]]:
    matches: list[tuple[Cue, Cue]] = []
    for ref in reference:
        candidates = [
            hyp
            for hyp in hypothesis
            if hyp.end >= ref.start - tolerance and hyp.start <= ref.end + tolerance
        ]
        if not candidates:
            continue
        ref_midpoint = (ref.start + ref.end) / 2
        best = max(
            candidates,
            key=lambda hyp: (
                _interval_iou(ref, hyp),
                -abs(((hyp.start + hyp.end) / 2) - ref_midpoint),
            ),
        )
        matches.append((ref, best))
    return matches


def _distribution(values: Iterable[float]) -> dict[str, float | None]:
    numbers = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not numbers:
        return {"mean": None, "median": None, "p95": None}
    return {
        "mean": round(statistics.fmean(numbers), 6),
        "median": round(statistics.median(numbers), 6),
        "p95": round(_percentile(numbers, 0.95), 6),
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    fraction = position - lower
    return float(values[lower]) * (1 - fraction) + float(values[upper]) * fraction


def calculate_metrics(reference: list[Cue], hypothesis: list[Cue], language: str) -> dict[str, Any]:
    reference_text = " ".join(cue.text for cue in reference)
    hypothesis_text = " ".join(cue.text for cue in hypothesis)
    reference_chars = list(normalize_for_cer(reference_text))
    hypothesis_chars = list(normalize_for_cer(hypothesis_text))
    matches = _timing_matches(reference, hypothesis, tolerance=0.5)
    duplicate_count = sum(
        1
        for previous, current in zip(hypothesis, hypothesis[1:])
        if normalize_for_cer(previous.text)
        and normalize_for_cer(previous.text) == normalize_for_cer(current.text)
    )
    start_offsets = [abs(ref.start - hyp.start) for ref, hyp in matches]
    end_offsets = [abs(ref.end - hyp.end) for ref, hyp in matches]
    metrics: dict[str, Any] = {
        "cer": round(error_rate(reference_chars, hypothesis_chars), 6),
        "wer": None,
        "reference_cue_count": len(reference),
        "hypothesis_cue_count": len(hypothesis),
        "missed_cue_count": len(reference) - len(matches),
        "missed_cue_rate": round((len(reference) - len(matches)) / len(reference), 6) if reference else 0.0,
        "duplicate_cue_count": duplicate_count,
        "duplicate_cue_rate": round(duplicate_count / len(hypothesis), 6) if hypothesis else 0.0,
        "timing_start_offset_seconds": _distribution(start_offsets),
        "timing_end_offset_seconds": _distribution(end_offsets),
    }
    if language in {"fr", "en"}:
        metrics["wer"] = round(
            error_rate(normalize_for_wer(reference_text), normalize_for_wer(hypothesis_text)),
            6,
        )
    return metrics


def load_manifest(path: Path) -> dict[str, Any]:
    data = read_json(path, user_input=True)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise BenchmarkError("manifest schema_version must be 1")
    samples = data.get("samples")
    configurations = data.get("configurations")
    if not isinstance(samples, list) or not samples:
        raise BenchmarkError("manifest requires a non-empty samples list")
    if not isinstance(configurations, list) or not configurations:
        raise BenchmarkError("manifest requires a non-empty configurations list")

    normalized_samples: list[dict[str, Any]] = []
    sample_ids: set[str] = set()
    for raw in samples:
        if not isinstance(raw, dict):
            raise BenchmarkError("each sample must be an object")
        sample_id = _safe_id(raw.get("id"), "sample")
        if sample_id in sample_ids:
            raise BenchmarkError(f"duplicate sample id: {sample_id}")
        sample_ids.add(sample_id)
        language = str(raw.get("language") or "").strip().lower()
        if language not in SUPPORTED_LANGUAGES:
            raise BenchmarkError(f"sample '{sample_id}' has unsupported language: {language}")
        tags = raw.get("acoustic_tags")
        if not isinstance(tags, list) or not all(str(tag).strip() for tag in tags):
            raise BenchmarkError(f"sample '{sample_id}' requires acoustic_tags")
        authorization = str(raw.get("authorization") or "").strip()
        if not authorization:
            raise BenchmarkError(f"sample '{sample_id}' requires authorization")
        media = _resolve_project_path(raw.get("media"))
        reference_srt = _resolve_project_path(raw.get("reference_srt"))
        language_annotations = None
        if raw.get("language_annotations"):
            language_annotations = _resolve_project_path(raw.get("language_annotations"))
        normalized_samples.append(
            {
                "id": sample_id,
                "media": media,
                "reference_srt": reference_srt,
                "language": language,
                "acoustic_tags": [str(tag).strip() for tag in tags],
                "authorization": authorization,
                "language_annotations": language_annotations,
            }
        )

    normalized_configs: list[dict[str, Any]] = []
    config_ids: set[str] = set()
    for raw in configurations:
        if not isinstance(raw, dict):
            raise BenchmarkError("each configuration must be an object")
        config_id = _safe_id(raw.get("id"), "configuration")
        if config_id in config_ids:
            raise BenchmarkError(f"duplicate configuration id: {config_id}")
        config_ids.add(config_id)
        device = str(raw.get("device") or "").strip()
        compute_type = str(raw.get("compute_type") or "").strip()
        model = str(raw.get("model") or "").strip()
        if device not in {"cpu", "cuda"} or not compute_type or not model:
            raise BenchmarkError(f"configuration '{config_id}' requires model/device/compute_type")
        normalized_configs.append(
            {
                "id": config_id,
                "model": model,
                "device": device,
                "compute_type": compute_type,
                "beam_size": int(raw.get("beam_size", 5)),
                "vad_filter": raw.get("vad_filter", True) is True,
                "condition_on_previous_text": raw.get("condition_on_previous_text", True) is True,
                "language": raw.get("language"),
                "local_files_only": True,
            }
        )

    routing_config_id = str(data.get("routing_config_id") or "").strip()
    if routing_config_id and routing_config_id not in config_ids:
        raise BenchmarkError(f"routing_config_id not found: {routing_config_id}")
    return {
        "schema_version": 1,
        "samples": normalized_samples,
        "configurations": normalized_configs,
        "routing_config_id": routing_config_id,
    }


def _safe_id(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", result):
        raise BenchmarkError(f"invalid {label} id: {result!r}")
    return result


def _resolve_project_path(value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        raise BenchmarkError("manifest path must not be empty")
    path = Path(text).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _filter_items(items: list[dict[str, Any]], selected: list[str], label: str) -> list[dict[str, Any]]:
    if not selected:
        return items
    wanted = set(selected)
    available = {item["id"] for item in items}
    missing = wanted - available
    if missing:
        raise BenchmarkError(f"unknown {label}: {', '.join(sorted(missing))}")
    return [item for item in items if item["id"] in wanted]


def preflight(samples: list[dict[str, Any]], configs: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if find_ffmpeg(PROJECT_ROOT) is None:
        errors.append("project-local FFmpeg is unavailable")
    for sample in samples:
        if not sample["media"].is_file():
            errors.append(f"sample '{sample['id']}' media is missing")
        if not sample["reference_srt"].is_file():
            errors.append(f"sample '{sample['id']}' reference SRT is missing")
        elif not parse_srt(sample["reference_srt"]):
            errors.append(f"sample '{sample['id']}' reference SRT has no valid cues")
        if sample.get("language_annotations"):
            try:
                load_language_annotations(sample["language_annotations"])
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"sample '{sample['id']}' language annotations invalid: {_sanitize(str(exc))}")
    for config in configs:
        if not _model_available(config["model"]):
            errors.append(f"configuration '{config['id']}' model is not available locally: {config['model']}")
        if config["device"] == "cuda":
            try:
                selected, _warnings = choose_device("cuda")
                if selected != "cuda":
                    errors.append(f"configuration '{config['id']}' did not select CUDA")
            except RuntimeError as exc:
                errors.append(f"configuration '{config['id']}' CUDA unavailable: {_sanitize(str(exc))}")
    return errors


def _model_available(model: str) -> bool:
    direct = Path(model).expanduser()
    if direct.is_dir():
        return True
    repository = MODEL_REPOSITORIES.get(model, f"models--Systran--faster-whisper-{model}")
    snapshots = PROJECT_ROOT / "models" / repository / "snapshots"
    return snapshots.is_dir() and any(child.is_dir() for child in snapshots.iterdir())


def corpus_fingerprint(samples: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for sample in sorted(samples, key=lambda item: item["id"]):
        digest.update(sample["id"].encode("utf-8"))
        digest.update(sample["language"].encode("utf-8"))
        digest.update("\0".join(sample["acoustic_tags"]).encode("utf-8"))
        digest.update(bytes.fromhex(_sha256_file(sample["media"])))
        digest.update(bytes.fromhex(_sha256_file(sample["reference_srt"])))
        if sample.get("language_annotations"):
            digest.update(bytes.fromhex(_sha256_file(sample["language_annotations"])))
    return digest.hexdigest().upper()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _prepare_audio(sample: dict[str, Any], temp_root: Path) -> tuple[Path, float]:
    from transcribe import convert_to_whisper_wav

    audio_path = temp_root / "audio" / f"{sample['id']}.16k.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    convert_to_whisper_wav(sample["media"], audio_path)
    with wave.open(str(audio_path), "rb") as handle:
        duration = handle.getnframes() / float(handle.getframerate())
    if duration < 30 or duration > 90:
        raise BenchmarkError(f"sample '{sample['id']}' duration must be 30-90 seconds; got {duration:.3f}")
    return audio_path, duration


def run_worker_process(
    sample: dict[str, Any],
    config: dict[str, Any],
    repeat: int,
    audio_path: Path,
    duration: float,
    temp_root: Path,
) -> dict[str, Any]:
    run_dir = temp_root / "runs" / sample["id"] / config["id"] / f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    srt_path = run_dir / "hypothesis.srt"
    job_path = run_dir / "job.json"
    resource_path = run_dir / "resources.json"
    paired_path = run_dir / "paired.json"
    write_json(
        job_path,
        {
            "audio_path": str(audio_path),
            "srt_path": str(srt_path),
            "model": config["model"],
            "device": config["device"],
            "compute_type": config["compute_type"],
            "language": config.get("language"),
            "beam_size": config["beam_size"],
            "vad_filter": config["vad_filter"],
            "condition_on_previous_text": config["condition_on_previous_text"],
            "decode_options": config.get("decode_options"),
            "candidate_strategy": config.get("candidate_strategy", "decode"),
            "resource_path": str(resource_path),
            "paired_path": str(paired_path),
        },
    )
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "__worker", str(job_path)]
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    peak_working_set: int | None = None
    peak_gpu_mib: float | None = None
    resource_warnings: set[str] = set()
    while process.poll() is None:
        working_set = _windows_peak_working_set(process.pid)
        if working_set is None:
            resource_warnings.add("Windows PeakWorkingSetSize unavailable")
        else:
            peak_working_set = max(peak_working_set or 0, working_set)
        if config["device"] == "cuda":
            gpu_mib = _gpu_memory_mib(process.pid)
            if gpu_mib is None:
                resource_warnings.add("nvidia-smi per-process GPU memory unavailable")
            else:
                peak_gpu_mib = max(peak_gpu_mib or 0.0, gpu_mib)
        time.sleep(0.25)
    stdout, stderr = process.communicate()
    elapsed = time.perf_counter() - started
    if resource_path.is_file():
        worker_resources = read_json(resource_path, user_input=True)
        if isinstance(worker_resources, dict):
            worker_peak = worker_resources.get("peak_working_set_bytes")
            worker_gpu = worker_resources.get("peak_gpu_memory_mib")
            if isinstance(worker_peak, (int, float)):
                peak_working_set = int(worker_peak)
                resource_warnings.discard("Windows PeakWorkingSetSize unavailable")
            if isinstance(worker_gpu, (int, float)):
                peak_gpu_mib = float(worker_gpu)
                resource_warnings.discard("nvidia-smi per-process GPU memory unavailable")
            for warning in worker_resources.get("warnings", []):
                resource_warnings.add(str(warning))
    write_text(
        run_dir / "worker.log",
        "STDOUT\n" + _sanitize(stdout) + "\n\nSTDERR\n" + _sanitize(stderr) + "\n",
    )
    result: dict[str, Any] = {
        "repeat": repeat,
        "status": "completed" if process.returncode == 0 and srt_path.is_file() else "failed",
        "performance": {
            "elapsed_seconds": round(elapsed, 6),
            "real_time_factor": round(elapsed / duration, 6),
            "peak_working_set_bytes": peak_working_set,
            "peak_gpu_memory_mib": round(peak_gpu_mib, 3) if peak_gpu_mib is not None else None,
            "warnings": sorted(resource_warnings),
        },
    }
    if result["status"] == "failed":
        result["error"] = _sanitize((stderr or stdout or f"worker exited {process.returncode}")[-1000:])
        return result
    result["metrics"] = calculate_metrics(
        parse_srt(sample["reference_srt"]),
        parse_srt(srt_path),
        sample["language"],
    )
    annotation_path = sample.get("language_annotations")
    annotations = load_language_annotations(annotation_path) if annotation_path else None
    result["code_switch_metrics"] = calculate_code_switch_metrics(
        parse_srt(sample["reference_srt"]),
        parse_srt(srt_path),
        annotations,
    )
    if paired_path.is_file():
        paired = read_json(paired_path, user_input=True)
        baseline_srt = run_dir / "baseline.srt"
        if baseline_srt.is_file():
            result["paired_baseline_metrics"] = calculate_metrics(
                parse_srt(sample["reference_srt"]), parse_srt(baseline_srt), sample["language"]
            )
            result["paired_baseline_code_switch_metrics"] = calculate_code_switch_metrics(
                parse_srt(sample["reference_srt"]),
                parse_srt(baseline_srt),
                annotations,
            )
        result["paired_performance"] = paired
    lang_path = srt_path.with_suffix(".lang.json")
    if lang_path.is_file():
        lang = read_json(lang_path)
        result["language_detection"] = {
            "source_language": lang.get("source_language"),
            "language_probability": lang.get("language_probability"),
            "forced_language": lang.get("forced_language"),
        }
    return result


def _windows_peak_working_set(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
        if not handle:
            return None
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), ctypes.sizeof(counters)
        )
        ctypes.windll.kernel32.CloseHandle(handle)
        return int(counters.PeakWorkingSetSize) if ok else None
    except (AttributeError, OSError, ValueError):
        return None


def _gpu_memory_mib(pid: int) -> float | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    values: list[float] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 2 and parts[0] == str(pid):
            try:
                values.append(float(parts[1]))
            except ValueError:
                continue
    return sum(values) if values else None


def run_routing_dry_run(
    sample: dict[str, Any], config: dict[str, Any], audio_path: Path, temp_root: Path,
    *, timeout_seconds: float | None = None,
) -> dict[str, Any]:
    routing_dir = temp_root / "routing" / sample["id"]
    routing_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-B",
        str(PROJECT_ROOT / "src" / "core" / "transcribe.py"),
        str(audio_path),
        "--model",
        config["model"],
        "--device",
        config["device"],
        "--compute-type",
        config["compute_type"],
        "--beam-size",
        str(config["beam_size"]),
        "--local-files-only",
        "--output-dir",
        str(routing_dir / "output"),
        "--work-dir",
        str(routing_dir / "work"),
        "--segment-asr-routing",
        "dry_run",
    ]
    if config.get("language"):
        command.extend(["--language", str(config["language"])])
    try:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "sample_id": sample["id"],
            "mode": "dry_run",
            "status": "failed",
            "subtitle_output_affected": False,
            "error": f"routing timed out after {timeout_seconds:g} seconds",
            "failure_category": "timeout",
        }
    reports: list[tuple[Path, dict[str, Any]]] = []
    for report_path in (routing_dir / "output" / "reports").rglob("*.json"):
        try:
            candidate = read_json(report_path)
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(candidate, dict)
            and candidate.get("report_type") == "segment_asr_routing_integration"
        ):
            reports.append((report_path, candidate))
    if result.returncode != 0 or not reports:
        return {
            "sample_id": sample["id"],
            "mode": "dry_run",
            "status": "failed",
            "subtitle_output_affected": False,
            "error": _sanitize((result.stderr or result.stdout or "routing report missing")[-1000:]),
        }
    _report_path, report = max(reports, key=lambda item: item[0].stat().st_mtime_ns)
    analyzer = report.get("analyzer") if isinstance(report.get("analyzer"), dict) else {}
    analyzer_summary = analyzer.get("summary") if isinstance(analyzer.get("summary"), dict) else {}
    return {
        "sample_id": sample["id"],
        "mode": "dry_run",
        "status": report.get("status"),
        "subtitle_output_affected": bool(report.get("subtitle_output_affected")),
        "fallback_used": bool(report.get("fallback_used")),
        "coverage_rate": report.get("coverage_rate"),
        "classification_counts": analyzer_summary,
    }


def aggregate_results(results: list[dict[str, Any]], configs: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for config in configs:
        runs = [
            run
            for sample in results
            if sample["configuration_id"] == config["id"]
            for run in sample["runs"]
            if run.get("status") == "completed"
        ]
        metric_names = ("cer", "wer", "missed_cue_rate", "duplicate_cue_rate")
        metrics = {
            name: _distribution(
                run["metrics"][name]
                for run in runs
                if run.get("metrics", {}).get(name) is not None
            )
            for name in metric_names
        }
        code_switch_metric_names = (
            "mer",
            "post_switch_first_token_error_rate",
            "language_span_recall",
        )
        code_switch_metrics = {
            name: _distribution(
                run["code_switch_metrics"][name]
                for run in runs
                if isinstance(run.get("code_switch_metrics"), dict)
                and run["code_switch_metrics"].get(name) is not None
            )
            for name in code_switch_metric_names
        }
        performance = {
            name: _distribution(
                run["performance"][name]
                for run in runs
                if run.get("performance", {}).get(name) is not None
            )
            for name in (
                "elapsed_seconds",
                "real_time_factor",
                "peak_working_set_bytes",
                "peak_gpu_memory_mib",
            )
        }
        paired_runs = [run for run in runs if isinstance(run.get("paired_performance"), dict)]
        paired = None
        if paired_runs:
            paired = {
                "baseline_cer": _distribution(
                    run["paired_baseline_metrics"]["cer"] for run in paired_runs
                    if isinstance(run.get("paired_baseline_metrics"), dict)
                ),
                "candidate_cer": _distribution(run["metrics"]["cer"] for run in paired_runs),
                "baseline_elapsed_seconds": _distribution(
                    run["paired_performance"].get("baseline_elapsed_seconds") for run in paired_runs
                    if run["paired_performance"].get("baseline_elapsed_seconds") is not None
                ),
                "candidate_incremental_seconds": _distribution(
                    run["paired_performance"].get("candidate_incremental_seconds") for run in paired_runs
                    if run["paired_performance"].get("candidate_incremental_seconds") is not None
                ),
                "retried_windows": sum(run["paired_performance"].get("retried_window_count", 0) for run in paired_runs),
                "accepted_windows": sum(run["paired_performance"].get("accepted_window_count", 0) for run in paired_runs),
                "rejected_windows": sum(run["paired_performance"].get("rejected_window_count", 0) for run in paired_runs),
                "model_reused_all_runs": all(run["paired_performance"].get("model_reused") is True for run in paired_runs),
            }
        summaries[config["id"]] = {
            "successful_runs": len(runs),
            "failed_runs": sum(
                1
                for sample in results
                if sample["configuration_id"] == config["id"]
                for run in sample["runs"]
                if run.get("status") != "completed"
            ),
            "metrics": metrics,
            "code_switch_metrics": code_switch_metrics,
            "performance": performance,
            "paired": paired,
        }
    return summaries


def compare_with_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    compatible = report.get("corpus_fingerprint") == baseline.get("corpus_fingerprint")
    comparison: dict[str, Any] = {
        "compatible_corpus": compatible,
        "baseline_schema_version": baseline.get("schema_version"),
        "configurations": {},
    }
    if not compatible:
        comparison["warning"] = "corpus fingerprint differs; deltas are not comparable"
        return comparison
    current_summaries = report.get("configuration_summaries", {})
    baseline_summaries = baseline.get("configuration_summaries", {})
    for config_id in sorted(set(current_summaries) & set(baseline_summaries)):
        deltas: dict[str, Any] = {"metrics": {}, "performance": {}}
        for section in ("metrics", "performance"):
            for name, current_distribution in current_summaries[config_id].get(section, {}).items():
                baseline_distribution = baseline_summaries[config_id].get(section, {}).get(name, {})
                current_value = current_distribution.get("mean")
                baseline_value = baseline_distribution.get("mean")
                deltas[section][name] = (
                    round(float(current_value) - float(baseline_value), 6)
                    if current_value is not None and baseline_value is not None
                    else None
                )
        comparison["configurations"][config_id] = deltas
    return comparison


def _environment() -> dict[str, Any]:
    def version(distribution: str) -> str | None:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return None

    nvidia = None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            nvidia = result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "faster_whisper": version("faster-whisper"),
        "ctranslate2": version("ctranslate2"),
        "nvidia_gpu": nvidia,
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _sanitize(message: str) -> str:
    cleaned = str(message).replace(str(PROJECT_ROOT), "<project>")
    cleaned = SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", cleaned)
    cleaned = re.sub(r"[A-Za-z]:\\[^\r\n]+", "<local-path>", cleaned)
    return cleaned.strip()


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write a recoverable report without exposing a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write_json(temporary, data)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _checkpoint_signature(
    *, fingerprint: str, configs: list[dict[str, Any]], repeat_count: int,
    candidate_id: str | None, routing_only: bool,
) -> dict[str, Any]:
    return {
        "corpus_fingerprint": fingerprint,
        "configuration_ids": [config["id"] for config in configs],
        "repeat_count": repeat_count,
        "candidate_id": candidate_id or None,
        "routing_only": routing_only,
        "local_files_only": True,
    }


def _load_checkpoint(path: Path, expected_signature: dict[str, Any]) -> dict[str, Any]:
    try:
        checkpoint = read_json(path, user_input=True)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"checkpoint is unreadable: {_sanitize(str(exc))}") from exc
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise BenchmarkError("checkpoint schema_version is unsupported")
    if checkpoint.get("signature") != expected_signature:
        raise BenchmarkError("checkpoint signature or corpus fingerprint differs")
    results = checkpoint.get("results", [])
    routing = checkpoint.get("routing_dry_run", [])
    if not isinstance(results, list) or not isinstance(routing, list):
        raise BenchmarkError("checkpoint result lists are invalid")
    return checkpoint


def _save_checkpoint(
    path: Path, signature: dict[str, Any], results: list[dict[str, Any]],
    routing: list[dict[str, Any]], *, completed: bool = False,
) -> None:
    _atomic_write_json(
        path,
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "report_type": "asr_benchmark_checkpoint",
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "completed": completed,
            "signature": signature,
            "results": results,
            "routing_dry_run": routing,
        },
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ASR Benchmark Report",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Corpus fingerprint: `{report['corpus_fingerprint']}`",
        f"- Git commit: `{report.get('git_commit') or 'unavailable'}`",
        f"- Samples: {report['sample_count']}",
        f"- Configurations: {report['configuration_count']}",
        f"- Repeat count: {report['repeat_count']}",
        "- Local files only: `true`",
        "",
        "## Configuration Summary",
        "",
        "| Configuration | Runs | CER mean | WER mean | Missed mean | Duplicate mean | RTF mean | Peak RAM mean | Peak GPU MiB mean |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for config_id, summary in report.get("configuration_summaries", {}).items():
        metric = summary["metrics"]
        performance = summary["performance"]
        lines.append(
            f"| `{config_id}` | {summary['successful_runs']} | {_fmt(metric['cer']['mean'])} | "
            f"{_fmt(metric['wer']['mean'])} | {_fmt(metric['missed_cue_rate']['mean'])} | "
            f"{_fmt(metric['duplicate_cue_rate']['mean'])} | {_fmt(performance['real_time_factor']['mean'])} | "
            f"{_fmt(performance['peak_working_set_bytes']['mean'])} | {_fmt(performance['peak_gpu_memory_mib']['mean'])} |"
        )
    code_switch = report.get("code_switch_summary", {})
    if code_switch:
        lines.extend(
            [
                "",
                "## Code-switch Metrics",
                "",
                "| Configuration | MER mean | Post-switch first-token error mean | Language-span recall mean |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for config_id, metrics in code_switch.items():
            lines.append(
                f"| `{config_id}` | {_fmt(metrics['mer']['mean'])} | "
                f"{_fmt(metrics['post_switch_first_token_error_rate']['mean'])} | "
                f"{_fmt(metrics['language_span_recall']['mean'])} |"
            )
    if report.get("routing_dry_run"):
        lines.extend(["", "## Segment Routing Dry Run", ""])
        for item in report["routing_dry_run"]:
            lines.append(
                f"- `{item['sample_id']}`: mode=`dry_run`, status=`{item.get('status')}`, "
                f"subtitle_output_affected=`{str(item.get('subtitle_output_affected', False)).lower()}`"
            )
    if report.get("baseline_comparison"):
        lines.extend(["", "## Baseline Comparison", ""])
        lines.append(
            f"- Compatible corpus: `{str(report['baseline_comparison'].get('compatible_corpus')).lower()}`"
        )
        if report["baseline_comparison"].get("warning"):
            lines.append(f"- Warning: {report['baseline_comparison']['warning']}")
    lines.extend(
        [
            "",
            "## Privacy",
            "",
            "This report contains metrics and hashes only. Transcript text and absolute media/reference paths are omitted.",
            "",
        ]
    )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any] | None:
    manifest_path = _resolve_project_path(args.manifest)
    manifest = load_manifest(manifest_path)
    samples = _filter_items(manifest["samples"], args.sample, "sample")
    configs = _filter_items(manifest["configurations"], args.config, "configuration")
    candidate_id = getattr(args, "candidate", None)
    if candidate_id:
        from asr_strategy import get_candidate

        adjusted = []
        for config in configs:
            candidate = get_candidate(candidate_id, "dry_run", config["model"])
            if candidate.strategy not in {"decode", "local_retry", "local_retry_selective"}:
                raise BenchmarkError(
                    f"candidate {candidate.candidate_id} requires pipeline orchestration"
                )
            item = dict(config)
            item["candidate_id"] = candidate.candidate_id
            item["candidate_version"] = candidate.version
            item["decode_options"] = asdict(candidate.decode_options)
            item["candidate_strategy"] = candidate.strategy
            adjusted.append(item)
        configs = adjusted
    routing_config: dict[str, Any] | None = None
    if args.include_routing_dry_run:
        routing_id = manifest.get("routing_config_id") or next(
            (config["id"] for config in configs if config["device"] == "cuda"),
            configs[0]["id"],
        )
        routing_config = next((config for config in configs if config["id"] == routing_id), None)
        if routing_config is None:
            raise BenchmarkError("routing configuration must be included by --config")
    repeat_count = args.repeat if args.repeat is not None else 3
    if repeat_count < 1:
        raise BenchmarkError("--repeat must be at least 1")
    output_dir = _resolve_project_path(args.output_dir)
    routing_only = bool(getattr(args, "routing_only", False))
    if routing_only:
        args.include_routing_dry_run = True
        if routing_config is None:
            routing_id = manifest.get("routing_config_id") or configs[0]["id"]
            routing_config = next((config for config in configs if config["id"] == routing_id), None)
    call_count = 0 if routing_only else len(samples) * len(configs) * repeat_count
    print(f"Samples: {len(samples)}")
    print(f"Configurations: {', '.join(config['id'] for config in configs)}")
    print(f"ASR calls: {call_count}")
    print(f"Models: {', '.join(sorted({config['model'] for config in configs}))}")
    print(f"Devices: {', '.join(sorted({config['device'] for config in configs}))}")
    print(f"Output directory: {output_dir}")
    print("Local files only: True")

    fingerprint = corpus_fingerprint(samples)

    errors = preflight(samples, configs)
    if errors:
        raise BenchmarkError("preflight failed:\n- " + "\n- ".join(errors))
    if args.dry_run:
        print("Dry run complete; no ASR work or report files were created.")
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    requested_run_id = str(getattr(args, "run_id", "") or "").strip()
    run_id = _safe_id(requested_run_id, "run") if requested_run_id else stamp
    temp_root = DEFAULT_TMP_DIR / run_id
    checkpoint_path = output_dir / "checkpoints" / f"{run_id}.json"
    signature = _checkpoint_signature(
        fingerprint=fingerprint,
        configs=configs,
        repeat_count=repeat_count,
        candidate_id=candidate_id,
        routing_only=routing_only,
    )
    prepared: dict[str, tuple[Path, float]] = {}
    results: list[dict[str, Any]] = []
    routing: list[dict[str, Any]] = []
    resumed = False
    if bool(getattr(args, "resume", False)):
        if not requested_run_id:
            raise BenchmarkError("--resume requires --run-id")
        if not checkpoint_path.is_file():
            raise BenchmarkError("checkpoint does not exist for --run-id")
        checkpoint = _load_checkpoint(checkpoint_path, signature)
        results = list(checkpoint["results"])
        routing = list(checkpoint["routing_dry_run"])
        resumed = True
    completed_result_keys = {
        (item.get("sample_id"), item.get("configuration_id")) for item in results
    }
    for sample in samples:
        audio_path, duration = _prepare_audio(sample, temp_root)
        prepared[sample["id"]] = (audio_path, duration)
        for config in ([] if routing_only else configs):
            if (sample["id"], config["id"]) in completed_result_keys:
                print(f"[{sample['id']}] {config['id']} reused from checkpoint", flush=True)
                continue
            print(f"[{sample['id']}] {config['id']} x{repeat_count}")
            runs = []
            for repeat in range(1, repeat_count + 1):
                print(
                    f"  repeat {repeat}/{repeat_count}: model={config['model']} "
                    f"device={config['device']}/{config['compute_type']}",
                    flush=True,
                )
                runs.append(
                    run_worker_process(sample, config, repeat, audio_path, duration, temp_root)
                )
            results.append(
                {
                    "sample_id": sample["id"],
                    "language": sample["language"],
                    "acoustic_tags": sample["acoustic_tags"],
                    "media_sha256": _sha256_file(sample["media"]),
                    "reference_sha256": _sha256_file(sample["reference_srt"]),
                    "duration_seconds": round(duration, 6),
                    "configuration_id": config["id"],
                    "runs": runs,
                }
            )
            _save_checkpoint(checkpoint_path, signature, results, routing)

    if args.include_routing_dry_run:
        assert routing_config is not None
        completed_routing_ids = {item.get("sample_id") for item in routing}
        for sample in samples:
            if sample["id"] in completed_routing_ids:
                print(f"[{sample['id']}] routing reused from checkpoint", flush=True)
                continue
            print(f"[{sample['id']}] segment routing dry_run", flush=True)
            audio_path, _duration = prepared[sample["id"]]
            routing.append(
                run_routing_dry_run(
                    sample,
                    routing_config,
                    audio_path,
                    temp_root,
                    timeout_seconds=getattr(args, "routing_timeout_seconds", None),
                )
            )
            _save_checkpoint(checkpoint_path, signature, results, routing)

    had_run_failure = any(
        run.get("status") != "completed"
        for sample_result in results
        for run in sample_result["runs"]
    )
    had_routing_failure = any(
        item.get("status") == "failed" or item.get("subtitle_output_affected") is True
        for item in routing
    )
    summaries = aggregate_results(results, configs)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_type": REPORT_TYPE,
        "status": "failed" if had_run_failure or had_routing_failure else "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_fingerprint": fingerprint,
        "git_commit": _git_commit(),
        "environment": _environment(),
        "sample_count": len(samples),
        "configuration_count": len(configs),
        "repeat_count": repeat_count,
        "local_files_only": True,
        "configurations": configs,
        "results": results,
        "configuration_summaries": summaries,
        "code_switch_summary": {
            config_id: summary.get("code_switch_metrics", {})
            for config_id, summary in summaries.items()
        },
        "routing_dry_run": routing,
        "recovery": {
            "run_id": run_id,
            "resumed": resumed,
            "checkpoint_completed": True,
        },
    }
    if args.baseline:
        baseline = read_json(_resolve_project_path(args.baseline), user_input=True)
        if not isinstance(baseline, dict) or baseline.get("report_type") != REPORT_TYPE:
            raise BenchmarkError("--baseline must be an ASR benchmark JSON report")
        report["baseline_comparison"] = compare_with_baseline(report, baseline)

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"asr_benchmark.{stamp}.json"
    markdown_path = output_dir / f"asr_benchmark.{stamp}.md"
    _atomic_write_json(json_path, report)
    write_text(markdown_path, render_markdown(report))
    _save_checkpoint(checkpoint_path, signature, results, routing, completed=True)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return report


def _worker(job_path: Path) -> int:
    job = read_json(job_path, user_input=True)
    from transcribe import transcribe_to_srt

    stop_sampling = threading.Event()
    resources: dict[str, Any] = {
        "peak_working_set_bytes": None,
        "peak_gpu_memory_mib": None,
        "warnings": [],
    }

    def sample_resources() -> None:
        peak_working_set: int | None = None
        peak_gpu_mib: float | None = None
        while not stop_sampling.is_set():
            working_set = _windows_peak_working_set(os.getpid())
            if working_set is not None:
                peak_working_set = max(peak_working_set or 0, working_set)
            if job["device"] == "cuda":
                gpu_mib = _gpu_memory_mib(os.getpid())
                if gpu_mib is not None:
                    peak_gpu_mib = max(peak_gpu_mib or 0.0, gpu_mib)
            stop_sampling.wait(0.25)
        final_working_set = _windows_peak_working_set(os.getpid())
        if final_working_set is not None:
            peak_working_set = max(peak_working_set or 0, final_working_set)
        resources["peak_working_set_bytes"] = peak_working_set
        resources["peak_gpu_memory_mib"] = peak_gpu_mib
        if peak_working_set is None:
            resources["warnings"].append("Windows PeakWorkingSetSize unavailable")
        if job["device"] == "cuda" and peak_gpu_mib is None:
            resources["warnings"].append("nvidia-smi per-process GPU memory unavailable")

    sampler = threading.Thread(target=sample_resources, name="asr-benchmark-resource-sampler", daemon=True)
    sampler.start()
    try:
        from asr_strategy import (
            AsrDecodeOptions,
            TranscriptionArtifact,
            merge_retry_artifact,
            retry_windows,
            selective_merge_retry_artifact,
            write_artifact_srt,
        )
        from transcribe import create_asr_session

        common = dict(
            audio_path=Path(job["audio_path"]),
            model_name=job["model"],
            model_dir=PROJECT_ROOT / "models",
            device=job["device"],
            compute_type=job["compute_type"],
            language=job.get("language"),
            beam_size=int(job["beam_size"]),
            vad_filter=bool(job["vad_filter"]),
            local_files_only=True,
            condition_on_previous_text=bool(job["condition_on_previous_text"]),
        )
        output_path = Path(job["srt_path"])
        options = AsrDecodeOptions(**job["decode_options"]) if job.get("decode_options") else None
        if job.get("candidate_strategy") in {"local_retry", "local_retry_selective"}:
            baseline_artifacts: list[TranscriptionArtifact] = []
            shared_session = None
            if job.get("candidate_strategy") == "local_retry_selective":
                shared_session = create_asr_session(
                    model_name=job["model"], model_dir=PROJECT_ROOT / "models",
                    device=job["device"], compute_type=job["compute_type"], local_files_only=True,
                )
            baseline_started = time.perf_counter()
            transcribe_to_srt(
                srt_path=output_path.with_name("baseline.srt"), artifact_out=baseline_artifacts,
                decode_options=None, session=shared_session, **common,
            )
            baseline_elapsed = time.perf_counter() - baseline_started
            windows = retry_windows(baseline_artifacts[0])
            if not windows:
                write_artifact_srt(output_path, baseline_artifacts[0])
                candidate_elapsed = 0.0
                selections = ()
            elif job.get("candidate_strategy") == "local_retry_selective":
                candidate_started = time.perf_counter()
                retry_cues = []
                for index, (window_start, window_end) in enumerate(windows):
                    window_artifacts: list[TranscriptionArtifact] = []
                    window_options = AsrDecodeOptions(**{
                        **asdict(options), "clip_timestamps": f"{window_start:.3f},{window_end:.3f}"
                    })
                    try:
                        transcribe_to_srt(
                            srt_path=output_path.with_name(f"retry-{index}.srt"),
                            artifact_out=window_artifacts, decode_options=window_options,
                            session=shared_session, **common,
                        )
                    except Exception:
                        window_artifacts = []
                    if window_artifacts:
                        retry_cues.extend(window_artifacts[0].cues)
                retry_artifact = TranscriptionArtifact(
                    cues=tuple(sorted(retry_cues, key=lambda cue: (cue.start, cue.end))),
                    language=baseline_artifacts[0].language,
                    language_probability=baseline_artifacts[0].language_probability,
                    duration_seconds=baseline_artifacts[0].duration_seconds,
                )
                merged, selections = selective_merge_retry_artifact(
                    baseline_artifacts[0], retry_artifact, windows
                )
                write_artifact_srt(output_path, merged)
                candidate_elapsed = time.perf_counter() - candidate_started
            else:
                candidate_started = time.perf_counter()
                clips = ",".join(f"{start:.3f},{end:.3f}" for start, end in windows)
                retry_options = AsrDecodeOptions(**{**asdict(options), "clip_timestamps": clips})
                retry_artifacts: list[TranscriptionArtifact] = []
                transcribe_to_srt(
                    srt_path=output_path.with_name("retry.srt"), artifact_out=retry_artifacts,
                    decode_options=retry_options, **common,
                )
                write_artifact_srt(
                    output_path,
                    merge_retry_artifact(baseline_artifacts[0], retry_artifacts[0], windows),
                )
                candidate_elapsed = time.perf_counter() - candidate_started
                selections = ()
            write_json(Path(job["paired_path"]), {
                "model_reused": shared_session is not None,
                "baseline_elapsed_seconds": round(baseline_elapsed, 6),
                "candidate_incremental_seconds": round(candidate_elapsed, 6),
                "retried_window_count": len(windows),
                "accepted_window_count": sum(1 for item in selections if item.accepted),
                "rejected_window_count": sum(1 for item in selections if not item.accepted),
                "rejection_reasons": {
                    reason: sum(1 for item in selections if item.reason == reason)
                    for reason in sorted({item.reason for item in selections if not item.accepted})
                },
                "quality_deltas": [item.safe_summary() for item in selections],
            })
        else:
            transcribe_to_srt(srt_path=output_path, decode_options=options, **common)
    finally:
        stop_sampling.set()
        sampler.join(timeout=5)
        write_json(Path(job["resource_path"]), resources)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local-only stage-three ASR benchmark.")
    parser.add_argument("--manifest", required=True, help="Local benchmark manifest JSON.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="JSON/Markdown report directory.")
    parser.add_argument("--sample", action="append", default=[], help="Run only this sample id; repeatable.")
    parser.add_argument("--config", action="append", default=[], help="Run only this configuration id; repeatable.")
    parser.add_argument("--repeat", type=int, default=None, help="Repeat each sample/configuration; default 3.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the plan without ASR work.")
    parser.add_argument("--baseline", default=None, help="Existing benchmark JSON used for delta reporting.")
    parser.add_argument("--candidate", default=None, help="Registered decode candidate applied to selected configurations.")
    parser.add_argument(
        "--include-routing-dry-run",
        action="store_true",
        help="Run the existing segment routing in dry_run mode after benchmark timing.",
    )
    parser.add_argument(
        "--routing-only", action="store_true",
        help="Prepare samples and run routing evidence without the benchmark ASR matrix.",
    )
    parser.add_argument(
        "--routing-timeout-seconds", type=float, default=None,
        help="Per-sample routing timeout; a timeout is checkpointed as a failed sample.",
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Stable checkpoint id for an interruptible benchmark run.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume completed sample/configuration items from --run-id checkpoint.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "__worker":
        if len(argv) != 2:
            print("worker requires a job JSON path", file=sys.stderr)
            return 2
        return _worker(Path(argv[1]))
    args = _build_parser().parse_args(argv)
    if args.routing_timeout_seconds is not None and args.routing_timeout_seconds <= 0:
        print("ERROR: --routing-timeout-seconds must be greater than zero", file=sys.stderr)
        return 2
    try:
        report = run_benchmark(args)
    except (BenchmarkError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {_sanitize(str(exc))}", file=sys.stderr)
        return 1
    return 1 if report is not None and report.get("status") != "completed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
