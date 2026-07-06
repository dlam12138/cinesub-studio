from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from encoding_utils import write_json
from ffmpeg_locator import find_ffprobe
from mixed_language_asr_evidence import probe_duration
from segment_asr_prototype import DEFAULT_SAMPLE_COUNT, DEFAULT_WINDOW_SECONDS, run_prototype_cli
from segment_asr_report_analyzer import Settings, analyze_reports
from segment_asr_srt_assembler import assemble_routed_srt


ROUTING_MODES = {"off", "dry_run", "apply"}
APPLY_SEGMENTS_UNAVAILABLE_REASON = "routed ASR segments are not available"
APPLY_COVERAGE_INCOMPLETE_REASON = "routed segment coverage is incomplete"
APPLY_UNKNOWN_DURATION_REASON = "segment routing apply requires known media duration"
APPLY_SKIP_WINDOW_REASON = "routed segment classification skip_window cannot be applied"
REPORT_DIR_NAME = "segment_asr_routing"
COVERAGE_TOLERANCE_SECONDS = 0.50
DEFAULT_APPLY_WINDOW_SECONDS = 120.0
DEFAULT_MAX_APPLY_WINDOWS = 80
APPLY_ASR_MODE_COUNT = 3


class SegmentAsrRoutingError(RuntimeError):
    """Controlled error for experimental segment ASR routing failures."""


@dataclass(frozen=True)
class SegmentAsrRoutingOptions:
    mode: str = "off"
    confidence_threshold: float = 0.70
    min_segments: int = 1
    strict: bool = False
    window_seconds: float = DEFAULT_APPLY_WINDOW_SECONDS
    max_windows: int = DEFAULT_MAX_APPLY_WINDOWS
    allow_large_run: bool = False


@dataclass(frozen=True)
class SegmentAsrRoutingResult:
    mode: str
    report_path: str = ""
    status: str = "off"
    fallback_used: bool = False
    fallback_reason: str = ""
    subtitle_output_affected: bool = False
    normal_srt_path: str = ""
    routed_srt_path: str = ""


def validate_options(options: SegmentAsrRoutingOptions) -> SegmentAsrRoutingOptions:
    mode = str(options.mode or "off").strip()
    if mode not in ROUTING_MODES:
        raise SegmentAsrRoutingError(
            f"Invalid segment ASR routing mode: {mode}. Expected off, dry_run, or apply."
        )
    try:
        threshold = float(options.confidence_threshold)
    except (TypeError, ValueError) as exc:
        raise SegmentAsrRoutingError("--segment-routing-confidence-threshold must be a number") from exc
    if not 0 <= threshold <= 1:
        raise SegmentAsrRoutingError("--segment-routing-confidence-threshold must be between 0 and 1")
    try:
        min_segments = int(options.min_segments)
    except (TypeError, ValueError) as exc:
        raise SegmentAsrRoutingError("--segment-routing-min-segments must be an integer") from exc
    if min_segments < 0:
        raise SegmentAsrRoutingError("--segment-routing-min-segments must be greater than or equal to 0")
    try:
        window_seconds = float(options.window_seconds)
    except (TypeError, ValueError) as exc:
        raise SegmentAsrRoutingError("--segment-routing-window-seconds must be a number") from exc
    if not math.isfinite(window_seconds) or window_seconds <= 0:
        raise SegmentAsrRoutingError("--segment-routing-window-seconds must be greater than zero")
    try:
        max_windows = int(options.max_windows)
    except (TypeError, ValueError) as exc:
        raise SegmentAsrRoutingError("--segment-routing-max-windows must be an integer") from exc
    if max_windows <= 0:
        raise SegmentAsrRoutingError("--segment-routing-max-windows must be greater than zero")
    return SegmentAsrRoutingOptions(
        mode=mode,
        confidence_threshold=threshold,
        min_segments=min_segments,
        strict=_coerce_bool(options.strict),
        window_seconds=window_seconds,
        max_windows=max_windows,
        allow_large_run=_coerce_bool(options.allow_large_run),
    )


def ensure_apply_is_not_strict(options: SegmentAsrRoutingOptions) -> None:
    validate_options(options)


