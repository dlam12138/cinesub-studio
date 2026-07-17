"""Capture a private, reproducible Stage 5 execution baseline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_src = Path(__file__).resolve().parents[1]
for _sub in ("core", "tools"):
    _path = str(_src / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from asr_benchmark import corpus_fingerprint, load_manifest
from encoding_utils import write_json
from runtime_env import runtime_diagnostics
from runtime_paths import resolve_runtime_paths


PROJECT_ROOT = resolve_runtime_paths(Path(__file__).resolve()).project_root
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "reports" / "stage5-closeout" / "baseline_snapshot.local.json"


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def build_snapshot(manifest_paths: list[Path], test_summary: str) -> dict[str, Any]:
    corpora: list[dict[str, Any]] = []
    for path in manifest_paths:
        manifest = load_manifest(path)
        corpora.append({
            "manifest": str(path.resolve().relative_to(PROJECT_ROOT)),
            "fingerprint": corpus_fingerprint(manifest["samples"]),
            "sample_count": len(manifest["samples"]),
            "configuration_ids": [item["id"] for item in manifest["configurations"]],
        })
    diagnostics = runtime_diagnostics()
    return {
        "schema_version": 1,
        "report_type": "stage5_execution_baseline",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": {
            "branch": _git(["branch", "--show-current"]),
            "commit": _git(["rev-parse", "HEAD"]),
            "status_porcelain": _git(["status", "--porcelain=v1"]).splitlines(),
        },
        "test_summary": test_summary,
        "corpora": corpora,
        "environment": {
            "runtime_layout": diagnostics.get("runtime_layout"),
            "python_version": diagnostics.get("python_version"),
            "python_source": diagnostics.get("python_source"),
            "ffmpeg_source": diagnostics.get("ffmpeg_source"),
            "ffmpeg_version": diagnostics.get("ffmpeg_version"),
            "cuda_ready": diagnostics.get("cuda_ready"),
            "nvidia_driver": diagnostics.get("nvidia_driver"),
            "known_models": diagnostics.get("known_models"),
            "diagnostic_summary": diagnostics.get("diagnostic_summary"),
            "local_files_only_required": True,
        },
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write_json(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture the private Stage 5 baseline snapshot.")
    parser.add_argument("--manifest", action="append", default=[])
    parser.add_argument("--test-summary", default="not_run")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        payload = build_snapshot([Path(path) for path in args.manifest], args.test_summary)
        output = Path(args.output)
        _atomic_json(output, payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Baseline snapshot: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
