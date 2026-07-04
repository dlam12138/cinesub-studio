from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from encoding_utils import read_json, write_json, write_text


CLASSIFICATIONS = (
    "keep_auto",
    "prefer_forced_fr",
    "prefer_forced_en",
    "needs_review",
    "skip_window",
)
LIMITATION_TEXT = (
    "This analyzer does not prove transcript correctness. It only summarizes M7.1 ASR "
    "comparison evidence and produces conservative routing suggestions for future design."
)
TARGET_LANGUAGES = {"fr", "en"}


class AnalyzerInputError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    confidence_threshold: float = 0.70
    min_segments: int = 1


@dataclass(frozen=True)
class RunEvidence:
    detected_language: str
    detected_language_probability: float | None
    segment_count: int
    preview: str
    has_error: bool
    usable: bool

    def to_summary(self) -> dict[str, Any]:
        return {
            "detected_language": self.detected_language,
            "detected_language_probability": self.detected_language_probability,
            "segment_count": self.segment_count,
            "has_error": self.has_error,
            "usable": self.usable,
        }


def main() -> int:
    args = parse_args()
    settings = Settings(
        confidence_threshold=args.confidence_threshold,
        min_segments=args.min_segments,
    )
    try:
        summary = analyze_reports(args.input_files, settings)
        markdown = render_markdown(summary)
        if args.output_json:
            _write_json_output(Path(args.output_json), summary)
        if args.output_md:
            _write_text_output(Path(args.output_md), markdown)
    except (AnalyzerInputError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(markdown)
    return 0


def _write_json_output(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)


def _write_text_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze M7.1 segment ASR reports and produce routing evidence."
    )
    parser.add_argument("input_files", nargs="+", help="M7.1 JSON report file(s).")
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.70,
        help="Minimum auto language probability for keep_auto.",
    )
    parser.add_argument(
        "--min-segments",
        type=int,
        default=1,
        help="Minimum segment count for a run to be usable.",
    )
    parser.add_argument("--output-json", default=None, help="Optional JSON summary path.")
    parser.add_argument("--output-md", default=None, help="Optional Markdown summary path.")
    args = parser.parse_args()
    if not 0 <= args.confidence_threshold <= 1:
        parser.error("--confidence-threshold must be between 0 and 1")
    if args.min_segments < 0:
        parser.error("--min-segments must be greater than or equal to 0")
    return args


def analyze_reports(input_files: list[str], settings: Settings) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    resolved_inputs: list[str] = []
    for input_file in input_files:
        path = Path(input_file)
        if not path.is_file():
            raise AnalyzerInputError(f"Input report not found: {path}")
        resolved_inputs.append(str(path))
        report = _read_report(path)
        windows.extend(_analyze_report(path, report, settings))

    counts = {classification: 0 for classification in CLASSIFICATIONS}
    for window in windows:
        counts[window["classification"]] += 1

    return {
        "schema_version": 1,
        "input_files": resolved_inputs,
        "settings": {
            "confidence_threshold": settings.confidence_threshold,
            "min_segments": settings.min_segments,
        },
        "summary": {
            "total_windows": len(windows),
            **counts,
        },
        "windows": windows,
    }