def run_segment_asr_routing(
    *,
    options: SegmentAsrRoutingOptions,
    media_path: Path,
    routing_input_path: Path,
    report_root: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
    normal_srt_path: Path | None = None,
    routed_srt_path: Path | None = None,
) -> SegmentAsrRoutingResult:
    options = validate_options(options)
    if options.mode == "off":
        return SegmentAsrRoutingResult(mode="off")

    report_dir = Path(report_root) / REPORT_DIR_NAME
    if options.mode == "apply":
        return _run_apply(
            options=options,
            media_path=media_path,
            routing_input_path=routing_input_path,
            report_dir=report_dir,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
            normal_srt_path=normal_srt_path,
            routed_srt_path=routed_srt_path,
        )

    try:
        return _run_dry_run(
            options=options,
            media_path=media_path,
            routing_input_path=routing_input_path,
            report_dir=report_dir,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
        )
    except Exception as exc:
        if options.strict:
            raise SegmentAsrRoutingError(f"segment ASR routing dry_run failed: {exc}") from exc
        try:
            return _write_fallback_report(
                options=options,
                media_path=media_path,
                routing_input_path=routing_input_path,
                report_dir=report_dir,
                model_name=model_name,
                device=device,
                compute_type=compute_type,
                local_files_only=local_files_only,
                fallback_reason=str(exc),
            )
        except Exception:
            return SegmentAsrRoutingResult(
                mode=options.mode,
                status="fallback",
                fallback_used=True,
                fallback_reason=str(exc),
            )


def _run_dry_run(
    *,
    options: SegmentAsrRoutingOptions,
    media_path: Path,
    routing_input_path: Path,
    report_dir: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
) -> SegmentAsrRoutingResult:
    prototype_report, analysis = _collect_routing_evidence(
        options=options,
        routing_input_path=routing_input_path,
        report_dir=report_dir,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
    )
    report = _base_report(
        options=options,
        media_path=media_path,
        routing_input_path=routing_input_path,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
        fallback_used=False,
        fallback_reason=None,
        status="dry_run_complete",
    )
    report["prototype_report"] = {
        "json_path": str(prototype_report.get("json_path") or ""),
        "markdown_path": str(prototype_report.get("markdown_path") or ""),
    }
    report["analyzer"] = analysis
    report["windows"] = analysis.get("windows", [])
    report_path = _write_integration_report(report_dir, media_path, report)
    return SegmentAsrRoutingResult(
        mode=options.mode,
        report_path=str(report_path.resolve()),
        status="dry_run_complete",
    )


