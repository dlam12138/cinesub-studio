from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from encoding_utils import run_text, write_json, write_text
from ffmpeg_locator import find_ffmpeg, find_ffprobe
from runtime_env import add_project_cuda_to_process, choose_device, default_compute_type
from runtime_paths import resolve_runtime_paths


PATHS = resolve_runtime_paths(Path(__file__).resolve())
PROJECT_ROOT = PATHS.project_root
MODEL_DIR = PROJECT_ROOT / "models"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "reports" / "asr_evidence"
DEFAULT_TMP_DIR = PROJECT_ROOT / ".tmp" / "asr-evidence"
LOW_CONFIDENCE_THRESHOLD = 0.50
TEXT_PREVIEW_LIMIT = 500

LIMITATIONS = [
    "This is sampled evidence only.",
    "It is not production ASR routing.",
    "It does not guarantee every spoken language was sampled.",
    "Production transcription still uses the existing task-level behavior.",
]


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SampleWindow:
    index: int
    start_seconds: float
    end_seconds: float


def main() -> int:
    args = parse_args()
    try:
        report = run_evidence_cli(args)
    except ModelUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"JSON report: {report['json_path']}")
    print(f"Markdown report: {report['markdown_path']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample a media file and collect mixed-language ASR evidence."
    )
    parser.add_argument("input", help="Input video or audio file.")
    parser.add_argument("--model", default="small", help="Whisper model name.")
    parser.add_argument(
        "--device",
        default="auto",
        choices=["cpu", "cuda", "auto"],
        help="Run device. auto prefers CUDA and falls back to CPU.",
    )
    parser.add_argument("--compute-type", default=None, help="Override faster-whisper compute type.")
    parser.add_argument("--samples", type=int, default=8, help="Number of sample windows.")
    parser.add_argument("--sample-seconds", type=float, default=30, help="Length of each sample window.")
    parser.add_argument(
        "--output-dir",
        default="output/reports/asr_evidence",
        help="Directory for JSON and Markdown evidence reports.",
    )
    parser.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow faster-whisper to download missing model files.",
    )
    return parser.parse_args()


