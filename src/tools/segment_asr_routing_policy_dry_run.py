from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from encoding_utils import read_json, write_json, write_text
from segment_asr_report_analyzer import (
    CLASSIFICATIONS,
    AnalyzerInputError,
    Settings as AnalyzerSettings,
    analyze_reports,
)
from segment_asr_routing_sandbox import expand_input_paths

TOOL_NAME = "segment_asr_routing_policy_dry_run"
SCHEMA_VERSION = 1

NOTES = [
    "This dry-run does not prove transcript correctness.",
    "This dry-run does not change production routing.",
    "candidate_ready_for_design is not production_ready.",
]

LIMITATION_TEXT = (
    "This dry-run does not prove transcript correctness and does not change production routing. "
    "candidate_ready_for_design is not production_ready."
)


@dataclass(frozen=True)
class GateSettings:
    confidence_threshold: float = 0.70
    min_segments: int = 1
    min_total_windows: int = 5
    max_needs_review_rate: float = 0.25
    max_skip_window_rate: float = 0.10


class DryRunInputError(ValueError):
    pass


def main() -> int:
    args = parse_args()
    gate_settings = GateSettings(
        confidence_threshold=args.confidence_threshold,
        min_segments=args.min_segments,
        min_total_windows=args.min_total_windows,
        max_needs_review_rate=args.max_needs_review_rate,
        max_skip_window_rate=args.max_skip_window_rate,
    )
    try:
        if args.sandbox_json:
            result = _load_from_sandbox_json(args.sandbox_json)
        else:
            result = _load_from_reports(args.input_files or [], gate_settings)
        readiness = evaluate_readiness(result, gate_settings)
        json_output = build_json_output(result, readiness, gate_settings)
        markdown = render_markdown(json_output)

        if args.output_json:
            _write_json_output(Path(args.output_json), json_output)
        if args.output_md:
            _write_text_output(Path(args.output_md), markdown)
    except (AnalyzerInputError, DryRunInputError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(markdown)
    return 0


def _write_json_output(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)


def _write_text_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, text)


