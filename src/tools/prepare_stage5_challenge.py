"""Download and inspect the pinned CAFE-small Stage 5 challenge corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from runtime_paths import resolve_runtime_paths


PATHS = resolve_runtime_paths()
PROJECT_ROOT = PATHS.project_root
RECORD_ID = "16964503"
VERSION = "v2.0.0"
LICENSE = "CC BY 4.0"
FILE_NAME = "cafe-small.zip"
FILE_SIZE = 320_908_945
FILE_MD5 = "c03ab09435ab8cd95e62c38c9c72dbd1"
DOWNLOAD_URL = f"https://zenodo.org/api/records/{RECORD_ID}/files/{FILE_NAME}/content"
TMP_ROOT = PROJECT_ROOT / ".tmp" / "stage5-challenge"
LOCAL_ROOT = PROJECT_ROOT / "tests" / "asr_challenge" / "local"
CORPUS_ROOT = "cafe-small"

# Fixed after inspecting the v2.0.0 ZAEBUC annotations.  Labels below only
# describe facts present in the published text/event markup; overlap remains a
# separate manual-review gate because this release has no speaker timestamps.
SAMPLE_SPECS = (
    ("cafe-fr-heavy-01", "chunk8_1.wav", ("french_heavy", "code_switching")),
    ("cafe-fr-heavy-02", "chunk1_5.wav", ("french_heavy", "code_switching")),
    ("cafe-fr-heavy-03", "chunk1_0.wav", ("french_heavy", "code_switching")),
    ("cafe-en-heavy-01", "chunk16v2_3.wav", ("english_heavy", "code_switching")),
    ("cafe-en-heavy-02", "chunk16v2_4.wav", ("english_heavy", "code_switching")),
    ("cafe-mixed-01", "chunk20_5.wav", ("natural_code_switching", "laughter")),
    ("cafe-mixed-02", "chunk28v2_2.wav", ("natural_code_switching", "noise")),
    ("cafe-mixed-03", "chunk2_5.wav", ("natural_code_switching",)),
    ("cafe-noise-01", "chunk9v2_0.wav", ("environmental_noise", "code_switching")),
    ("cafe-noise-02", "chunk20_3.wav", ("environmental_noise", "english_heavy")),
    ("cafe-laugh-01", "chunk18v2_2.wav", ("laughter",)),
    ("cafe-laugh-02", "chunk8_2.wav", ("laughter", "typing", "french_heavy")),
)


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _download() -> Path:
    target = TMP_ROOT / FILE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".zip.partial")
    if target.is_file() and target.stat().st_size == FILE_SIZE and _md5(target) == FILE_MD5:
        return target
    total = partial.stat().st_size if partial.exists() else 0
    failures = 0
    while total < FILE_SIZE:
        headers = {"User-Agent": "CineSub-Stage5-Challenge/1"}
        if total:
            headers["Range"] = f"bytes={total}-"
        request = urllib.request.Request(DOWNLOAD_URL, headers=headers)
        before = total
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status = getattr(response, "status", response.getcode())
                if total and status != 206:
                    partial.unlink(missing_ok=True)
                    total = 0
                    continue
                with partial.open("ab" if total else "wb") as output:
                    while total < FILE_SIZE:
                        chunk = response.read(min(4 * 1024 * 1024, FILE_SIZE - total))
                        if not chunk:
                            break
                        output.write(chunk)
                        total += len(chunk)
                        print(f"  {total / 1024 / 1024:.1f} MiB", end="\r", flush=True)
            failures = 0
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            failures += 1
            if failures >= 6:
                raise RuntimeError(f"CAFE download failed after {failures} retries: {exc}") from exc
            delay = min(30, 2 ** failures)
            print(f"\n  transient download error; retrying in {delay}s ({failures}/6)")
            time.sleep(delay)
            continue
        if total == before:
            raise RuntimeError(f"download made no progress at byte {total}")
    print()
    if partial.stat().st_size != FILE_SIZE or _md5(partial) != FILE_MD5:
        raise RuntimeError("CAFE-small size or MD5 verification failed")
    os.replace(partial, target)
    return target


def _safe_extract(archive_path: Path) -> Path:
    target = TMP_ROOT / "extracted"
    marker = target / ".complete.json"
    if marker.is_file():
        return target
    staging = TMP_ROOT / "extracting"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        root = staging.resolve()
        for member in archive.infolist():
            destination = (root / member.filename).resolve()
            if not destination.is_relative_to(root):
                raise RuntimeError(f"unsafe ZIP member: {member.filename}")
        archive.extractall(staging)
    if target.exists():
        shutil.rmtree(target)
    staging.replace(target)
    marker.write_text(
        json.dumps(
            {"record_id": RECORD_ID, "version": VERSION, "license": LICENSE, "file": FILE_NAME, "md5": FILE_MD5},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    return target


def _load_annotations(corpus: Path) -> dict[str, dict[str, object]]:
    annotation_path = corpus / "transcripts_ZAEBUC" / "cafe-small-zaebuc-annotation.json"
    entries = json.loads(annotation_path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, object]] = {}
    for entry in entries:
        filename = Path(str(entry.get("filename") or "")).name
        if filename:
            result[filename] = entry
    return result


def _build_challenge(extracted: Path) -> dict[str, object]:
    corpus = extracted / CORPUS_ROOT
    annotations = _load_annotations(corpus)
    audio_target = LOCAL_ROOT / "audio"
    transcript_target = LOCAL_ROOT / "references"
    audio_target.mkdir(parents=True, exist_ok=True)
    transcript_target.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, object]] = []
    for sample_id, filename, labels in SAMPLE_SPECS:
        source = corpus / "audio" / filename
        annotation = annotations.get(filename)
        if not source.is_file() or annotation is None:
            raise RuntimeError(f"Pinned CAFE sample is missing: {filename}")
        text = str(annotation.get("zaebuc_transcription") or "").strip()
        if not text:
            raise RuntimeError(f"Pinned CAFE transcript is empty: {filename}")
        duration = _wav_duration(source)
        if not 30 <= duration <= 90:
            raise RuntimeError(f"Pinned CAFE sample duration is outside 30-90 seconds: {filename}")
        audio_name = f"{sample_id}.wav"
        srt_name = f"{sample_id}.srt"
        audio_path = audio_target / audio_name
        srt_path = transcript_target / srt_name
        shutil.copyfile(source, audio_path)
        srt_path.write_text(
            f"1\n00:00:00,000 --> {_srt_timestamp(duration)}\n{text}\n",
            encoding="utf-8",
        )
        samples.append(
            {
                "id": sample_id,
                "media": f"tests/asr_challenge/local/audio/{audio_name}",
                "language": "mixed",
                "acoustic_tags": list(labels),
                "authorization": (
                    f"CAFE-small Zenodo {RECORD_ID} revision {VERSION}, {LICENSE}; "
                    "private local evaluation only."
                ),
                "source_filename": filename,
                "source_row_id": filename.removesuffix(".wav"),
                "audio": f"audio/{audio_name}",
                "reference_srt": f"tests/asr_challenge/local/references/{srt_name}",
                "duration_seconds": round(duration, 3),
                "labels": list(labels),
                "dialectness": annotation.get("dialectness"),
                "source_audio_sha256": _sha256(source),
                "reference_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "corpus": "CAFE-small",
        "routing_config_id": "large-v3-cuda-float16",
        "configurations": [
            {
                "id": "large-v3-cuda-float16",
                "model": "large-v3",
                "device": "cuda",
                "compute_type": "float16",
                "beam_size": 5,
                "vad_filter": True,
                "condition_on_previous_text": True,
            }
        ],
        "authorization": {
            "zenodo_record_id": RECORD_ID,
            "revision": VERSION,
            "license": LICENSE,
            "archive_file": FILE_NAME,
            "archive_bytes": FILE_SIZE,
            "archive_md5": FILE_MD5,
            "derived_use": "private local ASR evaluation; redistribution excluded",
        },
        "selection": {
            "method": "fixed IDs selected from published ZAEBUC annotations",
            "duration_range_seconds": [30, 90],
            "sample_count": len(samples),
            "limitations": [
                "CAFE-small primarily evaluates Algerian Arabic with natural French/English switching.",
                "The release has clip-level transcripts but no speaker-timed overlap labels.",
                "Overlap coverage requires manual listening and remains unverified.",
            ],
        },
        "samples": samples,
    }
    fingerprint_input = json.dumps(samples, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["corpus_fingerprint"] = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    (LOCAL_ROOT / "manifest.local.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def execute() -> None:
    archive = _download()
    extracted = _safe_extract(archive)
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    inventory = []
    for path in extracted.rglob("*"):
        if path.is_file():
            inventory.append({"path": path.relative_to(extracted).as_posix(), "bytes": path.stat().st_size})
    (LOCAL_ROOT / "source_inventory.json").write_text(
        json.dumps(
            {
                "schema_version": 1, "record_id": RECORD_ID, "version": VERSION,
                "license": LICENSE, "archive_md5": FILE_MD5, "files": inventory,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    manifest = _build_challenge(extracted)
    print(f"Verified and extracted {len(inventory)} files to {extracted}")
    print(f"Built {len(manifest['samples'])} fixed private samples; fingerprint {manifest['corpus_fingerprint']}")
    print("Overlap remains an explicit manual-review gate; it is not inferred from clip names.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    print("Stage 5 CAFE-small download plan")
    print(f"  Record: Zenodo {RECORD_ID} / {VERSION}")
    print(f"  License: {LICENSE}")
    print(f"  File: {FILE_NAME} ({FILE_SIZE / 1024 / 1024:.1f} MiB)")
    print(f"  MD5: {FILE_MD5}")
    print(f"  Temporary target: {TMP_ROOT}")
    print(f"  Private challenge target: {LOCAL_ROOT}")
    if not args.execute:
        print("Plan only. Re-run with --execute to download and inspect the corpus.")
        return 0
    execute()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
