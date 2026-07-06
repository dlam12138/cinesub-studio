from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from encoding_utils import read_json, write_text


REDACTED_TEXT = "[REDACTED]"


@dataclass(frozen=True)
class SmokeSummaryOptions:
    input_patterns: list[str]
    output_md: str
    title: str = "Segment ASR Routing Smoke Summary"


@dataclass(frozen=True)
class SmokeScenarioSummary:
    report_path: str
    status: str
    mode: str
    subtitle_output_affected: bool
    apply_attempted: bool
    apply_succeeded: bool
    fallback_used: bool
    fallback_reason: str
    duration_seconds: float | None
    window_seconds: float | None
    planned_window_count: int | None
    estimated_asr_calls: int | None
    max_windows: int | None
    cap_exceeded: bool
    allow_large_run: bool
    coverage_full: bool
    coverage_rate: float | None
    gap_count: int | None
    candidate_accepted: bool
    preview_only_rejected: bool
    strict: bool
    model: str
    device: str
    user_title: str
    user_message: str
    user_next_action: str
    media_path_redacted: str
    report_generated_at: str


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    import math

    return number if math.isfinite(number) else None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _redact_segments(value: Any) -> Any:
    """Replace any list of segment dicts with a count placeholder."""
    if isinstance(value, list):
        return {"count": len(value), "redacted": True}
    return value


def _safe_path(value: Any) -> str:
    """Return a path string with filename redacted for audit safety."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        path = Path(text)
        if path.name:
            if len(path.parts) > 1:
                return f"{path.parent.as_posix()}/[REDACTED]"
            return "[REDACTED]"
        return text.replace("\\", "/")
    except (TypeError, ValueError):
        return text


def _redact_value(key: str, value: Any) -> Any:
    """Recursively redact a value based on its key name."""
    lower_key = key.lower()

    # Any key containing 'path' -> redact filename
    if "path" in lower_key:
        return _safe_path(value)

    # Text-like keys -> redacted string
    if lower_key in {"text", "transcript", "preview", "text_preview", "full_text"}:
        return REDACTED_TEXT

    # Segment lists -> count placeholder
    if lower_key in {"segments", "full_segments"} and isinstance(value, list):
        return _redact_segments(value)

    # Recurse into dicts
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}

    # Recurse into lists (pass parent key down for scalar lists)
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]

    # Safe scalar
    return value


def _redact_report_for_audit(report: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-redacted copy safe for acceptance / audit bundles."""
    return {k: _redact_value(k, v) for k, v in report.items()}


def _extract_user_summary(report: dict[str, Any]) -> dict[str, str]:
    summary = report.get("user_summary") if isinstance(report.get("user_summary"), dict) else {}
    return {
        "status": str(summary.get("status") or report.get("status") or ""),
        "title": str(summary.get("title") or ""),
        "message": str(summary.get("message") or ""),
        "next_action": str(summary.get("next_action") or ""),
    }


def _summarize_scenario(report: dict[str, Any], report_path: str) -> SmokeScenarioSummary:
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    runtime_guardrails = (
        report.get("runtime_guardrails") if isinstance(report.get("runtime_guardrails"), dict) else {}
    )
    user_summary = _extract_user_summary(report)
    return SmokeScenarioSummary(
        report_path=str(report_path),
        status=str(report.get("status") or ""),
        mode=str(report.get("segment_asr_routing_mode") or report.get("mode") or ""),
        subtitle_output_affected=_coerce_bool(report.get("subtitle_output_affected")),
        apply_attempted=_coerce_bool(report.get("apply_attempted")),
        apply_succeeded=_coerce_bool(report.get("apply_succeeded")),
        fallback_used=_coerce_bool(report.get("fallback_used")),
        fallback_reason=str(report.get("fallback_reason") or ""),
        duration_seconds=_coerce_float(runtime_guardrails.get("duration_seconds")),
        window_seconds=_coerce_float(runtime_guardrails.get("window_seconds")),
        planned_window_count=_coerce_int(runtime_guardrails.get("planned_window_count")),
        estimated_asr_calls=_coerce_int(runtime_guardrails.get("estimated_asr_calls")),
        max_windows=_coerce_int(runtime_guardrails.get("max_windows")),
        cap_exceeded=_coerce_bool(runtime_guardrails.get("cap_exceeded")),
        allow_large_run=_coerce_bool(runtime_guardrails.get("allow_large_run")),
        coverage_full=_coerce_bool(report.get("coverage_full")),
        coverage_rate=_coerce_float(report.get("coverage_rate")),
        gap_count=_coerce_int(report.get("gap_count")),
        candidate_accepted=_coerce_bool(report.get("candidate_accepted")),
        preview_only_rejected=_coerce_bool(report.get("preview_only_rejected")),
        strict=_coerce_bool(report.get("strict")),
        model=str(metadata.get("model") or ""),
        device=str(metadata.get("device") or ""),
        user_title=user_summary["title"],
        user_message=user_summary["message"],
        user_next_action=user_summary["next_action"],
        media_path_redacted=_safe_path(metadata.get("media_path")),
        report_generated_at=str(report.get("generated_at") or ""),
    )


def _resolve_input_files(patterns: list[str]) -> list[str]:
    seen: set[str] = set()
    files: list[str] = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            if os.path.isfile(path) and path not in seen:
                seen.add(path)
                files.append(path)
    return sorted(files)


def _format_optional_int(value: int | None) -> str:
    return str(value) if value is not None else "unknown"


