from __future__ import annotations

import json

import web_server


def _report_payload(input_name: str = "movie.mp4") -> dict:
    return {
        "schema_version": 1,
        "report_type": "mixed_language_asr_evidence",
        "generated_at": "2026-07-04T00:00:00+00:00",
        "metadata": {
            "input_name": input_name,
            "input_path": f"D:/media/{input_name}",
            "model": "small",
        },
        "samples": [
            {
                "sample_index": 1,
                "start_seconds": 0,
                "end_seconds": 30,
                "detected_language": "en",
                "language_probability": 0.91,
                "text_preview": "hello",
                "segment_count": 1,
                "error": "",
            }
        ],
        "summary": {
            "mixed_language_likelihood": "none",
            "dominant_language": "en",
            "distinct_detected_languages": ["en"],
            "low_confidence_count": 0,
            "failed_sample_count": 0,
            "limitations": ["This is sampled evidence only."],
        },
    }


def test_asr_evidence_report_listing_reads_valid_reports_only(monkeypatch, tmp_path):
    report_dir = tmp_path / "output" / "reports" / "asr_evidence"
    report_dir.mkdir(parents=True)
    valid = report_dir / "movie.20260704T000000Z.asr_evidence.json"
    valid.write_text(json.dumps(_report_payload(), ensure_ascii=False), encoding="utf-8")
    invalid = report_dir / "broken.asr_evidence.json"
    invalid.write_text("{not json", encoding="utf-8")
    ignored = report_dir / "movie.json"
    ignored.write_text(json.dumps(_report_payload(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(web_server, "ASR_EVIDENCE_DIR", report_dir)

    payload = web_server._asr_evidence_reports()

    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["reports"][0]["file"] == valid.name
    assert payload["reports"][0]["summary"]["dominant_language"] == "en"


def test_asr_evidence_report_loading_rejects_traversal_and_non_report(monkeypatch, tmp_path):
    report_dir = tmp_path / "output" / "reports" / "asr_evidence"
    report_dir.mkdir(parents=True)
    monkeypatch.setattr(web_server, "ASR_EVIDENCE_DIR", report_dir)

    for name in ["../secret.asr_evidence.json", "C:/secret.asr_evidence.json", "movie.json"]:
        payload, status = web_server._asr_evidence_report(name)
        assert status == 404
        assert payload["ok"] is False


def test_asr_evidence_report_loading_returns_valid_report(monkeypatch, tmp_path):
    report_dir = tmp_path / "output" / "reports" / "asr_evidence"
    report_dir.mkdir(parents=True)
    report = report_dir / "movie.20260704T000000Z.asr_evidence.json"
    report.write_text(json.dumps(_report_payload("movie.mp4"), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web_server, "ASR_EVIDENCE_DIR", report_dir)

    payload, status = web_server._asr_evidence_report(report.name)

    assert status == 200
    assert payload["ok"] is True
    assert payload["file"] == report.name
    assert payload["report"]["metadata"]["input_name"] == "movie.mp4"
