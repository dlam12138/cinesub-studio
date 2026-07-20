from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


TIME_RE = re.compile(
    r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})"
    r"\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})"
)


def _read_blocks(path: Path) -> list[tuple[int, str, list[str]]]:
    raw = path.read_text(encoding="utf-8-sig").strip()
    rows: list[tuple[int, str, list[str]]] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        rows.append((int(lines[0]), lines[1], lines[2:]))
    return rows


def _split_bilingual(
    source: list[tuple[int, str, list[str]]],
    translated: list[tuple[int, str, list[str]]],
) -> dict[int, str]:
    source_by_id = {item_id: lines for item_id, _, lines in source}
    result: dict[int, str] = {}
    for item_id, _, lines in translated:
        source_lines = source_by_id.get(item_id, [])
        if lines[: len(source_lines)] == source_lines:
            target_lines = lines[len(source_lines) :]
        else:
            target_lines = lines[-1:]
        result[item_id] = "\n".join(target_lines).strip()
    return result


def _duration(time_line: str) -> float:
    match = TIME_RE.fullmatch(time_line.strip())
    if not match:
        return 0.0
    values = {name: int(value) for name, value in match.groupdict().items()}
    start = values["sh"] * 3600 + values["sm"] * 60 + values["ss"] + values["sms"] / 1000
    end = values["eh"] * 3600 + values["em"] * 60 + values["es"] + values["ems"] / 1000
    return max(0.0, end - start)


def _target_character_count(value: str) -> int:
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", value))
    if cjk_count:
        return cjk_count
    return len(re.sub(r"\s+", "", value))


