"""
CineSub Studio end-to-end sample runner.

This tool runs or inspects short real-video samples and writes a compact
acceptance report. It intentionally does not contain sample videos or API
secrets.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "tests" / "e2e_samples" / "samples.example.json"
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_STAGING_DIR = PROJECT_ROOT / "work" / "e2e_samples"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real short-film E2E checks and generate acceptance reports.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Sample config JSON. Defaults to tests/e2e_samples/samples.example.json.",
    )
    parser.add_argument(
        "--reports-dir",
        default=str(DEFAULT_REPORTS_DIR),
        help="Report output directory. Defaults to reports/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not run batch_worker.py; only validate config and collect existing artifacts.",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="Alias for --dry-run.",
    )
    parser.add_argument(
        "--sample",
        action="append",
        default=[],
        help="Run only the named sample id. Can be passed multiple times.",
    )
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    reports_dir = _resolve_path(args.reports_dir)

    try:
        config = _load_config(config_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot read E2E config: {exc}")
        return 1

    samples = config.get("samples", [])
    if not isinstance(samples, list):
        print("E2E config error: 'samples' must be a list.")
        return 1

    selected_samples = {sample_id.strip() for sample_id in args.sample if sample_id.strip()}
    if selected_samples:
        samples = [
            sample for sample in samples
            if isinstance(sample, dict)
            and str(sample.get("id") or sample.get("sample_name") or "").strip() in selected_samples
        ]
        if not samples:
            print(f"E2E config error: no samples matched: {', '.join(sorted(selected_samples))}")
            return 1

    dry_run = args.dry_run or args.no_run
    results: list[dict[str, Any]] = []
    had_pipeline_failure = False

    print(f"E2E samples: {len(samples)}")
    if dry_run:
        print("Dry run: batch_worker.py will not be called.")

    for raw_sample in samples:
        sample = _normalize_sample(raw_sample)
        sample["preflight"] = _preflight_sample(sample)
        print(f"\n[{sample['id']}] {sample['file']}")
        for message in sample["preflight"]["warnings"]:
            print(f"  Preflight warning: {message}")
        for message in sample["preflight"]["errors"]:
            print(f"  Preflight error: {message}")

        if not sample["source_file"].exists():
            print(f"  Missing sample file, skipped: {sample['source_file']}")
            result = _collect_sample_result(sample, "missing_sample", None, "")
            results.append(result)
            continue

        if sample["preflight"]["errors"] and not dry_run:
            print("  Preflight failed; pipeline was not started.")
            result = _collect_sample_result(sample, "preflight_failed", None, "")
            results.append(result)
            had_pipeline_failure = True
            continue

        returncode: int | None = None
        stderr_tail = ""
        if dry_run:
            print("  Dry run, collecting existing artifacts only.")
            status = "dry_run"
        else:
            sample["processing_file"] = _prepare_processing_file(sample)
            returncode, stderr_tail = _run_batch_worker(sample)
            status = "completed" if returncode == 0 else "pipeline_failed"
            if returncode != 0:
                had_pipeline_failure = True

        result = _collect_sample_result(sample, status, returncode, stderr_tail)
        results.append(result)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": _display_path(config_path),
        "dry_run": dry_run,
        "selected_samples": sorted(selected_samples),
        "samples": results,
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "e2e_sample_report.json"
    md_path = reports_dir / "e2e_sample_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print(f"\nReport written: {_display_path(json_path)}")
    print(f"Report written: {_display_path(md_path)}")

    return 1 if had_pipeline_failure else 0


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return data


def _normalize_sample(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each sample must be a JSON object")

    sample_id = str(raw.get("id") or raw.get("sample_name") or "").strip()
    if not sample_id:
        raise ValueError("sample id is required")

    file_value = str(raw.get("file") or raw.get("source_file") or "").strip()
    if not file_value:
        raise ValueError(f"sample '{sample_id}' requires file/source_file")

    source_file = _resolve_path(file_value)

    # 自动发现：如果 file 指定的路径不存在，且是目录或父目录是目录，则自动查找视频文件
    if not source_file.exists():
        parent = source_file if source_file.is_dir() else source_file.parent
        if parent.is_dir():
            video_exts = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v"}
            for child in sorted(parent.iterdir()):
                if child.suffix.lower() in video_exts:
                    source_file = child
                    break

    return {
        "id": sample_id,
        "artifact_stem": _safe_stem(sample_id),
        "file": file_value,
        "source_file": source_file,
        "processing_file": None,
        "language_profile": str(raw.get("language_profile") or "auto-detect"),
        "provider": str(raw.get("provider") or ""),
        "expected_language": raw.get("expected_language"),
        "manual_notes": str(raw.get("manual_notes") or ""),
        "extra_args": raw.get("extra_args") if isinstance(raw.get("extra_args"), list) else [],
        "preflight": {"errors": [], "warnings": []},
    }


def _preflight_sample(sample: dict[str, Any]) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    source = sample["source_file"]
    if not source.exists():
        warnings.append("source file does not exist; sample will be skipped")
    elif source.suffix.lower() not in {
        ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v",
        ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".wav",
    }:
        warnings.append(f"unusual media extension: {source.suffix or '(none)'}")

    _check_language_profile(sample, errors)
    if not _translation_disabled(sample):
        _check_provider(sample, errors, warnings)

    return {"errors": errors, "warnings": warnings}


def _check_language_profile(sample: dict[str, Any], errors: list[str]) -> None:
    try:
        from language_profile_store import get_language_profile
    except Exception as exc:
        errors.append(f"cannot load Language Profile store: {exc}")
        return

    profile = get_language_profile(sample["language_profile"])
    if not profile:
        errors.append(f"Language Profile not found: {sample['language_profile']}")


def _check_provider(sample: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    try:
        from provider_store import get_active_provider, get_provider
    except Exception as exc:
        errors.append(f"cannot load Provider store: {exc}")
        return

    if sample["provider"]:
        provider = get_provider(sample["provider"])
        provider_label = sample["provider"]
    else:
        provider = get_active_provider()
        provider_label = "(active)"

    if not provider:
        errors.append(
            f"Provider not found: {provider_label}; configure one or add --no-translate in extra_args"
        )
        return
    if provider.get("enabled") is False:
        errors.append(f"Provider disabled: {provider.get('id', provider_label)}")
    if not provider.get("api_base"):
        errors.append(f"Provider missing api_base: {provider.get('id', provider_label)}")
    if not provider.get("translation_model"):
        errors.append(f"Provider missing translation_model: {provider.get('id', provider_label)}")
    if not provider.get("api_key"):
        warnings.append(
            f"Provider has no saved API key: {provider.get('id', provider_label)}; "
            "SUBTITLE_LLM_API_KEY may still be used"
        )


def _translation_disabled(sample: dict[str, Any]) -> bool:
    return "--no-translate" in {str(arg) for arg in sample["extra_args"]}


def _prepare_processing_file(sample: dict[str, Any]) -> Path:
    """Stage each sample under a unique file stem to avoid output collisions."""
    source = sample["source_file"]
    staged_dir = DEFAULT_STAGING_DIR / sample["artifact_stem"]
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staged_dir / f"{sample['artifact_stem']}{source.suffix.lower()}"

    if staged_path.exists():
        try:
            if staged_path.stat().st_size == source.stat().st_size:
                return staged_path
            staged_path.unlink()
        except OSError:
            staged_path.unlink(missing_ok=True)

    try:
        staged_path.hardlink_to(source)
        print(f"  Staged sample as hardlink: {_display_path(staged_path)}")
    except OSError:
        shutil.copy2(source, staged_path)
        print(f"  Staged sample as copy: {_display_path(staged_path)}")
    return staged_path


def _run_batch_worker(sample: dict[str, Any]) -> tuple[int, str]:
    """Run batch_worker and capture returncode + stderr for diagnosis."""
    processing_file = sample.get("processing_file") or sample["source_file"]
    command = [
        sys.executable,
        "-B",
        str(PROJECT_ROOT / "src" / "pipeline" / "batch_worker.py"),
        "--input",
        str(processing_file.parent),
        "--language-profile",
        sample["language_profile"],
        "--no-move-completed",
    ]
    if sample["provider"]:
        command.extend(["--provider", sample["provider"]])
    command.extend(str(arg) for arg in sample["extra_args"])

    print("  Running:", " ".join(_quote_for_display(part) for part in command))
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"  batch_worker.py exit code: {completed.returncode}")
    # Keep last 2KB of stderr for diagnosis
    stderr_tail = completed.stderr[-2048:] if completed.stderr else ""
    if stderr_tail:
        print(f"  stderr tail: {stderr_tail[:500]}")
    return completed.returncode, stderr_tail


def _collect_sample_result(
    sample: dict[str, Any],
    status: str,
    returncode: int | None,
    stderr_tail: str = "",
) -> dict[str, Any]:
    stem = sample["artifact_stem"]
    # Standard directories (batch worker output)
    source_srt = _find_newest(PROJECT_ROOT / "output" / "source", [f"{stem}.*.srt"])
    zh_srt = _find_newest(PROJECT_ROOT / "output" / "zh", [f"{stem}.*.translated.*.srt"])
    bilingual_srt = _find_newest(PROJECT_ROOT / "output" / "bilingual", [f"{stem}.*.bilingual.*.srt"])
    quality_report_path = _find_newest(PROJECT_ROOT / "output" / "reports", [f"{stem}.*.quality_report.json"])
    review_srt = _find_newest(PROJECT_ROOT / "output" / "reports", [f"{stem}.*.review_needed.srt"])
    lang_json_path = _find_newest(PROJECT_ROOT / "output" / "source", [f"{stem}.*.lang.json"])

    # Fallback to root output directory (single-file processing output)
    if not source_srt:
        source_srt = _find_newest(PROJECT_ROOT / "output", [f"{stem}.*.srt"])
    if not lang_json_path:
        lang_json_path = _find_newest(PROJECT_ROOT / "output", [f"{stem}.*.lang.json"])
    if not quality_report_path:
        quality_report_path = _find_newest(PROJECT_ROOT / "output" / "reports", [f"{stem}.*.quality_report.json"])
    if not review_srt:
        review_srt = _find_newest(PROJECT_ROOT / "output" / "reports", [f"{stem}.*.review_needed.srt"])

    lang_data = _read_json(lang_json_path) if lang_json_path else {}
    quality_data = _read_json(quality_report_path) if quality_report_path else {}
    quality_summary = quality_data.get("summary", {}) if isinstance(quality_data, dict) else {}
    issues = quality_data.get("issues", []) if isinstance(quality_data, dict) else []

    quality_errors = _summary_count(quality_summary, issues, "errors", "error")
    quality_warnings = _summary_count(quality_summary, issues, "warnings", "warning")
    review_needed_count = _count_srt_entries(review_srt) if review_srt else 0

    result = {
        "sample_name": sample["id"],
        "source_file": _display_path(sample["source_file"]),
        "processing_file": _display_path(sample["processing_file"]) if sample.get("processing_file") else "",
        "source_exists": sample["source_file"].exists(),
        "status": status,
        "pipeline_returncode": returncode,
        "stderr_tail": stderr_tail[:1000] if stderr_tail else "",
        "language_profile": sample["language_profile"],
        "provider": sample["provider"],
        "preflight_errors": sample["preflight"]["errors"],
        "preflight_warnings": sample["preflight"]["warnings"],
        "expected_language": sample["expected_language"],
        "detected_language": lang_data.get("source_language"),
        "language_probability": lang_data.get("language_probability"),
        "forced_language": lang_data.get("forced_language"),
        "subtitle_count_source": _count_srt_entries(source_srt) if source_srt else 0,
        "subtitle_count_zh": _count_srt_entries(zh_srt) if zh_srt else 0,
        "subtitle_count_bilingual": _count_srt_entries(bilingual_srt) if bilingual_srt else 0,
        "quality_status": quality_data.get("status") if isinstance(quality_data, dict) else None,
        "quality_errors": quality_errors,
        "quality_warnings": quality_warnings,
        "review_needed_count": review_needed_count,
        "manual_notes": sample["manual_notes"],
        "artifacts": {
            "source_srt": _display_path(source_srt) if source_srt else "",
            "zh_srt": _display_path(zh_srt) if zh_srt else "",
            "bilingual_srt": _display_path(bilingual_srt) if bilingual_srt else "",
            "language_json": _display_path(lang_json_path) if lang_json_path else "",
            "quality_report": _display_path(quality_report_path) if quality_report_path else "",
            "review_needed_srt": _display_path(review_srt) if review_srt else "",
        },
    }
    result["conclusion"] = _conclusion(result)
    return result


def _summary_count(summary: Any, issues: Any, key: str, severity: str) -> int:
    if isinstance(summary, dict) and isinstance(summary.get(key), int):
        return int(summary[key])
    if isinstance(issues, list):
        return sum(1 for issue in issues if isinstance(issue, dict) and issue.get("severity") == severity)
    return 0


def _conclusion(result: dict[str, Any]) -> str:
    if result["status"] == "missing_sample":
        return "missing source video"
    if result["status"] == "preflight_failed":
        return "fix sample config"
    if result["status"] == "pipeline_failed":
        return "pipeline failed"

    expected = result.get("expected_language")
    detected = result.get("detected_language")
    forced = result.get("forced_language")
    if expected and detected and detected != expected and forced != expected:
        return "check ASR language detection"
    if result.get("subtitle_count_source", 0) == 0:
        return "no source subtitles found"
    if result.get("quality_errors", 0) > 0:
        return "review quality errors"
    if result.get("review_needed_count", 0) > 0:
        return "manual review needed"
    if result["status"] == "dry_run":
        return "dry-run artifact summary"
    return "pass"


def _find_newest(root: Path, patterns: list[str]) -> Path | None:
    if not root.exists():
        return None
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(path for path in root.glob(pattern) if path.is_file())
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _count_srt_entries(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    count = 0
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) >= 2 and lines[0].isdigit() and "-->" in lines[1]:
            count += 1
    return count


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# CineSub Studio E2E Sample Report",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Config: `{report['config']}`",
        f"- Dry run: `{report['dry_run']}`",
        f"- Selected samples: `{', '.join(report.get('selected_samples') or []) or 'all'}`",
        "",
        "| Sample | Status | Profile | Detected | Probability | Counts | Quality Errors | Quality Warnings | Review Needed | Preflight | Conclusion |",
        "|---|---|---|---|---:|---|---:|---:|---:|---|---|",
    ]
    for sample in report["samples"]:
        probability = sample.get("language_probability")
        probability_text = "" if probability is None else str(probability)
        counts = (
            f"source {sample.get('subtitle_count_source', 0)} / "
            f"zh {sample.get('subtitle_count_zh', 0)} / "
            f"bilingual {sample.get('subtitle_count_bilingual', 0)}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(sample.get("sample_name", "")),
                    _md_cell(sample.get("status", "")),
                    _md_cell(sample.get("language_profile", "")),
                    _md_cell(sample.get("detected_language") or ""),
                    _md_cell(probability_text),
                    _md_cell(counts),
                    _md_cell(str(sample.get("quality_errors", 0))),
                    _md_cell(str(sample.get("quality_warnings", 0))),
                    _md_cell(str(sample.get("review_needed_count", 0))),
                    _md_cell(_preflight_summary(sample)),
                    _md_cell(sample.get("conclusion", "")),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Artifacts", ""])
    for sample in report["samples"]:
        lines.append(f"### {sample.get('sample_name', '')}")
        lines.append("")
        lines.append(f"- Status: `{sample.get('status')}`")
        lines.append(f"- Source file: `{sample.get('source_file', '')}`")
        if sample.get("manual_notes"):
            lines.append(f"- Manual notes: {sample.get('manual_notes')}")
        if sample.get("preflight_errors"):
            lines.append("- Preflight errors:")
            for item in sample["preflight_errors"]:
                lines.append(f"  - {item}")
        if sample.get("preflight_warnings"):
            lines.append("- Preflight warnings:")
            for item in sample["preflight_warnings"]:
                lines.append(f"  - {item}")
        if sample.get("stderr_tail"):
            lines.append("- Pipeline stderr (tail):")
            for line in sample["stderr_tail"].splitlines()[:10]:
                lines.append(f"  > {line[:200]}")
        for name, path in sample.get("artifacts", {}).items():
            if path:
                lines.append(f"- {name}: `{path}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def _md_cell(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text.replace("|", "\\|")


def _preflight_summary(sample: dict[str, Any]) -> str:
    errors = len(sample.get("preflight_errors") or [])
    warnings = len(sample.get("preflight_warnings") or [])
    if errors or warnings:
        return f"{errors} error / {warnings} warning"
    return "ok"


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _safe_stem(value: str) -> str:
    cleaned = []
    for char in value.strip():
        if char.isalnum() or char in {"-", "_"}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "sample"


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _quote_for_display(value: str) -> str:
    if any(ch.isspace() for ch in value):
        return f'"{value}"'
    return value


if __name__ == "__main__":
    raise SystemExit(main())