def _read_report(path: Path) -> dict[str, Any]:
    try:
        data = read_json(path, user_input=True)
    except Exception as exc:
        raise AnalyzerInputError(f"Invalid JSON report {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AnalyzerInputError(f"Invalid report {path}: top-level JSON must be an object")
    return data


def _analyze_report(path: Path, report: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    raw_windows = report.get("windows")
    if not isinstance(raw_windows, list):
        raise AnalyzerInputError(f"Invalid report {path}: missing windows list")

    analyzed: list[dict[str, Any]] = []
    for position, raw_window in enumerate(raw_windows):
        if not isinstance(raw_window, dict):
            raise AnalyzerInputError(f"Invalid report {path}: window {position} must be an object")
        analyzed.append(_analyze_window(path, position, raw_window, settings))
    return analyzed


def _analyze_window(
    path: Path,
    position: int,
    raw_window: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    start_seconds, end_seconds = _window_times(path, position, raw_window)
    runs = _window_runs(path, position, raw_window, settings)
    classification, reason = classify_window(runs, settings)
    return {
        "source_file": str(path),
        "window_index": _window_index(raw_window, position),
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "classification": classification,
        "reason": reason,
        "auto": runs["auto"].to_summary(),
        "forced_fr": runs["forced_fr"].to_summary(),
        "forced_en": runs["forced_en"].to_summary(),
    }


def _window_times(path: Path, position: int, raw_window: dict[str, Any]) -> tuple[float, float]:
    source = raw_window.get("window") if isinstance(raw_window.get("window"), dict) else raw_window
    try:
        start = float(source["start_seconds"])
        end = float(source["end_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalyzerInputError(
            f"Invalid report {path}: window {position} requires start_seconds and end_seconds"
        ) from exc
    if end < start:
        raise AnalyzerInputError(f"Invalid report {path}: window {position} end is before start")
    return start, end


def _window_index(raw_window: dict[str, Any], position: int) -> int:
    value = raw_window.get("window_index", position)
    try:
        return int(value)
    except (TypeError, ValueError):
        return position


def _window_runs(
    path: Path,
    position: int,
    raw_window: dict[str, Any],
    settings: Settings,
) -> dict[str, RunEvidence]:
    if isinstance(raw_window.get("runs"), dict):
        raw_runs = _runs_from_mapping(raw_window["runs"])
    elif isinstance(raw_window.get("results"), list):
        raw_runs = _runs_from_results(path, position, raw_window["results"])
    else:
        raise AnalyzerInputError(
            f"Invalid report {path}: window {position} requires runs object or results list"
        )

    return {
        "auto": normalize_run(raw_runs.get("auto"), settings),
        "forced_fr": normalize_run(raw_runs.get("forced_fr"), settings),
        "forced_en": normalize_run(raw_runs.get("forced_en"), settings),
    }


def _runs_from_mapping(raw_runs: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in raw_runs.items():
        canonical = _canonical_mode(key)
        if canonical:
            normalized[canonical] = value
    return normalized


def _runs_from_results(path: Path, position: int, results: list[Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for result_index, result in enumerate(results):
        if not isinstance(result, dict):
            raise AnalyzerInputError(
                f"Invalid report {path}: window {position} result {result_index} must be an object"
            )
        mode = result.get("mode")
        canonical = _canonical_mode(mode)
        if not canonical and result.get("requested_language") in ("fr", "en"):
            canonical = f"forced_{result['requested_language']}"
        if not canonical:
            raise AnalyzerInputError(
                f"Invalid report {path}: window {position} result {result_index} has unknown mode"
            )
        normalized[canonical] = result
    return normalized


def _canonical_mode(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "auto": "auto",
        "forced-fr": "forced_fr",
        "forced-en": "forced_en",
        "fr": "forced_fr",
        "en": "forced_en",
    }
    return aliases.get(text)


def normalize_run(raw_run: Any, settings: Settings) -> RunEvidence:
    if not isinstance(raw_run, dict):
        return RunEvidence("", None, 0, "", True, False)

    error = str(raw_run.get("error") or "").strip()
    preview = str(raw_run.get("preview", raw_run.get("text_preview", "")) or "").strip()
    probability = _optional_float(
        raw_run.get("detected_language_probability", raw_run.get("language_probability"))
    )
    segment_count = _non_negative_int(raw_run.get("segment_count"))
    detected_language = str(raw_run.get("detected_language") or "").strip().lower()
    has_error = bool(error)
    usable = not has_error and segment_count >= settings.min_segments and bool(preview)
    return RunEvidence(
        detected_language=detected_language,
        detected_language_probability=probability,
        segment_count=segment_count,
        preview=preview,
        has_error=has_error,
        usable=usable,
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def classify_window(
    runs: dict[str, RunEvidence],
    settings: Settings,
) -> tuple[str, str]:
    auto = runs["auto"]
    forced_fr = runs["forced_fr"]
    forced_en = runs["forced_en"]
    usable_forced = [run for run in (forced_fr, forced_en) if run.usable]

    if not auto.usable and not usable_forced:
        return "skip_window", "No usable ASR run had text, enough segments, and no error."

    if _auto_is_confident(auto, settings):
        matching_forced = forced_fr if auto.detected_language == "fr" else forced_en
        opposite_forced = forced_en if auto.detected_language == "fr" else forced_fr
        if _strong_forced_contradiction(auto, opposite_forced, settings):
            return "needs_review", "Auto is confident, but the opposite forced-language run is also strong."
        if not matching_forced.usable and opposite_forced.usable:
            return "needs_review", "Auto is confident, but only the opposite forced-language run is usable."
        return "keep_auto", "Auto is confident for a target language without stronger contradiction."

    if forced_fr.usable and forced_en.usable:
        winner = _forced_winner(forced_fr, forced_en)
        if winner is None:
            return "needs_review", "Both forced-language runs look usable with conflicting evidence."
        classification = "prefer_forced_fr" if winner == "fr" else "prefer_forced_en"
        return classification, f"Auto is weak and forced-{winner} has stronger metadata evidence."

    if forced_fr.usable:
        return "prefer_forced_fr", "Auto is weak and forced-fr is the only usable forced-language run."
    if forced_en.usable:
        return "prefer_forced_en", "Auto is weak and forced-en is the only usable forced-language run."

    return "needs_review", "Auto is usable but not confident enough for a safe routing recommendation."


def _auto_is_confident(auto: RunEvidence, settings: Settings) -> bool:
    probability = auto.detected_language_probability
    return (
        auto.usable
        and auto.detected_language in TARGET_LANGUAGES
        and probability is not None
        and probability >= settings.confidence_threshold
    )


def _strong_forced_contradiction(
    auto: RunEvidence,
    opposite_forced: RunEvidence,
    settings: Settings,
) -> bool:
    if not opposite_forced.usable:
        return False
    probability = opposite_forced.detected_language_probability
    if probability is None:
        return False
    if opposite_forced.detected_language not in TARGET_LANGUAGES:
        return False
    return probability >= settings.confidence_threshold and probability >= (
        auto.detected_language_probability or 0
    )


def _forced_winner(forced_fr: RunEvidence, forced_en: RunEvidence) -> str | None:
    if forced_fr.detected_language == "fr" and forced_en.detected_language == "en":
        return None
    fr_score = _forced_score(forced_fr, "fr")
    en_score = _forced_score(forced_en, "en")
    if fr_score == en_score:
        return None
    return "fr" if fr_score > en_score else "en"


def _forced_score(run: RunEvidence, expected_language: str) -> tuple[int, float, int]:
    language_match = 1 if run.detected_language == expected_language else 0
    probability = run.detected_language_probability if run.detected_language_probability is not None else -1.0
    return (language_match, probability, run.segment_count)


def render_markdown(summary: dict[str, Any]) -> str:
    settings = summary.get("settings", {})
    counts = summary.get("summary", {})
    lines = [
        "# M7.2 Segment ASR Routing Evidence Summary",
        "",
        "## Settings",
        "",
        f"- Confidence threshold: `{settings.get('confidence_threshold', '')}`",
        f"- Minimum segments: `{settings.get('min_segments', '')}`",
        "",
        "## Aggregate Results",
        "",
        f"- Total windows: `{counts.get('total_windows', 0)}`",
    ]
    for classification in CLASSIFICATIONS:
        lines.append(f"- {classification}: `{counts.get(classification, 0)}`")

    lines.extend(
        [
            "",
            "## Per-Window Decisions",
            "",
            "| Source | Window | Time | Classification | Reason |",
            "| ------ | ------ | ---- | -------------- | ------ |",
        ]
    )
    for window in summary.get("windows", []):
        lines.append(
            "| {source} | {index} | {start:.3f}-{end:.3f}s | {classification} | {reason} |".format(
                source=_md_cell(Path(str(window.get("source_file", ""))).name),
                index=_md_cell(window.get("window_index", "")),
                start=float(window.get("start_seconds") or 0),
                end=float(window.get("end_seconds") or 0),
                classification=_md_cell(window.get("classification", "")),
                reason=_md_cell(window.get("reason", "")),
            )
        )

    lines.extend(
        [
            "",
            "## Notes And Limitations",
            "",
            f"- {LIMITATION_TEXT}",
            "- Forced-language runs are comparison evidence, not proof of semantic transcript correctness.",
            "- M7.2 does not change production transcription, subtitle generation, Web jobs, or pipeline behavior.",
            "",
        ]
    )
    return "\n".join(lines)


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


if __name__ == "__main__":
    raise SystemExit(main())
