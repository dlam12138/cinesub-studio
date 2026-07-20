import json
from pathlib import Path

import pytest
import web_server
from conftest import MemoryTestServer, json_test_handler

from review_detail_api import (
    ReviewDetailError,
    job_review_detail,
    pipeline_review_detail,
    review_detail,
)


def write_report(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "strategy_mode": "wenyi_review",
        "review_items": [
            {"category": "adopted_repair", "id": 1},
            {"category": "random_sample", "id": 2},
            {"category": "random_sample", "id": 3},
        ],
    }), encoding="utf-8")


def test_review_detail_filter_counts_and_pagination(tmp_path):
    output = tmp_path / "output"
    report = output / "movie.wenyi_review_report.json"
    write_report(report)
    page = review_detail(
        report_path=str(report), output_dir=output,
        categories="random_sample", offset=1, limit=1,
    )
    assert page["category_counts"] == {"adopted_repair": 1, "random_sample": 2}
    assert page["total"] == 2
    assert [row["id"] for row in page["review_items"]] == [3]


def test_job_missing_report_and_path_escape_are_rejected(tmp_path):
    output = tmp_path / "output"
    outside = tmp_path / "outside.json"
    write_report(outside)
    with pytest.raises(ReviewDetailError) as missing:
        job_review_detail(job={}, output_dir=output)
    assert missing.value.status == 404
    with pytest.raises(ReviewDetailError) as escaped:
        review_detail(report_path=str(outside), output_dir=output)
    assert escaped.value.status == 403


def test_pipeline_task_validation_and_malformed_report(tmp_path):
    states = tmp_path / "work" / "states"
    output = tmp_path / "output"
    states.mkdir(parents=True)
    report = output / "bad.json"
    report.parent.mkdir(parents=True)
    report.write_text("{bad", encoding="utf-8")
    (states / "movie.state.json").write_text(json.dumps({
        "semantic_review_report": str(report)
    }), encoding="utf-8")
    with pytest.raises(ReviewDetailError) as invalid:
        pipeline_review_detail(
            task_id="../movie", states_dir=states, output_dir=output
        )
    assert invalid.value.status == 400
    with pytest.raises(ReviewDetailError) as malformed:
        pipeline_review_detail(
            task_id="movie", states_dir=states, output_dir=output
        )
    assert malformed.value.status == 422


def test_limit_is_bounded(tmp_path):
    output = tmp_path / "output"
    report = output / "report.json"
    write_report(report)
    with pytest.raises(ReviewDetailError):
        review_detail(report_path=str(report), output_dir=output, limit=101)


def test_read_only_web_routes(monkeypatch, tmp_path):
    output = tmp_path / "output"
    work = tmp_path / "work"
    report = output / "movie.wenyi_review_report.json"
    write_report(report)
    states = work / "states"
    states.mkdir(parents=True)
    (states / "movie.state.json").write_text(json.dumps({
        "semantic_review_report": str(report)
    }), encoding="utf-8")
    monkeypatch.setattr(web_server, "OUTPUT_DIR", output)
    monkeypatch.setattr(web_server, "WORK_DIR", work)
    monkeypatch.setattr(
        web_server,
        "get_job",
        lambda job_id: {"semantic_review_report": str(report)}
        if job_id == "job-1" else None,
    )
    server = MemoryTestServer()
    status, _, payload = json_test_handler(
        server, web_server.Handler,
        method="GET", path="/api/jobs/job-1/review-detail?limit=1",
    )
    assert status == 200
    assert len(payload["review_items"]) == 1
    status, _, payload = json_test_handler(
        server, web_server.Handler,
        method="GET",
        path="/api/pipeline/review-detail?task=movie&categories=random_sample",
    )
    assert status == 200
    assert payload["total"] == 2