def _cps_warning_ids(
    source: list[tuple[int, str, list[str]]], translations: dict[int, str]
) -> set[int]:
    return {
        item_id
        for item_id, time_line, _ in source
        if _duration(time_line) > 0
        and _target_character_count(translations.get(item_id, "")) / _duration(time_line)
        > 8
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _pick_order(sample_id: str, cue_id: int, comparison: str) -> bool:
    digest = hashlib.sha256(
        f"semantic-review-v1|{comparison}|{sample_id}|{cue_id}".encode("utf-8")
    ).digest()
    return bool(digest[0] & 1)


def build_report(root: Path, sample_ids: list[str], output_dir: Path) -> dict[str, Any]:
    audit_ids: dict[str, set[int]] = {}
    audit_path = root / "manual-audit" / "manual-audit.tsv"
    if audit_path.is_file():
        with audit_path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file, delimiter="\t"):
                audit_ids.setdefault(str(row.get("sample_id") or ""), set()).add(
                    int(row["cue_id"])
                )

    report: dict[str, Any] = {"samples": {}, "totals": {}}
    blind: dict[str, list[dict[str, Any]]] = {"standard": [], "three_pass": []}
    keys: dict[str, list[dict[str, Any]]] = {"standard": [], "three_pass": []}

    for sample_id in sample_ids:
        sample_root = root / sample_id
        source_path = sample_root / "asr" / f"{sample_id}.large-v3.srt"
        if not source_path.is_file() and sample_id == "500001643693966-1-192":
            source_path = (
                root.parent
                / "ocr-eval"
                / "bv1xbyeeoeak"
                / "asr"
                / f"{sample_id}.large-v3.srt"
            )
        translation_root = sample_root / "translation"
        semantic_path = (
            translation_root
            / f"{sample_id}.semantic-review.flash-pro.bilingual.zh-CN.srt"
        )
        report_path = semantic_path.with_name(
            f"{semantic_path.stem}.semantic_review_report.json"
        )
        if not source_path.is_file() or not semantic_path.is_file() or not report_path.is_file():
            report["samples"][sample_id] = {"status": "missing"}
            continue

        source = _read_blocks(source_path)
        semantic_blocks = _read_blocks(semantic_path)
        semantic = _split_bilingual(source, semantic_blocks)
        stage_report = json.loads(report_path.read_text(encoding="utf-8"))
        source_ids = [row[0] for row in source]
        output_ids = [row[0] for row in semantic_blocks]
        source_times = [row[1] for row in source]
        output_times = [row[1] for row in semantic_blocks]
        repaired_ids = {
            int(item_id)
            for item_id, source_name in stage_report.get("final_sources", {}).items()
            if source_name == "repair"
        }
        budget_ids = set(stage_report.get("budget_violation_ids", []))
        cps_ids = _cps_warning_ids(source, semantic)
        report["samples"][sample_id] = {
            "status": "completed",
            "cue_count": len(source),
            "ids_equal": source_ids == output_ids,
            "times_equal": source_times == output_times,
            "empty_translation_ids": [
                item_id for item_id in source_ids if not semantic.get(item_id, "").strip()
            ],
            "issue_counts": stage_report.get("issue_counts", {}),
            "repair_adopted_ids": sorted(repaired_ids),
            "repair_adopted_count": len(repaired_ids),
            "budget_violation_ids": sorted(budget_ids),
            "cps_warning_ids": sorted(cps_ids),
            "consistency_issue_count": len(stage_report.get("consistency_issues", [])),
            "unresolved_ids": stage_report.get("unresolved_ids", []),
            "prompt_version": stage_report.get("prompt_version", ""),
        }

        source_by_id = {
            item_id: {"time": time_line, "text": "\n".join(lines)}
            for item_id, time_line, lines in source
        }
        unchanged = [item_id for item_id in source_ids if item_id not in repaired_ids]
        stride = max(1, len(unchanged) // 12)
        selected = (
            repaired_ids
            | audit_ids.get(sample_id, set())
            | set(unchanged[::stride][:12])
        )
        candidates = {
            "standard": translation_root
            / f"{sample_id}.standard.flash.bilingual.zh-CN.srt",
            "three_pass": translation_root
            / f"{sample_id}.three-pass.flash-pro.bilingual.zh-CN.srt",
        }
        cps_by_mode = {"semantic": len(cps_ids)}
        for comparison, candidate_path in candidates.items():
            if not candidate_path.is_file():
                continue
            candidate = _split_bilingual(source, _read_blocks(candidate_path))
            cps_by_mode[comparison] = len(_cps_warning_ids(source, candidate))
            for cue_id in sorted(selected):
                if cue_id not in source_by_id or cue_id not in candidate:
                    continue
                semantic_is_a = _pick_order(sample_id, cue_id, comparison)
                option_a = semantic[cue_id] if semantic_is_a else candidate[cue_id]
                option_b = candidate[cue_id] if semantic_is_a else semantic[cue_id]
                blind[comparison].append({
                    "sample_id": sample_id,
                    "cue_id": cue_id,
                    "time": source_by_id[cue_id]["time"],
                    "source": source_by_id[cue_id]["text"],
                    "option_a": option_a,
                    "option_b": option_b,
                    "fidelity_choice": "",
                    "fluency_choice": "",
                    "notes": "",
                })
                keys[comparison].append({
                    "sample_id": sample_id,
                    "cue_id": cue_id,
                    "semantic_option": "A" if semantic_is_a else "B",
                })
        report["samples"][sample_id]["cps_warning_counts"] = cps_by_mode

    known_checks: dict[str, bool] = {}
    known_specs = {
        "899379112-1-208": {
            "language_action_preserved": (93, ("提问", "请求", "问")),
            "biberon_150": (150, ("奶瓶",)),
            "biberon_155": (155, ("奶瓶",)),
        },
        "1049912620-1-208": {
            "fans_preserved": (12, ("粉丝",)),
        },
    }
    for sample_id, checks in known_specs.items():
        sample_root = root / sample_id
        source_path = sample_root / "asr" / f"{sample_id}.large-v3.srt"
        semantic_path = (
            sample_root
            / "translation"
            / f"{sample_id}.semantic-review.flash-pro.bilingual.zh-CN.srt"
        )
        if not source_path.is_file() or not semantic_path.is_file():
            continue
        translated = _split_bilingual(
            _read_blocks(source_path), _read_blocks(semantic_path)
        )
        for name, (cue_id, tokens) in checks.items():
            known_checks[f"{sample_id}:{name}"] = any(
                token in translated.get(cue_id, "") for token in tokens
            )
    interview_path = (
        root
        / "1049912620-1-208"
        / "translation"
        / "1049912620-1-208.semantic-review.flash-pro.bilingual.zh-CN.srt"
    )
    interview_source = (
        root / "1049912620-1-208" / "asr" / "1049912620-1-208.large-v3.srt"
    )
    if interview_path.is_file() and interview_source.is_file():
        interview = _split_bilingual(
            _read_blocks(interview_source), _read_blocks(interview_path)
        )
        known_checks["1049912620-1-208:no_invented_endurance"] = (
            "熬" not in interview.get(57, "")
        )
    report["known_regression_checks"] = known_checks

    completed = [
        value for value in report["samples"].values() if value.get("status") == "completed"
    ]
    cps_totals = {
        mode: sum(
            value.get("cps_warning_counts", {}).get(mode, 0) for value in completed
        )
        for mode in ("standard", "three_pass", "semantic")
    }
    report["totals"] = {
        "sample_count": len(completed),
        "cue_count": sum(value["cue_count"] for value in completed),
        "all_structurally_valid": bool(completed)
        and all(
            value["ids_equal"]
            and value["times_equal"]
            and not value["empty_translation_ids"]
            for value in completed
        ),
        "repair_adopted_count": sum(value["repair_adopted_count"] for value in completed),
        "budget_violation_count": sum(
            len(value["budget_violation_ids"]) for value in completed
        ),
        "cps_warning_count": sum(len(value["cps_warning_ids"]) for value in completed),
        "cps_warning_counts": cps_totals,
        "cps_not_worse_than_standard": (
            cps_totals["semantic"] <= cps_totals["standard"]
        ),
        "cps_not_worse_than_three_pass": (
            cps_totals["semantic"] <= cps_totals["three_pass"]
        ),
        "consistency_issue_count": sum(
            value["consistency_issue_count"] for value in completed
        ),
        "known_regressions_passed": bool(known_checks)
        and all(known_checks.values()),
    }
    _atomic_json(output_dir / "semantic-review-regression.json", report)
    fields = [
        "sample_id",
        "cue_id",
        "time",
        "source",
        "option_a",
        "option_b",
        "fidelity_choice",
        "fluency_choice",
        "notes",
    ]
    for comparison in ("standard", "three_pass"):
        _write_tsv(
            output_dir / f"blind-semantic-vs-{comparison}.tsv",
            blind[comparison],
            fields,
        )
        _atomic_json(
            output_dir / f"blind-semantic-vs-{comparison}.key.json",
            keys[comparison],
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build semantic-review regression artifacts.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--samples", nargs="+", required=True)
    args = parser.parse_args()
    report = build_report(args.root.resolve(), args.samples, args.output_dir.resolve())
    print(json.dumps(report["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
