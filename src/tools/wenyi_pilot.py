"""Run and evaluate the authorized three-sample WenYi pre-release pilot.

Provider secrets are loaded through provider_store and are never serialized or
printed. Outputs live under the ignored work/regression-6/wenyi-pilot tree.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGRESSION_ROOT = PROJECT_ROOT / "work" / "regression-6"
PILOT_ROOT = REGRESSION_ROOT / "wenyi-pilot"
STATUS_PATH = PILOT_ROOT / "pilot-status.json"
AUTOMATIC_PATH = PILOT_ROOT / "automatic-evaluation.json"
BLIND_MANIFEST_PATH = PILOT_ROOT / "blind-manifest.json"
DEFAULT_LIMIT = 200

SAMPLES = (
    {
        "id": "1049912620-1-208",
        "source": REGRESSION_ROOT
        / "1049912620-1-208"
        / "asr"
        / "1049912620-1-208.large-v3.srt",
        "baseline": REGRESSION_ROOT
        / "1049912620-1-208"
        / "translation"
        / "1049912620-1-208.semantic-review.flash-pro.bilingual.zh-CN.srt",
        "baseline_budget": 2,
    },
    {
        "id": "39998393486-1-192",
        "source": REGRESSION_ROOT
        / "39998393486-1-192"
        / "asr"
        / "39998393486-1-192.large-v3.srt",
        "baseline": REGRESSION_ROOT
        / "39998393486-1-192"
        / "translation"
        / "39998393486-1-192.semantic-review.flash-pro.bilingual.zh-CN.srt",
        "baseline_budget": 10,
    },
    {
        "id": "899379112-1-208",
        "source": REGRESSION_ROOT
        / "899379112-1-208"
        / "asr"
        / "899379112-1-208.large-v3.srt",
        "baseline": REGRESSION_ROOT
        / "899379112-1-208"
        / "translation"
        / "899379112-1-208.semantic-review.flash-pro.bilingual.zh-CN.srt",
        "baseline_budget": 0,
    },
)

REQUIRED_BLIND_IDS = {
    "1049912620-1-208": [12, 57],
    "39998393486-1-192": [],
    "899379112-1-208": [93, 150, 155],
}

KNOWN_CHECKS = (
    ("899379112-1-208", 93, "language_action_preserved", ("提问", "请求", "问"), ()),
    ("899379112-1-208", 150, "biberon_150", ("奶瓶",), ()),
    ("899379112-1-208", 155, "biberon_155", ("奶瓶",), ()),
    ("1049912620-1-208", 12, "fans_preserved", ("粉丝",), ()),
    ("1049912620-1-208", 57, "no_invented_endurance", (), ("熬",)),
)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_status(limit: int) -> dict[str, Any]:
    if STATUS_PATH.is_file():
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if int(data.get("request_limit", -1)) != limit:
            raise ValueError(
                "existing pilot status uses a different request limit; "
                "do not reset paid-request accounting"
            )
        return data
    return {
        "schema_version": 1,
        "authorized": True,
        "request_limit": limit,
        "requests_used": 0,
        "status": "pending",
        "samples": {},
    }


def _output_paths(sample_id: str) -> tuple[Path, Path]:
    sample_root = PILOT_ROOT / sample_id
    output = sample_root / (
        f"{sample_id}.wenyi-review.flash-pro.bilingual.zh-CN.srt"
    )
    report = output.with_name(f"{output.stem}.wenyi_review_report.json")
    return output, report


def _public_provider() -> dict[str, Any]:
    from provider_store import get_active_provider

    provider = get_active_provider() or {}
    required = (
        "protocol",
        "api_base",
        "api_key",
        "translation_model",
        "translation_quality_model",
    )
    missing = [field for field in required if not str(provider.get(field) or "").strip()]
    if missing:
        raise ValueError(
            "active provider is incomplete for WenYi pilot: " + ", ".join(missing)
        )
    return provider


def run(limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    from language_profile_store import resolve_language_profile_config
    from subtitle_translate import translate_srt
    from translation_reliability import TranslationTotalRequestLimitExceeded

    if limit < 1:
        raise ValueError("request limit must be positive")
    status = _read_status(limit)
    provider = _public_provider()
    profile = resolve_language_profile_config()
    quality = profile.get("quality", {}) or {}
    for sample in SAMPLES:
        sample_id = str(sample["id"])
        previous = status["samples"].get(sample_id, {})
        if previous.get("status") == "completed":
            continue
        remaining = limit - int(status["requests_used"])
        if remaining <= 0:
            status["status"] = "inconclusive"
            status["reason"] = "total_request_limit_exhausted"
            _atomic_json(STATUS_PATH, status)
            break
        source = Path(sample["source"])
        baseline = Path(sample["baseline"])
        if not source.is_file() or not baseline.is_file():
            raise FileNotFoundError(f"{sample_id}: source or baseline is missing")
        output, report = _output_paths(sample_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        status["status"] = "running"
        status["active_sample"] = sample_id
        _atomic_json(STATUS_PATH, status)
        try:
            summary = translate_srt(
                input_path=source,
                output_path=output,
                api_provider=str(provider["protocol"]),
                api_base=str(provider["api_base"]),
                api_key=str(provider["api_key"]),
                llm_model=str(provider["translation_model"]),
                translation_quality_model=str(provider["translation_quality_model"]),
                target_language=str(profile.get("target_language") or "zh-CN"),
                batch_size=20,
                temperature=0.2,
                translation_mode="bilingual",
                system_prompt=str(profile.get("translation_style") or ""),
                context_window=3,
                translation_strategy_mode="wenyi_review",
                scene_gap_seconds=30.0,
                max_cps_zh=float(quality.get("max_cps_zh", 8)),
                max_chars_per_subtitle_zh=int(
                    quality.get("max_chars_per_subtitle_zh", 36)
                ),
                profile_glossary=profile.get("glossary", []),
                max_http_requests=remaining,
            )
        except TranslationTotalRequestLimitExceeded:
            status["requests_used"] = limit
            status["samples"][sample_id] = {
                "status": "inconclusive",
                "reason": "total_request_limit_exhausted",
                "output": str(output),
                "report": str(report),
            }
            status["status"] = "inconclusive"
            status["reason"] = "total_request_limit_exhausted"
            status.pop("active_sample", None)
            _atomic_json(STATUS_PATH, status)
            break
        except Exception as exc:
            status["samples"][sample_id] = {
                "status": "inconclusive",
                "reason": type(exc).__name__,
                "error": str(exc)[:1000],
                "output": str(output),
                "report": str(report),
            }
            status["status"] = "inconclusive"
            status["reason"] = f"{sample_id}:{type(exc).__name__}"
            status.pop("active_sample", None)
            _atomic_json(STATUS_PATH, status)
            raise
        used = int(summary.actual_requests)
        status["requests_used"] = int(status["requests_used"]) + used
        status["samples"][sample_id] = {
            "status": "completed",
            "requests": used,
            "output": str(output),
            "report": str(report),
            "summary": summary.safe_summary(),
        }
        status.pop("active_sample", None)
        _atomic_json(STATUS_PATH, status)
    if all(
        status["samples"].get(str(sample["id"]), {}).get("status") == "completed"
        for sample in SAMPLES
    ):
        status["status"] = "completed"
        status.pop("reason", None)
        _atomic_json(STATUS_PATH, status)
    print(json.dumps({
        "status": status["status"],
        "requests_used": status["requests_used"],
        "request_limit": status["request_limit"],
        "samples": {
            sample_id: row.get("status")
            for sample_id, row in status["samples"].items()
        },
    }, ensure_ascii=False))
    return status


def _read_srt(path: Path) -> list[tuple[int, str, str]]:
    import re

    rows: list[tuple[int, str, str]] = []
    raw = path.read_text(encoding="utf-8-sig").strip()
    for block in re.split(r"\n\s*\n", raw):
        lines = block.splitlines()
        if len(lines) >= 3:
            rows.append((int(lines[0]), lines[1], "\n".join(lines[2:]).strip()))
    return rows


def _targets(
    source: list[tuple[int, str, str]],
    rendered: list[tuple[int, str, str]],
) -> dict[int, str]:
    source_by_id = {item_id: text for item_id, _, text in source}
    result: dict[int, str] = {}
    for item_id, _, text in rendered:
        prefix = source_by_id[item_id].strip() + "\n"
        result[item_id] = (
            text[len(prefix):].strip() if text.startswith(prefix) else text.strip()
        )
    return result


def evaluate() -> dict[str, Any]:
    status = _read_status(DEFAULT_LIMIT)
    samples: dict[str, Any] = {}
    translations: dict[str, dict[int, str]] = {}
    total_budget = 0
    proof_gate = True
    stage_errors: list[dict[str, Any]] = []
    for sample in SAMPLES:
        sample_id = str(sample["id"])
        output, report_path = _output_paths(sample_id)
        source = _read_srt(Path(sample["source"]))
        rendered = _read_srt(output) if output.is_file() else []
        source_ids = [row[0] for row in source]
        output_ids = [row[0] for row in rendered]
        source_times = [row[1] for row in source]
        output_times = [row[1] for row in rendered]
        target = _targets(source, rendered) if rendered else {}
        translations[sample_id] = target
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.is_file()
            else {}
        )
        budget_ids = [int(value) for value in report.get("budget_violation_ids", [])]
        total_budget += len(budget_ids)
        sample_proof = True
        for item_id, source_name in report.get("final_sources", {}).items():
            if source_name == "repair":
                judgment = report.get("repair_judgments", {}).get(str(item_id), {})
                mapping = judgment.get("mapping", {})
                sample_proof = sample_proof and (
                    judgment.get("confidence") == "high"
                    and judgment.get("choice") == mapping.get("candidate")
                )
            elif source_name == "shortening":
                judgment = report.get("shortening_judgments", {}).get(
                    str(item_id), {}
                )
                mapping = judgment.get("mapping", {})
                sample_proof = sample_proof and (
                    judgment.get("confidence") == "high"
                    and judgment.get("choice") == mapping.get("candidate")
                    and all(
                        judgment.get(field) is True
                        for field in (
                            "facts", "negation", "numbers", "entities",
                            "references", "logic",
                        )
                    )
                )
        proof_gate = proof_gate and sample_proof
        errors = report.get("stage_errors", [])
        safe_shortening_rejections = [
            row
            for row in errors
            if isinstance(row, dict)
            and str(row.get("stage") or "").startswith("shortening:")
            and "equivalence was not fully confirmed" in str(row.get("error") or "")
        ]
        unrecovered_errors = [
            row for row in errors if row not in safe_shortening_rejections
        ]
        if unrecovered_errors:
            stage_errors.extend(
                {"sample_id": sample_id, **row}
                for row in unrecovered_errors
                if isinstance(row, dict)
            )
        samples[sample_id] = {
            "cue_count": len(source),
            "completed": (
                status.get("samples", {}).get(sample_id, {}).get("status")
                == "completed"
            ),
            "ids_equal": source_ids == output_ids,
            "times_equal": source_times == output_times,
            "empty_translation_ids": [
                item_id for item_id in source_ids if not target.get(item_id, "").strip()
            ],
            "budget_violation_ids": budget_ids,
            "proof_gate_passed": sample_proof,
            "stage_error_count": len(unrecovered_errors),
            "safe_shortening_rejection_count": len(safe_shortening_rejections),
        }
    known: dict[str, bool] = {}
    for sample_id, item_id, name, required, forbidden in KNOWN_CHECKS:
        text = translations.get(sample_id, {}).get(item_id, "")
        known[f"{sample_id}:{name}"] = (
            (not required or any(token in text for token in required))
            and not any(token in text for token in forbidden)
        )
    structure = all(
        row["completed"]
        and row["ids_equal"]
        and row["times_equal"]
        and not row["empty_translation_ids"]
        for row in samples.values()
    )
    automatic_passed = (
        structure
        and bool(known)
        and all(known.values())
        and proof_gate
        and total_budget <= 3
        and not stage_errors
        and int(status.get("requests_used", DEFAULT_LIMIT + 1)) <= DEFAULT_LIMIT
    )
    result = {
        "schema_version": 1,
        "status": "passed" if automatic_passed else "failed",
        "samples": samples,
        "totals": {
            "cue_count": sum(row["cue_count"] for row in samples.values()),
            "structurally_valid": structure,
            "known_regressions_passed": all(known.values()) if known else False,
            "proof_gate_passed": proof_gate,
            "baseline_budget_violation_count": sum(
                int(sample["baseline_budget"]) for sample in SAMPLES
            ),
            "candidate_budget_violation_count": total_budget,
            "stage_error_count": len(stage_errors),
            "requests_used": status.get("requests_used"),
            "request_limit": status.get("request_limit"),
        },
        "known_regression_checks": known,
        "stage_errors": stage_errors,
        "automatic_gate_passed": automatic_passed,
    }
    _atomic_json(AUTOMATIC_PATH, result)
    manifest = {
        "schema_version": 1,
        "authorized": True,
        "samples": [
            {
                "id": str(sample["id"]),
                "source_srt": str(Path(sample["source"]).resolve()),
                "baseline_srt": str(Path(sample["baseline"]).resolve()),
                "candidate_srt": str(_output_paths(str(sample["id"]))[0].resolve()),
                "report": str(_output_paths(str(sample["id"]))[1].resolve()),
                "required_ids": REQUIRED_BLIND_IDS[str(sample["id"])],
            }
            for sample in SAMPLES
        ],
    }
    _atomic_json(BLIND_MANIFEST_PATH, manifest)
    print(json.dumps(result["totals"], ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument(
        "--max-http-requests", type=int, default=DEFAULT_LIMIT
    )
    commands.add_parser("evaluate")
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.max_http_requests)
        return 0 if result["status"] == "completed" else 2
    result = evaluate()
    return 0 if result["automatic_gate_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