def _collect_routing_evidence(
    *,
    options: SegmentAsrRoutingOptions,
    routing_input_path: Path,
    report_dir: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
    include_full_segments: bool = False,
    full_coverage: bool = False,
    window_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from argparse import Namespace

    prototype_dir = report_dir / "prototype"
    window_settings = _window_settings(
        full_coverage=full_coverage,
        window_seconds=window_seconds if full_coverage else None,
    )
    args = Namespace(
        input=str(routing_input_path),
        model=model_name,
        device=device,
        compute_type=compute_type,
        window=list(window_settings["manual_windows"]),
        samples=window_settings["samples"],
        sample_every_seconds=window_settings["sample_every_seconds"],
        window_seconds=window_settings["window_seconds"],
        output_dir=str(prototype_dir),
        allow_model_download=not local_files_only,
        include_full_segments=include_full_segments,
    )
    prototype_report = run_prototype_cli(args)
    prototype_json = str(prototype_report.get("json_path") or "")
    analysis = analyze_reports(
        [prototype_json],
        Settings(
            confidence_threshold=options.confidence_threshold,
            min_segments=options.min_segments,
        ),
    )
    return prototype_report, analysis


def _run_apply(
    *,
    options: SegmentAsrRoutingOptions,
    media_path: Path,
    routing_input_path: Path,
    report_dir: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
    normal_srt_path: Path | None,
    routed_srt_path: Path | None,
) -> SegmentAsrRoutingResult:
    prototype_report: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    full_segments_available = False
    preview_only_rejected = False
    candidate_path: Path | None = None
    candidate_accepted = False
    full_payload: dict[str, Any] | None = None
    runtime_guardrails: dict[str, Any] | None = None

    try:
        runtime_guardrails, guardrail_failure_reason = _build_apply_runtime_guardrails(
            media_path=media_path,
            options=options,
        )
        if guardrail_failure_reason:
            raise SegmentAsrRoutingError(guardrail_failure_reason)

        prototype_report, analysis = _collect_routing_evidence(
            options=options,
            routing_input_path=routing_input_path,
            report_dir=report_dir,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
            include_full_segments=True,
            full_coverage=True,
            window_seconds=options.window_seconds,
        )
        full_payload = get_full_routed_segments(
            prototype_report=prototype_report,
            analysis=analysis,
            media_path=media_path,
            routing_input_path=routing_input_path,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
        )
        full_segments_available = _payload_has_full_segment_data(full_payload)
        preview_only_rejected = not full_segments_available
        if not full_segments_available:
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)
        coverage = _validate_payload_coverage(full_payload)

        routed_payload = _build_routed_payload(
            full_payload=full_payload,
            analysis=analysis,
        )
        output_path = _routed_output_path(
            report_dir=report_dir,
            media_path=media_path,
            normal_srt_path=normal_srt_path,
            routed_srt_path=routed_srt_path,
        )
        candidate_path = _candidate_routed_output_path(report_dir=report_dir, media_path=media_path)
        assembler_metadata = assemble_routed_srt(routed_payload, candidate_path)
        if int(assembler_metadata.get("cue_count") or 0) <= 0:
            _cleanup_candidate(candidate_path)
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate_path, output_path)
        candidate_accepted = True

        report = _base_report(
            options=options,
            media_path=media_path,
            routing_input_path=routing_input_path,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
            fallback_used=False,
            fallback_reason=None,
            status="apply_complete",
            subtitle_output_affected=True,
            normal_srt_path=normal_srt_path,
            routed_srt_path=output_path,
            apply_attempted=True,
            apply_succeeded=True,
            full_routed_segments_available=True,
            preview_only_rejected=False,
            candidate_srt_path=candidate_path,
            candidate_accepted=True,
            coverage=coverage,
            routing_metadata=routed_payload.get("metadata"),
            window_planning=full_payload.get("window_planning"),
            runtime_guardrails=runtime_guardrails,
        )
        report["prototype_report"] = _prototype_report_summary(prototype_report)
        report["analyzer"] = analysis
        report["windows"] = analysis.get("windows", [])
        report["assembler"] = assembler_metadata
        report_path = _write_integration_report(report_dir, media_path, report)
        return SegmentAsrRoutingResult(
            mode=options.mode,
            report_path=str(report_path.resolve()),
            status="apply_complete",
            subtitle_output_affected=True,
            normal_srt_path=str(Path(normal_srt_path).resolve()) if normal_srt_path else "",
            routed_srt_path=str(output_path.resolve()),
        )
    except Exception as exc:
        if not candidate_accepted:
            _cleanup_candidate(candidate_path)
        fallback_reason = str(exc) or APPLY_SEGMENTS_UNAVAILABLE_REASON
        if options.strict:
            _write_apply_failure_report(
                options=options,
                media_path=media_path,
                routing_input_path=routing_input_path,
                report_dir=report_dir,
                model_name=model_name,
                device=device,
                compute_type=compute_type,
                local_files_only=local_files_only,
                normal_srt_path=normal_srt_path,
                routed_srt_path=routed_srt_path,
                prototype_report=prototype_report,
                analysis=analysis,
                full_routed_segments_available=full_segments_available,
                preview_only_rejected=preview_only_rejected,
                candidate_srt_path=candidate_path,
                candidate_accepted=False,
                coverage=(full_payload or {}).get("coverage") if isinstance(full_payload, dict) else None,
                routing_metadata=_analysis_routing_metadata(analysis),
                window_planning=(full_payload or {}).get("window_planning")
                if isinstance(full_payload, dict)
                else None,
                runtime_guardrails=runtime_guardrails,
                failure_reason=fallback_reason,
            )
            raise SegmentAsrRoutingError(
                f"segment ASR routing apply failed: {fallback_reason}; "
                "no routed subtitle output was accepted"
            ) from exc

        try:
            return _write_fallback_report(
                options=options,
                media_path=media_path,
                routing_input_path=routing_input_path,
                report_dir=report_dir,
                model_name=model_name,
                device=device,
                compute_type=compute_type,
                local_files_only=local_files_only,
                fallback_reason=fallback_reason,
                normal_srt_path=normal_srt_path,
                routed_srt_path=routed_srt_path,
                prototype_report=prototype_report,
                analysis=analysis,
                apply_attempted=True,
                apply_succeeded=False,
                full_routed_segments_available=full_segments_available,
                preview_only_rejected=preview_only_rejected,
                candidate_srt_path=candidate_path,
                candidate_accepted=False,
                coverage=(full_payload or {}).get("coverage") if isinstance(full_payload, dict) else None,
                routing_metadata=_analysis_routing_metadata(analysis),
                window_planning=(full_payload or {}).get("window_planning")
                if isinstance(full_payload, dict)
                else None,
                runtime_guardrails=runtime_guardrails,
            )
        except Exception:
            return SegmentAsrRoutingResult(
                mode=options.mode,
                status="fallback",
                fallback_used=True,
                fallback_reason=fallback_reason,
                normal_srt_path=str(Path(normal_srt_path).resolve()) if normal_srt_path else "",
            )


