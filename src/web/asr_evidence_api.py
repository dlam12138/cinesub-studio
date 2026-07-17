from __future__ import annotations

import json
from pathlib import Path


def _valid_report(data: object) -> bool:
    return bool(
        isinstance(data, dict)
        and data.get("report_type") == "mixed_language_asr_evidence"
        and isinstance(data.get("metadata"), dict)
        and isinstance(data.get("summary"), dict)
        and isinstance(data.get("samples"), list)
    )


def list_asr_evidence_reports(report_dir: Path, format_bytes) -> dict:
    reports: list[dict] = []
    if report_dir.exists():
        candidates = sorted(report_dir.glob("*.asr_evidence.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                stat = path.stat()
            except (OSError, json.JSONDecodeError):
                continue
            if not _valid_report(data):
                continue
            metadata = data["metadata"]
            summary = data["summary"]
            reports.append({
                "file": path.name, "path": str(path.resolve()), "bytes": stat.st_size,
                "display_size": format_bytes(stat.st_size), "generated_at": data.get("generated_at", ""),
                "input_name": metadata.get("input_name", ""), "input_path": metadata.get("input_path", ""),
                "model": metadata.get("model", ""),
                "summary": {
                    "mixed_language_likelihood": summary.get("mixed_language_likelihood", "none"),
                    "dominant_language": summary.get("dominant_language", ""),
                    "distinct_detected_languages": summary.get("distinct_detected_languages", []),
                    "low_confidence_count": summary.get("low_confidence_count", 0),
                    "failed_sample_count": summary.get("failed_sample_count", 0),
                },
            })
    return {"ok": True, "directory": str(report_dir.resolve()), "reports": reports, "count": len(reports)}


def get_asr_evidence_report(report_dir: Path, file_name: str) -> tuple[dict, int]:
    name = str(file_name or "").strip()
    if not name or Path(name).is_absolute() or Path(name).name != name or "/" in name or "\\" in name:
        return {"ok": False, "error": "Invalid report file name"}, 404
    if not name.endswith(".asr_evidence.json"):
        return {"ok": False, "error": "Unsupported ASR evidence report file"}, 404
    try:
        base = report_dir.resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.stat().st_size <= 0:
            return {"ok": False, "error": "ASR evidence report not found"}, 404
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Could not read ASR evidence report: {exc}"}, 400
    if not _valid_report(data):
        return {"ok": False, "error": "Invalid ASR evidence report."}, 400
    return {"ok": True, "file": path.name, "report": data}, 200
