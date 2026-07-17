"""Prepare a local, anonymous A/B listening-review pack for Stage 5."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


_src = Path(__file__).resolve().parents[1]
for _sub in ("core", "tools"):
    _path = str(_src / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from encoding_utils import read_json, read_text, write_json, write_text
from ffmpeg_locator import find_ffmpeg
from runtime_paths import resolve_runtime_paths


PROJECT_ROOT = resolve_runtime_paths(Path(__file__).resolve()).project_root
CATEGORIES = (
    "french_narrative",
    "distant_interview",
    "overlapping_dialogue",
    "complex_english",
    "natural_mixed_language",
)
DEFAULT_ROOT = PROJECT_ROOT / "output" / "reports" / "stage5-review"


class ReviewPackError(RuntimeError):
    pass


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write_json(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _existing_archive(name: str) -> str | None:
    path = PROJECT_ROOT / "archive" / name
    return str(path) if path.is_file() else None


def initialize_manifest(output_root: Path) -> Path:
    media = {
        "french_narrative": _existing_archive("The.Beautiful.Person.2008.1080p.WEBRip.x264.AAC5.1.mp4"),
        "distant_interview": _existing_archive("户外采访.mp4"),
        "overlapping_dialogue": _existing_archive("音乐剧.mp4"),
        "complex_english": _existing_archive("布林肯.mp4"),
        "natural_mixed_language": None,
    }
    manifest = {
        "schema_version": 1,
        "items": [
            {
                "id": category,
                "category": category,
                "media": media[category],
                "start_seconds": 0,
                "duration_seconds": 60,
                "baseline_srt": None,
                "candidate_srt": None,
                "notes": (
                    "No verified real-media source is currently assigned; supply one before review."
                    if media[category] is None else "Set a verified 60-90 second interval and both SRT paths."
                ),
            }
            for category in CATEGORIES
        ],
    }
    path = output_root / "review_manifest.local.json"
    _atomic_json(path, manifest)
    return path


def _parse_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", value)
    if not match:
        raise ValueError(value)
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _stamp(value: float) -> str:
    milliseconds = max(0, round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def clip_srt(source: Path, target: Path, start: float, duration: float) -> int:
    raw = read_text(source, user_input=True).strip()
    end = start + duration
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        left, right = [part.strip() for part in lines[1].split("-->", 1)]
        try:
            cue_start = _parse_timestamp(left)
            cue_end = _parse_timestamp(right)
        except ValueError:
            continue
        if cue_end <= start or cue_start >= end:
            continue
        shifted_start = max(cue_start, start) - start
        shifted_end = min(cue_end, end) - start
        blocks.append(
            f"{len(blocks) + 1}\n{_stamp(shifted_start)} --> {_stamp(shifted_end)}\n"
            + "\n".join(lines[2:])
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, "\n\n".join(blocks) + ("\n" if blocks else ""))
    return len(blocks)


def _extract_audio(ffmpeg: Path, media: Path, target: Path, start: float, duration: float) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(start), "-t", str(duration), "-i", str(media),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise ReviewPackError(f"audio extraction failed for {media.name}: {result.stderr[-300:]}")


def _validate_manifest(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ReviewPackError("review manifest schema_version must be 1")
    items = data.get("items")
    if not isinstance(items, list):
        raise ReviewPackError("review manifest items must be a list")
    categories = [str(item.get("category")) for item in items if isinstance(item, dict)]
    if len(categories) != len(set(categories)):
        raise ReviewPackError("review categories must be unique")
    if set(categories) != set(CATEGORIES):
        raise ReviewPackError("review manifest must contain all five fixed categories")
    return items


def build_pack(manifest_path: Path, output_root: Path) -> dict[str, Any]:
    items = _validate_manifest(read_json(manifest_path, user_input=True))
    ffmpeg_text = find_ffmpeg(PROJECT_ROOT)
    if not ffmpeg_text:
        raise ReviewPackError("project-local FFmpeg is unavailable")
    ffmpeg = Path(ffmpeg_text)
    public_items: list[dict[str, Any]] = []
    private_mapping: dict[str, Any] = {"schema_version": 1, "items": []}
    for raw in items:
        category = str(raw["category"])
        sample_id = str(raw.get("id") or category)
        missing = [
            key for key in ("media", "baseline_srt", "candidate_srt")
            if not raw.get(key) or not Path(str(raw[key])).is_file()
        ]
        duration = float(raw.get("duration_seconds", 0))
        start = float(raw.get("start_seconds", 0))
        if not 60 <= duration <= 90:
            missing.append("verified_60_90_second_interval")
        if missing:
            public_items.append({
                "id": sample_id, "category": category, "status": "missing",
                "missing": sorted(set(missing)),
            })
            continue
        sample_root = output_root / "packet" / sample_id
        _extract_audio(ffmpeg, Path(raw["media"]), sample_root / "audio.wav", start, duration)
        candidate_is_a = int(hashlib.sha256(sample_id.encode("utf-8")).hexdigest(), 16) % 2 == 0
        mapping = {
            "A": "candidate" if candidate_is_a else "baseline",
            "B": "baseline" if candidate_is_a else "candidate",
        }
        sources = {
            "A": Path(raw["candidate_srt"] if candidate_is_a else raw["baseline_srt"]),
            "B": Path(raw["baseline_srt"] if candidate_is_a else raw["candidate_srt"]),
        }
        counts = {
            label: clip_srt(source, sample_root / f"subtitle_{label}.srt", start, duration)
            for label, source in sources.items()
        }
        if not all(counts.values()):
            raise ReviewPackError(f"review subtitles are empty for {sample_id}")
        private_mapping["items"].append({"id": sample_id, "category": category, "mapping": mapping})
        public_items.append({
            "id": sample_id, "category": category, "status": "ready",
            "duration_seconds": duration, "cue_counts": counts,
            "audio_sha256": hashlib.sha256((sample_root / "audio.wav").read_bytes()).hexdigest(),
        })

    review_form = {
        "schema_version": 1,
        "items": [
            {
                "id": item["id"], "category": item["category"],
                "review_status": "pending" if item["status"] == "ready" else "missing",
                "preferred_version": None,
                "missed_cues": {"A": None, "B": None},
                "duplicates": {"A": None, "B": None},
                "wrong_language": {"A": None, "B": None},
                "post_switch_first_token_errors": {"A": None, "B": None},
                "timeline_issues": {"A": None, "B": None},
                "readability": {"A": None, "B": None},
                "candidate_net_degradation": None,
            }
            for item in public_items
        ],
    }
    _atomic_json(output_root / "private_mapping.json", private_mapping)
    _atomic_json(output_root / "review_form.json", review_form)
    summary = {
        "schema_version": 1,
        "report_type": "stage5_manual_review_pack",
        "ready_count": sum(item["status"] == "ready" for item in public_items),
        "missing_count": sum(item["status"] != "ready" for item in public_items),
        "items": public_items,
    }
    _atomic_json(output_root / "review_pack_summary.json", summary)
    lines = [
        "# Stage 5 Manual Listening Review",
        "",
        "Listen to each `audio.wav` while comparing `subtitle_A.srt` and `subtitle_B.srt`.",
        "Do not open `private_mapping.json` until all judgments are recorded.",
        "",
        "Record missed cues, duplicates, wrong-language spans, post-switch first-token errors, timeline issues, readability, and preference in `review_form.json`.",
        "",
    ]
    for item in public_items:
        lines.append(f"- `{item['category']}`: `{item['status']}`")
    write_text(output_root / "README.md", "\n".join(lines) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the private Stage 5 listening-review pack.")
    parser.add_argument("--output-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--manifest")
    args = parser.parse_args(argv)
    output_root = Path(args.output_root).resolve()
    try:
        if args.initialize:
            path = initialize_manifest(output_root)
            print(f"Review manifest: {path}")
            return 0
        if not args.manifest:
            raise ReviewPackError("--manifest is required unless --initialize is used")
        summary = build_pack(Path(args.manifest), output_root)
    except (OSError, ValueError, json.JSONDecodeError, ReviewPackError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Ready: {summary['ready_count']}; missing: {summary['missing_count']}")
    return 0 if summary["missing_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
