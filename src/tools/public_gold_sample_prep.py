from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import http.client
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable

from ffmpeg_locator import find_ffmpeg
from runtime_paths import resolve_runtime_paths

TOOL_VERSION = "public-gold-sample-prep-v1"
DATASET_NAME = "MediaSpeech French"
DATASET_VERSION = "SLR108 / MediaSpeech 1.1"
DATASET_LICENSE = "CC BY 4.0"
DATASET_CITATION = "MediaSpeech: Multilanguage ASR Benchmark and Dataset (2021)"
OPENSLR_PAGE = "https://www.openslr.org/108/"
OFFICIAL_DOWNLOADS = {
    "https://openslr.trmal.net/resources/108/FR.tgz",
    "https://openslr.elda.org/resources/108/FR.tgz",
    "https://openslr.magicdatatech.com/resources/108/FR.tgz",
    "https://github.com/NTRLab/MediaSpeech/releases/download/1.1/FR.zip",
}
AUDIO_SUFFIXES = {".wav", ".flac", ".opus", ".mp3", ".ogg", ".m4a"}
TEXT_SUFFIXES = {".txt"}
TABLE_SUFFIXES = {".tsv", ".csv"}
EXPECTED_SAMPLE_IDS = tuple(f"sample-{number:02d}" for number in range(1, 7))
SUMMRE_REPOSITORY = "linagora/SUMM-RE"
SUMMRE_REVISION = "6b5492d1cea1e483131627c939f82c3989c52b0d"
SUMMRE_LICENSE = "CC BY-SA 4.0"
SUMMRE_CITATION = "SUMM-RE: A corpus of French meeting-style conversations (2024)"
SUMMRE_SHARD_COUNTS = {"dev": 29, "test": 28}
SOURCE_SUFFIX_RE = re.compile(
    r"(?i)(?:[-_.](?:clip|segment|seg|chunk|part|utt))?[-_.]?\d+(?:[-_.]\d+)?$"
)


@dataclass(frozen=True)
class Candidate:
    audio_member: str
    transcript_member: str
    transcript: str
    source_group_id: str
    duration_seconds: float
    rms: float
    word_count: int
    words_per_second: float
    audio_sha256: str


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _official_source_url(value: str) -> str:
    normalized = urllib.parse.urlsplit(str(value).strip())
    canonical = urllib.parse.urlunsplit(
        (normalized.scheme.casefold(), normalized.netloc.casefold(), normalized.path, "", "")
    )
    if canonical not in OFFICIAL_DOWNLOADS:
        raise ValueError(
            "MediaSpeech download URL is not one of the French sources listed by OpenSLR SLR108"
        )
    return canonical


