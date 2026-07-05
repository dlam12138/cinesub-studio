from __future__ import annotations

from pathlib import Path

from segment_asr_srt_assembler import assemble_routed_srt


def test_assembler_converts_local_timestamps_and_writes_utf8(tmp_path):
    output = tmp_path / "routed.srt"

    metadata = assemble_routed_srt(
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 300.0,
                    "end_seconds": 360.0,
                    "selected_run": "forced-fr",
                    "segments": [{"start": 0.0, "end": 2.5, "text": "Bonjour été."}],
                }
            ]
        },
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert "00:05:00,000 --> 00:05:02,500" in text
    assert "Bonjour été." in text
    assert metadata["cue_count"] == 1
    assert metadata["selected_run_counts"] == {"forced-fr": 1}


def test_assembler_sorts_and_renumbers_cues(tmp_path):
    output = tmp_path / "routed.srt"

    assemble_routed_srt(
        {
            "windows": [
                {
                    "window_index": 2,
                    "start_seconds": 10.0,
                    "end_seconds": 20.0,
                    "selected_run": "auto",
                    "segments": [{"start": 1.0, "end": 2.0, "text": "Second"}],
                },
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 10.0,
                    "selected_run": "forced-en",
                    "segments": [{"start": 1.0, "end": 2.0, "text": "First"}],
                },
            ]
        },
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert text.index("1\n00:00:01,000") < text.index("2\n00:00:11,000")
    assert text.index("First") < text.index("Second")


def test_assembler_drops_empty_and_invalid_segments(tmp_path):
    output = tmp_path / "routed.srt"

    metadata = assemble_routed_srt(
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 10.0,
                    "selected_run": "auto",
                    "segments": [
                        {"start": 0.0, "end": 1.0, "text": "   "},
                        {"start": 2.0, "end": 2.0, "text": "bad"},
                        {"start": "nan", "end": 3.0, "text": "bad"},
                        {"start": 4.0, "end": 5.0, "text": "Good"},
                    ],
                }
            ]
        },
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert "Good" in text
    assert "bad" not in text
    assert metadata["cue_count"] == 1
    assert metadata["dropped_segment_count"] == 3


def test_assembler_adjusts_overlaps_conservatively(tmp_path):
    output = tmp_path / "routed.srt"

    metadata = assemble_routed_srt(
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 10.0,
                    "selected_run": "auto",
                    "segments": [
                        {"start": 0.0, "end": 3.0, "text": "A"},
                        {"start": 2.0, "end": 4.0, "text": "B"},
                    ],
                }
            ]
        },
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert "00:00:03,001 --> 00:00:04,000" in text
    assert metadata["adjusted_overlap_count"] == 1


def test_assembler_supports_global_timestamps_and_atomic_replacement(tmp_path):
    output = tmp_path / "routed.srt"
    output.write_text("old", encoding="utf-8")

    metadata = assemble_routed_srt(
        {
            "windows": [
                {
                    "window_index": 1,
                    "start_seconds": 100.0,
                    "end_seconds": 120.0,
                    "timestamp_scope": "global",
                    "selected_run": "auto",
                    "segments": [{"start": 5.0, "end": 6.0, "text": "Global"}],
                }
            ]
        },
        output,
    )

    assert output.read_text(encoding="utf-8").startswith("1\n00:00:05,000")
    assert not Path(f"{output}.tmp").exists()
    assert metadata["cue_count"] == 1
