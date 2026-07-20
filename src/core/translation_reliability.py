from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

VALID_RELIABILITY_MODES = {"off", "preview"}
DEFAULT_MAX_EXTRA_REQUESTS = 12
REPAIR_STRATEGY_VERSION = "window-v3-quality-chain"
REPAIR_REQUEST_TEMPERATURE = 0.0
LLM_BOILERPLATE_PATTERNS = (
    r"(?:好的|当然)[，,\s]*(?:以下|下面)(?:是|为)?.*(?:翻译|字幕)",
    r"(?:以下|下面)(?:是|为)?.*(?:翻译|字幕)",
    r"这是.*翻译(?:结果|版本|内容)?",
    r"翻译如下",
    r"我(?:可以|来)?帮你.*翻译",
    r"Here (?:is|are).*(?:translation|subtitle)",
    r"I(?:'ve| have) translated",
    r"Sure[!,\.\s]+here (?:is|are).*(?:translation|subtitle)",
    r"Certainly[!,\.\s]+here (?:is|are).*(?:translation|subtitle)",
    r"```",
)
UNTRANSLATED_INDICATORS = {
    "ja": r"[぀-ゟ゠-ヿ]",
    "ko": r"[가-힯]",
    "ar": r"[؀-ۿ]",
    "th": r"[฀-๿]",
    "ru": r"[Ѐ-ӿ]",
}


