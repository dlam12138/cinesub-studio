from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from encoding_utils import write_json
from segment_asr_prototype import DEFAULT_SAMPLE_COUNT, DEFAULT_WINDOW_SECONDS, run_prototype_cli
from segment_asr_report_analyzer import Settings, analyze_reports


ROUTING_MODES = {"off", "dry_run", "apply"}
APPLY_DEFERRED_REASON = "apply mode is deferred to M8.2"
REPORT_DIR_NAME = "segment_asr_routing"


class SegmentAsrRoutingError(RuntimeError):
    """Controlled error for experimental segment ASR routing failures."""


@dataclass(frozen=True)
class SegmentAsrRoutingOptions:
    mode: str = "off"
    confidence_threshold: float = 0.70
    min_segments: int = 1
    strict: bool = False


@dataclass(frozen=True)
class SegmentAsrRoutingResult:
    mode: str
    report_path: str = ""
    status: str = "off"
    fallback_used: bool = False
    fallback_reason: str = ""
    subtitle_output_affected: bool = False


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
    return SegmentAsrRoutingOptions(
        mode=mode,
        confidence_threshold=threshold,
        min_segments=min_segments,
        strict=bool(options.strict),
    )


def ensure_apply_is_not_strict(options: SegmentAsrRoutingOptions) -> None:
    options = validate_options(options)
    if options.mode == "apply" and options.strict:
        raise SegmentAsrRoutingError(APPLY_DEFERRED_REASON)


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
) -> SegmentAsrRoutingResult:
    options = validate_options(options)
    ensure_apply_is_not_strict(options)
    if options.mode == "off":
        return SegmentAsrRoutingResult(mode="off")

    report_dir = Path(report_root) / REPORT_DIR_NAME
    if options.mode == "apply":
        return _write_apply_deferred_report(
            options=options,
            media_path=media_path,
            routing_input_path=routing_input_path,
            report_dir=report_dir,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
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
    from argparse import Namespace

    prototype_dir = report_dir / "prototype"
    window_settings = _window_settings()
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
        "json_path": prototype_json,
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


def _write_apply_deferred_report(
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
    report = _base_report(
        options=options,
        media_path=media_path,
        routing_input_path=routing_input_path,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        local_files_only=local_files_only,
        fallback_used=True,
        fallback_reason=APPLY_DEFERRED_REASON,
        status="apply_deferred",
    )
    report_path = _write_integration_report(report_dir, media_path, report)
    return SegmentAsrRoutingResult(
        mode=options.mode,
        report_path=str(report_path.resolve()),
        status="apply_deferred",
        fallback_used=True,
        fallback_reason=APPLY_DEFERRED_REASON,
    )


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
    )
    report_path = _write_integration_report(report_dir, media_path, report)
    return SegmentAsrRoutingResult(
        mode=options.mode,
        report_path=str(report_path.resolve()),
        status="fallback",
        fallback_used=True,
        fallback_reason=fallback_reason,
    )


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
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "report_type": "segment_asr_routing_integration",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "experimental": True,
        "segment_asr_routing_mode": options.mode,
        "subtitle_output_affected": False,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "confidence_threshold": options.confidence_threshold,
        "min_segments": options.min_segments,
        "strict": options.strict,
        "window_planning": _window_settings(),
        "metadata": {
            "media_path": str(Path(media_path).resolve()),
            "routing_input_path": str(Path(routing_input_path).resolve()),
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
            "local_files_only": local_files_only,
        },
    }


def _window_settings() -> dict[str, Any]:
    return {
        "samples": DEFAULT_SAMPLE_COUNT,
        "sample_every_seconds": None,
        "window_seconds": DEFAULT_WINDOW_SECONDS,
        "manual_windows": [],
    }


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
