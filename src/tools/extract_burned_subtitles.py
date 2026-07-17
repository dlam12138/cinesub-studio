from __future__ import annotations

import argparse
import difflib
import json
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


def _midpoint(time_line: str) -> float:
    start, end = time_line.split(" --> ", 1)
    return (_seconds(start) + _seconds(end)) / 2


def _extract_frame(
    ffmpeg: str, media: Path, cue: SubtitleItem, destination: Path, crop_height: int
) -> None:
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{_midpoint(cue.time_line):.3f}", "-i", str(media),
        "-frames:v", "1",
        "-vf", f"crop=iw:{crop_height}:0:ih-{crop_height},scale=2560:-1",
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
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
            futures = [
                executor.submit(
                    _extract_frame,
                    ffmpeg,
                    media,
                    cue,
                    frames_dir / f"{cue.index:06d}.png",
                    args.crop_height,
                )
                for cue in cues
            ]
            for future in futures:
                future.result()

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
    extracted: list[OcrCue] = []
    for row in raw_rows:
        cue = cue_by_id.get(int(row["id"]))
        if not cue:
            continue
        languages = row.get("languages", {})
        french = _best_line(languages.get("en-US", []), chinese=False)
        chinese = _best_line(languages.get("zh-Hans-CN", []), chinese=True)
        extracted.append(OcrCue(
            cue.index,
            cue.time_line,
            french,
            chinese,
            sampled_frame_ids=[cue.index],
            french_observations=[french] if french else [],
            chinese_observations=[chinese] if chinese else [],
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
        "sampled_frame_count": len(cues),
        "sampling_mode": args.sampling_mode,
        "sample_interval_seconds": args.sample_interval if args.sampling_mode == "uniform" else None,
        "crop_height": args.crop_height,
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
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reuse-raw", action="store_true")
    return 0 if run(parser.parse_args())["output_cue_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
