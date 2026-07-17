"""Generate a transcript-free Stage 5 ASR promotion decision."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_src = Path(__file__).resolve().parents[1]
for _sub in ("core", "tools"):
    _path = str(_src / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from asr_strategy import get_candidate
from encoding_utils import read_json, write_json, write_text
from runtime_paths import resolve_runtime_paths


PROJECT_ROOT = resolve_runtime_paths(Path(__file__).resolve()).project_root
REPORT_TYPE = "stage5_asr_go_no_go"


class DecisionError(RuntimeError):
    pass


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write_json(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _mean(values: Iterable[float | int | None]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return statistics.fmean(numbers) if numbers else None


def _expanded_tags(tags: Iterable[str]) -> set[str]:
    aliases = {
        "noise": {"noise", "environmental_noise", "laughter", "typing"},
        "distant": {"distant", "far_field"},
        "low_volume": {"low_volume", "quiet"},
        "overlapping_speech": {"overlapping_speech", "overlap"},
        "code_switching": {"code_switching", "natural_code_switching"},
    }
    expanded: set[str] = set()
    for tag in tags:
        expanded.update(aliases.get(str(tag), {str(tag)}))
    return expanded


def _completed_runs(report: dict[str, Any], config_id: str, tags: set[str] | None = None) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for result in report.get("results", []):
        if not isinstance(result, dict) or result.get("configuration_id") != config_id:
            continue
        if tags and not _expanded_tags(tags).intersection(str(tag) for tag in result.get("acoustic_tags", [])):
            continue
        runs.extend(
            run for run in result.get("runs", [])
            if isinstance(run, dict) and run.get("status") == "completed"
        )
    return runs


def _metric(runs: list[dict[str, Any]], name: str) -> float | None:
    return _mean(
        run.get("metrics", {}).get(name)
        for run in runs
        if isinstance(run.get("metrics"), dict)
    )


def _paired_baseline_runs(candidate_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"status": "completed", "metrics": run["paired_baseline_metrics"]}
        for run in candidate_runs
        if isinstance(run.get("paired_baseline_metrics"), dict)
    ]


def _timing_p95(runs: list[dict[str, Any]]) -> float | None:
    values: list[float | None] = []
    for run in runs:
        metrics = run.get("metrics", {})
        if not isinstance(metrics, dict):
            continue
        for key in ("timing_start_offset_seconds", "timing_end_offset_seconds"):
            distribution = metrics.get(key, {})
            values.append(distribution.get("p95") if isinstance(distribution, dict) else None)
    return _mean(values)


def _relative_improvement(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (baseline - candidate) / baseline


def _candidate_id(report: dict[str, Any]) -> str:
    ids = {
        str(config.get("candidate_id"))
        for config in report.get("configurations", [])
        if isinstance(config, dict) and config.get("candidate_id")
    }
    if len(ids) != 1:
        raise DecisionError("candidate report must contain exactly one candidate_id")
    return ids.pop()


def evaluate_round(
    baseline: dict[str, Any], candidate: dict[str, Any], *, config_id: str,
) -> dict[str, Any]:
    if baseline.get("local_files_only") is not True or candidate.get("local_files_only") is not True:
        raise DecisionError("all promotion reports must confirm local_files_only=true")
    candidate_id = _candidate_id(candidate)
    definition = get_candidate(candidate_id, "dry_run", next(
        (
            str(item.get("model")) for item in candidate.get("configurations", [])
            if isinstance(item, dict) and item.get("id") == config_id
        ),
        "large-v3",
    ))
    baseline_runs = _completed_runs(baseline, config_id)
    candidate_runs = _completed_runs(candidate, config_id)
    target_tags = set(definition.target_tags)
    baseline_target = _completed_runs(baseline, config_id, target_tags)
    candidate_target = _completed_runs(candidate, config_id, target_tags)
    baseline_source = "frozen_report"
    if baseline.get("corpus_fingerprint") != candidate.get("corpus_fingerprint"):
        paired_all = _paired_baseline_runs(candidate_runs)
        paired_target = _paired_baseline_runs(candidate_target)
        if len(paired_all) != len(candidate_runs) or len(paired_target) != len(candidate_target):
            raise DecisionError("baseline and candidate corpus fingerprints differ without complete paired baselines")
        baseline_runs = paired_all
        baseline_target = paired_target
        baseline_source = "paired_candidate_run"
    blockers: list[str] = []
    if not baseline_runs or not candidate_runs:
        blockers.append("comparable completed ASR runs are missing")

    baseline_cer = _metric(baseline_runs, "cer")
    candidate_cer = _metric(candidate_runs, "cer")
    baseline_target_cer = _metric(baseline_target, "cer")
    candidate_target_cer = _metric(candidate_target, "cer")
    overall_improvement = _relative_improvement(baseline_cer, candidate_cer)
    target_improvement = _relative_improvement(baseline_target_cer, candidate_target_cer)
    if overall_improvement is None or overall_improvement < 0.05:
        blockers.append("overall CER relative improvement is below 5%")
    if target_improvement is None or target_improvement < 0.10:
        blockers.append("target-subset CER relative improvement is below 10%")

    regression_metrics = {
        "missed_cue_rate": (_metric(baseline_runs, "missed_cue_rate"), _metric(candidate_runs, "missed_cue_rate")),
        "duplicate_cue_rate": (_metric(baseline_runs, "duplicate_cue_rate"), _metric(candidate_runs, "duplicate_cue_rate")),
        "timing_p95_seconds": (_timing_p95(baseline_runs), _timing_p95(candidate_runs)),
    }
    for name, (before, after) in regression_metrics.items():
        if before is None or after is None:
            blockers.append(f"{name} comparison is unavailable")
        elif after > before:
            blockers.append(f"{name} regressed")

    incremental_ratios = []
    for run in candidate_runs:
        paired = run.get("paired_performance")
        if not isinstance(paired, dict):
            continue
        baseline_elapsed = paired.get("baseline_elapsed_seconds")
        incremental = paired.get("candidate_incremental_seconds")
        if isinstance(baseline_elapsed, (int, float)) and baseline_elapsed > 0 and isinstance(incremental, (int, float)):
            incremental_ratios.append(float(incremental) / float(baseline_elapsed))
    incremental_ratio = _mean(incremental_ratios)
    if definition.strategy.startswith("local_retry"):
        if incremental_ratio is None or incremental_ratio > 0.25:
            blockers.append("local-retry incremental elapsed ratio exceeds 25% or is unavailable")

    return {
        "candidate_id": candidate_id,
        "corpus_fingerprint": candidate.get("corpus_fingerprint"),
        "config_id": config_id,
        "baseline_source": baseline_source,
        "completed_runs": len(candidate_runs),
        "overall_cer_relative_improvement": round(overall_improvement, 6) if overall_improvement is not None else None,
        "target_cer_relative_improvement": round(target_improvement, 6) if target_improvement is not None else None,
        "incremental_elapsed_ratio": round(incremental_ratio, 6) if incremental_ratio is not None else None,
        "non_regression": {
            name: {"baseline": before, "candidate": after}
            for name, (before, after) in regression_metrics.items()
        },
        "passed": not blockers,
        "blockers": blockers,
    }


def evaluate_manual_review(path: Path | None) -> dict[str, Any]:
    required = {
        "french_narrative", "distant_interview", "overlapping_dialogue",
        "complex_english", "natural_mixed_language",
    }
    if path is None or not path.is_file():
        return {"complete": False, "passed": False, "missing_categories": sorted(required)}
    data = read_json(path, user_input=True)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise DecisionError("manual review schema_version must be 1")
    items = data.get("items", [])
    completed = {
        str(item.get("category")) for item in items
        if isinstance(item, dict) and item.get("review_status") == "completed"
    }
    missing = sorted(required - completed)
    degradations = [
        str(item.get("category")) for item in items
        if isinstance(item, dict) and item.get("review_status") == "completed"
        and item.get("candidate_net_degradation") is not False
    ]
    return {
        "complete": not missing,
        "passed": not missing and not degradations,
        "missing_categories": missing,
        "degraded_categories": degradations,
    }


def evaluate_mixed_route_screen(report: dict[str, Any]) -> dict[str, Any]:
    items = report.get("routing_dry_run", [])
    if not isinstance(items, list):
        items = []
    totals = {
        "total_windows": 0, "keep_auto": 0, "prefer_forced_fr": 0,
        "prefer_forced_en": 0, "needs_review": 0, "skip_window": 0,
    }
    failed_samples = 0
    subtitle_affected = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "failed":
            failed_samples += 1
        subtitle_affected = subtitle_affected or item.get("subtitle_output_affected") is True
        counts = item.get("classification_counts", {})
        if isinstance(counts, dict):
            for key in totals:
                totals[key] += int(counts.get(key, 0) or 0)
    windows = totals["total_windows"]
    needs_review_rate = totals["needs_review"] / windows if windows else None
    routed_choices = totals["prefer_forced_fr"] + totals["prefer_forced_en"]
    blockers: list[str] = []
    if not items or failed_samples:
        blockers.append("routing samples are missing or failed")
    if subtitle_affected:
        blockers.append("dry_run unexpectedly affected subtitle output")
    if not windows:
        blockers.append("routing produced no analyzable windows")
    if routed_choices == 0:
        blockers.append("routing produced no forced-language candidate choices")
    if needs_review_rate is None or needs_review_rate > 0.25:
        blockers.append("needs_review rate exceeds 25% or is unavailable")
    blockers.append("MER, post-switch first-token error, and language-span recall promotion evidence is incomplete")
    return {
        "candidate_id": "mixed-route-v1",
        "sample_count": len(items),
        "failed_sample_count": failed_samples,
        "subtitle_output_affected": subtitle_affected,
        "classification_counts": totals,
        "needs_review_rate": round(needs_review_rate, 6) if needs_review_rate is not None else None,
        "promotion_ready": False,
        "blockers": blockers,
    }


def build_decision(
    baseline: dict[str, Any], candidates: list[dict[str, Any]], *, config_id: str,
    manual_review: Path | None,
) -> dict[str, Any]:
    if not candidates or len(candidates) > 2:
        raise DecisionError("one screening round or two independent candidate rounds are required")
    rounds = [evaluate_round(baseline, candidate, config_id=config_id) for candidate in candidates]
    ids = {item["candidate_id"] for item in rounds}
    fingerprints = {item["corpus_fingerprint"] for item in rounds}
    if len(ids) != 1 or len(fingerprints) != 1:
        raise DecisionError("candidate rounds must use the same candidate and corpus")
    manual = evaluate_manual_review(manual_review)
    automatic_passed = all(item["passed"] for item in rounds)
    if automatic_passed and len(rounds) != 2:
        return {
            "schema_version": 1,
            "report_type": REPORT_TYPE,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate_id": rounds[0]["candidate_id"],
            "decision": "requires_second_full_round",
            "apply_allowed": False,
            "production_default_must_remain_off": True,
            "automatic_rounds": rounds,
            "manual_review": manual,
        }
    decision = "go" if automatic_passed and manual["passed"] else (
        "pending_manual_review" if automatic_passed else "no_go"
    )
    return {
        "schema_version": 1,
        "report_type": REPORT_TYPE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_id": rounds[0]["candidate_id"],
        "decision": decision,
        "apply_allowed": decision == "go",
        "production_default_must_remain_off": True,
        "automatic_rounds": rounds,
        "manual_review": manual,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Stage 5 ASR Go/No-Go",
        "",
        f"- Candidate: `{report['candidate_id']}`",
        f"- Decision: `{report['decision']}`",
        f"- Apply allowed: `{str(report['apply_allowed']).lower()}`",
        "- Production default remains: `off`",
        "",
        "## Automatic rounds",
        "",
    ]
    for index, item in enumerate(report["automatic_rounds"], start=1):
        lines.append(
            f"- Round {index}: passed=`{str(item['passed']).lower()}`, "
            f"overall CER improvement=`{item['overall_cer_relative_improvement']}`, "
            f"target improvement=`{item['target_cer_relative_improvement']}`"
        )
        for blocker in item["blockers"]:
            lines.append(f"  - Blocker: {blocker}")
    for screen in report.get("alternative_candidate_screens", []):
        lines.extend([
            "",
            f"## Alternative screen: `{screen['candidate_id']}`",
            "",
            f"- Promotion ready: `{str(screen['promotion_ready']).lower()}`",
            f"- Needs-review rate: `{screen['needs_review_rate']}`",
        ])
        for blocker in screen["blockers"]:
            lines.append(f"  - Blocker: {blocker}")
    lines.extend([
        "",
        "## Manual review",
        "",
        f"- Complete: `{str(report['manual_review']['complete']).lower()}`",
        f"- Passed: `{str(report['manual_review']['passed']).lower()}`",
        "",
        "This report contains metrics and category-level decisions only; no transcript text or absolute media paths.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a Stage 5 ASR promotion decision.")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate-report", action="append", required=True)
    parser.add_argument("--config", default="large-v3-cuda-float16")
    parser.add_argument("--manual-review")
    parser.add_argument("--routing-report", help="Optional mixed-route routing-only benchmark report.")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        baseline = read_json(args.baseline, user_input=True)
        candidates = [read_json(path, user_input=True) for path in args.candidate_report]
        report = build_decision(
            baseline,
            candidates,
            config_id=args.config,
            manual_review=Path(args.manual_review) if args.manual_review else None,
        )
        if args.routing_report:
            routing_report = read_json(args.routing_report, user_input=True)
            if not isinstance(routing_report, dict):
                raise DecisionError("routing report must be an object")
            report["alternative_candidate_screens"] = [evaluate_mixed_route_screen(routing_report)]
        output = Path(args.output_dir)
        _atomic_json(output / "stage5_go_no_go.json", report)
        write_text(output / "stage5_go_no_go.md", render_markdown(report))
    except (DecisionError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Decision: {report['decision']}")
    return 0 if report["decision"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
