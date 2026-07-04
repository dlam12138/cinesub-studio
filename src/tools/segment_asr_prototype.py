from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mixed_language_asr_evidence as evidence
from encoding_utils import write_json, write_text
from ffmpeg_locator import find_ffmpeg, find_ffprobe
from runtime_env import add_project_cuda_to_process, choose_device, default_compute_type


PROJECT_ROOT = evidence.PROJECT_ROOT
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "reports" / "asr_segment_prototype"
DEFAULT_TMP_DIR = PROJECT_ROOT / ".tmp" / "asr-segment-prototype"
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_SAMPLE_COUNT = 8

ModelUnavailable = evidence.ModelUnavailable
SampleWindow = evidence.SampleWindow
create_whisper_model = evidence.create_whisper_model

LIMITATIONS = [
    "This is an experimental segment-level ASR prototype.",
    "It does not change production transcription or pipeline behavior.",
    "It does not generate formal subtitle outputs.",
    "Forced-language rows are transcription comparison modes, not proof of detected language.",
]


@dataclass(frozen=True)
class TranscriptionMode:
    name: str
    requested_language: str | None


TRANSCRIPTION_MODES = (
    TranscriptionMode("auto", None),
    TranscriptionMode("forced-fr", "fr"),
    TranscriptionMode("forced-en", "en"),
)


def main() -> int:
    args = parse_args()
    try:
        report = run_prototype_cli(args)
    except ModelUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"JSON report: {report['json_path']}")
    print(f"Markdown report: {report['markdown_path']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare auto and forced-language ASR on selected media windows."
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
    parser.add_argument(
        "--window",
        action="append",
        default=[],
        help="Manual time window, for example 00:05:00-00:07:00. Can be repeated.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLE_COUNT,
        help="Number of evenly spaced automatic windows when --window is omitted.",
    )
    parser.add_argument(
        "--sample-every-seconds",
        type=float,
        default=None,
        help="Automatic window start interval. Takes precedence over --samples.",
    )
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=DEFAULT_WINDOW_SECONDS,
        help="Duration for each automatic window.",
    )
    parser.add_argument(
        "--output-dir",
        default="output/reports/asr_segment_prototype",
        help="Directory for JSON and Markdown prototype reports.",
    )
    parser.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow faster-whisper to download missing model files.",
    )
    return parser.parse_args()


def run_prototype_cli(args: argparse.Namespace) -> dict[str, Any]:
    input_path = _local_or_absolute_path(args.input)
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

    duration = evidence.probe_duration(input_path, ffprobe)
    windows = plan_windows(
        duration_seconds=duration,
        manual_windows=args.window,
        samples=args.samples,
        sample_every_seconds=args.sample_every_seconds,
        window_seconds=args.window_seconds,
    )

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

    window_results: list[dict[str, Any]] = []
    for window in windows:
        sample_path = tmp_dir / f"window-{window.index:03}.wav"
        try:
            evidence.extract_sample(input_path, sample_path, window, ffmpeg)
            mode_results = analyze_window_modes(model, sample_path)
        except Exception as exc:
            mode_results = [mode_error(mode, exc) for mode in TRANSCRIPTION_MODES]
        finally:
            try:
                sample_path.unlink(missing_ok=True)
            except OSError:
                pass
        window_results.append(
            {
                "window_index": window.index,
                "start_seconds": window.start_seconds,
                "end_seconds": window.end_seconds,
                "results": mode_results,
            }
        )

    report = build_report(
        input_path=input_path,
        model_name=args.model,
        device=device,
        compute_type=compute_type,
        allow_model_download=args.allow_model_download,
        duration_seconds=duration,
        manual_windows=args.window,
        samples=args.samples,
        sample_every_seconds=args.sample_every_seconds,
        window_seconds=args.window_seconds,
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        windows=window_results,
        device_warnings=device_warnings,
    )
    paths = write_reports(report, output_dir, input_path)
    report.update(paths)
    return report


