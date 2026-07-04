from __future__ import annotations

import argparse
import glob as glob_module
import sys
from pathlib import Path
from typing import Any

from segment_asr_report_analyzer import (
    CLASSIFICATIONS,
    AnalyzerInputError,
    Settings,
    analyze_reports,
)

NOTES = [
    "This sandbox does not prove transcript correctness.",
    "This sandbox does not change production routing.",
]
LIMITATION_TEXT = (
    "This sandbox does not prove transcript correctness and does not change production routing. "
    "It only replays M7.2 analyzer decisions over fixed evidence inputs."
)


def main() -> int:
    args = parse_args()
    try:
        input_files = expand_input_paths(args.input_files)
        if not input_files:
            print("Error: No input files found.", file=sys.stderr)
            return 1

        confidence_thresholds, min_segments_list = _resolve_sweep(args)
        runs, baseline_settings = _run_sweep(
            input_files, confidence_thresholds, min_segments_list
        )
        json_output = build_json_output(input_files, baseline_settings, runs)
        markdown = render_markdown(json_output)

        if args.output_json:
            _write_json(Path(args.output_json), json_output)
        if args.output_md:
            _write_text(Path(args.output_md), markdown)
    except (AnalyzerInputError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(markdown)
    return 0


def _resolve_sweep(args: argparse.Namespace) -> tuple[list[float], list[int]]:
    if args.sweep_confidence_thresholds:
        thresholds = _parse_float_list(args.sweep_confidence_thresholds)
        for v in thresholds:
            if not 0 <= v <= 1:
                raise ValueError(f"Confidence threshold {v} must be between 0 and 1")
    else:
        thresholds = [args.confidence_threshold]

    if args.sweep_min_segments:
        mins = _parse_int_list(args.sweep_min_segments)
        for v in mins:
            if v < 0:
                raise ValueError(f"min-segments {v} must be >= 0")
    else:
        mins = [args.min_segments]

    return thresholds, mins


def _parse_float_list(text: str) -> list[float]:
    values: list[float] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(float(part))
        except ValueError as exc:
            raise ValueError(f"Invalid float in sweep list: {part}") from exc
    if not values:
        raise ValueError("Sweep list is empty")
    return values


def _parse_int_list(text: str) -> list[int]:
    values: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError as exc:
            raise ValueError(f"Invalid integer in sweep list: {part}") from exc
    if not values:
        raise ValueError("Sweep list is empty")
    return values


def _run_sweep(
    input_files: list[str],
    confidence_thresholds: list[float],
    min_segments_list: list[int],
) -> tuple[list[dict[str, Any]], dict[str, float | int]]:
    runs: list[dict[str, Any]] = []
    baseline_classifications: dict[tuple[str, int], str] | None = None

    for confidence_threshold in confidence_thresholds:
        for min_segments in min_segments_list:
            settings = Settings(
                confidence_threshold=confidence_threshold,
                min_segments=min_segments,
            )
            result = analyze_reports(input_files, settings)
            current_classifications = _extract_classifications(result)

            if baseline_classifications is None:
                baseline_classifications = current_classifications
                changed = 0
            else:
                changed = _count_changed(baseline_classifications, current_classifications)

            summary = result.get("summary", {})
            runs.append(
                {
                    "settings": {
                        "confidence_threshold": confidence_threshold,
                        "min_segments": min_segments,
                    },
                    "summary": {
                        "total_windows": summary.get("total_windows", 0),
                        **{k: summary.get(k, 0) for k in CLASSIFICATIONS},
                    },
                    "changed_from_baseline": changed,
                }
            )

    assert baseline_classifications is not None
    baseline_settings = {
        "confidence_threshold": confidence_thresholds[0],
        "min_segments": min_segments_list[0],
    }
    return runs, baseline_settings


def _extract_classifications(result: dict[str, Any]) -> dict[tuple[str, int], str]:
    out: dict[tuple[str, int], str] = {}
    for window in result.get("windows", []):
        key = (str(window.get("source_file", "")), int(window.get("window_index", 0)))
        out[key] = str(window.get("classification", ""))
    return out


def _count_changed(
    baseline: dict[tuple[str, int], str],
    current: dict[tuple[str, int], str],
) -> int:
    changed = 0
    for key, baseline_class in baseline.items():
        current_class = current.get(key)
        if current_class is None or current_class != baseline_class:
            changed += 1
    # Also count keys in current but not in baseline (should not happen with same inputs)
    for key in current:
        if key not in baseline:
            changed += 1
    return changed


def build_json_output(
    input_files: list[str],
    baseline_settings: dict[str, float | int],
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tool": "segment_asr_routing_sandbox",
        "input_files": input_files,
        "baseline_settings": baseline_settings,
        "runs": runs,
        "notes": list(NOTES),
    }


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# M7.4 Segment ASR Routing Sandbox Replay",
        "",
        "## Inputs",
        "",
    ]
    for path in data.get("input_files", []):
        lines.append(f"- `{Path(path).name}`")

    baseline = data.get("baseline_settings", {})
    lines.extend(
        [
            "",
            "## Baseline Settings",
            "",
            f"- Confidence threshold: `{baseline.get('confidence_threshold', '')}`",
            f"- Minimum segments: `{baseline.get('min_segments', '')}`",
            "",
            "## Parameter Sweep",
            "",
            "| Confidence Threshold | Min Segments | Total Windows | keep_auto | prefer_forced_fr | prefer_forced_en | needs_review | skip_window | Changed From Baseline |",
            "| -------------------- | ------------ | ------------- | --------- | ---------------- | ---------------- | ------------ | ----------- | --------------------- |",
        ]
    )

    for run in data.get("runs", []):
        settings = run.get("settings", {})
        summary = run.get("summary", {})
        cells = [
            str(settings.get("confidence_threshold", "")),
            str(settings.get("min_segments", "")),
            str(summary.get("total_windows", 0)),
        ]
        for classification in CLASSIFICATIONS:
            cells.append(str(summary.get(classification, 0)))
        cells.append(str(run.get("changed_from_baseline", 0)))
        lines.append("| " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "## Classification Distribution",
            "",
        ]
    )
    for run in data.get("runs", []):
        settings = run.get("settings", {})
        summary = run.get("summary", {})
        lines.append(
            f"- **ct={settings.get('confidence_threshold')}, ms={settings.get('min_segments')}**: "
            f"total={summary.get('total_windows', 0)}, "
            + ", ".join(f"{k}={summary.get(k, 0)}" for k in CLASSIFICATIONS)
        )

    lines.extend(
        [
            "",
            "## Changes From Baseline",
            "",
        ]
    )
    baseline_run = data.get("runs", [None])[0]
    for run in data.get("runs", []):
        settings = run.get("settings", {})
        changed = run.get("changed_from_baseline", 0)
        if run is baseline_run:
            lines.append(
                f"- **ct={settings.get('confidence_threshold')}, ms={settings.get('min_segments')}**: "
                "baseline (0 changes)"
            )
        else:
            lines.append(
                f"- **ct={settings.get('confidence_threshold')}, ms={settings.get('min_segments')}**: "
                f"{changed} window(s) differ from baseline"
            )

    lines.extend(
        [
            "",
            "## Notes And Limitations",
            "",
            f"- {LIMITATION_TEXT}",
        ]
    )
    return "\n".join(lines)