def run_evidence_cli(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        raise RuntimeError(f"Input file not found: {input_path}")

    output_dir = _project_path(args.output_dir)
    tmp_dir = DEFAULT_TMP_DIR / _run_id(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(PROJECT_ROOT / ".cache" / "huggingface" / "hub"))

    ffmpeg = find_ffmpeg(PROJECT_ROOT)
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Place bundled ffmpeg.exe in tools/ffmpeg/bin/.")
    ffprobe = find_ffprobe(PROJECT_ROOT)
    if not ffprobe:
        raise RuntimeError("ffprobe not found. Place bundled ffprobe.exe in tools/ffmpeg/bin/.")

    duration = probe_duration(input_path, ffprobe)
    windows = plan_sample_windows(duration, args.samples, args.sample_seconds)

    add_project_cuda_to_process()
    try:
        device, device_warnings = choose_device(args.device)
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    compute_type = default_compute_type(device, args.compute_type)

    model = create_whisper_model(
        model_name=args.model,
        device=device,
        compute_type=compute_type,
        local_files_only=not args.allow_model_download,
    )

    samples: list[dict[str, Any]] = []
    for window in windows:
        sample_path = tmp_dir / f"sample-{window.index:03}.wav"
        try:
            extract_sample(input_path, sample_path, window, ffmpeg)
            samples.append(analyze_sample(model, sample_path, window))
        except Exception as exc:
            samples.append(sample_error(window, exc))
        finally:
            try:
                sample_path.unlink(missing_ok=True)
            except OSError:
                pass

    report = build_report(
        input_path=input_path,
        model_name=args.model,
        device=device,
        compute_type=compute_type,
        allow_model_download=args.allow_model_download,
        sample_count=args.samples,
        sample_seconds=args.sample_seconds,
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        duration_seconds=duration,
        samples=samples,
        device_warnings=device_warnings,
    )
    paths = write_reports(report, output_dir, input_path)
    report.update(paths)
    return report


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _run_id(input_path: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{sanitize_stem(input_path.stem)}-{stamp}"


def sanitize_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return cleaned or "media"


def probe_duration(input_path: Path, ffprobe: str) -> float:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_path),
    ]
    result = run_text(command, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffprobe failed to read media duration: {detail}")
    try:
        duration = float((result.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe did not return a valid media duration.") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("Media duration must be greater than zero.")
    return duration


def plan_sample_windows(
    duration_seconds: float,
    sample_count: int,
    sample_seconds: float,
) -> list[SampleWindow]:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    count = max(1, int(sample_count or 1))
    length = max(1.0, float(sample_seconds or 1.0))
    duration = float(duration_seconds)
    if duration <= length:
        return [SampleWindow(index=1, start_seconds=0.0, end_seconds=round(duration, 3))]

    max_start = duration - length
    if count == 1:
        starts = [max_start / 2]
    else:
        starts = [(max_start * i) / (count - 1) for i in range(count)]

    windows: list[SampleWindow] = []
    for index, start in enumerate(starts, start=1):
        start = round(max(0.0, start), 3)
        end = round(min(duration, start + length), 3)
        windows.append(SampleWindow(index=index, start_seconds=start, end_seconds=end))
    return windows


def extract_sample(
    input_path: Path,
    sample_path: Path,
    window: SampleWindow,
    ffmpeg: str,
) -> None:
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, window.end_seconds - window.start_seconds)
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{window.start_seconds:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(sample_path),
    ]
    result = run_text(command, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed to extract sample: {detail}")


def create_whisper_model(
    *,
    model_name: str,
    device: str,
    compute_type: str,
    local_files_only: bool,
    model_class: Any | None = None,
) -> Any:
    if model_class is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed. Run .\\install.ps1 first.") from exc
        model_class = WhisperModel
    try:
        return model_class(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(MODEL_DIR),
            local_files_only=local_files_only,
        )
    except Exception as exc:
        if local_files_only:
            raise ModelUnavailable(
                "Model not found locally. Re-run with --allow-model-download, or pre-cache the model."
            ) from exc
        raise


def analyze_sample(model: Any, sample_path: Path, window: SampleWindow) -> dict[str, Any]:
    segments_iter, info = model.transcribe(
        str(sample_path),
        language=None,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    segments = list(segments_iter)
    preview = _preview_text(getattr(segment, "text", "") for segment in segments)
    probability = getattr(info, "language_probability", None)
    return {
        "sample_index": window.index,
        "start_seconds": window.start_seconds,
        "end_seconds": window.end_seconds,
        "detected_language": getattr(info, "language", None),
        "language_probability": round(probability, 4) if probability is not None else None,
        "text_preview": preview,
        "segment_count": len(segments),
        "error": "",
    }


def _preview_text(parts: Any) -> str:
    text = " ".join(str(part).strip() for part in parts if str(part).strip())
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= TEXT_PREVIEW_LIMIT:
        return text
    return text[:TEXT_PREVIEW_LIMIT].rstrip() + "..."


def sample_error(window: SampleWindow, exc: Exception) -> dict[str, Any]:
    return {
        "sample_index": window.index,
        "start_seconds": window.start_seconds,
        "end_seconds": window.end_seconds,
        "detected_language": None,
        "language_probability": None,
        "text_preview": "",
        "segment_count": 0,
        "error": str(exc),
    }


def classify_summary(
    samples: list[dict[str, Any]],
    *,
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    valid = [
        sample
        for sample in samples
        if sample.get("detected_language") and not sample.get("error")
    ]
    counts = Counter(str(sample["detected_language"]) for sample in valid)
    distinct = sorted(counts)
    dominant = ""
    if counts:
        dominant = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    low_confidence_count = 0
    high_confidence_languages: set[str] = set()
    language_probabilities: dict[str, list[float]] = defaultdict(list)
    for sample in valid:
        language = str(sample["detected_language"])
        probability = sample.get("language_probability")
        if isinstance(probability, (int, float)):
            language_probabilities[language].append(float(probability))
            if probability >= low_confidence_threshold:
                high_confidence_languages.add(language)
            else:
                low_confidence_count += 1
        else:
            low_confidence_count += 1

    non_dominant_count = sum(count for lang, count in counts.items() if lang != dominant)
    if len(high_confidence_languages) >= 2 or non_dominant_count >= 2:
        likelihood = "likely"
    elif len(distinct) >= 2:
        likelihood = "possible"
    else:
        likelihood = "none"

    return {
        "distinct_detected_languages": distinct,
        "language_counts": dict(sorted(counts.items())),
        "language_average_probabilities": {
            language: round(sum(values) / len(values), 4)
            for language, values in sorted(language_probabilities.items())
            if values
        },
        "dominant_language": dominant,
        "low_confidence_count": low_confidence_count,
        "mixed_language_likelihood": likelihood,
        "failed_sample_count": sum(1 for sample in samples if sample.get("error")),
        "limitations": list(LIMITATIONS),
    }


def build_report(
    *,
    input_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    allow_model_download: bool,
    sample_count: int,
    sample_seconds: float,
    ffmpeg_path: str,
    ffprobe_path: str,
    duration_seconds: float,
    samples: list[dict[str, Any]],
    device_warnings: list[str] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "schema_version": 1,
        "report_type": "mixed_language_asr_evidence",
        "generated_at": generated_at,
        "metadata": {
            "input_path": str(input_path),
            "input_name": input_path.name,
            "duration_seconds": round(duration_seconds, 3),
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
            "local_files_only": not allow_model_download,
            "allow_model_download": allow_model_download,
            "sample_count": sample_count,
            "sample_seconds": sample_seconds,
            "ffmpeg_path": ffmpeg_path,
            "ffprobe_path": ffprobe_path,
            "device_warnings": list(device_warnings or []),
        },
        "samples": samples,
        "summary": classify_summary(samples),
    }


def write_reports(report: dict[str, Any], output_dir: Path, input_path: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{sanitize_stem(input_path.stem)}.{stamp}.asr_evidence"
    json_path = output_dir / f"{base}.json"
    markdown_path = output_dir / f"{base}.md"
    write_json(json_path, report)
    write_text(markdown_path, render_markdown_report(report))
    return {
        "json_path": str(json_path.resolve()),
        "markdown_path": str(markdown_path.resolve()),
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    metadata = report.get("metadata", {})
    summary = report.get("summary", {})
    lines = [
        "# Mixed-Language ASR Evidence",
        "",
        f"- Input: `{metadata.get('input_path', '')}`",
        f"- Generated: {report.get('generated_at', '')}",
        f"- Model: {metadata.get('model', '')}",
        f"- Device: {metadata.get('device', '')} / {metadata.get('compute_type', '')}",
        f"- Local files only: {metadata.get('local_files_only', True)}",
        f"- Duration seconds: {metadata.get('duration_seconds', '')}",
        "",
        "## Summary",
        "",
        f"- Mixed-language likelihood: `{summary.get('mixed_language_likelihood', 'none')}`",
        f"- Dominant language: `{summary.get('dominant_language', '')}`",
        f"- Distinct detected languages: {', '.join(summary.get('distinct_detected_languages', [])) or '(none)'}",
        f"- Low-confidence samples: {summary.get('low_confidence_count', 0)}",
        f"- Failed samples: {summary.get('failed_sample_count', 0)}",
        "",
        "## Samples",
        "",
        "| # | Time | Language | Probability | Segments | Preview / Error |",
        "| - | ---- | -------- | ----------- | -------- | --------------- |",
    ]
    for sample in report.get("samples", []):
        preview = sample.get("error") or sample.get("text_preview") or ""
        lines.append(
            "| {idx} | {start:.3f}-{end:.3f}s | {lang} | {prob} | {segments} | {preview} |".format(
                idx=sample.get("sample_index", ""),
                start=float(sample.get("start_seconds") or 0),
                end=float(sample.get("end_seconds") or 0),
                lang=_md_cell(sample.get("detected_language") or ""),
                prob=_md_cell(sample.get("language_probability") if sample.get("language_probability") is not None else ""),
                segments=_md_cell(sample.get("segment_count", 0)),
                preview=_md_cell(preview),
            )
        )
    lines.extend(["", "## Limitations", ""])
    for item in summary.get("limitations") or LIMITATIONS:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


if __name__ == "__main__":
    raise SystemExit(main())