def normalize_reliability_config(value: object = None, *, max_extra_requests: object = None) -> dict:
    raw = value if isinstance(value, dict) else {}
    mode_value = value if isinstance(value, str) else raw.get("mode", "off")
    mode = str(mode_value or "off").strip()
    if mode not in VALID_RELIABILITY_MODES:
        raise ValueError("translation reliability mode must be 'off' or 'preview'")
    limit_value = raw.get("max_extra_requests", max_extra_requests)
    if limit_value in (None, ""):
        limit_value = DEFAULT_MAX_EXTRA_REQUESTS
    try:
        limit = int(limit_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("translation max extra requests must be an integer") from exc
    if not 0 <= limit <= 50:
        raise ValueError("translation max extra requests must be between 0 and 50")
    return {"mode": mode, "max_extra_requests": limit}


def _normalized_text(value: str) -> str:
    return re.sub(r"[^\w一-鿿]+", "", value.casefold(), flags=re.UNICODE)


def adjacent_translation_overlap_count(values: list[str]) -> int:
    """Count conservative duplicate/containment regressions between adjacent cues."""
    normalized = [_normalized_text(value) for value in values]
    count = 0
    for left, right in zip(normalized, normalized[1:], strict=False):
        if min(len(left), len(right)) < 4:
            continue
        if left == right or left in right or right in left:
            count += 1
    return count


def build_repair_windows(
    item_count: int,
    issue_positions: dict[int, tuple[str, ...]],
) -> list[tuple[int, int, tuple[int, ...]]]:
    """Expand blocker positions by one cue and merge touching repair windows."""
    windows: list[tuple[int, int, list[int]]] = []
    for position in sorted(issue_positions):
        if position < 0 or position >= item_count:
            raise ValueError("repair issue position is outside the subtitle range")
        start = max(0, position - 1)
        end = min(item_count, position + 2)
        if windows and start <= windows[-1][1]:
            previous_start, previous_end, blockers = windows[-1]
            windows[-1] = (previous_start, max(previous_end, end), blockers + [position])
        else:
            windows.append((start, end, [position]))
    return [(start, end, tuple(blockers)) for start, end, blockers in windows]


def blocking_translation_issues(
    source_text: str, translation_text: str, target_language: str,
) -> tuple[str, ...]:
    """Return conservative, text-only issue codes safe for automatic repair."""
    source = str(source_text or "").strip()
    translation = str(translation_text or "").strip()
    issues: list[str] = []
    if not translation:
        return ("empty_translation",)
    if any(re.search(pattern, translation, re.IGNORECASE) for pattern in LLM_BOILERPLATE_PATTERNS):
        issues.append("llm_boilerplate")
    source_normalized = _normalized_text(source)
    translated_normalized = _normalized_text(translation)
    if len(source_normalized) >= 4 and source_normalized == translated_normalized:
        issues.append("identical_translation")
    if target_language in {"zh-CN", "zh-TW"}:
        for pattern in UNTRANSLATED_INDICATORS.values():
            if len(re.findall(pattern, translation)) >= 3:
                issues.append("possibly_untranslated")
                break
    return tuple(dict.fromkeys(issues))


class TranslationReliabilityError(RuntimeError):
    def __init__(self, message: str, *, kind: str, splittable: bool = False, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.splittable = splittable
        self.status = status


class TranslationBudgetExceeded(TranslationReliabilityError):
    def __init__(self) -> None:
        super().__init__(
            "Translation reliability extra-request budget was exhausted.",
            kind="budget_exhausted",
            splittable=False,
        )


class TranslationTotalRequestLimitExceeded(TranslationReliabilityError):
    def __init__(self, limit: int) -> None:
        super().__init__(
            f"Translation HTTP request limit ({limit}) was exhausted.",
            kind="total_request_limit_exhausted",
            splittable=False,
        )


@dataclass
class TranslationRequestTracker:
    mode: str = "off"
    max_extra_requests: int = DEFAULT_MAX_EXTRA_REQUESTS
    max_total_requests: int | None = None
    actual_requests: int = 0
    extra_requests: int = 0

    def before_request(self, *, extra: bool) -> None:
        if (
            self.max_total_requests is not None
            and self.actual_requests >= self.max_total_requests
        ):
            raise TranslationTotalRequestLimitExceeded(self.max_total_requests)
        if extra and self.mode == "preview" and self.extra_requests >= self.max_extra_requests:
            raise TranslationBudgetExceeded()
        self.actual_requests += 1
        if extra:
            self.extra_requests += 1

    @property
    def budget_exhausted(self) -> bool:
        return self.mode == "preview" and self.extra_requests >= self.max_extra_requests

    @property
    def total_request_limit_exhausted(self) -> bool:
        return (
            self.max_total_requests is not None
            and self.actual_requests >= self.max_total_requests
        )


@dataclass
class TranslationRunSummary:
    mode: str
    total_items: int
    strategy_mode: str = "standard"
    cache_hits: int = 0
    actual_requests: int = 0
    extra_requests: int = 0
    split_count: int = 0
    repaired_ids: list[int] = field(default_factory=list)
    unresolved_ids: list[int] = field(default_factory=list)
    issue_counts: dict[str, int] = field(default_factory=dict)
    repair_windows_attempted: int = 0
    repair_windows_accepted: int = 0
    repair_windows_rejected: int = 0
    adjacent_overlap_rejections: int = 0
    repair_window_rejection_counts: dict[str, int] = field(default_factory=dict)
    rejected_candidate_issue_counts: dict[str, int] = field(default_factory=dict)
    flash_initial_requests: int = 0
    flash_correction_requests: int = 0
    quality_candidate_requests: int = 0
    judge_requests: int = 0
    candidate_stage_rejection_counts: dict[str, int] = field(default_factory=dict)
    judge_rejection_counts: dict[str, int] = field(default_factory=dict)
    quality_model_unavailable: bool = False
    initial_pass_requests: int = 0
    reflection_pass_requests: int = 0
    final_pass_requests: int = 0
    compression_pass_requests: int = 0
    three_pass_cached_batches: int = 0
    three_pass_completed_batches: int = 0
    budget_violation_ids: list[int] = field(default_factory=list)
    semantic_analysis_requests: int = 0
    semantic_review_requests: int = 0
    semantic_repair_requests: int = 0
    semantic_judge_requests: int = 0
    semantic_consistency_requests: int = 0
    semantic_cached_batches: int = 0
    semantic_no_issue_ids: list[int] = field(default_factory=list)
    semantic_repair_candidate_ids: list[int] = field(default_factory=list)
    semantic_repair_accepted_ids: list[int] = field(default_factory=list)
    semantic_repair_rejected_ids: list[int] = field(default_factory=list)
    semantic_repair_tie_ids: list[int] = field(default_factory=list)
    semantic_consistency_issue_count: int = 0
    semantic_suspected_asr_error_count: int = 0
    wenyi_analysis_requests: int = 0
    wenyi_translation_requests: int = 0
    wenyi_review_requests: int = 0
    wenyi_cross_line_requests: int = 0
    wenyi_repair_requests: int = 0
    wenyi_judge_requests: int = 0
    wenyi_shortening_requests: int = 0
    wenyi_shortening_judge_requests: int = 0
    wenyi_consistency_requests: int = 0
    wenyi_cached_batches: int = 0
    wenyi_repair_accepted_ids: list[int] = field(default_factory=list)
    wenyi_shortening_accepted_ids: list[int] = field(default_factory=list)
    quality_model_fallback: bool = False
    failed_stage: str = ""
    budget_exhausted: bool = False
    total_request_limit_exhausted: bool = False
    review_required: bool = False

    def safe_summary(self) -> dict:
        payload = asdict(self)
        repaired_ids = payload.pop("repaired_ids")
        unresolved_ids = payload.pop("unresolved_ids")
        budget_violation_ids = payload.pop("budget_violation_ids")
        semantic_no_issue_ids = payload.pop("semantic_no_issue_ids")
        semantic_repair_candidate_ids = payload.pop("semantic_repair_candidate_ids")
        semantic_repair_accepted_ids = payload.pop("semantic_repair_accepted_ids")
        semantic_repair_rejected_ids = payload.pop("semantic_repair_rejected_ids")
        semantic_repair_tie_ids = payload.pop("semantic_repair_tie_ids")
        wenyi_repair_accepted_ids = payload.pop("wenyi_repair_accepted_ids")
        wenyi_shortening_accepted_ids = payload.pop("wenyi_shortening_accepted_ids")
        payload["repaired_count"] = len(repaired_ids)
        payload["unresolved_count"] = len(unresolved_ids)
        payload["budget_violation_count"] = len(budget_violation_ids)
        payload["semantic_no_issue_count"] = len(semantic_no_issue_ids)
        payload["semantic_repair_candidate_count"] = len(
            semantic_repair_candidate_ids
        )
        payload["semantic_repair_accepted_count"] = len(
            semantic_repair_accepted_ids
        )
        payload["semantic_repair_rejected_count"] = len(
            semantic_repair_rejected_ids
        )
        payload["semantic_repair_tie_count"] = len(semantic_repair_tie_ids)
        payload["wenyi_repair_accepted_count"] = len(wenyi_repair_accepted_ids)
        payload["wenyi_shortening_accepted_count"] = len(
            wenyi_shortening_accepted_ids
        )
        return payload


ProgressCallback = Callable[[dict], None]