def expand_input_paths(raw_inputs: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for raw in raw_inputs:
        if _contains_glob(raw):
            matches = glob_module.glob(raw, recursive=False)
            for match in sorted(matches):
                resolved = str(Path(match).resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    expanded.append(resolved)
        else:
            resolved = str(Path(raw).resolve())
            if resolved not in seen:
                seen.add(resolved)
                expanded.append(resolved)
    # Filter to existing files only; non-existent explicit paths are errors in analyze_reports
    # but we keep them here so AnalyzerInputError can be raised later with a clear message.
    return expanded


def _contains_glob(text: str) -> bool:
    return any(c in text for c in "*?[")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="M7.4 sandbox: replay M7.2 analyzer over fixed evidence with parameter sweep."
    )
    parser.add_argument("input_files", nargs="+", help="M7.1/M7.3 JSON report file(s). Supports glob patterns.")
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.70,
        help="Baseline confidence threshold (default: 0.70).",
    )
    parser.add_argument(
        "--min-segments",
        type=int,
        default=1,
        help="Baseline minimum segments (default: 1).",
    )
    parser.add_argument(
        "--sweep-confidence-thresholds",
        default=None,
        help="Comma-separated list of confidence thresholds to sweep (e.g. 0.60,0.70,0.80).",
    )
    parser.add_argument(
        "--sweep-min-segments",
        default=None,
        help="Comma-separated list of min-segments to sweep (e.g. 1,2,3).",
    )
    parser.add_argument("--output-json", default=None, help="Optional JSON summary path.")
    parser.add_argument("--output-md", default=None, help="Optional Markdown summary path.")
    args = parser.parse_args()
    if not 0 <= args.confidence_threshold <= 1:
        parser.error("--confidence-threshold must be between 0 and 1")
    if args.min_segments < 0:
        parser.error("--min-segments must be >= 0")
    return args


def _write_json(path: Path, data: dict[str, Any]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