def get_full_routed_segments(
    *,
    prototype_report: dict[str, Any],
    analysis: dict[str, Any],
    media_path: Path,
    routing_input_path: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
) -> dict[str, Any] | None:
    """Return full timestamped routed segments from an M8.3 prototype report."""
    metadata = prototype_report.get("metadata") if isinstance(prototype_report, dict) else None
    if not isinstance(metadata, dict) or metadata.get("include_full_segments") is not True:
        return None

    duration_seconds = _known_duration_seconds(metadata.get("duration_seconds"))
    if duration_seconds is None:
        raise SegmentAsrRoutingError(APPLY_UNKNOWN_DURATION_REASON)

    raw_windows = prototype_report.get("windows")
    if not isinstance(raw_windows, list):
        return None

    windows: list[dict[str, Any]] = []
    for raw_window in raw_windows:
        if not isinstance(raw_window, dict):
            continue
        runs = _full_runs_from_prototype_window(raw_window)
        windows.append(
            {
                "window_index": _int_or_default(raw_window.get("window_index"), len(windows) + 1),
                "start_seconds": _float_or_default(raw_window.get("start_seconds"), 0.0),
                "end_seconds": _float_or_default(raw_window.get("end_seconds"), 0.0),
                "timestamp_scope": "local",
                "runs": runs,
            }
        )

    coverage = _calculate_window_coverage(windows, duration_seconds)
    return {
        "schema_version": 1,
        "media_path": str(Path(media_path).resolve()),
        "routing_input_path": str(Path(routing_input_path).resolve()),
        "duration_seconds": duration_seconds,
        "window_planning": {
            "mode": "full_coverage",
            "window_seconds": _float_or_default(metadata.get("window_seconds"), DEFAULT_WINDOW_SECONDS),
            "window_count": len(windows),
        },
        "coverage": coverage,
        "windows": windows,
    }


def _full_runs_from_prototype_window(raw_window: dict[str, Any]) -> dict[str, Any]:
    runs: dict[str, Any] = {}
    raw_results = raw_window.get("results")
    if not isinstance(raw_results, list):
        return runs
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        mode = _canonical_run_key(result.get("mode"), result.get("requested_language"))
        if not mode:
            continue
        full_available = result.get("full_segments_available") is True
        raw_segments = result.get("segments")
        segments = _normalize_full_segments(raw_segments) if full_available else None
        error = str(result.get("error") or "")
        runs[mode] = {
            "usable": full_available and isinstance(segments, list) and not error,
            "full_segments_available": full_available and isinstance(segments, list),
            "requested_language": result.get("requested_language"),
            "detected_language": result.get("detected_language"),
            "language_probability": result.get("language_probability"),
            "segment_count": _int_or_default(result.get("segment_count"), 0),
            "error": error,
            "segments": segments,
        }
    return runs


def _canonical_run_key(mode: Any, requested_language: Any = None) -> str | None:
    text = str(mode or "").strip().lower().replace("_", "-")
    aliases = {
        "auto": "auto",
        "forced-fr": "forced-fr",
        "forced-en": "forced-en",
        "fr": "forced-fr",
        "en": "forced-en",
    }
    if text in aliases:
        return aliases[text]
    if requested_language in ("fr", "en"):
        return f"forced-{requested_language}"
    return None


def _normalize_full_segments(raw_segments: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw_segments, list):
        return None
    segments: list[dict[str, Any]] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            return None
        if "start" not in raw_segment or "end" not in raw_segment or "text" not in raw_segment:
            return None
        try:
            start = float(raw_segment["start"])
            end = float(raw_segment["end"])
        except (TypeError, ValueError):
            return None
        text = str(raw_segment.get("text") or "").strip()
        segments.append({"start": start, "end": end, "text": text})
    return segments


def _known_duration_seconds(value: Any) -> float | None:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