def _local_or_absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _run_id(input_path: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{evidence.sanitize_stem(input_path.stem)}-{stamp}"


def plan_windows(
    *,
    duration_seconds: float,
    manual_windows: list[str] | tuple[str, ...] | None,
    samples: int,
    sample_every_seconds: float | None,
    window_seconds: float,
) -> list[SampleWindow]:
    if not _positive_finite(duration_seconds):
        raise ValueError("duration_seconds must be greater than zero")
    if not _positive_finite(window_seconds):
        raise ValueError("window_seconds must be greater than zero")

    manual = [value for value in (manual_windows or []) if str(value).strip()]
    if manual:
        return parse_manual_windows(manual, duration_seconds)

    if sample_every_seconds is not None:
        if not _positive_finite(sample_every_seconds):
            raise ValueError("sample_every_seconds must be greater than zero")
        return plan_interval_windows(
            duration_seconds=duration_seconds,
            sample_every_seconds=float(sample_every_seconds),
            window_seconds=float(window_seconds),
        )

    return evidence.plan_sample_windows(duration_seconds, samples, window_seconds)


def parse_manual_windows(values: list[str] | tuple[str, ...], duration_seconds: float) -> list[SampleWindow]:
    windows: list[SampleWindow] = []
    for index, value in enumerate(values, start=1):
        start, end = parse_window_range(value)
        if end > duration_seconds + 0.001:
            raise ValueError(f"Window exceeds media duration: {value}")
        windows.append(
            SampleWindow(
                index=index,
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
            )
        )
    return windows


def parse_window_range(value: str) -> tuple[float, float]:
    text = str(value or "").strip()
    parts = re.split(r"\s*-\s*", text, maxsplit=1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid window range: {value}")
    start = parse_timecode(parts[0])
    end = parse_timecode(parts[1])
    if start < 0 or end < 0:
        raise ValueError(f"Window times must be non-negative: {value}")
    if end <= start:
        raise ValueError(f"Window end must be after start: {value}")
    return start, end


def parse_timecode(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Empty timecode")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        seconds = float(text)
    else:
        parts = text.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"Invalid timecode: {value}")
        try:
            numbers = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError(f"Invalid timecode: {value}") from exc
        if any(number < 0 for number in numbers):
            raise ValueError(f"Invalid timecode: {value}")
        if len(parts) == 2:
            minutes, seconds_part = numbers
            seconds = minutes * 60 + seconds_part
        else:
            hours, minutes, seconds_part = numbers
            seconds = hours * 3600 + minutes * 60 + seconds_part
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"Invalid timecode: {value}")
    return seconds


def plan_interval_windows(
    *,
    duration_seconds: float,
    sample_every_seconds: float,
    window_seconds: float,
) -> list[SampleWindow]:
    if not _positive_finite(duration_seconds):
        raise ValueError("duration_seconds must be greater than zero")
    if not _positive_finite(sample_every_seconds):
        raise ValueError("sample_every_seconds must be greater than zero")
    if not _positive_finite(window_seconds):
        raise ValueError("window_seconds must be greater than zero")

    windows: list[SampleWindow] = []
    start = 0.0
    index = 1
    while start < duration_seconds:
        end = min(duration_seconds, start + window_seconds)
        windows.append(
            SampleWindow(
                index=index,
                start_seconds=round(start, 3),
                end_seconds=round(end, 3),
            )
        )
        index += 1
        start += sample_every_seconds
    return windows


def _positive_finite(value: float | int) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def analyze_window_modes(model: Any, sample_path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for mode in TRANSCRIPTION_MODES:
        try:
            results.append(analyze_window_mode(model, sample_path, mode))
        except Exception as exc:
            results.append(mode_error(mode, exc))
    return results


def analyze_window_mode(model: Any, sample_path: Path, mode: TranscriptionMode) -> dict[str, Any]:
    segments_iter, info = model.transcribe(
        str(sample_path),
        language=mode.requested_language,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    segments = list(segments_iter)
    preview = evidence._preview_text(getattr(segment, "text", "") for segment in segments)
    probability = getattr(info, "language_probability", None)
    return {
        "mode": mode.name,
        "requested_language": mode.requested_language,
        "detected_language": getattr(info, "language", None),
        "language_probability": round(probability, 4) if probability is not None else None,
        "segment_count": len(segments),
        "text_preview": preview,
        "error": "",
    }


def mode_error(mode: TranscriptionMode, exc: Exception) -> dict[str, Any]:
    return {
        "mode": mode.name,
        "requested_language": mode.requested_language,
        "detected_language": None,
        "language_probability": None,
        "segment_count": 0,
        "text_preview": "",
        "error": str(exc),
    }


def build_report(
    *,
    input_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    allow_model_download: bool,
    duration_seconds: float,
    manual_windows: list[str] | tuple[str, ...] | None,
    samples: int,
    sample_every_seconds: float | None,
    window_seconds: float,
    ffmpeg_path: str,
    ffprobe_path: str,
    windows: list[dict[str, Any]],
    device_warnings: list[str] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    window_source = "manual" if manual_windows else ("sample-every-seconds" if sample_every_seconds else "samples")
    return {
        "schema_version": 1,
        "report_type": "segment_asr_prototype",
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
            "window_source": window_source,
            "manual_windows": list(manual_windows or []),
            "samples": samples,
            "sample_every_seconds": sample_every_seconds,
            "window_seconds": window_seconds,
            "ffmpeg_path": ffmpeg_path,
            "ffprobe_path": ffprobe_path,
            "device_warnings": list(device_warnings or []),
        },
        "windows": windows,
        "summary": {
            "window_count": len(windows),
            "mode_count": len(TRANSCRIPTION_MODES),
            "limitations": list(LIMITATIONS),
        },
    }


def write_reports(report: dict[str, Any], output_dir: Path, input_path: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{evidence.sanitize_stem(input_path.stem)}.{stamp}.asr_segment_prototype"
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
        "# Segment-Level ASR Prototype",
        "",
        f"- Input: `{metadata.get('input_path', '')}`",
        f"- Generated: {report.get('generated_at', '')}",
        f"- Model: {metadata.get('model', '')}",
        f"- Device: {metadata.get('device', '')} / {metadata.get('compute_type', '')}",
        f"- Local files only: {metadata.get('local_files_only', True)}",
        f"- Duration seconds: {metadata.get('duration_seconds', '')}",
        f"- Window source: `{metadata.get('window_source', '')}`",
        "",
        "## Interpretation",
        "",
        "Forced-language rows are transcription comparison modes, not proof of detected language.",
        "Use this report to compare preview quality and confidence before considering future routing.",
        "",
        "## Windows",
        "",
        "| Window | Time | Mode | requested_language | detected_language | language_probability | segment_count | text_preview / error |",
        "| ------ | ---- | ---- | ------------------ | ----------------- | -------------------- | ------------- | -------------------- |",
    ]
    for window in report.get("windows", []):
        start = float(window.get("start_seconds") or 0)
        end = float(window.get("end_seconds") or 0)
        for result in window.get("results", []):
            preview = result.get("error") or result.get("text_preview") or ""
            requested = result.get("requested_language")
            lines.append(
                "| {idx} | {start:.3f}-{end:.3f}s | {mode} | {requested} | {detected} | {prob} | {segments} | {preview} |".format(
                    idx=_md_cell(window.get("window_index", "")),
                    start=start,
                    end=end,
                    mode=_md_cell(result.get("mode", "")),
                    requested=_md_cell("auto" if requested is None else requested),
                    detected=_md_cell(result.get("detected_language") or ""),
                    prob=_md_cell(
                        result.get("language_probability")
                        if result.get("language_probability") is not None
                        else ""
                    ),
                    segments=_md_cell(result.get("segment_count", 0)),
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
