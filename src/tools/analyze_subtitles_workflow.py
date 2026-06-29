#!/usr/bin/env python3
"""Run the subtitle style analysis workflow from Python.

This keeps path checks, process execution, and JSON reading out of PowerShell.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "reference_subtitles"
DEFAULT_ANALYSIS_FILE = PROJECT_ROOT / "data" / "analysis_report.json"
DEFAULT_PROMPT_FILE = PROJECT_ROOT / "data" / "prompt.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run subtitle style analysis and prompt generation.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Directory containing reference subtitles.")
    parser.add_argument("--analysis-output", default=str(DEFAULT_ANALYSIS_FILE), help="Output JSON report path.")
    parser.add_argument("--prompt-output", default=str(DEFAULT_PROMPT_FILE), help="Output prompt JSON path.")
    parser.add_argument("--lang", default="zh", help="Target language for prompt generation.")
    parser.add_argument("--no-recursive", action="store_true", help="Do not scan subdirectories.")
    return parser.parse_args()


def run_checked(command: list[str]) -> None:
    print()
    print(" ".join(command))
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def count_candidate_files(input_dir: Path, recursive: bool) -> tuple[int, int, int]:
    globber = input_dir.rglob if recursive else input_dir.glob
    srt_count = sum(1 for _ in globber("*.srt"))
    zip_count = sum(1 for _ in globber("*.zip"))
    rar_count = sum(1 for _ in globber("*.rar"))
    return srt_count, zip_count, rar_count


def main() -> int:
    args = parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    analysis_file = Path(args.analysis_output).expanduser().resolve()
    prompt_file = Path(args.prompt_output).expanduser().resolve()
    recursive = not args.no_recursive

    print("=" * 60)
    print("CineSub Studio - Subtitle Style Workflow")
    print("=" * 60)
    print(f"Input dir      : {input_dir}")
    print(f"Analysis output: {analysis_file}")
    print(f"Prompt output  : {prompt_file}")
    print(f"Recursive      : {recursive}")

    if not input_dir.exists():
        print()
        print(f"ERROR: input directory does not exist: {input_dir}")
        print("Place reference .srt files under data/reference_subtitles, then rerun.")
        return 1

    srt_count, zip_count, rar_count = count_candidate_files(input_dir, recursive)
    if srt_count == 0 and zip_count == 0:
        print()
        print(f"ERROR: no .srt or .zip subtitle files found in: {input_dir}")
        if rar_count:
            print(f"Found {rar_count} .rar file(s); extract them manually with 7-Zip or WinRAR first.")
        print("Expected bilingual reference subtitles as .srt, or .zip files containing .srt.")
        return 1

    print()
    print(f"Found {srt_count} .srt file(s), {zip_count} .zip file(s), {rar_count} .rar file(s).")

    analyzer = PROJECT_ROOT / "src" / "tools" / "subtitle_analyzer.py"
    prompt_generator = PROJECT_ROOT / "src" / "tools" / "style_prompt_generator.py"

    analyzer_command = [
        sys.executable,
        "-B",
        str(analyzer),
        str(input_dir),
        "--output",
        str(analysis_file),
    ]
    if recursive:
        analyzer_command.insert(-2, "--recursive")

    run_checked(analyzer_command)

    run_checked([
        sys.executable,
        "-B",
        str(prompt_generator),
        str(analysis_file),
        "--lang",
        args.lang,
        "--output",
        str(prompt_file),
    ])

    if prompt_file.exists():
        prompt_data = json.loads(prompt_file.read_text(encoding="utf-8"))
        print()
        print("=" * 60)
        print("Done")
        print("=" * 60)
        print(f"Style summary: {prompt_data.get('style_summary', '')}")
        metrics = prompt_data.get("metrics", {})
        if isinstance(metrics, dict):
            print(f"Metrics files: {metrics.get('total_files', 0)}")
        print(f"Prompt file  : {prompt_file}")
        print("Review system_prompt before copying it into a Language Profile.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