def build_apply_runtime_guardrails(
    *,
    duration_seconds: Any,
    window_seconds: Any,
    max_windows: Any,
    allow_large_run: bool,
) -> tuple[dict[str, Any], str | None]:
    duration = _known_duration_seconds(duration_seconds)
    try:
        window = float(window_seconds)
    except (TypeError, ValueError):
        window = float("nan")
    try:
        cap = int(max_windows)
    except (TypeError, ValueError):
        cap = 0

    guardrails: dict[str, Any] = {
        "window_seconds": window if math.isfinite(window) else None,
        "planned_window_count": None,
        "estimated_asr_calls": None,
        "max_windows": cap if cap > 0 else None,
        "cap_exceeded": False,
        "allow_large_run": _coerce_bool(allow_large_run),
    }
    if duration is not None:
        guardrails["duration_seconds"] = round(duration, 3)

    if duration is None:
        return guardrails, APPLY_UNKNOWN_DURATION_REASON
    if not math.isfinite(window) or window <= 0:
        raise SegmentAsrRoutingError("--segment-routing-window-seconds must be greater than zero")
    if cap <= 0:
        raise SegmentAsrRoutingError("--segment-routing-max-windows must be greater than zero")

    planned_count = int(math.ceil(duration / window))
    estimated_calls = planned_count * APPLY_ASR_MODE_COUNT
    cap_exceeded = planned_count > cap
    guardrails.update(
        {
            "window_seconds": float(window),
            "planned_window_count": planned_count,
            "estimated_asr_calls": estimated_calls,
            "max_windows": cap,
            "cap_exceeded": cap_exceeded,
        }
    )
    if cap_exceeded and not _coerce_bool(allow_large_run):
        return guardrails, f"segment routing apply window count {planned_count} exceeds max {cap}"
    return guardrails, None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _build_apply_runtime_guardrails(
    *,
    media_path: Path,
    options: SegmentAsrRoutingOptions,
) -> tuple[dict[str, Any], str | None]:
    duration = _probe_media_duration(media_path)
    return build_apply_runtime_guardrails(
        duration_seconds=duration,
        window_seconds=options.window_seconds,
        max_windows=options.max_windows,
        allow_large_run=options.allow_large_run,
    )


def _probe_media_duration(media_path: Path) -> float | None:
    ffprobe = find_ffprobe(Path(__file__).resolve().parents[2])
    if not ffprobe:
        return None
    try:
        return probe_duration(Path(media_path), ffprobe)
    except RuntimeError:
        return None


def _calculate_window_coverage(
    windows: list[dict[str, Any]],
    duration_seconds: float,
) -> dict[str, Any]:
    intervals: list[tuple[float, float]] = []
    for window in windows:
        try:
            start = float(window["start_seconds"])
            end = float(window["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        clipped_start = max(0.0, min(duration_seconds, start))
        clipped_end = max(0.0, min(duration_seconds, end))
        if clipped_end > clipped_start:
            intervals.append((clipped_start, clipped_end))

    intervals.sort()
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + COVERAGE_TOLERANCE_SECONDS:
            merged.append((start, end))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))

    covered_seconds = sum(end - start for start, end in merged)
    gap_count = _coverage_gap_count(merged, duration_seconds)
    coverage_rate = covered_seconds / duration_seconds if duration_seconds > 0 else 0.0
    full_coverage = gap_count == 0 and coverage_rate >= 0.99
    return {
        "full_coverage": full_coverage,
        "coverage_rate": round(coverage_rate, 6),
        "gap_count": gap_count,
        "covered_seconds": round(covered_seconds, 3),
        "duration_seconds": round(duration_seconds, 3),
        "coverage_tolerance_seconds": COVERAGE_TOLERANCE_SECONDS,
    }


def _coverage_gap_count(merged: list[tuple[float, float]], duration_seconds: float) -> int:
    if not merged:
        return 1
    gap_count = 0
    cursor = 0.0
    for start, end in merged:
        if start > cursor + COVERAGE_TOLERANCE_SECONDS:
            gap_count += 1
        cursor = max(cursor, end)
    if duration_seconds > cursor + COVERAGE_TOLERANCE_SECONDS:
        gap_count += 1
    return gap_count


def _validate_payload_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = payload.get("coverage") if isinstance(payload, dict) else None
    if not isinstance(coverage, dict):
        raise SegmentAsrRoutingError(APPLY_COVERAGE_INCOMPLETE_REASON)
    if coverage.get("full_coverage") is not True:
        raise SegmentAsrRoutingError(APPLY_COVERAGE_INCOMPLETE_REASON)
    try:
        coverage_rate = float(coverage.get("coverage_rate"))
        gap_count = int(coverage.get("gap_count"))
    except (TypeError, ValueError) as exc:
        raise SegmentAsrRoutingError(APPLY_COVERAGE_INCOMPLETE_REASON) from exc
    if coverage_rate < 0.99 or gap_count != 0:
        raise SegmentAsrRoutingError(APPLY_COVERAGE_INCOMPLETE_REASON)
    return coverage


