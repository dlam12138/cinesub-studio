from __future__ import annotations

import json
import sys
from pathlib import Path

from encoding_utils import read_json
from output_paths import plan_pipeline_outputs
from task_state import TaskState


def safe_console_print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(str(text).encode(encoding, errors="replace").decode(encoding, errors="replace"))


def show_status(states_dir: Path) -> int:
    state_files = sorted(states_dir.glob("*.state.json")) if states_dir.exists() else []
    if not state_files:
        print("No task records found.")
        return 0
    print(f"\nTask status ({len(state_files)}):\n")
    print(f"  {'file':<40} {'status':<12} {'stage':<20} {'retry'}")
    print(f"  {'-' * 40} {'-' * 12} {'-' * 20} {'-' * 6}")
    for state_file in state_files:
        task = TaskState.load(state_file)
        if task is None:
            continue
        retry = f"{task.retry_count}/{task.max_retries}" if task.retry_count > 0 else "-"
        print(f"  {task.file:<40} {task.status:<12} {task.stage:<20} {retry}")
        if task.error:
            print(f"    error: {task.error[:100]}")
    print()
    return 0


def show_review(output_root: Path) -> int:
    reports_dir = plan_pipeline_outputs(output_root, "", "", "", "bilingual").reports_dir
    report_files = sorted(reports_dir.glob("*.quality_report.json")) if reports_dir.exists() else []
    if not report_files:
        safe_console_print("No quality reports found.")
        return 0
    safe_console_print(f"\n{'=' * 70}\n  Review summary - {len(report_files)} report(s)\n{'=' * 70}\n")
    totals = {"issues": 0, "errors": 0, "warnings": 0}
    for report_file in report_files:
        try:
            data = read_json(report_file)
        except (OSError, json.JSONDecodeError):
            continue
        summary, issues = data.get("summary", {}), data.get("issues", [])
        if not issues:
            continue
        status = data.get("status", "?")
        icon = {"pass": "OK", "warning": "WARN", "fail": "FAIL"}.get(status, "?")
        safe_console_print(f"  {icon} {report_file.stem.replace('.quality_report', '')}")
        safe_console_print(
            f"    status: {status} | issues: {summary.get('total_issues', 0)} "
            f"(errors: {summary.get('errors', 0)}, warnings: {summary.get('warnings', 0)})"
        )
        order = {"error": 0, "warning": 1, "info": 2}
        sorted_issues = sorted(issues, key=lambda item: order.get(item.get("severity", "info"), 99))
        for issue in sorted_issues[:10]:
            level = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(issue.get("severity"), "?")
            index = issue.get("index", 0)
            safe_console_print(
                f"    {level} {'#' + str(index) if index > 0 else 'global'} "
                f"[{issue.get('type', '?')}] {issue.get('text', '')[:80]}"
            )
        if len(sorted_issues) > 10:
            safe_console_print(f"    ... {len(sorted_issues) - 10} more issue(s); use --review-file for details")
        totals["issues"] += summary.get("total_issues", 0)
        totals["errors"] += summary.get("errors", 0)
        totals["warnings"] += summary.get("warnings", 0)
        safe_console_print()
    safe_console_print(f"{'=' * 70}")
    safe_console_print(
        f"  Total: {totals['issues']} issue(s) ({totals['errors']} errors, {totals['warnings']} warnings)"
    )
    safe_console_print(f"  Reports: {reports_dir}")
    safe_console_print(f"  Review subtitles: {reports_dir}/*.review_needed.srt")
    safe_console_print(f"{'=' * 70}\n\nTip: use --review-file <report path> for full details.\n")
    return 0 if totals["errors"] == 0 else 1


def show_review_detail(report_path: Path) -> int:
    if not report_path.exists():
        safe_console_print(f"Report not found: {report_path}")
        return 1
    try:
        data = read_json(report_path)
    except (OSError, json.JSONDecodeError) as exc:
        safe_console_print(f"Could not read report: {exc}")
        return 1
    summary, issues = data.get("summary", {}), data.get("issues", [])
    safe_console_print(f"\n{'=' * 70}\n  Quality report: {report_path.name}\n{'=' * 70}")
    safe_console_print(f"  status: {data.get('status', '?')}")
    safe_console_print(f"  total entries: {data.get('total_entries', 0)}")
    safe_console_print(f"  source: {data.get('source_srt', '')}")
    safe_console_print(f"  translated: {data.get('translated_srt', '')}")
    safe_console_print(
        f"  issues: {summary.get('total_issues', 0)} (errors: {summary.get('errors', 0)}, "
        f"warnings: {summary.get('warnings', 0)}, info: {summary.get('info', 0)})"
    )
    if not issues:
        safe_console_print("\n  [OK] no issues")
    else:
        safe_console_print("\n  details:")
        for issue in issues:
            safe_console_print(
                f"    [{issue.get('severity', '?').upper()}] #{issue.get('index', 0)} "
                f"[{issue.get('type', '?')}] {issue.get('text', '')}"
            )
            if issue.get("snippet"):
                safe_console_print(f"      content: {issue.get('snippet')}")
    safe_console_print(f"{'=' * 70}\n")
    return 0 if data.get("status") != "fail" else 1
