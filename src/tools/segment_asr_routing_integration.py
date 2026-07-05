from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from encoding_utils import write_json
from segment_asr_prototype import DEFAULT_SAMPLE_COUNT, DEFAULT_WINDOW_SECONDS, run_prototype_cli
from segment_asr_report_analyzer import Settings, analyze_reports
from segment_asr_srt_assembler import assemble_routed_srt


ROUTING_MODES = {"off", "dry_run", "apply"}
APPLY_SEGMENTS_UNAVAILABLE_REASON = "routed ASR segments are not available"
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
    return SegmentAsrRoutingOptions(
        mode=mode,
        confidence_threshold=threshold,
        min_segments=min_segments,
        strict=bool(options.strict),
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
) -> tuple[dict[str, Any], dict[str, Any]]:
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

    try:
        prototype_report, analysis = _collect_routing_evidence(
            options=options,
            routing_input_path=routing_input_path,
            report_dir=report_dir,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
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
        full_segments_available = _payload_has_full_segments(full_payload)
        preview_only_rejected = not full_segments_available
        if not full_segments_available:
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)

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
    """Return full timestamped routed segments when a provider exists.

    M7.1 currently persists preview-only evidence, so live M8.2 returns
    unavailable here. Tests may monkeypatch this helper with real segments.
    """
    return None


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
) -> dict[str, Any]:
    return {
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
            "normal_srt_path": str(Path(normal_srt_path).resolve()) if normal_srt_path else "",
            "routed_srt_path": str(Path(routed_srt_path).resolve()) if routed_srt_path else "",
        },
    }


def _prototype_report_summary(prototype_report: dict[str, Any]) -> dict[str, str]:
    return {
        "json_path": str(prototype_report.get("json_path") or ""),
        "markdown_path": str(prototype_report.get("markdown_path") or ""),
    }


def _payload_has_full_segments(payload: Any) -> bool:
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
                if isinstance(run, dict) and _segments_are_full(run.get("segments")):
                    return True
                if _segments_are_full(run):
                    return True
    return False


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
    skipped_count = 0

    for analyzed in analysis.get("windows", []):
        if not isinstance(analyzed, dict):
            continue
        window_index = int(analyzed.get("window_index", 0) or 0)
        classification = str(analyzed.get("classification") or "")
        selected_run = _selected_run_for_classification(classification)
        if selected_run is None:
            skipped_count += 1
            routed_windows.append(
                {
                    "window_index": window_index,
                    "start_seconds": analyzed.get("start_seconds", 0.0),
                    "end_seconds": analyzed.get("end_seconds", 0.0),
                    "classification": classification,
                    "selected_run": "skip_window",
                    "segments": None,
                }
            )
            continue

        full_window = full_windows.get(window_index)
        if full_window is None:
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)

        segments = _segments_for_selected_run(full_window, selected_run)
        if not _segments_are_full(segments):
            raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)

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

    if not routed_windows and skipped_count:
        raise SegmentAsrRoutingError(APPLY_SEGMENTS_UNAVAILABLE_REASON)
    return {"windows": routed_windows}


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


def _segments_for_selected_run(window: dict[str, Any], selected_run: str) -> Any:
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
                return run.get("segments")
            if isinstance(run, list):
                return run
    if str(window.get("selected_run") or "") in ("", selected_run):
        return window.get("segments")
    return None


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