def _float_or_default(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _write_fallback_report(
    *,
    options: SegmentAsrRoutingOptions,
    media_path: Path,
    routing_input_path: Path,
    report_dir: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
    fallback_reason: str,
    normal_srt_path: Path | None = None,
    routed_srt_path: Path | None = None,
    prototype_report: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
    apply_attempted: bool = False,
    apply_succeeded: bool = False,
    full_routed_segments_available: bool = False,
    preview_only_rejected: bool = False,
    candidate_srt_path: Path | None = None,
    candidate_accepted: bool = False,
    coverage: dict[str, Any] | None = None,
    routing_metadata: dict[str, Any] | None = None,
    window_planning: dict[str, Any] | None = None,
    runtime_guardrails: dict[str, Any] | None = None,
) -> SegmentAsrRoutingResult:
    report = _base_report(
        options=options,
        media_path=media_path,
        routing_input_path=routing_input_path,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
        fallback_used=True,
        fallback_reason=fallback_reason,
        status="fallback",
        normal_srt_path=normal_srt_path,
        routed_srt_path=routed_srt_path,
        apply_attempted=apply_attempted,
        apply_succeeded=apply_succeeded,
        full_routed_segments_available=full_routed_segments_available,
        preview_only_rejected=preview_only_rejected,
        candidate_srt_path=candidate_srt_path,
        candidate_accepted=candidate_accepted,
        coverage=coverage,
        routing_metadata=routing_metadata,
        window_planning=window_planning,
        runtime_guardrails=runtime_guardrails,
    )
    if prototype_report is not None:
        report["prototype_report"] = _prototype_report_summary(prototype_report)
    if analysis is not None:
        report["analyzer"] = analysis
        report["windows"] = analysis.get("windows", [])
    report_path = _write_integration_report(report_dir, media_path, report)
    return SegmentAsrRoutingResult(
        mode=options.mode,
        report_path=str(report_path.resolve()),
        status="fallback",
        fallback_used=True,
        fallback_reason=fallback_reason,
        normal_srt_path=str(Path(normal_srt_path).resolve()) if normal_srt_path else "",
        routed_srt_path=str(Path(routed_srt_path).resolve()) if routed_srt_path else "",
    )


def _write_apply_failure_report(
    *,
    options: SegmentAsrRoutingOptions,
    media_path: Path,
    routing_input_path: Path,
    report_dir: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
    normal_srt_path: Path | None,
    routed_srt_path: Path | None,
    prototype_report: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    full_routed_segments_available: bool,
    preview_only_rejected: bool,
    candidate_srt_path: Path | None,
    candidate_accepted: bool,
    coverage: dict[str, Any] | None,
    routing_metadata: dict[str, Any] | None,
    window_planning: dict[str, Any] | None,
    runtime_guardrails: dict[str, Any] | None,
    failure_reason: str,
) -> None:
    report = _base_report(
        options=options,
        media_path=media_path,
        routing_input_path=routing_input_path,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
        fallback_used=False,
        fallback_reason=None,
        status="apply_failed",
        normal_srt_path=normal_srt_path,
        routed_srt_path=routed_srt_path,
        apply_attempted=True,
        apply_succeeded=False,
        full_routed_segments_available=full_routed_segments_available,
        preview_only_rejected=preview_only_rejected,
        candidate_srt_path=candidate_srt_path,
        candidate_accepted=candidate_accepted,
        coverage=coverage,
        routing_metadata=routing_metadata,
        window_planning=window_planning,
        runtime_guardrails=runtime_guardrails,
    )
    report["apply_failure_reason"] = failure_reason
    report["strict_failure_note"] = "no routed subtitle output was accepted"
    if prototype_report is not None:
        report["prototype_report"] = _prototype_report_summary(prototype_report)
    if analysis is not None:
        report["analyzer"] = analysis
        report["windows"] = analysis.get("windows", [])
    _write_integration_report(report_dir, media_path, report)


def _base_report(
    *,
    options: SegmentAsrRoutingOptions,
    media_path: Path,
    routing_input_path: Path,
    model_name: str,
    device: str,
    compute_type: str | None,
    local_files_only: bool,
    fallback_used: bool,
    fallback_reason: str | None,
    status: str,
    subtitle_output_affected: bool = False,
    normal_srt_path: Path | None = None,
    routed_srt_path: Path | None = None,
    apply_attempted: bool = False,
    apply_succeeded: bool = False,
    full_routed_segments_available: bool = False,
    preview_only_rejected: bool = False,
    candidate_srt_path: Path | None = None,
    candidate_accepted: bool = False,
    coverage: dict[str, Any] | None = None,
    routing_metadata: dict[str, Any] | None = None,
    window_planning: dict[str, Any] | None = None,
    runtime_guardrails: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "report_type": "segment_asr_routing_integration",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "experimental": True,
        "segment_asr_routing_mode": options.mode,
        "subtitle_output_affected": subtitle_output_affected,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "apply_attempted": apply_attempted,
        "apply_succeeded": apply_succeeded,
        "full_routed_segments_available": full_routed_segments_available,
        "preview_only_rejected": preview_only_rejected,
        "candidate_srt_path": str(Path(candidate_srt_path).resolve()) if candidate_srt_path else "",
        "candidate_accepted": candidate_accepted,
        "coverage_full": bool(coverage.get("full_coverage")) if isinstance(coverage, dict) else False,
        "coverage_rate": coverage.get("coverage_rate") if isinstance(coverage, dict) else None,
        "gap_count": coverage.get("gap_count") if isinstance(coverage, dict) else None,
        "needs_review_window_count": (
            routing_metadata.get("needs_review_window_count") if isinstance(routing_metadata, dict) else 0
        ),
        "skip_window_count": (
            routing_metadata.get("skip_window_count") if isinstance(routing_metadata, dict) else 0
        ),
        "selected_run_counts": (
            dict(routing_metadata.get("selected_run_counts") or {})
            if isinstance(routing_metadata, dict)
            else {}
        ),
        "confidence_threshold": options.confidence_threshold,
        "min_segments": options.min_segments,
        "strict": options.strict,
        "window_planning": window_planning or _window_settings(),
        "metadata": {
            "media_path": str(Path(media_path).resolve()),
            "routing_input_path": str(Path(routing_input_path).resolve()),
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
            "local_files_only": local_files_only,
            "normal_srt_path": str(Path(normal_srt_path).resolve()) if normal_srt_path else "",
            "routed_srt_path": str(Path(routed_srt_path).resolve()) if routed_srt_path else "",
        },
    }
    if isinstance(runtime_guardrails, dict):
        report["runtime_guardrails"] = runtime_guardrails
    if isinstance(coverage, dict):
        report["coverage"] = coverage
    return report


def _prototype_report_summary(prototype_report: dict[str, Any]) -> dict[str, str]:
    return {
        "json_path": str(prototype_report.get("json_path") or ""),
        "markdown_path": str(prototype_report.get("markdown_path") or ""),
    }


def _payload_has_full_segment_data(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    windows = payload.get("windows")
    if not isinstance(windows, list):
        return False
    for window in windows:
        if not isinstance(window, dict):
            continue
        if _segments_are_full(window.get("segments")):
            return True
        runs = window.get("runs")
        if isinstance(runs, dict):
            for run in runs.values():
                if (
                    isinstance(run, dict)
                    and _run_has_full_segment_data(run)
                    and isinstance(run.get("segments"), list)
                ):
                    return True
                if _segments_are_full(run):
                    return True
    return False


def _payload_has_full_segments(payload: Any) -> bool:
    return _payload_has_full_segment_data(payload)


def _segments_are_full(segments: Any) -> bool:
    if not isinstance(segments, list):
        return False
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if str(segment.get("text") or "").strip() and "start" in segment and "end" in segment:
            return True
    return False


def _build_routed_payload(
    *,
    full_payload: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    full_windows = {
        int(window.get("window_index", index)): window
        for index, window in enumerate(full_payload.get("windows", []))
        if isinstance(window, dict)
    }
    routed_windows: list[dict[str, Any]] = []
    selected_run_counts: dict[str, int] = {}
    needs_review_count = 0
    skip_window_count = 0

    for analyzed in analysis.get("windows", []):
        if not isinstance(analyzed, dict):
            continue
        window_index = int(analyzed.get("window_index", 0) or 0)
        classification = str(analyzed.get("classification") or "")
        if classification == "needs_review":
            needs_review_count += 1
        if classification == "skip_window":
            skip_window_count += 1
        selected_run = _selected_run_for_classification(classification)
        if selected_run is None:
            raise SegmentAsrRoutingError(APPLY_SKIP_WINDOW_REASON)

        full_window = full_windows.get(window_index)
        if full_window is None:
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)

        run = _run_for_selected_run(full_window, selected_run)
        segments = run.get("segments") if isinstance(run, dict) else None
        if not isinstance(run, dict) or not _run_has_full_segment_data(run):
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)
        if not _segments_are_full(segments):
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)

        selected_run_counts[selected_run] = selected_run_counts.get(selected_run, 0) + 1
        routed_windows.append(
            {
                "window_index": window_index,
                "start_seconds": analyzed.get("start_seconds", full_window.get("start_seconds", 0.0)),
                "end_seconds": analyzed.get("end_seconds", full_window.get("end_seconds", 0.0)),
                "classification": classification,
                "selected_run": selected_run,
                "timestamp_scope": full_window.get("timestamp_scope", "local"),
                "segments": segments,
            }
        )

    if not routed_windows and skip_window_count:
        raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)
    return {
        "windows": routed_windows,
        "metadata": {
            "needs_review_window_count": needs_review_count,
            "skip_window_count": skip_window_count,
            "selected_run_counts": selected_run_counts,
        },
    }


