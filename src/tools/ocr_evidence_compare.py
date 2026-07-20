"""Compare burned-subtitle OCR with ASR and translation outputs as weak evidence.

This tool deliberately reports agreement/disagreement signals rather than CER/WER.
Burned subtitles and OCR are noisy, may be editorial translations, and must never
be treated as promotion-grade ground truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from encoding_utils import read_json
from provider_store import resolve_provider_config
from runtime_paths import resolve_runtime_paths
from subtitle_translate import (
    SubtitleItem,
    _build_request_body,
    _call_llm_api,
    _parse_api_response,
    read_srt,
    write_srt,
)
from translation_reliability import TranslationRequestTracker, blocking_translation_issues

SCHEMA_VERSION = 1
RULE_VERSION = "ocr-weak-evidence-v1"
HIGH_STABILITY_THRESHOLD = 0.75
MIN_HIGH_STABILITY_COVERAGE = 0.60
MIN_RELATIVE_DISAGREEMENT_IMPROVEMENT = 0.10
MAX_RATE_REGRESSION = 0.02
LATIN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
CJK = re.compile(r"[\u3400-\u9fff]")


class OcrEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class OcrCue(Cue):
    source_text: str
    target_text: str
    stability: float | None = None
    sampled_frame_count: int | None = None


@dataclass(frozen=True)
class AlignmentGroup:
    ocr: tuple[OcrCue, ...]
    hypothesis: tuple[Cue, ...]

    @property
    def start(self) -> float:
        return min(cue.start for cue in (*self.ocr, *self.hypothesis))

    @property
    def end(self) -> float:
        return max(cue.end for cue in (*self.ocr, *self.hypothesis))


def _seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _bounds(time_line: str) -> tuple[float, float]:
    start, end = time_line.split(" --> ", 1)
    return _seconds(start), _seconds(end)


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _to_cues(items: Sequence[SubtitleItem]) -> list[Cue]:
    cues: list[Cue] = []
    for item in items:
        start, end = _bounds(item.time_line)
        if end < start:
            raise OcrEvidenceError(f"subtitle cue {item.index} ends before it starts")
        cues.append(Cue(item.index, start, end, item.text.strip()))
    return cues


def _to_target_cues(items: Sequence[SubtitleItem]) -> list[Cue]:
    """Accept translated-only SRT or the project's source+Chinese bilingual SRT."""
    normalized: list[SubtitleItem] = []
    for item in items:
        lines = [line.strip() for line in item.text.splitlines() if line.strip()]
        target_lines = [line for line in lines if CJK.search(line)]
        text = " ".join(target_lines).strip() if target_lines else item.text.strip()
        normalized.append(SubtitleItem(item.index, item.time_line, text))
    return _to_cues(normalized)


def _split_ocr_text(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return "", ""
    source = lines[0]
    target_lines = [line for line in lines[1:] if CJK.search(line)]
    target = " ".join(target_lines).strip()
    return source, target


def _sidecar_rows(path: Path | None) -> tuple[dict[int, dict[str, Any]], list[str]]:
    if path is None:
        return {}, ["OCR evidence sidecar is missing; all OCR cues are low confidence."]
    raw = read_json(path, user_input=True)
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise OcrEvidenceError("OCR sidecar schema_version must be 1")
    rows = raw.get("cues")
    if not isinstance(rows, list):
        raise OcrEvidenceError("OCR sidecar requires a cues list")
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("index"), int):
            raise OcrEvidenceError("OCR sidecar cue rows require integer index")
        result[row["index"]] = row
    return result, []


def load_ocr_cues(path: Path, sidecar: Path | None = None) -> tuple[list[OcrCue], list[str]]:
    metadata, warnings = _sidecar_rows(sidecar)
    cues: list[OcrCue] = []
    for item in read_srt(path):
        start, end = _bounds(item.time_line)
        source, target = _split_ocr_text(item.text)
        row = metadata.get(item.index, {})
        stability_raw = row.get("stability")
        stability = None
        if isinstance(stability_raw, (int, float)) and math.isfinite(float(stability_raw)):
            stability = min(1.0, max(0.0, float(stability_raw)))
        frame_count = row.get("sampled_frame_count")
        cues.append(OcrCue(
            item.index, start, end, item.text.strip(), source, target, stability,
            int(frame_count) if isinstance(frame_count, int) and frame_count >= 0 else None,
        ))
    return cues, warnings


def _positive_overlap(left: Cue, right: Cue) -> bool:
    return min(left.end, right.end) - max(left.start, right.start) > 1e-6


