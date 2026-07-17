"""Create local-only language-span annotation templates without guessing labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import wave
from pathlib import Path
from typing import Any

from encoding_utils import read_json, write_json


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / handle.getframerate()
    except (OSError, wave.Error):
        return None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write_json(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_templates(manifest_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path, user_input=True)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("samples"), list):
        raise ValueError("manifest samples are unavailable")
    entries: list[dict[str, Any]] = []
    for sample in manifest["samples"]:
        if not isinstance(sample, dict):
            continue
        tags = {str(tag) for tag in sample.get("acoustic_tags", [])}
        if sample.get("language") != "mixed" and not tags.intersection({"code_switching", "natural_code_switching"}):
            continue
        media = Path(str(sample.get("media") or ""))
        if not media.is_absolute():
            media = (manifest_path.parent / media).resolve()
            if not media.is_file():
                media = (Path.cwd() / str(sample.get("media") or "")).resolve()
        if not media.is_file():
            entries.append({"sample_id": sample.get("id"), "status": "missing_media"})
            continue
        target = output_dir / f"{sample['id']}.language-spans.local.json"
        if not target.exists():
            _atomic_json(target, {
                "schema_version": 1,
                "sample_id": sample["id"],
                "media_sha256": _sha256(media),
                "duration_seconds": _duration(media),
                "instructions": "Add ordered, non-overlapping start/end/language spans after human listening; do not infer labels from filenames.",
                "spans": [],
            })
        entries.append({
            "sample_id": sample["id"], "status": "template_ready",
            "annotation_file": target.name,
        })
    summary = {"schema_version": 1, "items": entries}
    _atomic_json(output_dir / "annotation_templates_summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare private code-switch annotation templates.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        summary = build_templates(Path(args.manifest).resolve(), Path(args.output_dir).resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Templates: {len(summary['items'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