def _analysis_routing_metadata(analysis: dict[str, Any] | None) -> dict[str, Any]:
    needs_review_count = 0
    skip_window_count = 0
    selected_run_counts: dict[str, int] = {}
    windows = analysis.get("windows", []) if isinstance(analysis, dict) else []
    if not isinstance(windows, list):
        return {
            "needs_review_window_count": 0,
            "skip_window_count": 0,
            "selected_run_counts": {},
        }
    for window in windows:
        if not isinstance(window, dict):
            continue
        classification = str(window.get("classification") or "")
        if classification == "needs_review":
            needs_review_count += 1
        if classification == "skip_window":
            skip_window_count += 1
        try:
            selected_run = _selected_run_for_classification(classification) if classification else None
        except SegmentAsrRoutingError:
            selected_run = None
        if selected_run:
            selected_run_counts[selected_run] = selected_run_counts.get(selected_run, 0) + 1
    return {
        "needs_review_window_count": needs_review_count,
        "skip_window_count": skip_window_count,
        "selected_run_counts": selected_run_counts,
    }


def _selected_run_for_classification(classification: str) -> str | None:
    if classification == "keep_auto":
        return "auto"
    if classification == "prefer_forced_fr":
        return "forced-fr"
    if classification == "prefer_forced_en":
        return "forced-en"
    if classification == "needs_review":
        return "auto"
    if classification == "skip_window":
        return None
    raise SegmentAsrRoutingError(f"unsupported routing classification: {classification}")


