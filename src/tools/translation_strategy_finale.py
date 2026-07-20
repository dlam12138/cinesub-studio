"""Run the frozen Semantic/WenYi hybrid pilot and compare three strategies.

Paid calls are limited to the hybrid challenger. Existing semantic_review and
wenyi_review SRTs are read-only baselines and are never regenerated here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGRESSION_ROOT = PROJECT_ROOT / "work" / "regression-6"
WENYI_ROOT = REGRESSION_ROOT / "wenyi-pilot"
FINALE_ROOT = REGRESSION_ROOT / "strategy-finale"
STATUS_PATH = FINALE_ROOT / "hybrid-status.json"
AUTOMATIC_PATH = FINALE_ROOT / "automatic-evaluation.json"
BLIND_PATH = FINALE_ROOT / "blind-review.tsv"
BLIND_KEY_PATH = FINALE_ROOT / "blind-key.json"
BLIND_SCORE_PATH = FINALE_ROOT / "blind-score.json"
DEFAULT_LIMIT = 200
TARGET_COUNT = 151
SEED = "semantic-wenyi-frozen-finale-v1-151"

SAMPLES = (
    {"id": "1049912620-1-208", "required_ids": [12, 57]},
    {"id": "39998393486-1-192", "required_ids": []},
    {"id": "899379112-1-208", "required_ids": [93, 150, 155]},
)
KNOWN_CHECKS = (
    ("1049912620-1-208", 12, "fans_preserved", ("粉丝",), ()),
    ("1049912620-1-208", 57, "no_invented_endurance", (), ("耐力",)),
    (
        "899379112-1-208",
        93,
        "language_action_preserved",
        ("提问", "询问", "解释"),
        (),
    ),
    ("899379112-1-208", 150, "biberon_150", ("奶瓶",), ()),
    ("899379112-1-208", 155, "biberon_155", ("奶瓶",), ()),
)
RISK_CATEGORIES = {
    "adopted_repair",
    "adopted_shortening",
    "unresolved_budget",
    "consistency_definite",
    "consistency_variant",
    "cross_line_issue",
    "asr_warning",
    "semantic_repair",
    "semantic_budget",
    "semantic_consistency",
    "known_regression",
}
STRATEGIES = (
    "semantic_review",
    "wenyi_review",
    "semantic_wenyi_review",
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


def _atomic_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(rows[0]), delimiter="\t"
            )
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _paths(sample_id: str) -> dict[str, Path]:
    sample_root = REGRESSION_ROOT / sample_id
    semantic = (
        sample_root
        / "translation"
        / f"{sample_id}.semantic-review.flash-pro.bilingual.zh-CN.srt"
    )
    wenyi = (
        WENYI_ROOT
        / sample_id
        / f"{sample_id}.wenyi-review.flash-pro.bilingual.zh-CN.srt"
    )
    hybrid = (
        FINALE_ROOT
        / sample_id
        / f"{sample_id}.semantic-wenyi-review.flash-pro.bilingual.zh-CN.srt"
    )
    return {
        "source": (
            sample_root / "asr" / f"{sample_id}.large-v3.srt"
        ),
        "semantic_review": semantic,
        "semantic_report": semantic.with_name(
            f"{semantic.stem}.semantic_review_report.json"
        ),
        "wenyi_review": wenyi,
        "wenyi_report": wenyi.with_name(
            f"{wenyi.stem}.wenyi_review_report.json"
        ),
        "semantic_wenyi_review": hybrid,
        "hybrid_report": hybrid.with_name(
            f"{hybrid.stem}.semantic_wenyi_review_report.json"
        ),
        "hybrid_cache": (
            FINALE_ROOT / sample_id / f"{sample_id}.hybrid-cache.json"
        ),
    }


def _read_srt(path: Path) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    raw = path.read_text(encoding="utf-8-sig").strip()
    for block in re.split(r"\n\s*\n", raw):
        lines = block.splitlines()
        if len(lines) >= 3:
            rows.append(
                (int(lines[0]), lines[1], "\n".join(lines[2:]).strip())
            )
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
            text[len(prefix):].strip()
            if text.startswith(prefix)
            else text.strip()
        )
    return result


def _read_status(limit: int) -> dict[str, Any]:
    if STATUS_PATH.is_file():
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if int(status.get("request_limit", -1)) != limit:
            raise ValueError(
                "existing finale uses a different paid-request limit"
            )
        return status
    return {
        "schema_version": 1,
        "frozen_version": "semantic-wenyi-review-v1",
        "authorized": True,
        "request_limit": limit,
        "requests_used": 0,
        "status": "pending",
        "samples": {},
    }


def _provider() -> dict[str, Any]:
    from provider_store import get_active_provider

    provider = get_active_provider() or {}
    required = (
        "protocol",
        "api_base",
        "api_key",
        "translation_model",
        "translation_quality_model",
    )
    missing = [
        field for field in required
        if not str(provider.get(field) or "").strip()
    ]
    if missing:
        raise ValueError(
            "active provider is incomplete: " + ", ".join(missing)
        )
    return provider


def run(limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    from language_profile_store import resolve_language_profile_config
    from subtitle_translate import (
        _atomic_write_srt,
        _save_translation_cache,
        read_srt,
    )
    from translation_reliability import (
        TranslationRequestTracker,
        TranslationRunSummary,
        TranslationTotalRequestLimitExceeded,
    )
    from wenyi_subtitle_strategy import run_wenyi_review

    status = _read_status(limit)
    provider = _provider()
    profile = resolve_language_profile_config()
    quality = profile.get("quality", {}) or {}
    for sample in SAMPLES:
        sample_id = sample["id"]
        if (
            status["samples"].get(sample_id, {}).get("status")
            == "completed"
        ):
            continue
        remaining = limit - int(status["requests_used"])
        if remaining <= 0:
            status["status"] = "inconclusive"
            status["reason"] = "total_request_limit_exhausted"
            _atomic_json(STATUS_PATH, status)
            break
        paths = _paths(sample_id)
        for key in (
            "source",
            "semantic_review",
            "semantic_report",
            "wenyi_review",
            "wenyi_report",
        ):
            if not paths[key].is_file():
                raise FileNotFoundError(f"{sample_id}: missing {key}")
        source_rows = _read_srt(paths["source"])
        semantic_rows = _read_srt(paths["semantic_review"])
        baseline = _targets(source_rows, semantic_rows)
        if set(baseline) != {row[0] for row in source_rows}:
            raise ValueError(f"{sample_id}: semantic baseline ids differ")
        semantic_report = json.loads(
            paths["semantic_report"].read_text(encoding="utf-8")
        )
        source_warning_ids = {
            int(value)
            for warning in (
                semantic_report.get("video_analysis", {})
                .get("suspected_asr_errors", [])
            )
            for value in warning.get("evidence_ids", [])
        }
        items = read_srt(paths["source"])
        paths["semantic_wenyi_review"].parent.mkdir(
            parents=True, exist_ok=True
        )
        tracker = TranslationRequestTracker(
            mode="off", max_total_requests=remaining
        )
        summary = TranslationRunSummary(
            mode="off",
            total_items=len(items),
            strategy_mode="semantic_wenyi_review",
        )
        status["status"] = "running"
        status["active_sample"] = sample_id
        _atomic_json(STATUS_PATH, status)
        try:
            hybrid_cache: dict[int, str] = {}
            run_wenyi_review(
                items=items,
                cached_translations=hybrid_cache,
                translation_cache_path=paths["hybrid_cache"],
                output_path=paths["semantic_wenyi_review"],
                target_language=str(
                    profile.get("target_language") or "zh-CN"
                ),
                profile_prompt=str(
                    profile.get("translation_style") or ""
                ),
                profile_glossary=profile.get("glossary", []),
                flash_model=str(provider["translation_model"]),
                pro_model=str(provider["translation_quality_model"]),
                batch_size=20,
                temperature=0.2,
                api_provider=str(provider["protocol"]),
                api_base=str(provider["api_base"]),
                api_key=str(provider["api_key"]),
                context_window=3,
                scene_gap_seconds=30.0,
                max_cps_zh=float(quality.get("max_cps_zh", 8)),
                max_chars_per_subtitle_zh=int(
                    quality.get("max_chars_per_subtitle_zh", 36)
                ),
                tracker=tracker,
                summary=summary,
                strategy_mode="semantic_wenyi_review",
                baseline_translations=baseline,
                source_warning_ids=source_warning_ids,
                semantic_baseline_report=semantic_report,
            )
            _atomic_write_srt(items, paths["semantic_wenyi_review"])
            _save_translation_cache(paths["hybrid_cache"], hybrid_cache)
        except TranslationTotalRequestLimitExceeded:
            status["requests_used"] = limit
            status["samples"][sample_id] = {
                "status": "inconclusive",
                "reason": "total_request_limit_exhausted",
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
            }
            status["status"] = "inconclusive"
            status["reason"] = f"{sample_id}:{type(exc).__name__}"
            status.pop("active_sample", None)
            _atomic_json(STATUS_PATH, status)
            raise
        summary.actual_requests = tracker.actual_requests
        status["requests_used"] += tracker.actual_requests
        status["samples"][sample_id] = {
            "status": "completed",
            "requests": tracker.actual_requests,
            "output": str(paths["semantic_wenyi_review"]),
            "report": str(paths["hybrid_report"]),
            "summary": summary.safe_summary(),
        }
        status.pop("active_sample", None)
        _atomic_json(STATUS_PATH, status)
    if all(
        status["samples"].get(sample["id"], {}).get("status")
        == "completed"
        for sample in SAMPLES
    ):
        status["status"] = "completed"
        status.pop("reason", None)
        _atomic_json(STATUS_PATH, status)
    return status


def _proof_passed(report: dict[str, Any]) -> bool:
    fields = (
        "facts",
        "negation",
        "numbers",
        "entities",
        "references",
        "logic",
        "issue_resolved",
        "no_new_error",
    )
    for item_id, source_name in report.get("final_sources", {}).items():
        if source_name not in {"repair", "shortening"}:
            continue
        judgments = (
            report.get("repair_judgments", {})
            if source_name == "repair"
            else report.get("shortening_judgments", {})
        )
        judgment = judgments.get(str(item_id), {})
        mapping = judgment.get("mapping", {})
        if not (
            judgment.get("confidence") == "high"
            and judgment.get("choice") == mapping.get("candidate")
            and all(judgment.get(field) is True for field in fields)
        ):
            return False
    return True


def evaluate() -> dict[str, Any]:
    status = _read_status(DEFAULT_LIMIT)
    samples: dict[str, Any] = {}
    translations: dict[str, dict[int, str]] = {}
    for sample in SAMPLES:
        sample_id = sample["id"]
        paths = _paths(sample_id)
        source = _read_srt(paths["source"])
        rendered = (
            _read_srt(paths["semantic_wenyi_review"])
            if paths["semantic_wenyi_review"].is_file() else []
        )
        target = _targets(source, rendered) if rendered else {}
        translations[sample_id] = target
        report = (
            json.loads(paths["hybrid_report"].read_text(encoding="utf-8"))
            if paths["hybrid_report"].is_file() else {}
        )
        samples[sample_id] = {
            "cue_count": len(source),
            "completed": (
                status.get("samples", {}).get(sample_id, {}).get("status")
                == "completed"
            ),
            "ids_equal": [row[0] for row in source]
            == [row[0] for row in rendered],
            "times_equal": [row[1] for row in source]
            == [row[1] for row in rendered],
            "empty_translation_ids": [
                row[0] for row in source
                if not target.get(row[0], "").strip()
            ],
            "proof_gate_passed": _proof_passed(report),
            "budget_violation_ids": [
                int(value)
                for value in report.get("budget_violation_ids", [])
            ],
            "stage_errors": report.get("stage_errors", []),
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
    proof = all(row["proof_gate_passed"] for row in samples.values())
    no_stage_errors = not any(
        row["stage_errors"] for row in samples.values()
    )
    result = {
        "schema_version": 1,
        "frozen_version": "semantic-wenyi-review-v1",
        "status": (
            "passed"
            if structure and proof and no_stage_errors and all(known.values())
            else "failed"
        ),
        "samples": samples,
        "known_regression_checks": known,
        "totals": {
            "cue_count": sum(row["cue_count"] for row in samples.values()),
            "structurally_valid": structure,
            "proof_gate_passed": proof,
            "known_regressions_passed": all(known.values()),
            "stage_error_count": sum(
                len(row["stage_errors"]) for row in samples.values()
            ),
            "budget_violation_count": sum(
                len(row["budget_violation_ids"])
                for row in samples.values()
            ),
            "requests_used": status.get("requests_used", 0),
            "request_limit": status.get("request_limit", DEFAULT_LIMIT),
        },
    }
    _atomic_json(AUTOMATIC_PATH, result)
    return result


def _report_categories(
    strategy: str, report: dict[str, Any]
) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    for row in report.get("review_items", []):
        if isinstance(row, dict) and str(row.get("id", "")).isdigit():
            result.setdefault(int(row["id"]), set()).add(
                str(row.get("category") or "review_item")
            )
    if strategy == "semantic_review":
        for item_id, source_name in report.get("final_sources", {}).items():
            if source_name == "repair":
                result.setdefault(int(item_id), set()).add("semantic_repair")
        for item_id in report.get("budget_violation_ids", []):
            result.setdefault(int(item_id), set()).add("semantic_budget")
        for issue in report.get("consistency_issues", []):
            if isinstance(issue, dict) and str(issue.get("id", "")).isdigit():
                result.setdefault(int(issue["id"]), set()).add(
                    "semantic_consistency"
                )
    return result


def build_blind() -> dict[str, Any]:
    pool: dict[tuple[str, int], dict[str, Any]] = {}
    risks: set[tuple[str, int]] = set()
    for sample in SAMPLES:
        sample_id = sample["id"]
        paths = _paths(sample_id)
        source_rows = _read_srt(paths["source"])
        source = {row[0]: {"time": row[1], "text": row[2]} for row in source_rows}
        ordered_ids = [row[0] for row in source_rows]
        targets = {
            strategy: _targets(
                source_rows, _read_srt(paths[strategy])
            )
            for strategy in STRATEGIES
        }
        reports = {
            "semantic_review": json.loads(
                paths["semantic_report"].read_text(encoding="utf-8")
            ),
            "wenyi_review": json.loads(
                paths["wenyi_report"].read_text(encoding="utf-8")
            ),
            "semantic_wenyi_review": json.loads(
                paths["hybrid_report"].read_text(encoding="utf-8")
            ),
        }
        categories: dict[int, set[str]] = {}
        for strategy, report in reports.items():
            for item_id, values in _report_categories(
                strategy, report
            ).items():
                categories.setdefault(item_id, set()).update(values)
        for item_id in sample["required_ids"]:
            categories.setdefault(item_id, set()).add("known_regression")
        positions = {item_id: index for index, item_id in enumerate(ordered_ids)}
        for item_id in ordered_ids:
            category = categories.get(item_id, set())
            if category & RISK_CATEGORIES:
                risks.add((sample_id, item_id))
            position = positions[item_id]
            pool[(sample_id, item_id)] = {
                "sample_id": sample_id,
                "cue_id": item_id,
                "category": ",".join(sorted(category)) or "unchanged",
                "time": source[item_id]["time"],
                "source": source[item_id]["text"],
                "context_before": "\n".join(
                    source[value]["text"]
                    for value in ordered_ids[max(0, position - 2):position]
                ),
                "context_after": "\n".join(
                    source[value]["text"]
                    for value in ordered_ids[position + 1:position + 3]
                ),
                "translations": {
                    strategy: targets[strategy][item_id]
                    for strategy in STRATEGIES
                },
                "translation_context_before": {
                    strategy: "\n".join(
                        targets[strategy][value]
                        for value in ordered_ids[
                            max(0, position - 2):position
                        ]
                    )
                    for strategy in STRATEGIES
                },
                "translation_context_after": {
                    strategy: "\n".join(
                        targets[strategy][value]
                        for value in ordered_ids[
                            position + 1:position + 3
                        ]
                    )
                    for strategy in STRATEGIES
                },
            }
    if len(risks) > TARGET_COUNT:
        raise ValueError(
            f"risk item count {len(risks)} exceeds {TARGET_COUNT}"
        )
    remaining = [key for key in pool if key not in risks]
    remaining.sort(
        key=lambda key: hashlib.sha256(
            f"{SEED}:{key[0]}:{key[1]}".encode("utf-8")
        ).hexdigest()
    )
    selected = sorted(risks) + remaining[:TARGET_COUNT - len(risks)]
    if len(selected) != TARGET_COUNT:
        raise ValueError(f"only {len(selected)} cues are available")
    permutations = list(itertools.permutations(STRATEGIES))
    rows: list[dict[str, Any]] = []
    keys: list[dict[str, Any]] = []
    for sample_id, item_id in selected:
        source = pool[(sample_id, item_id)]
        digest = hashlib.sha256(
            f"{SEED}:abc:{sample_id}:{item_id}".encode("utf-8")
        ).digest()
        order = permutations[digest[0] % len(permutations)]
        option_map = dict(zip(("A", "B", "C"), order))
        rows.append({
            "sample_id": sample_id,
            "cue_id": item_id,
            "category": source["category"],
            "time": source["time"],
            "source": source["source"],
            "context_before": source["context_before"],
            "context_after": source["context_after"],
            "option_a": source["translations"][option_map["A"]],
            "option_b": source["translations"][option_map["B"]],
            "option_c": source["translations"][option_map["C"]],
            "option_a_context_before": source[
                "translation_context_before"
            ][option_map["A"]],
            "option_a_context_after": source[
                "translation_context_after"
            ][option_map["A"]],
            "option_b_context_before": source[
                "translation_context_before"
            ][option_map["B"]],
            "option_b_context_after": source[
                "translation_context_after"
            ][option_map["B"]],
            "option_c_context_before": source[
                "translation_context_before"
            ][option_map["C"]],
            "option_c_context_after": source[
                "translation_context_after"
            ][option_map["C"]],
            "fidelity_order": "",
            "naturalness_order": "",
            "severe_error_options": "",
            "notes": "",
        })
        keys.append({
            "sample_id": sample_id,
            "cue_id": item_id,
            "options": option_map,
        })
    _atomic_tsv(BLIND_PATH, rows)
    _atomic_json(
        BLIND_KEY_PATH,
        {
            "schema_version": 1,
            "seed": SEED,
            "count": TARGET_COUNT,
            "keys": keys,
        },
    )
    return {
        "count": len(rows),
        "risk_count": len(risks),
        "random_fill_count": len(rows) - len(risks),
        "review": str(BLIND_PATH),
        "answer_key": str(BLIND_KEY_PATH),
    }


def _pairs(order: str) -> dict[tuple[str, str], int]:
    value = str(order or "").strip().upper()
    groups = [set(group.split("=")) for group in value.split(">")]
    flattened = set().union(*groups) if groups else set()
    if flattened != {"A", "B", "C"} or sum(map(len, groups)) != 3:
        raise ValueError(f"invalid three-way order: {order!r}")
    rank = {
        option: index for index, group in enumerate(groups)
        for option in group
    }
    return {
        pair: (
            0 if rank[pair[0]] == rank[pair[1]]
            else 1 if rank[pair[0]] < rank[pair[1]]
            else -1
        )
        for pair in (("A", "B"), ("A", "C"), ("B", "C"))
    }


def score_blind() -> dict[str, Any]:
    key = json.loads(BLIND_KEY_PATH.read_text(encoding="utf-8"))
    answers = {
        (row["sample_id"], int(row["cue_id"])): row["options"]
        for row in key["keys"]
    }
    metrics = {
        strategy: {
            "fidelity_wins": 0,
            "fidelity_losses": 0,
            "fidelity_ties": 0,
            "naturalness_wins": 0,
            "naturalness_losses": 0,
            "naturalness_ties": 0,
            "severe_errors": 0,
            "categories": {},
        }
        for strategy in STRATEGIES
    }
    reviewed = 0
    with BLIND_PATH.open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            fidelity = _pairs(row["fidelity_order"])
            naturalness = _pairs(row["naturalness_order"])
            option_map = answers[
                (row["sample_id"], int(row["cue_id"]))
            ]
            category_names = str(
                row.get("category") or "unchanged"
            ).split(",")
            for pair, outcome in fidelity.items():
                left, right = option_map[pair[0]], option_map[pair[1]]
                if outcome == 0:
                    metrics[left]["fidelity_ties"] += 1
                    metrics[right]["fidelity_ties"] += 1
                else:
                    winner, loser = (
                        (left, right) if outcome > 0 else (right, left)
                    )
                    metrics[winner]["fidelity_wins"] += 1
                    metrics[loser]["fidelity_losses"] += 1
                    for category in category_names:
                        values = metrics[winner]["categories"].setdefault(
                            category, {"wins": 0, "losses": 0}
                        )
                        values["wins"] += 1
                        values = metrics[loser]["categories"].setdefault(
                            category, {"wins": 0, "losses": 0}
                        )
                        values["losses"] += 1
            for pair, outcome in naturalness.items():
                left, right = option_map[pair[0]], option_map[pair[1]]
                if outcome == 0:
                    metrics[left]["naturalness_ties"] += 1
                    metrics[right]["naturalness_ties"] += 1
                else:
                    winner, loser = (
                        (left, right) if outcome > 0 else (right, left)
                    )
                    metrics[winner]["naturalness_wins"] += 1
                    metrics[loser]["naturalness_losses"] += 1
            severe_options = {
                value.strip().upper()
                for value in str(
                    row.get("severe_error_options") or ""
                ).split(",")
                if value.strip() and value.strip().upper() != "NONE"
            }
            if not severe_options <= {"A", "B", "C"}:
                raise ValueError("invalid severe_error_options")
            for option in severe_options:
                metrics[option_map[option]]["severe_errors"] += 1
            reviewed += 1
    for strategy, values in metrics.items():
        non_ties = values["fidelity_wins"] + values["fidelity_losses"]
        values["fidelity_preference_rate"] = round(
            values["fidelity_wins"] / non_ties if non_ties else 0.0,
            6,
        )
        natural_non_ties = (
            values["naturalness_wins"] + values["naturalness_losses"]
        )
        values["naturalness_preference_rate"] = round(
            (
                values["naturalness_wins"] / natural_non_ties
                if natural_non_ties else 0.0
            ),
            6,
        )
    result = {"reviewed": reviewed, "strategies": metrics}
    _atomic_json(BLIND_SCORE_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("run", "evaluate", "build-blind", "score-blind")
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    if args.action == "run":
        result = run(args.limit)
    elif args.action == "evaluate":
        result = evaluate()
    elif args.action == "build-blind":
        result = build_blind()
    else:
        result = score_blind()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