def _format_optional_float(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "unknown"


def _render_markdown(
    options: SmokeSummaryOptions,
    summaries: list[SmokeScenarioSummary],
    redacted_reports: list[tuple[str, dict[str, Any]]],
) -> str:
    lines: list[str] = []
    lines.append(f"# {options.title}")
    lines.append("")
    lines.append(f"- Generated from {len(summaries)} report(s)")
    lines.append(f"- Input patterns: {', '.join(options.input_patterns)}")
    lines.append("")

    if not summaries:
        lines.append("No reports matched the input patterns.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Scenario Summary")
    lines.append("")
    lines.append(
        "| Report | Mode | Status | Affected | Applied | Fallback | Strict | Coverage | Windows | ASR Calls |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    for s in summaries:
        affected = "yes" if s.subtitle_output_affected else "no"
        applied = "yes" if s.apply_succeeded else ("attempted" if s.apply_attempted else "no")
        fallback = f"yes ({s.fallback_reason})" if s.fallback_used else "no"
        strict = "yes" if s.strict else "no"
        coverage = "full" if s.coverage_full else f"{_format_optional_float(s.coverage_rate)}"
        lines.append(
            f"| `{Path(s.report_path).name}` | {s.mode} | {s.status} | {affected} | {applied} | {fallback} | {strict} | {coverage} | {_format_optional_int(s.planned_window_count)} | {_format_optional_int(s.estimated_asr_calls)} |"
        )
    lines.append("")

    for index, s in enumerate(summaries, start=1):
        lines.append(f"### Scenario {index}: {s.user_title or s.status}")
        lines.append("")
        lines.append(f"- **Report:** `{s.report_path}`")
        lines.append(f"- **Mode:** {s.mode}")
        lines.append(f"- **Status:** {s.status}")
        lines.append(f"- **Strict:** {'yes' if s.strict else 'no'}")
        lines.append(f"- **Subtitle output affected:** {'yes' if s.subtitle_output_affected else 'no'}")
        lines.append(f"- **Apply attempted:** {'yes' if s.apply_attempted else 'no'}")
        lines.append(f"- **Apply succeeded:** {'yes' if s.apply_succeeded else 'no'}")
        lines.append(f"- **Fallback used:** {'yes' if s.fallback_used else 'no'}")
        if s.fallback_used and s.fallback_reason:
            lines.append(f"- **Fallback reason:** {s.fallback_reason}")
        lines.append(f"- **Candidate accepted:** {'yes' if s.candidate_accepted else 'no'}")
        lines.append(f"- **Preview-only rejected:** {'yes' if s.preview_only_rejected else 'no'}")
        lines.append(f"- **Coverage full:** {'yes' if s.coverage_full else 'no'}")
        lines.append(f"- **Coverage rate:** {_format_optional_float(s.coverage_rate)}")
        lines.append(f"- **Gap count:** {_format_optional_int(s.gap_count)}")
        lines.append(f"- **Duration (seconds):** {_format_optional_float(s.duration_seconds)}")
        lines.append(f"- **Window seconds:** {_format_optional_float(s.window_seconds)}")
        lines.append(f"- **Planned windows:** {_format_optional_int(s.planned_window_count)}")
        lines.append(f"- **Estimated ASR calls:** {_format_optional_int(s.estimated_asr_calls)}")
        lines.append(f"- **Max windows:** {_format_optional_int(s.max_windows)}")
        lines.append(f"- **Cap exceeded:** {'yes' if s.cap_exceeded else 'no'}")
        lines.append(f"- **Allow large run:** {'yes' if s.allow_large_run else 'no'}")
        lines.append(f"- **Model:** {s.model}")
        lines.append(f"- **Device:** {s.device}")
        lines.append(f"- **Media (redacted path):** `{s.media_path_redacted}`")
        lines.append(f"- **Report generated at:** {s.report_generated_at}")
        if s.user_message:
            lines.append(f"- **User message:** {s.user_message}")
        if s.user_next_action:
            lines.append(f"- **Next action:** {s.user_next_action}")
        lines.append("")

    lines.append("## Redacted Report Snapshots")
    lines.append("")
    lines.append(
        "The following JSON snapshots have been redacted to remove transcript text, full segment payloads, and absolute paths. They are safe for acceptance / audit bundles."
    )
    lines.append("")
    for path, redacted in redacted_reports:
        lines.append(f"### `{Path(path).name}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(redacted, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def summarize_smoke_reports(options: SmokeSummaryOptions) -> str:
    files = _resolve_input_files(options.input_patterns)
    summaries: list[SmokeScenarioSummary] = []
    redacted_reports: list[tuple[str, dict[str, Any]]] = []

    for path in files:
        try:
            report = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(report, dict):
            continue
        if report.get("report_type") != "segment_asr_routing_integration":
            continue
        summaries.append(_summarize_scenario(report, path))
        redacted_reports.append((path, _redact_report_for_audit(report)))

    markdown = _render_markdown(options, summaries, redacted_reports)
    output_path = Path(options.output_md)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(output_path, markdown)
    return str(output_path.resolve())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize segment ASR routing smoke reports into a redacted Markdown document."
    )
    parser.add_argument(
        "input_patterns",
        nargs="+",
        help="Glob pattern(s) for segment_asr_routing JSON report files.",
    )
    parser.add_argument(
        "--output-md",
        required=True,
        help="Path to write the Markdown summary.",
    )
    parser.add_argument(
        "--title",
        default="Segment ASR Routing Smoke Summary",
        help="Title for the Markdown summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    options = SmokeSummaryOptions(
        input_patterns=list(args.input_patterns),
        output_md=args.output_md,
        title=args.title,
    )
    output_path = summarize_smoke_reports(options)
    print(f"Smoke summary written to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
