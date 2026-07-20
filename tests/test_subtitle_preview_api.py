from pathlib import Path

import pytest

from subtitle_preview_api import (
    MAX_PREVIEW_LIMIT,
    SubtitlePreviewError,
    job_subtitle_preview,
    pipeline_subtitle_preview,
    preview_srt,
)


def _srt(path: Path, count: int = 3, *, bom: bool = False) -> Path:
    blocks = []
    for index in range(1, count + 1):
        blocks.append(
            f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},900\n"
            f"source {index}\n译文 {index}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8-sig" if bom else "utf-8")
    return path


def test_preview_srt_returns_bounded_paginated_cues_without_paths(tmp_path):
    path = _srt(tmp_path / "sample.srt", count=4)

    payload = preview_srt(path, artifact="bilingual", offset="1", limit="2")

    assert payload == {
        "ok": True,
        "artifact": "bilingual",
        "offset": 1,
        "limit": 2,
        "total": 4,
        "has_more": True,
        "cues": [
            {"index": 2, "start": "00:00:02,000", "end": "00:00:02,900", "text_lines": ["source 2", "译文 2"]},
            {"index": 3, "start": "00:00:03,000", "end": "00:00:03,900", "text_lines": ["source 3", "译文 3"]},
        ],
    }
    assert str(path) not in str(payload)


def test_preview_limit_is_capped_and_bom_is_supported(tmp_path):
    path = _srt(tmp_path / "bom.srt", count=2, bom=True)

    payload = preview_srt(path, artifact="source", limit=999)

    assert payload["limit"] == MAX_PREVIEW_LIMIT
    assert payload["total"] == 2


@pytest.mark.parametrize(("offset", "limit", "message"), [
    ("bad", 10, "offset must be an integer"),
    (-1, 10, "offset must not be negative"),
    (0, 0, "limit must be greater than zero"),
])
def test_preview_rejects_invalid_pagination(tmp_path, offset, limit, message):
    path = _srt(tmp_path / "sample.srt")

    with pytest.raises(SubtitlePreviewError, match=message):
        preview_srt(path, artifact="source", offset=offset, limit=limit)


def test_preview_rejects_empty_non_srt_and_malformed_files(tmp_path):
    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")
    assert preview_srt(empty, artifact="source")["cues"] == []

    text = tmp_path / "sample.txt"
    text.write_text("not srt", encoding="utf-8")
    with pytest.raises(SubtitlePreviewError, match="not found"):
        preview_srt(text, artifact="source")

    malformed = tmp_path / "malformed.srt"
    malformed.write_text("this is not an srt", encoding="utf-8")
    with pytest.raises(SubtitlePreviewError, match="malformed"):
        preview_srt(malformed, artifact="source")


def test_preview_rejects_invalid_timeline(tmp_path):
    path = tmp_path / "bad-time.srt"
    path.write_text("1\nnot a timeline\nhello\n", encoding="utf-8")

    with pytest.raises(SubtitlePreviewError, match="invalid time line"):
        preview_srt(path, artifact="source")


def test_pipeline_preview_uses_existing_artifact_resolver(tmp_path):
    path = _srt(tmp_path / "pipeline.srt")
    calls = []

    def resolver(task_id, artifact):
        calls.append((task_id, artifact))
        return path, ""

    payload = pipeline_subtitle_preview(
        task_id="movie", artifact="source", offset=0, limit=1, resolver=resolver
    )

    assert calls == [("movie", "source")]
    assert payload["task_id"] == "movie"
    assert len(payload["cues"]) == 1


def test_pipeline_preview_rejects_quality_reports_before_resolving():
    with pytest.raises(SubtitlePreviewError, match="Unknown subtitle artifact"):
        pipeline_subtitle_preview(
            task_id="movie",
            artifact="quality_report",
            offset=0,
            limit=10,
            resolver=lambda *_: (_ for _ in ()).throw(AssertionError("must not resolve")),
        )


def test_job_preview_accepts_only_metadata_paths_inside_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    path = _srt(output / "job.srt")
    payload = job_subtitle_preview(
        job_id="job-1",
        artifact="translated",
        offset=0,
        limit=2,
        job={"translated_output": str(path)},
        output_dir=output,
    )
    assert payload["job_id"] == "job-1"

    outside = _srt(tmp_path / "outside.srt")
    with pytest.raises(SubtitlePreviewError, match="not found"):
        job_subtitle_preview(
            job_id="job-1",
            artifact="translated",
            offset=0,
            limit=2,
            job={"translated_output": str(outside)},
            output_dir=output,
        )


def test_job_preview_rejects_unknown_job_and_artifact(tmp_path):
    with pytest.raises(SubtitlePreviewError, match="Job not found"):
        job_subtitle_preview(
            job_id="missing", artifact="source", offset=0, limit=10, job=None, output_dir=tmp_path
        )
    with pytest.raises(SubtitlePreviewError, match="Unknown subtitle artifact"):
        job_subtitle_preview(
            job_id="job", artifact="quality_report", offset=0, limit=10, job={}, output_dir=tmp_path
        )


def test_web_server_routes_preview_before_generic_job_route():
    source = Path("src/web/web_server.py").read_text(encoding="utf-8")
    assert source.index('endswith("/preview")') < source.index('if parsed.path.startswith("/api/jobs/"):\n')
    assert 'if parsed.path == "/api/pipeline/preview":' in source
