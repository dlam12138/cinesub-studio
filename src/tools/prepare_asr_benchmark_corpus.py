"""Prepare the private Stage 3 ASR benchmark corpus from pinned FLEURS data.

The downloaded parquet shards, extraction dependency, media, references, and
provenance stay in gitignored project-local directories.  The command is a
plan-only operation unless ``--execute`` is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ffmpeg_locator import find_ffmpeg
from runtime_paths import resolve_runtime_paths


PATHS = resolve_runtime_paths()
PROJECT_ROOT = PATHS.project_root
REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
LICENSE = "CC BY 4.0"
DATASET_URL = "https://huggingface.co/datasets/google/fleurs"
SHARDS = {
    "fr_fr": ("parquet-data/fr_fr/test-00000-of-00001.parquet", 445_464_011),
    "en_us": ("parquet-data/en_us/test-00000-of-00001.parquet", 401_722_686),
    "cmn_hans_cn": ("parquet-data/cmn_hans_cn/test-00000-of-00001.parquet", 695_674_033),
}
TMP_ROOT = PROJECT_ROOT / ".tmp" / "asr-corpus-tools"
LOCAL_ROOT = PROJECT_ROOT / "tests" / "asr_benchmark" / "local"
MANIFEST_PATH = PROJECT_ROOT / "tests" / "asr_benchmark" / "manifest.local.json"


@dataclass(frozen=True)
class Utterance:
    language: str
    row_id: int
    path: Path
    text: str
    duration: float
    sha256: str


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str
    language: str
    row_id: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True)
    if result.returncode:
        detail = (result.stderr or result.stdout)[-2000:]
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")


def _download(url: str, target: Path, expected_size: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == expected_size:
        return
    partial = target.with_suffix(target.suffix + ".partial")
    total = partial.stat().st_size if partial.exists() else 0
    while total < expected_size:
        headers = {"User-Agent": "CineSub-ASR-Benchmark/1"}
        if total:
            headers["Range"] = f"bytes={total}-"
        request = urllib.request.Request(url, headers=headers)
        before = total
        with urllib.request.urlopen(request, timeout=120) as response:
            status = getattr(response, "status", response.getcode())
            if total and status != 206:
                partial.unlink(missing_ok=True)
                total = 0
                continue
            with partial.open("ab" if total else "wb") as output:
                while total < expected_size:
                    chunk = response.read(min(4 * 1024 * 1024, expected_size - total))
                    if not chunk:
                        break
                    output.write(chunk)
                    total += len(chunk)
                    print(f"  {target.name}: {total / 1024 / 1024:.1f} MiB", end="\r", flush=True)
        if total == before:
            raise RuntimeError(f"download made no progress for {target.name} at byte {total}")
    print()
    if partial.stat().st_size != expected_size:
        raise RuntimeError(
            f"unexpected shard size for {target.name}: {partial.stat().st_size} != {expected_size}"
        )
    os.replace(partial, target)


def _ensure_pyarrow() -> None:
    target = TMP_ROOT / "python"
    sys.path.insert(0, str(target))
    try:
        import pyarrow.parquet  # noqa: F401
        return
    except ImportError:
        pass
    target.mkdir(parents=True, exist_ok=True)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--target",
            str(target),
            "pyarrow==21.0.0",
        ]
    )
    sys.path.insert(0, str(target))


def _duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def _extract_utterances(language: str, shard: Path, count: int = 80) -> list[Utterance]:
    import pyarrow.parquet as pq

    table = pq.read_table(shard, columns=["id", "num_samples", "audio", "raw_transcription"])
    rows = table.to_pylist()
    extracted = TMP_ROOT / "utterances" / language
    extracted.mkdir(parents=True, exist_ok=True)
    utterances: list[Utterance] = []
    for row in sorted(rows, key=lambda item: int(item["id"])):
        audio = row["audio"] or {}
        payload = audio.get("bytes")
        text = str(row.get("raw_transcription") or "").strip()
        if not payload or not text:
            continue
        row_id = int(row["id"])
        target = extracted / f"{row_id}.wav"
        target.write_bytes(payload)
        duration = int(row.get("num_samples") or 0) / 16000.0
        if duration < 1.5 or duration > 20:
            target.unlink(missing_ok=True)
            continue
        utterances.append(Utterance(language, row_id, target, text, duration, _sha256(target)))
        if len(utterances) >= count:
            break
    if len(utterances) < count:
        raise RuntimeError(f"not enough usable {language} utterances: {len(utterances)}")
    return utterances


def _srt_time(value: float) -> str:
    millis = max(0, round(value * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _write_srt(path: Path, cues: list[Cue]) -> None:
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end, cue.row_id))
    blocks = [
        f"{index}\n{_srt_time(cue.start)} --> {_srt_time(cue.end)}\n{cue.text}"
        for index, cue in enumerate(ordered, 1)
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _concat(ffmpeg: Path, utterances: list[Utterance], target: Path) -> list[Cue]:
    listing = target.with_suffix(".concat.txt")
    listing.write_text(
        "\n".join(f"file '{item.path.as_posix().replace(chr(39), chr(39) * 2)}'" for item in utterances),
        encoding="utf-8",
    )
    _run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ]
    )
    listing.unlink(missing_ok=True)
    cues: list[Cue] = []
    cursor = 0.0
    for item in utterances:
        cues.append(Cue(cursor, cursor + item.duration, item.text, item.language, item.row_id))
        cursor += item.duration
    return cues


def _take_duration(pool: list[Utterance], start: int, target: float = 42.0) -> list[Utterance]:
    chosen: list[Utterance] = []
    total = 0.0
    for item in pool[start:]:
        chosen.append(item)
        total += item.duration
        if total >= target:
            break
    if not 30 <= total <= 90:
        raise RuntimeError(f"unable to build 30-90 second sample; duration={total:.3f}")
    return chosen


def _filter(ffmpeg: Path, source: Path, target: Path, expression: str) -> None:
    _run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-af", expression, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ]
    )


def _noise(ffmpeg: Path, source: Path, target: Path, duration: float) -> None:
    _run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.025:duration={duration:.6f}:seed=3407",
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ]
    )


def _overlap(ffmpeg: Path, first: Path, second: Path, target: Path) -> None:
    _run(
        [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(first), "-i", str(second),
            "-filter_complex", "[0:a]volume=0.9[a];[1:a]volume=0.75[b];[a][b]amix=inputs=2:duration=longest:normalize=0",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ]
    )


def _make_samples(pools: dict[str, list[Utterance]], ffmpeg: Path) -> list[dict[str, Any]]:
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    work = TMP_ROOT / "composition"
    work.mkdir(parents=True, exist_ok=True)
    specs: list[dict[str, Any]] = []

    def add(sample_id: str, language: str, tags: list[str], cues: list[Cue], wav: Path, transform: str) -> None:
        output = LOCAL_ROOT / f"{sample_id}.wav"
        shutil.copy2(wav, output)
        _write_srt(LOCAL_ROOT / f"{sample_id}.reference.srt", cues)
        duration = _duration(output)
        if not 30 <= duration <= 90:
            raise RuntimeError(f"sample {sample_id} duration outside 30-90 seconds: {duration:.3f}")
        specs.append({
            "id": sample_id,
            "language": language,
            "acoustic_tags": tags,
            "duration_seconds": round(duration, 6),
            "transform": transform,
            "cues": [{"language": c.language, "row_id": c.row_id} for c in cues],
            "media_sha256": _sha256(output),
        })

    fr = pools["fr_fr"]
    en = pools["en_us"]
    zh = pools["cmn_hans_cn"]
    offsets = iter(range(0, 80, 8))

    for sample_id in ("fr-clean-01", "fr-clean-02"):
        items = _take_duration(fr, next(offsets))
        wav = work / f"{sample_id}.wav"
        cues = _concat(ffmpeg, items, wav)
        add(sample_id, "fr", ["clean", "dialogue"], cues, wav, "concat")

    items = _take_duration(fr, next(offsets))
    base = work / "fr-noise-base.wav"
    cues = _concat(ffmpeg, items, base)
    wav = work / "fr-noise-music.wav"
    _noise(ffmpeg, base, wav, _duration(base))
    add("fr-noise-music", "fr", ["noise", "pink_noise"], cues, wav, "pink-noise seed=3407 amplitude=0.025")

    a_items = _take_duration(fr, next(offsets), 36)
    b_items = _take_duration(fr, next(offsets), 36)
    a_wav, b_wav = work / "fr-overlap-a.wav", work / "fr-overlap-b.wav"
    a_cues, b_cues = _concat(ffmpeg, a_items, a_wav), _concat(ffmpeg, b_items, b_wav)
    wav = work / "fr-overlap.wav"
    _overlap(ffmpeg, a_wav, b_wav, wav)
    add("fr-overlap", "fr", ["overlapping_speech"], a_cues + b_cues, wav, "two-track amix 0.9/0.75")

    items = _take_duration(fr, next(offsets), 48)
    base = work / "fr-fast-base.wav"
    cues = _concat(ffmpeg, items, base)
    wav = work / "fr-fast.wav"
    _filter(ffmpeg, base, wav, "atempo=1.25")
    cues = [Cue(c.start / 1.25, c.end / 1.25, c.text, c.language, c.row_id) for c in cues]
    add("fr-fast", "fr", ["fast_speech"], cues, wav, "atempo=1.25")

    items = _take_duration(fr, next(offsets))
    base = work / "fr-distant-base.wav"
    cues = _concat(ffmpeg, items, base)
    wav = work / "fr-distant-quiet.wav"
    _filter(ffmpeg, base, wav, "volume=0.32,aecho=0.8:0.45:70:0.18")
    add("fr-distant-quiet", "fr", ["distant", "low_volume"], cues, wav, "volume=0.32,aecho")

    items = _take_duration(en, 0)
    wav = work / "en-clean.wav"
    cues = _concat(ffmpeg, items, wav)
    add("en-clean", "en", ["clean", "dialogue"], cues, wav, "concat")

    a_items, b_items = _take_duration(en, 10, 36), _take_duration(en, 20, 36)
    a_wav, b_wav = work / "en-complex-a.wav", work / "en-complex-b.wav"
    a_cues, b_cues = _concat(ffmpeg, a_items, a_wav), _concat(ffmpeg, b_items, b_wav)
    mixed = work / "en-complex-mixed.wav"
    _overlap(ffmpeg, a_wav, b_wav, mixed)
    wav = work / "en-complex.wav"
    _noise(ffmpeg, mixed, wav, _duration(mixed))
    add("en-complex", "en", ["noise", "overlapping_speech"], a_cues + b_cues, wav, "amix plus pink-noise")

    def alternating(sample_id: str, first: list[Utterance], second: list[Utterance], labels: list[str]) -> None:
        chosen: list[Utterance] = []
        total = 0.0
        for left, right in zip(first, second):
            chosen.extend([left, right])
            total += left.duration + right.duration
            if total >= 42:
                break
        wav = work / f"{sample_id}.wav"
        cues = _concat(ffmpeg, chosen, wav)
        add(sample_id, "mixed", ["code_switching", *labels], cues, wav, "alternating-language concat")

    alternating("mixed-fr-en", fr[56:], en[32:], ["fr", "en"])
    alternating("mixed-zh-en", zh[0:], en[48:], ["zh", "en"])
    return specs


def _write_manifest(specs: list[dict[str, Any]]) -> None:
    example = PROJECT_ROOT / "tests" / "asr_benchmark" / "manifest.example.json"
    manifest = json.loads(example.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in specs}
    authorization = (
        f"Derived locally from Google FLEURS revision {REVISION}, test split, {LICENSE}; "
        "source rows and deterministic transforms recorded in local provenance.json."
    )
    for sample in manifest["samples"]:
        sample["media"] = f"tests/asr_benchmark/local/{sample['id']}.wav"
        sample["reference_srt"] = f"tests/asr_benchmark/local/{sample['id']}.reference.srt"
        sample["authorization"] = authorization
        sample["acoustic_tags"] = by_id[sample["id"]]["acoustic_tags"]
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute() -> None:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    _ensure_pyarrow()
    shard_paths: dict[str, Path] = {}
    for language, (relative, size) in SHARDS.items():
        target = TMP_ROOT / "shards" / f"{language}.test.parquet"
        url = f"{DATASET_URL}/resolve/{REVISION}/{relative}?download=true"
        print(f"Downloading {language} from pinned FLEURS revision...")
        _download(url, target, size)
        shard_paths[language] = target
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("project FFmpeg is unavailable")
    pools = {language: _extract_utterances(language, path) for language, path in shard_paths.items()}
    specs = _make_samples(pools, Path(ffmpeg))
    provenance = {
        "schema_version": 1,
        "dataset": "google/fleurs",
        "dataset_url": DATASET_URL,
        "revision": REVISION,
        "split": "test",
        "license": LICENSE,
        "shards": {
            language: {"path": relative, "size": size, "sha256": _sha256(shard_paths[language])}
            for language, (relative, size) in SHARDS.items()
        },
        "samples": specs,
    }
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    (LOCAL_ROOT / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_manifest(specs)
    print(f"Prepared {len(specs)} samples in {LOCAL_ROOT}")
    print(f"Manifest: {MANIFEST_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Download and construct the private corpus.")
    args = parser.parse_args()
    total = sum(size for _path, size in SHARDS.values())
    print("Stage 3 corpus download plan")
    print(f"  Source: {DATASET_URL} @ {REVISION}")
    print(f"  License: {LICENSE}")
    print(f"  Download: {total / 1024 / 1024 / 1024:.2f} GiB")
    print(f"  Temporary target: {TMP_ROOT}")
    print(f"  Private corpus target: {LOCAL_ROOT}")
    if not args.execute:
        print("Plan only. Re-run with --execute to download and prepare the corpus.")
        return 0
    execute()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