def _time_gap(left: Cue, right: Cue) -> float:
    if _positive_overlap(left, right):
        return 0.0
    return max(left.start, right.start) - min(left.end, right.end)


def align_by_time(
    ocr_cues: Sequence[OcrCue], hypothesis: Sequence[Cue], *, tolerance: float = 0.5,
) -> list[AlignmentGroup]:
    """Return ordered connected components from a time-only bipartite graph."""
    if tolerance < 0:
        raise ValueError("alignment tolerance must not be negative")
    ocr_edges: list[list[int]] = [[] for _ in ocr_cues]
    hyp_edges: list[list[int]] = [[] for _ in hypothesis]
    for ocr_index, ocr in enumerate(ocr_cues):
        for hyp_index, hyp in enumerate(hypothesis):
            if hyp.start > ocr.end and hyp.start - ocr.end > tolerance:
                break
            if _positive_overlap(ocr, hyp):
                ocr_edges[ocr_index].append(hyp_index)
                hyp_edges[hyp_index].append(ocr_index)
    # Tolerance repairs clock shifts only for cues that have no real overlap. It
    # must not connect every pair of adjacent cues into one giant component.
    for ocr_index, ocr in enumerate(ocr_cues):
        if ocr_edges[ocr_index] or not hypothesis:
            continue
        nearest = min(
            range(len(hypothesis)),
            key=lambda index: (_time_gap(ocr, hypothesis[index]), abs(ocr.start - hypothesis[index].start)),
        )
        gap = _time_gap(ocr, hypothesis[nearest])
        if 1e-6 < gap <= tolerance:
            ocr_edges[ocr_index].append(nearest)
            hyp_edges[nearest].append(ocr_index)

    visited_ocr: set[int] = set()
    visited_hyp: set[int] = set()
    groups: list[AlignmentGroup] = []
    seeds = sorted(
        [(cue.start, "ocr", index) for index, cue in enumerate(ocr_cues)]
        + [(cue.start, "hyp", index) for index, cue in enumerate(hypothesis)]
    )
    for _, kind, index in seeds:
        if (kind == "ocr" and index in visited_ocr) or (kind == "hyp" and index in visited_hyp):
            continue
        pending = [(kind, index)]
        component_ocr: set[int] = set()
        component_hyp: set[int] = set()
        while pending:
            current_kind, current = pending.pop()
            if current_kind == "ocr":
                if current in visited_ocr:
                    continue
                visited_ocr.add(current)
                component_ocr.add(current)
                pending.extend(("hyp", item) for item in ocr_edges[current])
            else:
                if current in visited_hyp:
                    continue
                visited_hyp.add(current)
                component_hyp.add(current)
                pending.extend(("ocr", item) for item in hyp_edges[current])
        groups.append(AlignmentGroup(
            tuple(ocr_cues[item] for item in sorted(component_ocr)),
            tuple(hypothesis[item] for item in sorted(component_hyp)),
        ))
    return sorted(groups, key=lambda group: (group.start, group.end))


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", str(value or "").casefold())


def _distance(left: Sequence[str], right: Sequence[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_item in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_item != right_item),
            ))
        previous = current
    return previous[-1]


def _duration(cues: Iterable[Cue]) -> float:
    intervals = sorted((cue.start, cue.end) for cue in cues if cue.end > cue.start)
    if not intervals:
        return 0.0
    total = 0.0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def _rate(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 6) if denominator > 0 else 0.0


def _duplicate_rate(cues: Sequence[Cue]) -> float:
    duplicates = sum(
        bool(_normalize(left.text)) and _normalize(left.text) == _normalize(right.text)
        for left, right in zip(cues, cues[1:])
    )
    return _rate(float(duplicates), float(len(cues)))


def _script_anomaly_rate(cues: Sequence[Cue], language: str) -> float:
    nonempty = [cue for cue in cues if _normalize(cue.text)]
    if not nonempty or language not in {"fr", "en", "es", "de", "pt", "it"}:
        return 0.0
    anomalies = sum(not LATIN.search(cue.text) for cue in nonempty)
    return _rate(float(anomalies), float(len(nonempty)))