def _load_from_sandbox_json(sandbox_json_path: str) -> dict[str, Any]:
    path = Path(sandbox_json_path)
    if not path.is_file():
        raise DryRunInputError(f"Sandbox JSON not found: {path}")
    try:
        data = read_json(path, user_input=True)
    except Exception as exc:
        raise DryRunInputError(f"Invalid sandbox JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DryRunInputError(f"Invalid sandbox JSON {path}: top-level must be an object")

    # Prefer to replay the analysis from the original input files if they are still present.
    input_files = data.get("input_files", [])
    baseline_settings = data.get("baseline_settings", {})
    if input_files and isinstance(input_files, list):
        existing = [p for p in input_files if Path(p).is_file()]
        if existing:
            settings = AnalyzerSettings(
                confidence_threshold=float(baseline_settings.get("confidence_threshold", 0.70)),
                min_segments=int(baseline_settings.get("min_segments", 1)),
            )
            result = analyze_reports(existing, settings)
            # Check for unstable routing decisions from the sandbox sweep.
            runs = data.get("runs", [])
            unstable = any(
                run.get("changed_from_baseline", 0) > 0
                for run in runs
                if isinstance(run, dict)
            )
            result["unstable_routing"] = unstable
            return result

    # Fallback: use only the summary-level data from the sandbox JSON.
    runs = data.get("runs")
    if not isinstance(runs, list) or not runs:
        raise DryRunInputError(
            f"Invalid sandbox JSON {path}: missing 'runs' or no baseline run is available"
        )

    baseline_run = runs[0]
    if not isinstance(baseline_run, dict):
        raise DryRunInputError(
            f"Invalid sandbox JSON {path}: baseline run is malformed"
        )

    summary = baseline_run.get("summary")
    if not isinstance(summary, dict):
        raise DryRunInputError(
            f"Invalid sandbox JSON {path}: baseline run summary is missing or malformed"
        )

    result = {
        "input_files": input_files if isinstance(input_files, list) else [],
        "settings": baseline_settings if isinstance(baseline_settings, dict) else {},
        "summary": {k: int(summary.get(k, 0)) for k in ("total_windows", *CLASSIFICATIONS)},
        "windows": [],
    }
    unstable = any(
        run.get("changed_from_baseline", 0) > 0
        for run in runs
        if isinstance(run, dict)
    )
    result["unstable_routing"] = unstable
    return result


def _load_from_reports(input_files: list[str], gate_settings: GateSettings) -> dict[str, Any]:
    if not input_files:
        raise DryRunInputError("No input files provided.")
    expanded = expand_input_paths(input_files)
    if not expanded:
        raise DryRunInputError("No input files found after expanding globs.")
    settings = AnalyzerSettings(
        confidence_threshold=gate_settings.confidence_threshold,
        min_segments=gate_settings.min_segments,
    )
    result = analyze_reports(expanded, settings)
    result["unstable_routing"] = False
    return result


def evaluate_readiness(result: dict[str, Any], gate_settings: GateSettings) -> dict[str, Any]:
    summary = result.get("summary", {})
    total_windows = int(summary.get("total_windows", 0))
    needs_review = int(summary.get("needs_review", 0))
    skip_window = int(summary.get("skip_window", 0))

    needs_review_rate = needs_review / total_windows if total_windows > 0 else 0.0
    skip_window_rate = skip_window / total_windows if total_windows > 0 else 0.0

    blockers: list[str] = []

    if total_windows < gate_settings.min_total_windows:
        blockers.append(
            f"total_windows {total_windows} is below min_total_windows {gate_settings.min_total_windows}"
        )
    if total_windows == 0:
        blockers.append("no usable windows found in input")

    if needs_review_rate > gate_settings.max_needs_review_rate:
        blockers.append(
            f"needs_review_rate {needs_review_rate:.2f} exceeds max_needs_review_rate {gate_settings.max_needs_review_rate}"
        )
    if skip_window_rate > gate_settings.max_skip_window_rate:
        blockers.append(
            f"skip_window_rate {skip_window_rate:.2f} exceeds max_skip_window_rate {gate_settings.max_skip_window_rate}"
        )

    if result.get("unstable_routing"):
        blockers.append("parameter sweep shows unstable routing decisions")

    if total_windows == 0 or total_windows < gate_settings.min_total_windows:
        status = "insufficient_evidence"
    elif blockers:
        status = "not_ready"
    else:
        status = "candidate_ready_for_design"

    return {
        "status": status,
        "reason": _build_reason(status, blockers),
        "blockers": blockers,
        "needs_review_rate": needs_review_rate,
        "skip_window_rate": skip_window_rate,
    }


def _build_reason(status: str, blockers: list[str]) -> str:
    if status == "insufficient_evidence":
        return "Insufficient evidence: " + "; ".join(blockers) if blockers else "Not enough windows to evaluate."
    if status == "not_ready":
        return "Not ready: " + "; ".join(blockers)
    return (
        "Evidence meets conservative gates. candidate_ready_for_design means the evidence is structured "
        "enough to justify designing a future production integration proposal. It does not enable or validate production routing."
    )


def build_json_output(
    result: dict[str, Any],
    readiness: dict[str, Any],
    gate_settings: GateSettings,
) -> dict[str, Any]:
    summary = result.get("summary", {})
    review_windows = _build_review_windows(result.get("windows", []))

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "input_files": result.get("input_files", []),
        "settings": {
            "confidence_threshold": gate_settings.confidence_threshold,
            "min_segments": gate_settings.min_segments,
            "min_total_windows": gate_settings.min_total_windows,
            "max_needs_review_rate": gate_settings.max_needs_review_rate,
            "max_skip_window_rate": gate_settings.max_skip_window_rate,
        },
        "readiness": {
            "status": readiness["status"],
            "reason": readiness["reason"],
            "blockers": readiness["blockers"],
        },
        "summary": {
            "total_windows": summary.get("total_windows", 0),
            "keep_auto": summary.get("keep_auto", 0),
            "prefer_forced_fr": summary.get("prefer_forced_fr", 0),
            "prefer_forced_en": summary.get("prefer_forced_en", 0),
            "needs_review": summary.get("needs_review", 0),
            "skip_window": summary.get("skip_window", 0),
            "needs_review_rate": readiness["needs_review_rate"],
            "skip_window_rate": readiness["skip_window_rate"],
        },
        "review_windows": review_windows,
        "notes": list(NOTES),
    }


def _build_review_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for window in windows:
        classification = window.get("classification", "")
        if classification in ("needs_review", "skip_window"):
            reviewed.append({
                "source_file": str(window.get("source_file", "")),
                "window_index": window.get("window_index", 0),
                "classification": classification,
                "reason": window.get("reason", ""),
            })
    return reviewed


