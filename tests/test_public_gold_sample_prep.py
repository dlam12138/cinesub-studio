from __future__ import annotations

import hashlib
import io
import json
import wave
import zipfile
from pathlib import Path

import pytest
from public_gold_sample_prep import (
    TOOL_VERSION,
    ArchiveReader,
    build_bundles,
    inspect_archive,
    prepare_summre,
    select_bundles,
)


def _wav_bytes(seconds: float = 3.0, amplitude: int = 1000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        sample = int(amplitude).to_bytes(2, "little", signed=True)
        handle.writeframes(sample * round(seconds * 16000))
    return output.getvalue()


def _archive(path: Path, *, groups: int = 3, clips_per_group: int = 6) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for group in range(1, groups + 1):
            for clip in range(1, clips_per_group + 1):
                stem = f"source-{group:02d}/clip-{clip:03d}"
                archive.writestr(
                    f"{stem}.wav",
                    _wav_bytes(amplitude=200 * group + clip),
                )
                archive.writestr(
                    f"{stem}.txt",
                    f"transcription humaine groupe {group} extrait {clip}",
                )
    return path


def test_archive_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.wav", _wav_bytes())
        archive.writestr("../escape.txt", "texte")

    with pytest.raises(ValueError, match="unsafe member path"):
        inspect_archive(archive_path, tmp_path / "discovery.json")


def test_audio_transcript_pairing_and_real_source_groups_are_stable(tmp_path: Path) -> None:
    archive_path = _archive(tmp_path / "fixture.zip")
    first = inspect_archive(archive_path, tmp_path / "first.json")
    repeated = inspect_archive(archive_path, tmp_path / "second.json")

    assert first == repeated
    assert first["pairing_rule"] == "same_relative_stem"
    assert first["source_group_rule"] == ["audio_parent_directory"]
    assert first["candidate_group_count"] == 3
    assert first["candidate_count"] == 18
    assert {row["source_group_id"] for row in first["candidates"]} == {
        "source-01", "source-02", "source-03"
    }


def test_unreliable_source_group_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "flat.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name in ("alpha", "bravo", "charlie"):
            archive.writestr(f"{name}.wav", _wav_bytes())
            archive.writestr(f"{name}.txt", "transcription humaine")

    with pytest.raises(ValueError, match="reliable parent or stable filename"):
        inspect_archive(archive_path, tmp_path / "discovery.json")
    discovery = json.loads(
        (tmp_path / "discovery.json").read_text(encoding="utf-8")
    )
    assert discovery["source_group_reliable"] is False
    assert discovery["candidate_group_count"] == 0


def test_selection_is_deterministic_and_identity_binds_archive_and_tool() -> None:
    candidates = []
    for group in range(3):
        for clip in range(6):
            digest = hashlib.sha256(f"{group}:{clip}".encode()).hexdigest()
            candidates.append({
                "audio_member": f"group-{group}/clip-{clip}.wav",
                "transcript_member": f"group-{group}/clip-{clip}.txt",
                "transcript": f"texte {group} {clip}",
                "source_group_id": f"group-{group}",
                "duration_seconds": 3.0,
                "rms": 0.01 * (group + 1) + clip / 1000,
                "word_count": 3 + clip,
                "words_per_second": (3 + clip) / 3,
                "audio_sha256": digest,
            })
    discovery = {
        "archive_local_download_sha256": "a" * 64,
        "candidates": candidates,
    }
    kwargs = {"min_duration": 5, "max_duration": 8}

    first = select_bundles(discovery, **kwargs)
    repeated = select_bundles(discovery, **kwargs)
    changed_archive = select_bundles(
        {**discovery, "archive_local_download_sha256": "b" * 64},
        **kwargs,
    )

    assert first == repeated
    assert first["selection_identity"] != changed_archive["selection_identity"]
    assert first["tool_version"] == TOOL_VERSION
    assert first["model_results_read"] is False
    assert len({sample["source_group_id"] for sample in first["samples"]}) >= 3
    selected_hashes = [
        clip["audio_sha256"]
        for sample in first["samples"]
        for clip in sample["clips"]
    ]
    assert len(selected_hashes) == len(set(selected_hashes))


def _parse_srt_times(path: Path) -> list[tuple[int, int]]:
    rows = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        timestamp = block.splitlines()[1]
        start, end = timestamp.split(" --> ")

        def milliseconds(value: str) -> int:
            hours, minutes, rest = value.split(":")
            seconds, millis = rest.split(",")
            return (
                int(hours) * 3_600_000
                + int(minutes) * 60_000
                + int(seconds) * 1000
                + int(millis)
            )

        rows.append((milliseconds(start), milliseconds(end)))
    return rows


def test_bundle_duration_timeline_manifest_and_private_clip_audit(tmp_path: Path) -> None:
    archive_path = _archive(tmp_path / "fixture.zip")
    discovery = inspect_archive(archive_path, tmp_path / "discovery.json")
    selection = select_bundles(discovery, min_duration=5, max_duration=8)
    output_dir = tmp_path / "prepared"

    manifest = build_bundles(
        archive_path,
        discovery,
        selection,
        output_dir,
    )

    assert manifest["campaign_scope"] == "stage3a_public_gold"
    assert len(manifest["samples"]) == 6
    assert all(row["reference_type"] == "gold_verbatim" for row in manifest["samples"])
    assert len({row["source_group_id"] for row in manifest["samples"]}) >= 3
    seen = set()
    for sample in manifest["samples"]:
        assert 5 <= sample["end"] <= 8
        wav_path = output_dir / sample["media_path"]
        with wave.open(str(wav_path), "rb") as handle:
            assert (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) == (
                16000, 1, 2
            )
        intervals = _parse_srt_times(output_dir / sample["reference_path"])
        assert all(end > start for start, end in intervals)
        assert all(
            intervals[index][0] >= intervals[index - 1][1]
            for index in range(1, len(intervals))
        )
        audit = json.loads((output_dir / sample["clips_path"]).read_text(encoding="utf-8"))
        for clip in audit["clips"]:
            assert clip["audio_sha256"] not in seen
            seen.add(clip["audio_sha256"])


def test_archive_reader_never_extracts_members_implicitly(tmp_path: Path) -> None:
    archive_path = _archive(tmp_path / "fixture.zip")
    with ArchiveReader(archive_path) as reader:
        assert reader.read("source-01/clip-001.txt").decode() == (
            "transcription humaine groupe 1 extrait 1"
        )
    assert not (tmp_path / "source-01").exists()


def test_summre_fallback_streams_only_dev_test_and_uses_real_meeting_ids(
    tmp_path: Path, monkeypatch
) -> None:
    records = []
    for meeting in range(1, 4):
        for speaker in range(1, 3):
            records.append({
                "meeting_id": f"meeting-{meeting:02d}",
                "speaker_id": f"speaker-{speaker:02d}",
                "audio_id": f"meeting-{meeting:02d}-speaker-{speaker:02d}",
                "audio": {"bytes": _wav_bytes(seconds=7)},
                "segments": [
                    {"start": 0.0, "end": 3.0, "transcript": "bonjour à tous"},
                    {"start": 3.1, "end": 6.1, "transcript": "suite de réunion"},
                ],
            })

    calls = []

    def fake_records(split, shard_index):
        calls.append((split, shard_index))
        if shard_index:
            return []
        return [
            {**row, "split": split, "shard_index": 0, "row_index": index}
            for index, row in enumerate(records)
        ]

    def fake_audio(split, shard_index, selected_row_indexes):
        assert split == "dev"
        assert shard_index == 0
        return {
            index: records[index]["audio"]["bytes"]
            for index in selected_row_indexes
        }

    monkeypatch.setattr(
        "public_gold_sample_prep._summre_shard_records", fake_records
    )
    monkeypatch.setattr(
        "public_gold_sample_prep._summre_shard_audio", fake_audio
    )

    manifest = prepare_summre(
        tmp_path / "prepared",
        splits=("dev", "test"),
        min_duration=5,
        max_duration=8,
    )

    assert calls[0] == ("dev", 0)
    assert all(call[0] in {"dev", "test"} for call in calls)
    assert all(call[0] != "train" for call in calls)
    assert manifest["dataset"] == "SUMM-RE"
    assert manifest["dataset_license"] == "CC BY-SA 4.0"
    assert len(manifest["samples"]) == 6
    assert len({row["source_group_id"] for row in manifest["samples"]}) == 3
    selection = json.loads(
        (tmp_path / "prepared" / "public-gold-selection.local.json").read_text(
            encoding="utf-8"
        )
    )
    assert selection["streaming"] is True
    assert selection["streaming_transport"] == "parquet_http_range"
    assert selection["train_used"] is False
    assert selection["model_results_read"] is False