def calculate_asr_signals(
    ocr_cues: Sequence[OcrCue], hypothesis: Sequence[Cue], *, language: str, tolerance: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    groups = align_by_time(ocr_cues, hypothesis, tolerance=tolerance)
    stable_source_duration = 0.0
    total_source_duration = _duration(cue for cue in ocr_cues if _normalize(cue.source_text))
    edit_distance = 0
    reference_characters = 0
    missing_duration = 0.0
    details: list[dict[str, Any]] = []
    for group in groups:
        stable = [
            cue for cue in group.ocr
            if cue.stability is not None and cue.stability >= HIGH_STABILITY_THRESHOLD
            and _normalize(cue.source_text)
        ]
        source_text = " ".join(cue.source_text for cue in stable)
        hypothesis_text = " ".join(cue.text for cue in group.hypothesis)
        if stable:
            stable_source_duration += _duration(stable)
            normalized_source = list(_normalize(source_text))
            normalized_hypothesis = list(_normalize(hypothesis_text))
            edit_distance += _distance(normalized_source, normalized_hypothesis)
            reference_characters += len(normalized_source)
            if not normalized_hypothesis:
                missing_duration += _duration(stable)
            local_rate = _rate(
                float(_distance(normalized_source, normalized_hypothesis)),
                float(len(normalized_source)),
            )
            details.append({
                "start": group.start,
                "end": group.end,
                "ocr_source": source_text,
                "hypothesis_source": hypothesis_text,
                "ocr_source_disagreement_rate": local_rate,
            })
    unmatched_hypothesis = [
        cue for group in groups if not group.ocr for cue in group.hypothesis if _normalize(cue.text)
    ]
    metrics = {
        "high_stability_ocr_coverage": _rate(stable_source_duration, total_source_duration),
        "ocr_source_disagreement_rate": (
            _rate(float(edit_distance), float(reference_characters)) if reference_characters else None
        ),
        "ocr_text_without_asr_coverage_rate": _rate(missing_duration, stable_source_duration),
        "asr_without_ocr_coverage_rate": _rate(_duration(unmatched_hypothesis), _duration(hypothesis)),
        "duplicate_cue_rate": _duplicate_rate(hypothesis),
        "language_script_anomaly_rate": _script_anomaly_rate(hypothesis, language),
        "high_stability_reference_characters": reference_characters,
        "ocr_cue_count": len(ocr_cues),
        "asr_cue_count": len(hypothesis),
    }
    return metrics, details


def _target_disagreement(ocr_text: str, translation: str) -> float | None:
    reference = list(_normalize(ocr_text))
    if not reference:
        return None
    return _rate(float(_distance(reference, list(_normalize(translation)))), float(len(reference)))


def calculate_translation_signals(
    ocr_cues: Sequence[OcrCue], source: Sequence[Cue], translation: Sequence[Cue], *, tolerance: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_groups = align_by_time(ocr_cues, source, tolerance=tolerance)
    translation_groups = align_by_time(ocr_cues, translation, tolerance=tolerance)
    source_by_ocr = {
        cue.index: " ".join(item.text for item in group.hypothesis)
        for group in source_groups for cue in group.ocr
    }
    translation_by_ocr = {
        cue.index: " ".join(item.text for item in group.hypothesis)
        for group in translation_groups for cue in group.ocr
    }
    blocker_counts: dict[str, int] = {}
    target_distance = 0
    target_characters = 0
    abnormal_lengths = 0
    comparable = 0
    details: list[dict[str, Any]] = []
    for cue in ocr_cues:
        source_text = source_by_ocr.get(cue.index, "")
        translated_text = translation_by_ocr.get(cue.index, "")
        issues = list(blocking_translation_issues(source_text, translated_text, "zh-CN"))
        if cue.target_text and not _normalize(translated_text):
            issues.append("ocr_target_without_translation")
        source_len = len(_normalize(source_text))
        translated_len = len(_normalize(translated_text))
        if source_len and translated_len and not 0.2 <= translated_len / source_len <= 4.0:
            issues.append("abnormal_length_ratio")
            abnormal_lengths += 1
        for issue in dict.fromkeys(issues):
            blocker_counts[issue] = blocker_counts.get(issue, 0) + 1
        disagreement = _target_disagreement(cue.target_text, translated_text)
        if disagreement is not None:
            comparable += 1
            normalized_target = list(_normalize(cue.target_text))
            target_distance += _distance(normalized_target, list(_normalize(translated_text)))
            target_characters += len(normalized_target)
        details.append({
            "ocr_index": cue.index,
            "start": cue.start,
            "end": cue.end,
            "source": source_text,
            "ocr_target": cue.target_text,
            "translation": translated_text,
            "issues": list(dict.fromkeys(issues)),
            "ocr_target_disagreement_rate": disagreement,
            "needs_review": bool(issues or (disagreement is not None and disagreement >= 0.45)),
        })
    metrics = {
        "ocr_target_disagreement_rate": (
            _rate(float(target_distance), float(target_characters)) if target_characters else None
        ),
        "translation_blocking_issue_count": sum(blocker_counts.values()),
        "translation_issue_counts": blocker_counts,
        "abnormal_length_ratio_count": abnormal_lengths,
        "comparable_ocr_target_cue_count": comparable,
        "duplicate_cue_rate": _duplicate_rate(translation),
        "translation_cue_count": len(translation),
    }
    return metrics, details


def _project_path(project_root: Path, value: object, label: str, *, required: bool = True) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise OcrEvidenceError(f"{label} path is required")
        return None
    candidate = Path(raw)
    path = (candidate if candidate.is_absolute() else project_root / candidate).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise OcrEvidenceError(f"{label} must remain inside the project root") from exc
    if not path.is_file():
        raise OcrEvidenceError(f"{label} was not found")
    return path


def _safe_id(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", result):
        raise OcrEvidenceError(f"{label} may contain only letters, digits, '.', '_' and '-'")
    return result


def load_manifest(path: Path, project_root: Path) -> dict[str, Any]:
    raw = read_json(path, user_input=True)
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise OcrEvidenceError("manifest schema_version must be 1")
    rows = raw.get("samples")
    if not isinstance(rows, list) or not rows:
        raise OcrEvidenceError("manifest requires a non-empty samples list")
    samples: list[dict[str, Any]] = []
    sample_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise OcrEvidenceError("each sample must be an object")
        sample_id = _safe_id(row.get("id"), "sample id")
        if sample_id in sample_ids:
            raise OcrEvidenceError(f"duplicate sample id: {sample_id}")
        sample_ids.add(sample_id)
        baseline = row.get("baseline")
        if not isinstance(baseline, dict):
            raise OcrEvidenceError(f"sample '{sample_id}' requires baseline")
        candidates = row.get("candidates", [])
        if not isinstance(candidates, list):
            raise OcrEvidenceError(f"sample '{sample_id}' candidates must be a list")
        candidate_ids: set[str] = set()

        def output(raw_output: dict[str, Any], output_id: str) -> dict[str, Any]:
            if not isinstance(raw_output, dict):
                raise OcrEvidenceError(f"output '{output_id}' must be an object")
            return {
                "id": output_id,
                "source_srt": _project_path(project_root, raw_output.get("source_srt"), "source_srt"),
                "translation_srt": _project_path(
                    project_root, raw_output.get("translation_srt"), "translation_srt", required=False
                ),
            }

        normalized_candidates = []
        for candidate in candidates:
            candidate_id = _safe_id(candidate.get("id") if isinstance(candidate, dict) else "", "candidate id")
            if candidate_id in candidate_ids or candidate_id == "baseline":
                raise OcrEvidenceError(f"duplicate or reserved candidate id: {candidate_id}")
            candidate_ids.add(candidate_id)
            normalized_candidates.append(output(candidate, candidate_id))
        samples.append({
            "id": sample_id,
            "language": str(row.get("language") or "").strip().lower(),
            "tags": [str(tag).strip() for tag in row.get("tags", []) if str(tag).strip()],
            "ocr_srt": _project_path(project_root, row.get("ocr_srt"), "ocr_srt"),
            "ocr_sidecar": _project_path(
                project_root, row.get("ocr_sidecar"), "ocr_sidecar", required=False
            ),
            "baseline": output(baseline, "baseline"),
            "candidates": normalized_candidates,
        })
    return {"schema_version": SCHEMA_VERSION, "samples": samples}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _candidate_decision(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    base_asr, candidate_asr = baseline["asr_signals"], candidate["asr_signals"]
    coverage = candidate_asr["high_stability_ocr_coverage"]
    base_disagreement = base_asr["ocr_source_disagreement_rate"]
    candidate_disagreement = candidate_asr["ocr_source_disagreement_rate"]
    if coverage < MIN_HIGH_STABILITY_COVERAGE or base_disagreement is None or candidate_disagreement is None:
        return {"decision": "insufficient_evidence", "relative_disagreement_improvement": None}
    if base_disagreement == 0:
        improvement = 0.0 if candidate_disagreement == 0 else -1.0
    else:
        improvement = round((base_disagreement - candidate_disagreement) / base_disagreement, 6)
    no_rate_regression = all(
        candidate_asr[name] <= base_asr[name] + MAX_RATE_REGRESSION
        for name in (
            "ocr_text_without_asr_coverage_rate",
            "asr_without_ocr_coverage_rate",
            "duplicate_cue_rate",
        )
    )
    base_blockers = baseline.get("translation_signals", {}).get("translation_blocking_issue_count", 0)
    candidate_blockers = candidate.get("translation_signals", {}).get("translation_blocking_issue_count", 0)
    eligible = (
        improvement >= MIN_RELATIVE_DISAGREEMENT_IMPROVEMENT
        and no_rate_regression
        and candidate_blockers <= base_blockers
    )
    return {
        "decision": "eligible_for_gold_benchmark" if eligible else "rejected_by_weak_screen",
        "relative_disagreement_improvement": improvement,
        "rate_regression_within_limit": no_rate_regression,
        "translation_blockers_not_increased": candidate_blockers <= base_blockers,
        "apply_allowed": False,
    }


def _parse_json_object(value: str) -> dict[str, Any]:
    cleaned = str(value or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise OcrEvidenceError("LLM judge did not return a JSON object")
    parsed = json.loads(cleaned[start:end + 1])
    if not isinstance(parsed, dict):
        raise OcrEvidenceError("LLM judge result must be an object")
    preference = parsed.get("preference")
    if preference not in {"A", "B", "tie", "uncertain"}:
        raise OcrEvidenceError("LLM judge preference must be A, B, tie, or uncertain")
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise OcrEvidenceError("LLM judge confidence must be between 0 and 1")
    categories = parsed.get("categories")
    if not isinstance(categories, list) or not all(isinstance(item, str) for item in categories):
        raise OcrEvidenceError("LLM judge categories must be a string list")
    if not isinstance(parsed.get("reason"), str):
        raise OcrEvidenceError("LLM judge reason must be a string")
    return parsed


def _judge_prompt() -> str:
    return (
        "You are a conservative subtitle comparison judge. Burned OCR is noisy and its Chinese may be "
        "editorial or shifted to a neighboring cue. Compare translation A and B using the OCR source, "
        "burned Chinese, and one neighboring window on each side. Identify only clear omission, "
        "mistranslation, repetition, or cross-cue fragmentation. Do not assume the burned Chinese is ground "
        "truth. Return exactly one JSON object with preference=A|B|tie|uncertain, confidence=0..1, "
        "categories as an array, and a short reason."
    )


def _run_llm_judges(
    *, samples: list[dict[str, Any]], provider_id: str, maximum: int, cache_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    if maximum <= 0:
        return [], {"mode": "uncertain", "actual_requests": 0, "cache_hits": 0, "budget_exhausted": True}, []
    provider = resolve_provider_config(provider_id)
    if not provider.get("api_key"):
        raise OcrEvidenceError("the selected Provider has no API key")
    cache = read_json(cache_path) if cache_path.is_file() else {}
    if not isinstance(cache, dict):
        cache = {}
    judgments: list[dict[str, Any]] = []
    warnings: list[str] = []
    tracker = TranslationRequestTracker(mode="preview", max_extra_requests=maximum)
    cache_hits = 0
    candidates: list[dict[str, Any]] = []
    for sample in samples:
        baseline_details = sample["details"]["baseline"].get("translation", [])
        by_index = {item.get("ocr_index"): item for item in baseline_details}
        ordered_indices = sorted(index for index in by_index if isinstance(index, int))
        for output_id, output_details in sample["details"].items():
            if output_id == "baseline":
                continue
            candidate_by_index = {
                item.get("ocr_index"): item for item in output_details.get("translation", [])
            }
            for position, index in enumerate(ordered_indices):
                baseline = by_index[index]
                candidate = candidate_by_index.get(index)
                if not candidate or baseline.get("translation") == candidate.get("translation"):
                    continue
                if baseline.get("needs_review") is False and candidate.get("needs_review") is False:
                    continue
                candidates.append({
                    "sample_id": sample["id"],
                    "candidate_id": output_id,
                    "ocr_index": index,
                    "previous": by_index.get(ordered_indices[position - 1]) if position else None,
                    "current": baseline,
                    "candidate_translation": candidate.get("translation", ""),
                    "next": by_index.get(ordered_indices[position + 1]) if position + 1 < len(ordered_indices) else None,
                })
    for item in candidates:
        if tracker.actual_requests >= maximum:
            break
        identity = json.dumps(item, ensure_ascii=False, sort_keys=True)
        cache_key = hashlib.sha256(
            f"{provider_id}|{provider['llm_model']}|{RULE_VERSION}|{identity}".encode("utf-8")
        ).hexdigest()
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            judgment = cached
            cache_hits += 1
        else:
            swap = int(cache_key[-1], 16) % 2 == 1
            baseline_text = item["current"].get("translation", "")
            candidate_text = item["candidate_translation"]
            label_a, label_b = ((candidate_text, baseline_text) if swap else (baseline_text, candidate_text))
            payload = {
                "previous": item["previous"],
                "current": {
                    "source": item["current"].get("source", ""),
                    "burned_chinese": item["current"].get("ocr_target", ""),
                    "translation_A": label_a,
                    "translation_B": label_b,
                },
                "next": item["next"],
            }
            try:
                body = _build_request_body(
                    batch=payload,
                    effective_prompt=_judge_prompt(),
                    effective_model=provider["llm_model"],
                    temperature=0.0,
                    api_provider=provider["api_provider"],
                )
                response = _call_llm_api(
                    api_provider=provider["api_provider"],
                    api_base=provider["api_base"],
                    api_key=provider["api_key"],
                    body=body,
                    tracker=tracker,
                    request_is_extra=True,
                )
                judgment = _parse_json_object(_parse_api_response(provider["api_provider"], response))
                preference = judgment["preference"]
                if swap and preference in {"A", "B"}:
                    preference = "B" if preference == "A" else "A"
                judgment = {**judgment, "preference": preference}
                cache[cache_key] = judgment
                _atomic_json(cache_path, cache)
            except Exception as exc:
                warnings.append(
                    f"LLM judge failed for {item['sample_id']}/{item['candidate_id']}/"
                    f"{item['ocr_index']}: {type(exc).__name__}"
                )
                continue
        judgments.append({
            "sample_id": item["sample_id"],
            "candidate_id": item["candidate_id"],
            "ocr_index": item["ocr_index"],
            "judgment": judgment,
        })
    summary = {
        "mode": "uncertain",
        "provider_id": provider_id,
        "model_id": provider["llm_model"],
        "actual_requests": tracker.actual_requests,
        "cache_hits": cache_hits,
        "budget": maximum,
        "budget_exhausted": len(candidates) > len(judgments),
    }
    return judgments, summary, warnings


def _evaluate_output(
    output: dict[str, Any], ocr_cues: Sequence[OcrCue], language: str, tolerance: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _to_cues(read_srt(output["source_srt"]))
    asr_signals, asr_details = calculate_asr_signals(
        ocr_cues, source, language=language, tolerance=tolerance
    )
    result: dict[str, Any] = {"id": output["id"], "asr_signals": asr_signals}
    details: dict[str, Any] = {"asr": asr_details, "translation": []}
    if output["translation_srt"]:
        translation = _to_target_cues(read_srt(output["translation_srt"]))
        translation_signals, translation_details = calculate_translation_signals(
            ocr_cues, source, translation, tolerance=tolerance
        )
        result["translation_signals"] = translation_signals
        details["translation"] = translation_details
    return result, details


def _public_file_hashes(sample: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, Path] = {
        "ocr_srt": sample["ocr_srt"],
        "baseline_source_srt": sample["baseline"]["source_srt"],
    }
    if sample["ocr_sidecar"]:
        paths["ocr_sidecar"] = sample["ocr_sidecar"]
    if sample["baseline"]["translation_srt"]:
        paths["baseline_translation_srt"] = sample["baseline"]["translation_srt"]
    for candidate in sample["candidates"]:
        paths[f"candidate_{candidate['id']}_source_srt"] = candidate["source_srt"]
        if candidate["translation_srt"]:
            paths[f"candidate_{candidate['id']}_translation_srt"] = candidate["translation_srt"]
    return {name: _sha256(path) for name, path in paths.items()}


def _review_items(samples: Sequence[dict[str, Any]]) -> list[SubtitleItem]:
    items: list[SubtitleItem] = []
    for sample in samples:
        baseline = sample["details"]["baseline"]
        candidate_details = {
            key: value for key, value in sample["details"].items() if key != "baseline"
        }
        for detail in sorted(
            baseline.get("asr", []), key=lambda item: item["ocr_source_disagreement_rate"], reverse=True
        )[:20]:
            lines = [
                f"[{sample['id']}] OCR source: {detail['ocr_source']}",
                f"baseline ASR: {detail['hypothesis_source']}",
            ]
            for candidate_id, candidate in candidate_details.items():
                match = next(
                    (row for row in candidate.get("asr", []) if row["start"] == detail["start"]), None
                )
                if match:
                    lines.append(f"{candidate_id} ASR: {match['hypothesis_source']}")
            items.append(SubtitleItem(
                len(items) + 1,
                f"{_timestamp(detail['start'])} --> {_timestamp(detail['end'])}",
                "\n".join(lines),
            ))
        for detail in [
            row for row in baseline.get("translation", []) if row.get("needs_review")
        ][:20]:
            lines = [
                f"[{sample['id']}] source: {detail['source']}",
                f"burned Chinese: {detail['ocr_target']}",
                f"baseline translation: {detail['translation']}",
            ]
            for candidate_id, candidate in candidate_details.items():
                match = next(
                    (
                        row for row in candidate.get("translation", [])
                        if row.get("ocr_index") == detail.get("ocr_index")
                    ),
                    None,
                )
                if match:
                    lines.append(f"{candidate_id} translation: {match['translation']}")
            items.append(SubtitleItem(
                len(items) + 1,
                f"{_timestamp(detail['start'])} --> {_timestamp(detail['end'])}",
                "\n".join(lines),
            ))
    return items


def _mean(values: Iterable[float | int | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return round(sum(numbers) / len(numbers), 6) if numbers else None


def _aggregate_group(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    output_ids = sorted({output["id"] for sample in samples for output in sample["outputs"]})
    outputs: dict[str, Any] = {}
    for output_id in output_ids:
        matching = [
            output for sample in samples for output in sample["outputs"] if output["id"] == output_id
        ]
        decisions: dict[str, int] = {}
        for output in matching:
            decision = output.get("weak_screen", {}).get("decision")
            if decision:
                decisions[decision] = decisions.get(decision, 0) + 1
        outputs[output_id] = {
            "sample_count": len(matching),
            "ocr_source_disagreement_rate_mean": _mean(
                output["asr_signals"]["ocr_source_disagreement_rate"] for output in matching
            ),
            "high_stability_ocr_coverage_mean": _mean(
                output["asr_signals"]["high_stability_ocr_coverage"] for output in matching
            ),
            "ocr_text_without_asr_coverage_rate_mean": _mean(
                output["asr_signals"]["ocr_text_without_asr_coverage_rate"] for output in matching
            ),
            "duplicate_cue_rate_mean": _mean(
                output["asr_signals"]["duplicate_cue_rate"] for output in matching
            ),
            "translation_blocking_issue_count": sum(
                output.get("translation_signals", {}).get("translation_blocking_issue_count", 0)
                for output in matching
            ),
            "weak_screen_decisions": decisions,
        }
    return {"sample_count": len(samples), "outputs": outputs}


def _aggregates(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    tags = sorted({tag for sample in samples for tag in sample.get("tags", [])})
    candidate_ids = sorted({
        output["id"] for sample in samples for output in sample["outputs"] if output["id"] != "baseline"
    })
    return {
        "all_samples": _aggregate_group(samples),
        "by_tag": {
            tag: _aggregate_group([sample for sample in samples if tag in sample.get("tags", [])])
            for tag in tags
        },
        "by_candidate": {
            candidate_id: _aggregate_group([
                sample for sample in samples
                if any(output["id"] == candidate_id for output in sample["outputs"])
            ])
            for candidate_id in candidate_ids
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OCR Weak-Evidence Comparison",
        "",
        "> OCR is noisy weak evidence. These results are not CER/WER, ground truth, or permission to apply a candidate.",
        "",
        f"- Rule version: `{report['rule_version']}`",
        f"- Samples: {len(report['samples'])}",
        f"- LLM judge: `{report['llm_judge']['mode']}`",
        "",
        "| Sample | Output | Source disagreement | Stable coverage | Missing ASR | Duplicate | Translation blockers | Decision |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for sample in report["samples"]:
        for output in sample["outputs"]:
            asr = output["asr_signals"]
            translation = output.get("translation_signals", {})
            decision = output.get("weak_screen", {}).get("decision", "baseline")
            lines.append(
                f"| `{sample['id']}` | `{output['id']}` | {asr['ocr_source_disagreement_rate']} | "
                f"{asr['high_stability_ocr_coverage']} | {asr['ocr_text_without_asr_coverage_rate']} | "
                f"{asr['duplicate_cue_rate']} | {translation.get('translation_blocking_issue_count', '-')} | "
                f"`{decision}` |"
            )
    lines.extend([
        "",
        "Any `eligible_for_gold_benchmark` result must still pass the frozen gold benchmark, two complete rounds, and manual review.",
        "",
    ])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = resolve_runtime_paths()
    project_root = paths.project_root.resolve()
    manifest_path = _project_path(project_root, args.manifest, "manifest")
    assert manifest_path is not None
    manifest = load_manifest(manifest_path, project_root)
    if args.tolerance < 0:
        raise OcrEvidenceError("tolerance must not be negative")
    if not 0 <= args.max_llm_cues <= 100:
        raise OcrEvidenceError("max_llm_cues must be between 0 and 100")
    if args.llm_judge == "uncertain" and (not args.provider or args.max_llm_cues <= 0):
        raise OcrEvidenceError("uncertain LLM judging requires --provider and --max-llm-cues greater than zero")
    run_id = _safe_id(
        args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "run id"
    )
    output_root_value = Path(args.output_dir)
    output_base = (
        output_root_value if output_root_value.is_absolute() else project_root / output_root_value
    ).resolve()
    try:
        output_base.relative_to(project_root)
    except ValueError as exc:
        raise OcrEvidenceError("output_dir must remain inside the project root") from exc
    output_dir = output_base / run_id
    evaluated_samples: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    for sample in manifest["samples"]:
        ocr_cues, warnings = load_ocr_cues(sample["ocr_srt"], sample["ocr_sidecar"])
        all_warnings.extend(f"{sample['id']}: {warning}" for warning in warnings)
        baseline, baseline_details = _evaluate_output(
            sample["baseline"], ocr_cues, sample["language"], args.tolerance
        )
        outputs = [baseline]
        details = {"baseline": baseline_details}
        for candidate_config in sample["candidates"]:
            candidate, candidate_details = _evaluate_output(
                candidate_config, ocr_cues, sample["language"], args.tolerance
            )
            candidate["weak_screen"] = _candidate_decision(baseline, candidate)
            outputs.append(candidate)
            details[candidate["id"]] = candidate_details
        evaluated_samples.append({
            "id": sample["id"],
            "language": sample["language"],
            "tags": sample["tags"],
            "input_sha256": _public_file_hashes(sample),
            "outputs": outputs,
            "warnings": warnings,
            "details": details,
        })
    llm_summary: dict[str, Any] = {
        "mode": "off", "actual_requests": 0, "cache_hits": 0, "budget_exhausted": False
    }
    judgments: list[dict[str, Any]] = []
    if args.llm_judge == "uncertain":
        judgments, llm_summary, judge_warnings = _run_llm_judges(
            samples=evaluated_samples,
            provider_id=args.provider,
            maximum=args.max_llm_cues,
            cache_path=output_base / "llm-judge-cache.local.json",
        )
        all_warnings.extend(judge_warnings)
    public_samples = [
        {key: value for key, value in sample.items() if key != "details"}
        for sample in evaluated_samples
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "ocr_weak_evidence_comparison",
        "rule_version": RULE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "alignment": {"method": "time_only_monotonic_many_to_many", "tolerance_seconds": args.tolerance},
        "thresholds": {
            "high_stability": HIGH_STABILITY_THRESHOLD,
            "minimum_high_stability_coverage": MIN_HIGH_STABILITY_COVERAGE,
            "minimum_relative_disagreement_improvement": MIN_RELATIVE_DISAGREEMENT_IMPROVEMENT,
            "maximum_rate_regression": MAX_RATE_REGRESSION,
        },
        "llm_judge": llm_summary,
        "warnings": all_warnings,
        "samples": public_samples,
        "aggregates": _aggregates(public_samples),
        "caveat": (
            "OCR and burned translations are weak evidence, not ground truth. Candidate eligibility only "
            "permits subsequent gold-benchmark evaluation and never permits production apply."
        ),
    }
    local_details = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "samples": evaluated_samples,
        "llm_judgments": judgments,
        "contains_subtitle_text": True,
    }
    _atomic_json(output_dir / "summary.json", report)
    _atomic_text(output_dir / "summary.md", _markdown(report))
    _atomic_json(output_dir / "details.local.json", local_details)
    review_path = output_dir / "review_needed.srt"
    review_items = _review_items(evaluated_samples)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".review_needed.", suffix=".tmp", dir=str(output_dir)
    )
    os.close(descriptor)
    try:
        write_srt(review_items, Path(temporary))
        os.replace(temporary, review_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    print(json.dumps({
        "run_id": run_id,
        "sample_count": len(public_samples),
        "llm_requests": llm_summary["actual_requests"],
        "output": str(output_dir.relative_to(project_root)),
    }, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare OCR, ASR, and translations as non-ground-truth weak evidence."
    )
    parser.add_argument("--manifest", required=True, help="Project-local OCR evidence manifest JSON.")
    parser.add_argument("--output-dir", default="output/reports/ocr_evidence")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--tolerance", type=float, default=0.5)
    parser.add_argument("--llm-judge", choices=("off", "uncertain"), default="off")
    parser.add_argument("--provider", default="")
    parser.add_argument("--max-llm-cues", type=int, default=0)
    try:
        run(parser.parse_args())
    except (OcrEvidenceError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"OCR evidence comparison failed: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