def download_archive(source_url: str, output_dir: Path) -> dict[str, Any]:
    source_url = _official_source_url(source_url)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / PurePosixPath(urllib.parse.urlsplit(source_url).path).name
    partial = target.with_suffix(target.suffix + ".part")
    existing = partial.stat().st_size if partial.is_file() else 0
    request = urllib.request.Request(source_url, method="HEAD")
    with urllib.request.urlopen(request, timeout=60) as response:
        expected_size = int(response.headers.get("Content-Length") or 0)
    print(f"Source: {source_url}")
    print(f"Expected size: {expected_size or 'unknown'} bytes")
    print(f"Target: {target}")
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    request = urllib.request.Request(source_url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as exc:
        if exc.code != 416 or not partial.is_file():
            raise
        response = None
    mode = "ab" if existing and response and response.status == 206 else "wb"
    if response is not None:
        with response, partial.open(mode) as output:
            shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
    if expected_size and partial.stat().st_size != expected_size:
        raise RuntimeError(
            f"MediaSpeech download is incomplete: {partial.stat().st_size}/{expected_size} bytes"
        )
    os.replace(partial, target)
    payload = {
        "dataset": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "dataset_license": DATASET_LICENSE,
        "source_page": OPENSLR_PAGE,
        "source_url": source_url,
        "download_bytes": target.stat().st_size,
        "local_download_sha256": sha256_file(target),
        "digest_authority": "locally_computed_not_publisher_supplied",
        "archive_name": target.name,
    }
    _write_json(output_dir / f"{target.name}.download.local.json", payload)
    return payload


def _safe_member_name(name: str) -> str:
    normalized = str(name).replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise ValueError(f"Archive contains an unsafe member path: {name}")
    return path.as_posix()


class ArchiveReader:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self._archive: tarfile.TarFile | zipfile.ZipFile | None = None
        self._kind = ""

    def __enter__(self) -> "ArchiveReader":
        if zipfile.is_zipfile(self.path):
            self._archive = zipfile.ZipFile(self.path)
            self._kind = "zip"
        elif tarfile.is_tarfile(self.path):
            self._archive = tarfile.open(self.path, "r:*")
            self._kind = "tar"
        else:
            raise ValueError("MediaSpeech archive must be a supported ZIP or TAR archive")
        for name in self.names():
            _safe_member_name(name)
        return self

    def __exit__(self, *_args: object) -> None:
        assert self._archive is not None
        self._archive.close()

    @property
    def kind(self) -> str:
        return self._kind

    def names(self) -> list[str]:
        assert self._archive is not None
        if isinstance(self._archive, zipfile.ZipFile):
            return [
                info.filename for info in self._archive.infolist()
                if not info.is_dir()
            ]
        return [
            member.name for member in self._archive.getmembers()
            if member.isfile()
        ]

    def open(self, name: str) -> BinaryIO:
        assert self._archive is not None
        if isinstance(self._archive, zipfile.ZipFile):
            return self._archive.open(name)
        member = self._archive.getmember(name)
        handle = self._archive.extractfile(member)
        if handle is None:
            raise FileNotFoundError(name)
        return handle

    def read(self, name: str) -> bytes:
        with self.open(name) as handle:
            return handle.read()

    def copy_to(self, name: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.open(name) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    raise ValueError("Transcript is not valid UTF-8 or Windows-1252 text")


def _layout(names: Iterable[str], suffixes: set[str]) -> list[str]:
    counts: dict[str, int] = {}
    for name in names:
        path = PurePosixPath(name)
        if path.suffix.casefold() not in suffixes:
            continue
        parent = path.parent.as_posix()
        counts[parent] = counts.get(parent, 0) + 1
    return [f"{key}:{counts[key]}" for key in sorted(counts)]


def _table_pairs(reader: ArchiveReader, names: list[str]) -> dict[str, tuple[str, str]]:
    pairs: dict[str, tuple[str, str]] = {}
    audio_by_name = {
        PurePosixPath(name).name.casefold(): name
        for name in names
        if PurePosixPath(name).suffix.casefold() in AUDIO_SUFFIXES
    }
    for table_name in names:
        suffix = PurePosixPath(table_name).suffix.casefold()
        if suffix not in TABLE_SUFFIXES:
            continue
        text = _decode_text(reader.read(table_name))
        dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        try:
            rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
        except csv.Error:
            continue
        if not rows:
            continue
        columns = {column.casefold(): column for column in rows[0] if column}
        audio_column = next(
            (columns[key] for key in ("audio", "audio_path", "path", "file", "filename", "wav")
             if key in columns),
            None,
        )
        text_column = next(
            (columns[key] for key in ("transcript", "text", "sentence", "transcription")
             if key in columns),
            None,
        )
        if not audio_column or not text_column:
            continue
        for row_number, row in enumerate(rows, start=2):
            value = str(row.get(audio_column) or "").replace("\\", "/")
            member = value if value in names else audio_by_name.get(PurePosixPath(value).name.casefold())
            transcript = str(row.get(text_column) or "").strip()
            if member and transcript:
                pairs[member] = (f"{table_name}#row-{row_number}", transcript)
    return pairs


def _same_stem_pairs(reader: ArchiveReader, names: list[str]) -> dict[str, tuple[str, str]]:
    text_by_stem = {
        PurePosixPath(name).with_suffix("").as_posix().casefold(): name
        for name in names
        if PurePosixPath(name).suffix.casefold() in TEXT_SUFFIXES
    }
    pairs: dict[str, tuple[str, str]] = {}
    for audio in names:
        path = PurePosixPath(audio)
        if path.suffix.casefold() not in AUDIO_SUFFIXES:
            continue
        transcript_member = text_by_stem.get(path.with_suffix("").as_posix().casefold())
        if transcript_member:
            pairs[audio] = (transcript_member, "")
    return pairs


def _derive_source_group(audio_member: str, all_audio: list[str]) -> tuple[str, str]:
    path = PurePosixPath(audio_member)
    parents = {
        PurePosixPath(name).parent.as_posix()
        for name in all_audio
    }
    if len(parents) >= 3 and path.parent.as_posix() not in {"", "."}:
        return path.parent.as_posix(), "audio_parent_directory"
    if re.fullmatch(
        r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        path.stem,
    ):
        raise ValueError(
            "MediaSpeech uses flat per-clip UUID names without recoverable source metadata"
        )
    stem = SOURCE_SUFFIX_RE.sub("", path.stem).strip("-_.")
    if not stem or stem == path.stem:
        raise ValueError(
            "MediaSpeech layout does not expose a reliable parent or stable filename source key"
        )
    return stem, "filename_prefix_before_segment_or_timestamp_suffix"


def _wave_metrics(raw: bytes) -> tuple[float, float]:
    with wave.open(io.BytesIO(raw), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        if rate <= 0 or width not in {1, 2, 3, 4}:
            raise ValueError("Unsupported WAV format")
        sample = handle.readframes(frames)
    if not sample:
        return 0.0, 0.0
    if width == 1:
        values = (value - 128 for value in sample)
    elif width == 2:
        values = (value[0] for value in struct.iter_unpack("<h", sample))
    elif width == 4:
        values = (value[0] for value in struct.iter_unpack("<i", sample))
    else:
        values = (
            int.from_bytes(sample[index:index + 3], "little", signed=True)
            for index in range(0, len(sample) - 2, 3)
        )
    squares = [value * value for value in values]
    rms = math.sqrt(sum(squares) / max(len(squares), 1))
    full_scale = float((1 << (8 * width - 1)) - 1)
    return frames / rate, rms / max(full_scale, 1.0) / math.sqrt(max(channels, 1))


def _ffprobe_duration(member: str, reader: ArchiveReader) -> tuple[float, float, str]:
    suffix = PurePosixPath(member).suffix
    with tempfile.TemporaryDirectory(prefix="cinesub-public-gold-inspect-") as directory:
        source = Path(directory) / f"source{suffix}"
        reader.copy_to(member, source)
        ffmpeg = find_ffmpeg(resolve_runtime_paths().project_root)
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required to inspect non-WAV MediaSpeech audio")
        ffprobe = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        result = subprocess.run(
            [
                str(ffprobe), "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        duration = float(result.stdout.strip())
        converted = Path(directory) / "mono.wav"
        subprocess.run(
            [
                str(ffmpeg), "-v", "error", "-i", str(source), "-ac", "1",
                "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(converted),
            ],
            check=True,
            capture_output=True,
        )
        raw = converted.read_bytes()
        _, rms = _wave_metrics(raw)
        return duration, rms, hashlib.sha256(source.read_bytes()).hexdigest()


def inspect_archive(archive: Path, output: Path) -> dict[str, Any]:
    archive = archive.resolve()
    archive_sha = sha256_file(archive)
    with ArchiveReader(archive) as reader:
        names = reader.names()
        audio = [
            name for name in names
            if PurePosixPath(name).suffix.casefold() in AUDIO_SUFFIXES
        ]
        pairs = _same_stem_pairs(reader, names)
        pairing_rule = "same_relative_stem"
        if not pairs:
            pairs = _table_pairs(reader, names)
            pairing_rule = "tabular_audio_and_transcript_columns"
        if not pairs:
            raise ValueError("No stable audio/transcript pairing rule was found")
        candidates: list[Candidate] = []
        source_rules = set()
        source_group_error = ""
        for audio_member, (transcript_member, transcript) in sorted(pairs.items()):
            try:
                source_group_id, source_rule = _derive_source_group(
                    audio_member, audio
                )
            except ValueError as exc:
                source_group_error = str(exc)
                candidates.clear()
                source_rules.clear()
                break
            source_rules.add(source_rule)
            if not transcript:
                transcript = _decode_text(reader.read(transcript_member))
            if not transcript:
                continue
            raw = reader.read(audio_member)
            if PurePosixPath(audio_member).suffix.casefold() == ".wav":
                duration, rms = _wave_metrics(raw)
                audio_sha = hashlib.sha256(raw).hexdigest()
            else:
                duration, rms, audio_sha = _ffprobe_duration(audio_member, reader)
            words = len(transcript.split())
            if 3 <= duration <= 15 and words:
                candidates.append(Candidate(
                    audio_member=audio_member,
                    transcript_member=transcript_member,
                    transcript=transcript,
                    source_group_id=source_group_id,
                    duration_seconds=round(duration, 6),
                    rms=round(rms, 8),
                    word_count=words,
                    words_per_second=round(words / duration, 8),
                    audio_sha256=audio_sha,
                ))
    group_count = len({row.source_group_id for row in candidates})
    payload = {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "dataset": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "dataset_license": DATASET_LICENSE,
        "dataset_citation": DATASET_CITATION,
        "source_page": OPENSLR_PAGE,
        "archive_name": archive.name,
        "archive_bytes": archive.stat().st_size,
        "archive_local_download_sha256": archive_sha,
        "observed_archive_type": reader.kind,
        "observed_audio_layout": _layout(names, AUDIO_SUFFIXES),
        "observed_transcript_layout": _layout(names, TEXT_SUFFIXES | TABLE_SUFFIXES),
        "pairing_rule": pairing_rule,
        "source_group_rule": sorted(source_rules),
        "source_group_reliable": not bool(source_group_error),
        "source_group_error": source_group_error,
        "candidate_group_count": group_count,
        "candidate_count": len(candidates),
        "candidates": [asdict(row) for row in candidates],
    }
    _write_json(output, payload)
    if source_group_error:
        raise ValueError(
            f"{source_group_error}; discovery report written to {output}"
        )
    return payload


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot calculate a quantile from an empty corpus")
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def select_bundles(
    discovery: dict[str, Any],
    *,
    sample_count: int = 6,
    min_source_groups: int = 3,
    min_duration: float = 45,
    max_duration: float = 75,
    silence_ms: int = 250,
    seed: str = "stage3a-public-gold-v1",
) -> dict[str, Any]:
    if sample_count != 6:
        raise ValueError("The public-gold contract requires exactly six samples")
    candidates = [dict(row) for row in discovery.get("candidates", [])]
    group_ids = sorted({str(row["source_group_id"]) for row in candidates})
    if len(group_ids) < min_source_groups:
        raise ValueError("At least three reliably derived source groups are required")
    archive_sha = str(discovery.get("archive_local_download_sha256") or "")
    identity = hashlib.sha256(
        f"{archive_sha}:{TOOL_VERSION}:{seed}".encode("utf-8")
    ).hexdigest()
    wps_values = [float(row["words_per_second"]) for row in candidates]
    rms_values = [float(row["rms"]) for row in candidates]
    med_wps = _quantile(wps_values, 0.5)
    med_rms = _quantile(rms_values, 0.5)
    fast_wps = _quantile(wps_values, 0.75)
    low_rms = _quantile(rms_values, 0.25)
    categories = ("normal", "normal", "fast", "fast", "low_volume", "low_volume")
    used: set[str] = set()
    selected: list[dict[str, Any]] = []
    group_cursor = 0
    for sample_index, category in enumerate(categories, start=1):
        ordered_groups = group_ids[group_cursor:] + group_ids[:group_cursor]
        choice = None
        for group_id in ordered_groups:
            available = [
                row for row in candidates
                if row["source_group_id"] == group_id
                and row["audio_sha256"] not in used
            ]
            if category == "fast":
                available.sort(key=lambda row: (
                    -float(row["words_per_second"]),
                    hashlib.sha256(f"{identity}:{row['audio_sha256']}".encode()).hexdigest(),
                ))
                available = [
                    row for row in available
                    if float(row["words_per_second"]) >= fast_wps
                ] + [
                    row for row in available
                    if float(row["words_per_second"]) < fast_wps
                ]
            elif category == "low_volume":
                available.sort(key=lambda row: (
                    float(row["rms"]),
                    hashlib.sha256(f"{identity}:{row['audio_sha256']}".encode()).hexdigest(),
                ))
                available = [
                    row for row in available if float(row["rms"]) <= low_rms
                ] + [
                    row for row in available if float(row["rms"]) > low_rms
                ]
            else:
                available.sort(key=lambda row: (
                    abs(float(row["words_per_second"]) - med_wps)
                    + abs(float(row["rms"]) - med_rms),
                    hashlib.sha256(f"{identity}:{row['audio_sha256']}".encode()).hexdigest(),
                ))
            picked = []
            duration = 0.0
            for row in available:
                addition = float(row["duration_seconds"]) + (
                    silence_ms / 1000 if picked else 0
                )
                if duration + addition > max_duration:
                    continue
                picked.append(row)
                duration += addition
                if duration >= min_duration:
                    break
            if duration >= min_duration:
                choice = (group_id, picked, duration)
                break
        if choice is None:
            raise ValueError(f"Unable to build {category} bundle within duration limits")
        group_id, picked, duration = choice
        used.update(str(row["audio_sha256"]) for row in picked)
        group_cursor = (group_ids.index(group_id) + 1) % len(group_ids)
        selected.append({
            "sample_id": f"sample-{sample_index:02d}",
            "selection_category": category,
            "source_group_id": group_id,
            "duration_seconds": round(duration, 6),
            "clips": picked,
        })
    if len({row["source_group_id"] for row in selected}) < min_source_groups:
        raise ValueError("Selected bundles do not retain at least three real source groups")
    return {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "selection_identity": identity,
        "selection_seed": seed,
        "archive_local_download_sha256": archive_sha,
        "selection_signals": [
            "duration_seconds", "word_count", "words_per_second", "rms",
            "source_group_id", "audio_sha256",
        ],
        "model_results_read": False,
        "quantiles": {
            "median_words_per_second": med_wps,
            "fast_words_per_second_q75": fast_wps,
            "median_rms": med_rms,
            "low_rms_q25": low_rms,
        },
        "samples": selected,
    }


def _pcm16_mono(reader: ArchiveReader, member: str, temporary_dir: Path) -> bytes:
    raw = reader.read(member)
    if PurePosixPath(member).suffix.casefold() == ".wav":
        with wave.open(io.BytesIO(raw), "rb") as handle:
            if (
                handle.getnchannels() == 1
                and handle.getframerate() == 16000
                and handle.getsampwidth() == 2
                and handle.getcomptype() == "NONE"
            ):
                return handle.readframes(handle.getnframes())
    suffix = PurePosixPath(member).suffix
    source = temporary_dir / f"source-{hashlib.sha256(member.encode()).hexdigest()[:12]}{suffix}"
    output = source.with_suffix(".pcm.wav")
    source.write_bytes(raw)
    ffmpeg = find_ffmpeg(resolve_runtime_paths().project_root)
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to normalize MediaSpeech bundle audio")
    subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-i", str(source), "-ac", "1",
            "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    with wave.open(str(output), "rb") as handle:
        return handle.readframes(handle.getnframes())


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def build_bundles(
    archive: Path,
    discovery: dict[str, Any],
    selection: dict[str, Any],
    output_dir: Path,
    *,
    silence_ms: int = 250,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_samples = []
    with ArchiveReader(archive) as reader, tempfile.TemporaryDirectory(
        prefix="cinesub-public-gold-build-"
    ) as temporary:
        temporary_dir = Path(temporary)
        for sample in selection["samples"]:
            sample_id = sample["sample_id"]
            wav_path = output_dir / f"{sample_id}.wav"
            srt_path = output_dir / f"{sample_id}.gold.fr.srt"
            clips_path = output_dir / f"{sample_id}.clips.local.json"
            clip_rows = []
            cursor_frames = 0
            silence_frames = round(16000 * silence_ms / 1000)
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                for clip_index, clip in enumerate(sample["clips"], start=1):
                    if clip_index > 1:
                        output.writeframes(b"\0\0" * silence_frames)
                        cursor_frames += silence_frames
                    pcm = _pcm16_mono(reader, clip["audio_member"], temporary_dir)
                    start = cursor_frames / 16000
                    output.writeframes(pcm)
                    cursor_frames += len(pcm) // 2
                    end = cursor_frames / 16000
                    clip_rows.append({
                        "clip_index": clip_index,
                        "start": round(start, 6),
                        "end": round(end, 6),
                        "audio_member": clip["audio_member"],
                        "transcript_member": clip["transcript_member"],
                        "transcript": clip["transcript"],
                        "audio_sha256": clip["audio_sha256"],
                    })
            srt_blocks = [
                (
                    f"{row['clip_index']}\n"
                    f"{_srt_time(row['start'])} --> {_srt_time(row['end'])}\n"
                    f"{row['transcript']}"
                )
                for row in clip_rows
            ]
            srt_path.write_text("\n\n".join(srt_blocks) + "\n", encoding="utf-8")
            _write_json(clips_path, {
                "schema_version": 1,
                "sample_id": sample_id,
                "source_group_id": sample["source_group_id"],
                "clips": clip_rows,
            })
            duration = cursor_frames / 16000
            manifest_samples.append({
                "id": sample_id,
                "source_group_id": sample["source_group_id"],
                "source_asset_id": f"prepared-bundle:{sample_id}",
                "media_path": wav_path.name,
                "start": 0,
                "end": round(duration, 6),
                "reference_language": "fr",
                "reference_type": "gold_verbatim",
                "reference_path": srt_path.name,
                "clips_path": clips_path.name,
                "authorized": True,
            })
    manifest = {
        "schema_version": 1,
        "authorized": True,
        "campaign_scope": "stage3a_public_gold",
        "dataset": DATASET_NAME,
        "dataset_version": DATASET_VERSION,
        "dataset_license": DATASET_LICENSE,
        "dataset_citation": DATASET_CITATION,
        "source_page": OPENSLR_PAGE,
        "archive_local_download_sha256": discovery[
            "archive_local_download_sha256"
        ],
        "archive_hash_prefix": str(
            discovery["archive_local_download_sha256"]
        )[:12],
        "selection_identity": selection["selection_identity"],
        "samples": manifest_samples,
    }
    _write_json(output_dir / "public-gold-manifest.local.json", manifest)
    _write_json(output_dir / "public-gold-selection.local.json", selection)
    return manifest


def _merge_summre_segments(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments = []
    for row in raw_segments:
        transcript = str(row.get("transcript") or "").strip()
        start = float(row.get("start") or 0)
        end = float(row.get("end") or 0)
        if transcript and end > start:
            segments.append({"start": start, "end": end, "transcript": transcript})
    segments.sort(key=lambda row: (row["start"], row["end"]))
    clips = []
    current: list[dict[str, Any]] = []
    for segment in segments:
        if current and (
            segment["start"] - current[-1]["end"] > 2.0
            or segment["end"] - current[0]["start"] > 15.0
        ):
            duration = current[-1]["end"] - current[0]["start"]
            if 2.999 <= duration <= 15.001:
                clips.append({
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                    "transcript": " ".join(row["transcript"] for row in current),
                })
            current = []
        current.append(segment)
        duration = current[-1]["end"] - current[0]["start"]
        if duration >= 2.999:
            if duration <= 15.001:
                clips.append({
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                    "transcript": " ".join(row["transcript"] for row in current),
                })
            current = []
    return clips


def _select_summre_clips(
    clips: list[dict[str, Any]],
    *,
    min_duration: float,
    max_duration: float,
    silence_ms: int,
) -> list[dict[str, Any]]:
    selected = []
    duration = 0.0
    for clip in clips:
        addition = clip["end"] - clip["start"]
        if selected:
            addition += silence_ms / 1000
        if duration + addition > max_duration:
            continue
        selected.append(clip)
        duration += addition
        if duration >= min_duration:
            return selected
    return []


def _summre_audio_bytes(audio: object) -> bytes:
    if isinstance(audio, dict):
        raw = audio.get("bytes")
        if isinstance(raw, bytes):
            return raw
        path = str(audio.get("path") or "")
        if path:
            if re.match(r"^https?://", path):
                with urllib.request.urlopen(path, timeout=120) as response:
                    return response.read()
            candidate = Path(path)
            if candidate.is_file():
                return candidate.read_bytes()
    raise ValueError("SUMM-RE streaming record did not expose undecoded audio bytes")


def _normalized_audio_frames(raw: bytes, temporary_dir: Path, key: str) -> bytes:
    try:
        with wave.open(io.BytesIO(raw), "rb") as handle:
            if (
                handle.getnchannels() == 1
                and handle.getframerate() == 16000
                and handle.getsampwidth() == 2
                and handle.getcomptype() == "NONE"
            ):
                return handle.readframes(handle.getnframes())
    except (EOFError, wave.Error):
        pass
    source = temporary_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:12]}.audio"
    output = source.with_suffix(".wav")
    source.write_bytes(raw)
    ffmpeg = find_ffmpeg(resolve_runtime_paths().project_root)
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to normalize SUMM-RE audio")
    subprocess.run(
        [
            str(ffmpeg), "-v", "error", "-i", str(source), "-ac", "1",
            "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    with wave.open(str(output), "rb") as handle:
        return handle.readframes(handle.getnframes())


def _summre_shard_url(split: str, shard_index: int) -> str:
    count = SUMMRE_SHARD_COUNTS[split]
    filename = f"{split}-{shard_index:05d}-of-{count:05d}.parquet"
    return (
        f"https://huggingface.co/datasets/{SUMMRE_REPOSITORY}/resolve/"
        f"{SUMMRE_REVISION}/data/{split}/{filename}"
    )


def _summre_shard_records(split: str, shard_index: int) -> list[dict[str, Any]]:
    try:
        import fsspec
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "SUMM-RE range streaming requires pyarrow and fsspec[http]"
        ) from exc
    url = _summre_shard_url(split, shard_index)
    with fsspec.open(
        url,
        "rb",
        block_size=8 * 1024 * 1024,
        cache_type="readahead",
    ) as handle:
        table = pq.ParquetFile(handle).read(
            columns=["meeting_id", "speaker_id", "audio_id", "segments"]
        )
    return [
        {**row, "split": split, "shard_index": shard_index, "row_index": index}
        for index, row in enumerate(table.to_pylist())
    ]


def _summre_shard_audio(
    split: str,
    shard_index: int,
    selected_row_indexes: set[int],
    cache_dir: Path,
) -> dict[int, bytes]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("SUMM-RE preparation requires pyarrow") from exc
    local_shard = _download_summre_shard(split, shard_index, cache_dir)
    column = pq.ParquetFile(local_shard).read(columns=["audio"]).column("audio")
    values = {
        row_index: _summre_audio_bytes(column[row_index].as_py())
        for row_index in sorted(selected_row_indexes)
    }
    del column
    gc.collect()
    return values


def _download_summre_shard(
    split: str,
    shard_index: int,
    cache_dir: Path,
    *,
    attempts: int = 8,
) -> Path:
    url = _summre_shard_url(split, shard_index)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / PurePosixPath(urllib.parse.urlsplit(url).path).name
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=60) as response:
        expected_size = int(response.headers.get("Content-Length") or 0)
    if expected_size <= 0:
        raise RuntimeError("SUMM-RE shard server did not provide Content-Length")
    if target.is_file() and target.stat().st_size == expected_size:
        return target
    print(f"Selected SUMM-RE shard: {url}")
    print(f"Expected size: {expected_size} bytes")
    print(f"Target: {target}")
    for _attempt in range(attempts):
        existing = partial.stat().st_size if partial.is_file() else 0
        if existing == expected_size:
            break
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                mode = "ab" if existing and response.status == 206 else "wb"
                with partial.open(mode) as output:
                    while chunk := response.read(8 * 1024 * 1024):
                        output.write(chunk)
        except (OSError, urllib.error.URLError, http.client.IncompleteRead):
            continue
    actual_size = partial.stat().st_size if partial.is_file() else 0
    if actual_size != expected_size:
        raise RuntimeError(
            f"SUMM-RE shard download is incomplete: {actual_size}/{expected_size}"
        )
    os.replace(partial, target)
    _write_json(target.with_suffix(target.suffix + ".download.local.json"), {
        "repository": SUMMRE_REPOSITORY,
        "revision": SUMMRE_REVISION,
        "split": split,
        "shard_index": shard_index,
        "source_url": url,
        "bytes": expected_size,
        "local_download_sha256": sha256_file(target),
        "digest_authority": "locally_computed_not_publisher_supplied",
    })
    return target


def prepare_summre(
    output_dir: Path,
    *,
    splits: tuple[str, ...] = ("dev", "test"),
    min_source_groups: int = 3,
    min_duration: float = 45,
    max_duration: float = 75,
    silence_ms: int = 250,
) -> dict[str, Any]:
    if not splits or any(split not in {"dev", "test"} for split in splits):
        raise ValueError("SUMM-RE public gold permits only dev and test splits")
    selected_by_meeting: dict[str, list[dict[str, Any]]] = {}
    observed_records = 0
    for split in splits:
        for shard_index in range(SUMMRE_SHARD_COUNTS[split]):
            for record in _summre_shard_records(split, shard_index):
                observed_records += 1
                meeting_id = str(record.get("meeting_id") or "").strip()
                audio_id = str(record.get("audio_id") or "").strip()
                if not meeting_id or not audio_id:
                    continue
                rows = selected_by_meeting.setdefault(meeting_id, [])
                if len(rows) >= 2:
                    continue
                clips = _select_summre_clips(
                    _merge_summre_segments(list(record.get("segments") or [])),
                    min_duration=min_duration,
                    max_duration=max_duration,
                    silence_ms=silence_ms,
                )
                if not clips:
                    continue
                rows.append({
                    "split": split,
                    "shard_index": int(record["shard_index"]),
                    "row_index": int(record["row_index"]),
                    "meeting_id": meeting_id,
                    "speaker_id": str(record.get("speaker_id") or ""),
                    "audio_id": audio_id,
                    "clips": clips,
                })
                ready = [
                    key for key, values in selected_by_meeting.items()
                    if len(values) >= 2
                ]
                if len(ready) >= min_source_groups:
                    selected_by_meeting = {
                        key: selected_by_meeting[key][:2]
                        for key in sorted(ready)[:min_source_groups]
                    }
                    break
            if len(selected_by_meeting) >= min_source_groups and all(
                len(rows) >= 2 for rows in selected_by_meeting.values()
            ):
                break
        if len(selected_by_meeting) >= min_source_groups and all(
            len(rows) >= 2 for rows in selected_by_meeting.values()
        ):
            break
    if len(selected_by_meeting) < min_source_groups or not all(
        len(rows) >= 2 for rows in selected_by_meeting.values()
    ):
        raise RuntimeError(
            "SUMM-RE streaming did not yield two eligible tracks for three meetings"
        )
    flattened = [
        row for meeting_id in sorted(selected_by_meeting)
        for row in selected_by_meeting[meeting_id]
    ]
    snapshot_material = {
        "repository": SUMMRE_REPOSITORY,
        "revision": SUMMRE_REVISION,
        "records": [
            {
                "split": row["split"],
                "meeting_id": row["meeting_id"],
                "audio_id": row["audio_id"],
                "clips": row["clips"],
            }
            for row in flattened
        ],
        "tool_version": TOOL_VERSION,
    }
    snapshot_sha = hashlib.sha256(
        json.dumps(
            snapshot_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_samples = []
    selection_samples = []
    with tempfile.TemporaryDirectory(
        prefix="cinesub-public-gold-summre-"
    ) as temporary:
        temporary_dir = Path(temporary)
        shard_cache = output_dir.parent / "source" / "summre-selected-shards"
        audio_by_record: dict[tuple[str, int, int], bytes] = {}
        selected_shards = sorted({
            (row["split"], int(row["shard_index"])) for row in flattened
        })
        for split, shard_index in selected_shards:
            indexes = {
                int(row["row_index"]) for row in flattened
                if row["split"] == split and int(row["shard_index"]) == shard_index
            }
            for row_index, raw in _summre_shard_audio(
                split, shard_index, indexes, shard_cache
            ).items():
                audio_by_record[(split, shard_index, row_index)] = raw
        for sample_number, row in enumerate(flattened, start=1):
            sample_id = f"sample-{sample_number:02d}"
            audio_raw = audio_by_record.pop((
                row["split"],
                int(row["shard_index"]),
                int(row["row_index"]),
            ))
            frames = _normalized_audio_frames(
                audio_raw, temporary_dir, row["audio_id"]
            )
            audio_sha = hashlib.sha256(audio_raw).hexdigest()
            wav_path = output_dir / f"{sample_id}.wav"
            srt_path = output_dir / f"{sample_id}.gold.fr.srt"
            clips_path = output_dir / f"{sample_id}.clips.local.json"
            cursor_frames = 0
            silence_frames = round(16000 * silence_ms / 1000)
            clip_rows = []
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                for clip_index, clip in enumerate(row["clips"], start=1):
                    if clip_index > 1:
                        output.writeframes(b"\0\0" * silence_frames)
                        cursor_frames += silence_frames
                    source_start = max(round(float(clip["start"]) * 16000), 0)
                    source_end = min(
                        round(float(clip["end"]) * 16000),
                        len(frames) // 2,
                    )
                    if source_end <= source_start:
                        raise ValueError("SUMM-RE clip falls outside its audio track")
                    start = cursor_frames / 16000
                    pcm = frames[source_start * 2:source_end * 2]
                    output.writeframes(pcm)
                    cursor_frames += len(pcm) // 2
                    end = cursor_frames / 16000
                    clip_rows.append({
                        "clip_index": clip_index,
                        "start": round(start, 6),
                        "end": round(end, 6),
                        "source_start": round(float(clip["start"]), 6),
                        "source_end": round(float(clip["end"]), 6),
                        "transcript": clip["transcript"],
                    })
            srt_path.write_text(
                "\n\n".join(
                    (
                        f"{clip['clip_index']}\n"
                        f"{_srt_time(clip['start'])} --> {_srt_time(clip['end'])}\n"
                        f"{clip['transcript']}"
                    )
                    for clip in clip_rows
                ) + "\n",
                encoding="utf-8",
            )
            _write_json(clips_path, {
                "schema_version": 1,
                "sample_id": sample_id,
                "split": row["split"],
                "meeting_id": row["meeting_id"],
                "speaker_id": row["speaker_id"],
                "audio_id": row["audio_id"],
                "audio_sha256": audio_sha,
                "clips": clip_rows,
            })
            duration = cursor_frames / 16000
            manifest_samples.append({
                "id": sample_id,
                "source_group_id": row["meeting_id"],
                "source_asset_id": row["audio_id"],
                "media_path": wav_path.name,
                "start": 0,
                "end": round(duration, 6),
                "reference_language": "fr",
                "reference_type": "gold_verbatim",
                "reference_path": srt_path.name,
                "clips_path": clips_path.name,
                "authorized": True,
            })
            selection_samples.append({
                "sample_id": sample_id,
                "split": row["split"],
                "meeting_id": row["meeting_id"],
                "speaker_id": row["speaker_id"],
                "audio_id": row["audio_id"],
                "audio_sha256": audio_sha,
                "duration_seconds": round(duration, 6),
                "clip_count": len(clip_rows),
            })
    manifest = {
        "schema_version": 1,
        "authorized": True,
        "campaign_scope": "stage3a_public_gold",
        "dataset": "SUMM-RE",
        "dataset_version": SUMMRE_REVISION,
        "dataset_license": SUMMRE_LICENSE,
        "dataset_citation": SUMMRE_CITATION,
        "source_page": f"https://huggingface.co/datasets/{SUMMRE_REPOSITORY}",
        "dataset_snapshot_sha256": snapshot_sha,
        "archive_hash_prefix": snapshot_sha[:12],
        "selection_identity": snapshot_sha,
        "samples": manifest_samples,
    }
    selection = {
        "schema_version": 1,
        "tool_version": TOOL_VERSION,
        "dataset": "SUMM-RE",
        "dataset_revision": SUMMRE_REVISION,
        "license": SUMMRE_LICENSE,
        "splits_allowed": ["dev", "test"],
        "splits_used": sorted({row["split"] for row in flattened}),
        "streaming": True,
        "streaming_transport": "parquet_http_range",
        "download_granularity": "selected_parquet_shards_and_columns",
        "train_used": False,
        "observed_record_count": observed_records,
        "selection_signals": [
            "meeting_id", "speaker_id", "segment_duration",
            "transcript_word_count", "dataset_revision",
        ],
        "model_results_read": False,
        "selection_identity": snapshot_sha,
        "samples": selection_samples,
    }
    _write_json(output_dir / "public-gold-manifest.local.json", manifest)
    _write_json(output_dir / "public-gold-selection.local.json", selection)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare deterministic private MediaSpeech French public-gold bundles."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    download = subparsers.add_parser("download")
    download.add_argument("--dataset", choices=("mediaspeech-fr",), required=True)
    download.add_argument("--source-url", required=True)
    download.add_argument("--output-dir", type=Path, required=True)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--archive", type=Path, required=True)
    inspect.add_argument("--output", type=Path, required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--archive", type=Path, required=True)
    prepare.add_argument("--discovery", type=Path, default=None)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--sample-count", type=int, default=6)
    prepare.add_argument("--min-source-groups", type=int, default=3)
    prepare.add_argument("--min-duration", type=float, default=45)
    prepare.add_argument("--max-duration", type=float, default=75)
    prepare.add_argument("--silence-ms", type=int, default=250)
    prepare.add_argument("--seed", default="stage3a-public-gold-v1")
    summre = subparsers.add_parser("prepare-summre")
    summre.add_argument("--output-dir", type=Path, required=True)
    summre.add_argument(
        "--split",
        action="append",
        choices=("dev", "test"),
        default=[],
    )
    summre.add_argument("--min-source-groups", type=int, default=3)
    summre.add_argument("--min-duration", type=float, default=45)
    summre.add_argument("--max-duration", type=float, default=75)
    summre.add_argument("--silence-ms", type=int, default=250)
    args = parser.parse_args()
    if args.action == "download":
        payload = download_archive(args.source_url, args.output_dir)
    elif args.action == "inspect":
        payload = inspect_archive(args.archive, args.output)
    elif args.action == "prepare":
        discovery_path = args.discovery or (
            args.output_dir / "public-gold-discovery.local.json"
        )
        discovery = (
            json.loads(discovery_path.read_text(encoding="utf-8"))
            if discovery_path.is_file()
            else inspect_archive(args.archive, discovery_path)
        )
        selection = select_bundles(
            discovery,
            sample_count=args.sample_count,
            min_source_groups=args.min_source_groups,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            silence_ms=args.silence_ms,
            seed=args.seed,
        )
        payload = build_bundles(
            args.archive,
            discovery,
            selection,
            args.output_dir,
            silence_ms=args.silence_ms,
        )
    else:
        payload = prepare_summre(
            args.output_dir,
            splits=tuple(args.split or ("dev", "test")),
            min_source_groups=args.min_source_groups,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            silence_ms=args.silence_ms,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