def _run_for_selected_run(window: dict[str, Any], selected_run: str) -> Any:
    runs = window.get("runs")
    if isinstance(runs, dict):
        candidate_keys = {
            selected_run,
            selected_run.replace("-", "_"),
            selected_run.replace("_", "-"),
        }
        for key in candidate_keys:
            run = runs.get(key)
            if isinstance(run, dict):
                return run
            if isinstance(run, list):
                return {"full_segments_available": True, "segments": run}
    if str(window.get("selected_run") or "") in ("", selected_run):
        return {
            "full_segments_available": isinstance(window.get("segments"), list),
            "segments": window.get("segments"),
        }
    return None


def _run_has_full_segment_data(run: dict[str, Any]) -> bool:
    return run.get("full_segments_available") is not False and isinstance(run.get("segments"), list)


def _routed_output_path(
    *,
    report_dir: Path,
    media_path: Path,
    normal_srt_path: Path | None,
    routed_srt_path: Path | None,
) -> Path:
    if routed_srt_path is not None:
        return Path(routed_srt_path)
    if normal_srt_path is not None:
        return Path(normal_srt_path).with_name(f"{Path(normal_srt_path).stem}.routed.srt")
    return report_dir / "routed" / f"{_safe_stem(Path(media_path).stem)}.routed.srt"


def _candidate_routed_output_path(*, report_dir: Path, media_path: Path) -> Path:
    candidates_dir = Path(report_dir) / "candidates"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return candidates_dir / f"{_safe_stem(Path(media_path).stem)}.{stamp}.candidate.srt"


def _cleanup_candidate(candidate_path: Path | None) -> None:
    if candidate_path is None:
        return
    try:
        Path(candidate_path).unlink(missing_ok=True)
    except OSError:
        pass


def _window_settings(*, full_coverage: bool = False, window_seconds: float | None = None) -> dict[str, Any]:
    resolved_window_seconds = _positive_float_or_default(window_seconds, DEFAULT_WINDOW_SECONDS)
    if full_coverage:
        return {
            "mode": "full_coverage",
            "samples": 0,
            "sample_every_seconds": resolved_window_seconds,
            "window_seconds": resolved_window_seconds,
            "manual_windows": [],
        }
    return {
        "samples": DEFAULT_SAMPLE_COUNT,
        "sample_every_seconds": None,
        "window_seconds": DEFAULT_WINDOW_SECONDS,
        "manual_windows": [],
    }


def _positive_float_or_default(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number > 0 else default


def _write_integration_report(report_dir: Path, media_path: Path, report: dict[str, Any]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = _safe_stem(Path(media_path).stem)
    path = report_dir / f"{stem}.{stamp}.segment_asr_routing.json"
    write_json(path, report)
    return path


def _safe_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)
    return cleaned.strip("._") or "media"
