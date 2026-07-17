from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


EXPERIMENT_MODES = {"off", "dry_run", "apply"}


@dataclass(frozen=True)
class AsrExperimentPlan:
    candidate_id: str
    mode: str = "off"
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in EXPERIMENT_MODES:
            raise ValueError(f"unsupported ASR experiment mode: {self.mode}")
        if not self.candidate_id.strip():
            raise ValueError("candidate_id is required")


@dataclass(frozen=True)
class AsrExperimentResult:
    candidate_id: str
    mode: str
    status: str
    output_affected: bool
    selected: str
    baseline: Any
    candidate: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str = ""


def run_asr_experiment(
    plan: AsrExperimentPlan,
    *,
    baseline: Any,
    candidate_runner: Callable[[dict[str, Any]], tuple[Any, dict[str, Any]]],
) -> AsrExperimentResult:
    """Run an ASR candidate behind an explicit mode without mutating production defaults."""
    if plan.mode == "off":
        return AsrExperimentResult(
            plan.candidate_id, "off", "disabled", False, "baseline", baseline,
            evidence={"candidate_executed": False},
        )
    try:
        candidate, evidence = candidate_runner(dict(plan.parameters))
    except Exception as exc:
        return AsrExperimentResult(
            plan.candidate_id, plan.mode, "fallback", False, "baseline", baseline,
            evidence={"candidate_executed": True, "error_category": type(exc).__name__},
            fallback_reason=str(exc)[:300],
        )
    if candidate is None:
        return AsrExperimentResult(
            plan.candidate_id, plan.mode, "fallback", False, "baseline", baseline,
            evidence=dict(evidence or {}), fallback_reason="candidate returned no output",
        )
    if plan.mode == "dry_run":
        return AsrExperimentResult(
            plan.candidate_id, "dry_run", "evaluated", False, "baseline", baseline,
            candidate=candidate, evidence=dict(evidence or {}),
        )
    return AsrExperimentResult(
        plan.candidate_id, "apply", "applied", True, "candidate", baseline,
        candidate=candidate, evidence=dict(evidence or {}),
    )