def render_markdown(data: dict[str, Any]) -> str:
    settings = data.get("settings", {})
    readiness = data.get("readiness", {})
    summary = data.get("summary", {})
    review_windows = data.get("review_windows", [])
    blockers = readiness.get("blockers", [])

    lines = [
        "# M7.5 Segment ASR Routing Policy Dry-Run",
        "",
        "## Inputs",
        "",
    ]
    for path in data.get("input_files", []):
        lines.append(f"- `{Path(path).name}`")

    lines.extend([
        "",
        "## Settings",
        "",
        f"- Confidence threshold: `{settings.get('confidence_threshold', '')}`",
        f"- Minimum segments: `{settings.get('min_segments', '')}`",
        f"- Minimum total windows: `{settings.get('min_total_windows', '')}`",
        f"- Maximum needs-review rate: `{settings.get('max_needs_review_rate', '')}`",
        f"- Maximum skip-window rate: `{settings.get('max_skip_window_rate', '')}`",
        "",
        "## Readiness Result",
        "",
        f"- Status: `{readiness.get('status', '')}`",
        f"- Reason: {readiness.get('reason', '')}",
    ])

    if blockers:
        lines.extend([
            "",
            "## Blockers",
            "",
        ])
        for blocker in blockers:
            lines.append(f"- {blocker}")

    lines.extend([
        "",
        "## Aggregate Classification Summary",
        "",
        f"- Total windows: `{summary.get('total_windows', 0)}`",
    ])
    for classification in CLASSIFICATIONS:
        lines.append(f"- {classification}: `{summary.get(classification, 0)}`")
    lines.extend([
        f"- needs_review_rate: `{summary.get('needs_review_rate', 0.0):.2f}`",
        f"- skip_window_rate: `{summary.get('skip_window_rate', 0.0):.2f}`",
    ])

    lines.extend([
        "",
        "## Review Windows",
        "",
    ])
    if review_windows:
        lines.append("| Source | Window | Classification | Reason |")
        lines.append("| ------ | ------ | -------------- | ------ |")
        for window in review_windows:
            lines.append(
                "| {source} | {index} | {classification} | {reason} |".format(
                    source=_md_cell(Path(str(window.get("source_file", ""))).name),
                    index=_md_cell(window.get("window_index", "")),
                    classification=_md_cell(window.get("classification", "")),
                    reason=_md_cell(window.get("reason", "")),
                )
            )
    else:
        lines.append("No review windows (or review windows unavailable from sandbox-only input).")

    lines.extend([
        "",
        "## Notes And Limitations",
        "",
        f"- {LIMITATION_TEXT}",
    ])
    return "\n".join(lines)


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


class _CleanErrorParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(f"Error: {message}", file=sys.stderr)
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = _CleanErrorParser(
        description="M7.5: Segment ASR Routing Policy Dry-Run and Readiness Gate."
    )
    parser.add_argument(
        "input_files",
        nargs="*",
        help="M7.1/M7.3 JSON report file(s). Supports glob patterns.",
    )
    parser.add_argument(
        "--sandbox-json",
        default=None,
        help="Optional path to an existing M7.4 sandbox JSON output.",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.70,
        help="Confidence threshold passed to the M7.2 analyzer (default: 0.70).",
    )
    parser.add_argument(
        "--min-segments",
        type=int,
        default=1,
        help="Minimum segments passed to the M7.2 analyzer (default: 1).",
    )
    parser.add_argument(
        "--min-total-windows",
        type=int,
        default=5,
        help="Minimum total windows for readiness evaluation (default: 5).",
    )
    parser.add_argument(
        "--max-needs-review-rate",
        type=float,
        default=0.25,
        help="Maximum acceptable needs_review rate (default: 0.25).",
    )
    parser.add_argument(
        "--max-skip-window-rate",
        type=float,
        default=0.10,
        help="Maximum acceptable skip_window rate (default: 0.10).",
    )
    parser.add_argument("--output-json", default=None, help="Optional JSON readiness report path.")
    parser.add_argument("--output-md", default=None, help="Optional Markdown readiness report path.")
    args = parser.parse_args()

    if not 0 <= args.confidence_threshold <= 1:
        parser.error("--confidence-threshold must be between 0 and 1")
    if args.min_segments < 0:
        parser.error("--min-segments must be >= 0")
    if args.min_total_windows < 0:
        parser.error("--min-total-windows must be >= 0")
    if not 0 <= args.max_needs_review_rate <= 1:
        parser.error("--max-needs-review-rate must be between 0 and 1")
    if not 0 <= args.max_skip_window_rate <= 1:
        parser.error("--max-skip-window-rate must be between 0 and 1")

    if not args.sandbox_json and not args.input_files:
        parser.error("Either input_files or --sandbox-json must be provided")

    return args


if __name__ == "__main__":
    raise SystemExit(main())
