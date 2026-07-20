from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ffmpeg_locator import find_ffmpeg
from runtime_paths import resolve_runtime_paths
from subtitle_translate import SubtitleItem, read_srt, write_srt

CJK = re.compile(r"[\u3400-\u9fff]")
SPACE_BETWEEN_CJK = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])")


@dataclass
class OcrCue:
    index: int
    time_line: str
    french: str
    chinese: str
    sampled_frame_ids: list[int] = field(default_factory=list)
    french_observations: list[str] = field(default_factory=list)
    chinese_observations: list[str] = field(default_factory=list)


def _seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _range_seconds(time_line: str) -> tuple[float, float]:
    start, end = time_line.split(" --> ", 1)
    return _seconds(start), _seconds(end)


def _sample_offsets(count: int) -> list[float]:
    if count < 1:
        raise ValueError("frames_per_cue must be at least 1.")
    return [round((index + 1) / (count + 1), 6) for index in range(count)]


def _parse_sampling_offsets(value: str) -> list[float]:
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        offsets = [float(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("sampling_offsets must be comma-separated numbers.") from exc
    if not offsets or any(not math.isfinite(item) or not 0 < item < 1 for item in offsets):
        raise ValueError("sampling_offsets values must be between 0 and 1.")
    if offsets != sorted(set(offsets)):
        raise ValueError("sampling_offsets must be unique and increasing.")
    return offsets


def _extract_frame(
    ffmpeg: str,
    media: Path,
    cue: SubtitleItem,
    destination: Path,
    crop_height: int,
    *,
    offset: float = 0.5,
    crop_top_ratio: float | None = None,
    crop_bottom_ratio: float | None = None,
) -> None:
    start, end = _range_seconds(cue.time_line)
    timestamp = start + max(0.0, end - start) * offset
    if crop_top_ratio is not None and crop_bottom_ratio is not None:
        crop_filter = (
            f"crop=iw:ih*{crop_bottom_ratio - crop_top_ratio:.6f}:"
            f"0:ih*{crop_top_ratio:.6f},scale=2560:-1"
        )
    else:
        crop_filter = f"crop=iw:{crop_height}:0:ih-{crop_height},scale=2560:-1"
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{timestamp:.3f}", "-i", str(media),
        "-frames:v", "1",
        "-vf", crop_filter,
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg frame extraction failed for cue {cue.index}: {result.stderr[-300:]}")


def _best_line(lines: list[str], *, chinese: bool) -> str:
    cleaned = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if not cleaned:
        return ""
    if chinese:
        candidates = sorted(cleaned, key=lambda value: len(CJK.findall(value)), reverse=True)
        result = candidates[0] if CJK.search(candidates[0]) else ""
        result = SPACE_BETWEEN_CJK.sub("", result)
        result = re.sub(r"^\s*厶\s*(?=[\u3400-\u9fff])", "", result)
        result = re.sub(r"^一(?=这是)", "", result)
        return result.strip()
    candidates = [line for line in cleaned if len(re.findall(r"[A-Za-zÀ-ÿ]", line)) >= 3]
    return max(candidates, key=len).strip() if candidates else ""


def _consensus_text(observations: list[str]) -> str:
    candidates = [str(value or "").strip() for value in observations if str(value or "").strip()]
    if not candidates:
        return ""
    normalized = [_normalize(value) for value in candidates]
    best_index = max(
        range(len(candidates)),
        key=lambda index: (
            sum(_similar(normalized[index], other) for other in normalized),
            len(normalized[index]),
            -index,
        ),
    )
    return candidates[best_index]


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())


def _similar(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


def _same_caption(left: OcrCue, right: OcrCue) -> bool:
    left_french, right_french = _normalize(left.french), _normalize(right.french)
    left_chinese, right_chinese = _normalize(left.chinese), _normalize(right.chinese)
    return (
        _similar(left_french, right_french) >= 0.78
        and _similar(left_chinese, right_chinese) >= 0.78
    )


def _merge_consecutive(cues: list[OcrCue]) -> list[OcrCue]:
    merged: list[OcrCue] = []
    for cue in cues:
        signature = (_normalize(cue.french), _normalize(cue.chinese))
        if not any(signature):
            continue
        if merged and (
            signature == (_normalize(merged[-1].french), _normalize(merged[-1].chinese))
            or _same_caption(merged[-1], cue)
        ):
            start = merged[-1].time_line.split(" --> ", 1)[0]
            end = cue.time_line.split(" --> ", 1)[1]
            merged[-1].time_line = f"{start} --> {end}"
            merged[-1].sampled_frame_ids.extend(cue.sampled_frame_ids)
            merged[-1].french_observations.extend(cue.french_observations)
            merged[-1].chinese_observations.extend(cue.chinese_observations)
            continue
        merged.append(cue)
    for index, cue in enumerate(merged, start=1):
        cue.index = index
    return merged


def _observation_stability(representative: str, observations: list[str]) -> float | None:
    normalized = [_normalize(value) for value in observations if _normalize(value)]
    expected = _normalize(representative)
    if not expected or not normalized:
        return None
    if len(normalized) == 1:
        # One frame proves presence, not persistence across frames.
        return 0.5
    return round(sum(_similar(expected, value) for value in normalized) / len(normalized), 6)


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _uniform_sampling_cues(timeline_cues: list[SubtitleItem], interval: float) -> list[SubtitleItem]:
    if not timeline_cues:
        return []
    end = max(_seconds(cue.time_line.split(" --> ", 1)[1]) for cue in timeline_cues)
    cues: list[SubtitleItem] = []
    start = 0.0
    while start < end:
        stop = min(start + interval, end)
        cues.append(SubtitleItem(len(cues) + 1, f"{_timestamp(start)} --> {_timestamp(stop)}", ""))
        start = stop
    return cues


def run(args: argparse.Namespace) -> dict:
    if args.sample_interval <= 0:
        raise ValueError("sample_interval must be greater than zero.")
    if args.crop_height <= 0:
        raise ValueError("crop_height must be greater than zero.")
    frames_per_cue = int(getattr(args, "frames_per_cue", 1))
    long_cue_frames = int(getattr(args, "long_cue_frames", 0) or frames_per_cue)
    long_cue_seconds = float(getattr(args, "long_cue_seconds", 4.0))
    if frames_per_cue < 1 or long_cue_frames < 1:
        raise ValueError("frames_per_cue and long_cue_frames must be at least 1.")
    if not math.isfinite(long_cue_seconds) or long_cue_seconds <= 0:
        raise ValueError("long_cue_seconds must be greater than zero.")
    configured_offsets = _parse_sampling_offsets(getattr(args, "sampling_offsets", ""))
    crop_top_ratio = getattr(args, "crop_top_ratio", None)
    crop_bottom_ratio = getattr(args, "crop_bottom_ratio", None)
    if (crop_top_ratio is None) != (crop_bottom_ratio is None):
        raise ValueError("crop_top_ratio and crop_bottom_ratio must be provided together.")
    if crop_top_ratio is not None:
        crop_top_ratio = float(crop_top_ratio)
        crop_bottom_ratio = float(crop_bottom_ratio)
        if not (
            math.isfinite(crop_top_ratio)
            and math.isfinite(crop_bottom_ratio)
            and 0 <= crop_top_ratio < crop_bottom_ratio <= 1
        ):
            raise ValueError("crop ratios must satisfy 0 <= top < bottom <= 1.")
    paths = resolve_runtime_paths()
    media = (paths.project_root / args.media).resolve()
    timeline = (paths.project_root / args.timeline).resolve()
    output_dir = (paths.project_root / args.output_dir).resolve()
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg(paths.project_root)
    if not ffmpeg:
        raise RuntimeError("Project-local FFmpeg was not found.")
    timeline_cues = read_srt(timeline)
    cues = (
        _uniform_sampling_cues(timeline_cues, args.sample_interval)
        if args.sampling_mode == "uniform"
        else timeline_cues
    )
    raw_path = output_dir / "raw-ocr.local.json"
    if not args.reuse_raw or not raw_path.is_file():
        frame_jobs: list[tuple[int, SubtitleItem, float]] = []
        next_frame_id = 1
        for cue in cues:
            start, end = _range_seconds(cue.time_line)
            count = long_cue_frames if end - start >= long_cue_seconds else frames_per_cue
            offsets = configured_offsets if configured_offsets else _sample_offsets(count)
            for offset in offsets:
                frame_jobs.append((next_frame_id, cue, offset))
                next_frame_id += 1
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
            futures = [
                executor.submit(
                    _extract_frame,
                    ffmpeg,
                    media,
                    cue,
                    frames_dir / f"{frame_id:06d}.png",
                    args.crop_height,
                    offset=offset,
                    crop_top_ratio=crop_top_ratio,
                    crop_bottom_ratio=crop_bottom_ratio,
                )
                for frame_id, cue, offset in frame_jobs
            ]
            for future in futures:
                future.result()
        frame_map_path = output_dir / "frame-map.local.json"
        frame_map_path.write_text(
            json.dumps(
                [
                    {"frame_id": frame_id, "cue_id": cue.index, "offset": offset}
                    for frame_id, cue, offset in frame_jobs
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        bridge = paths.project_root / "scripts" / "windows_ocr_bridge.ps1"
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(bridge),
                "-InputDirectory", str(frames_dir), "-OutputJson", str(raw_path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Windows OCR failed: {result.stderr[-1000:]}")
    raw_rows = json.loads(raw_path.read_text(encoding="utf-8"))
    if isinstance(raw_rows, dict):
        raw_rows = [raw_rows]
    cue_by_id = {cue.index: cue for cue in cues}
    frame_map_path = output_dir / "frame-map.local.json"
    if frame_map_path.is_file():
        frame_rows = json.loads(frame_map_path.read_text(encoding="utf-8"))
        frame_map = {
            int(row["frame_id"]): (int(row["cue_id"]), float(row["offset"]))
            for row in frame_rows
        }
    else:
        # Backward-compatible raw OCR caches used cue ids as frame ids.
        frame_map = {cue.index: (cue.index, 0.5) for cue in cues}
    observations: dict[int, dict[str, list]] = {
        cue.index: {"frame_ids": [], "offsets": [], "source": [], "target": []}
        for cue in cues
    }
    frame_observations: list[dict] = []
    for row in raw_rows:
        frame_id = int(row["id"])
        mapping = frame_map.get(frame_id)
        if not mapping:
            continue
        cue_id, offset = mapping
        languages = row.get("languages", {})
        source = _best_line(languages.get("en-US", []), chinese=False)
        target = _best_line(languages.get("zh-Hans-CN", []), chinese=True)
        values = observations.setdefault(
            cue_id, {"frame_ids": [], "offsets": [], "source": [], "target": []}
        )
        values["frame_ids"].append(frame_id)
        values["offsets"].append(offset)
        values["source"].append(source)
        values["target"].append(target)
        cue = cue_by_id.get(cue_id)
        if cue:
            start, end = _range_seconds(cue.time_line)
            frame_observations.append({
                "frame_id": frame_id,
                "cue_id": cue_id,
                "offset": offset,
                "timestamp": start + max(0.0, end - start) * offset,
                "source": source,
                "target": target,
            })
    extracted: list[OcrCue] = []
    multi_frame = any(
        len(values.get("frame_ids", [])) > 1 for values in observations.values()
    )
    if multi_frame:
        by_cue: dict[int, list[dict]] = {}
        for row in frame_observations:
            by_cue.setdefault(int(row["cue_id"]), []).append(row)
        for cue_id, cue in cue_by_id.items():
            rows = sorted(by_cue.get(cue_id, []), key=lambda row: row["timestamp"])
            if not rows:
                continue
            cue_start, cue_end = _range_seconds(cue.time_line)
            for index, row in enumerate(rows):
                start = (
                    cue_start
                    if index == 0
                    else (rows[index - 1]["timestamp"] + row["timestamp"]) / 2
                )
                end = (
                    cue_end
                    if index + 1 == len(rows)
                    else (row["timestamp"] + rows[index + 1]["timestamp"]) / 2
                )
                if end <= start:
                    continue
                extracted.append(OcrCue(
                    int(row["frame_id"]),
                    f"{_timestamp(start)} --> {_timestamp(end)}",
                    str(row["source"]),
                    str(row["target"]),
                    sampled_frame_ids=[int(row["frame_id"])],
                    french_observations=[str(row["source"])] if row["source"] else [],
                    chinese_observations=[str(row["target"])] if row["target"] else [],
                ))
    else:
        for cue_id, cue in cue_by_id.items():
            values = observations.get(cue_id, {})
            source_observations = list(values.get("source", []))
            target_observations = list(values.get("target", []))
            source = _consensus_text(source_observations)
            target = _consensus_text(target_observations)
            extracted.append(OcrCue(
                cue.index,
                cue.time_line,
                source,
                target,
                sampled_frame_ids=list(values.get("frame_ids", [])),
                french_observations=source_observations,
                chinese_observations=target_observations,
            ))
    merged = _merge_consecutive(extracted)
    sample_id = str(args.sample_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", sample_id):
        raise ValueError("sample_id may contain only letters, digits, '.', '_' and '-'.")
    output_srt = output_dir / f"{sample_id}.burned-bilingual.ocr.srt"
    write_srt(
        [
            SubtitleItem(cue.index, cue.time_line, "\n".join(filter(None, (cue.french, cue.chinese))))
            for cue in merged
        ],
        output_srt,
    )
    evidence_sidecar = output_dir / "ocr-evidence.local.json"
    evidence_sidecar.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_type": "burned_subtitle_ocr_sampling_evidence",
                "sample_id": sample_id,
                "confidence_available": False,
                "roi": {
                    "crop_height": args.crop_height if crop_top_ratio is None else None,
                    "crop_top_ratio": crop_top_ratio,
                    "crop_bottom_ratio": crop_bottom_ratio,
                },
                "sampling": {
                    "frames_per_cue": frames_per_cue,
                    "long_cue_frames": long_cue_frames,
                    "long_cue_seconds": long_cue_seconds,
                    "configured_offsets": configured_offsets,
                },
                "stability_definition": (
                    "Mean normalized text similarity to the merged representative; a single observation "
                    "is assigned 0.5 because persistence cannot be established."
                ),
                "cues": [
                    {
                        "index": cue.index,
                        "time_line": cue.time_line,
                        "sampled_frame_ids": cue.sampled_frame_ids,
                        "sampled_frame_count": len(cue.sampled_frame_ids),
                        "source_nonempty": bool(cue.french),
                        "target_nonempty": bool(cue.chinese),
                        "source_consensus": cue.french,
                        "target_consensus": cue.chinese,
                        "source_observations": cue.french_observations,
                        "target_observations": cue.chinese_observations,
                        "failure_reasons": [
                            reason
                            for reason, failed in (
                                ("source_ocr_empty", not cue.french),
                                ("target_ocr_empty", not cue.chinese),
                            )
                            if failed
                        ],
                        "source_stability": _observation_stability(
                            cue.french, cue.french_observations
                        ),
                        "target_stability": _observation_stability(
                            cue.chinese, cue.chinese_observations
                        ),
                        "stability": _observation_stability(
                            cue.french, cue.french_observations
                        ),
                    }
                    for cue in merged
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "source_type": "burned_subtitle_ocr",
        "sample_id": sample_id,
        "source_language": args.source_language,
        "soft_subtitle_track_available": False,
        "sampled_frame_count": len(frame_observations),
        "sampling_mode": args.sampling_mode,
        "sample_interval_seconds": args.sample_interval if args.sampling_mode == "uniform" else None,
        "crop_height": args.crop_height if crop_top_ratio is None else None,
        "crop_top_ratio": crop_top_ratio,
        "crop_bottom_ratio": crop_bottom_ratio,
        "frames_per_cue": frames_per_cue,
        "long_cue_frames": long_cue_frames,
        "long_cue_seconds": long_cue_seconds,
        "output_cue_count": len(merged),
        "french_nonempty": sum(bool(cue.french) for cue in merged),
        "chinese_nonempty": sum(bool(cue.chinese) for cue in merged),
        "requires_manual_review": True,
        "output_file": output_srt.name,
        "evidence_sidecar": evidence_sidecar.name,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract burned bilingual subtitles with Windows OCR.")
    parser.add_argument("--media", default="archive/fr_short.mp4")
    parser.add_argument("--timeline", default="output/source/fr_short.small.srt")
    parser.add_argument("--output-dir", default="work/bilibili-subtitles/BV1mJ2rBCEe8")
    parser.add_argument("--sample-id", default="BV1mJ2rBCEe8")
    parser.add_argument("--source-language", default="fr")
    parser.add_argument("--sampling-mode", choices=("timeline", "uniform"), default="timeline")
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--crop-height", type=int, default=170)
    parser.add_argument("--crop-top-ratio", type=float)
    parser.add_argument("--crop-bottom-ratio", type=float)
    parser.add_argument("--frames-per-cue", type=int, default=1)
    parser.add_argument("--long-cue-frames", type=int, default=0)
    parser.add_argument("--long-cue-seconds", type=float, default=4.0)
    parser.add_argument(
        "--sampling-offsets",
        default="",
        help="Optional comma-separated offsets between 0 and 1; overrides frame counts.",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reuse-raw", action="store_true")
    return 0 if run(parser.parse_args())["output_cue_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
