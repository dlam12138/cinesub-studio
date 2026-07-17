from __future__ import annotations

import pytest

from asr_experiment import AsrExperimentPlan, run_asr_experiment


def test_off_never_executes_candidate() -> None:
    result = run_asr_experiment(
        AsrExperimentPlan("candidate", "off"),
        baseline="baseline",
        candidate_runner=lambda _params: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert result.selected == "baseline"
    assert result.output_affected is False


def test_dry_run_preserves_baseline_and_records_candidate() -> None:
    result = run_asr_experiment(
        AsrExperimentPlan("candidate", "dry_run", {"beam": 3}),
        baseline="baseline",
        candidate_runner=lambda params: ("candidate", {"beam": params["beam"]}),
    )
    assert result.selected == "baseline"
    assert result.candidate == "candidate"
    assert result.evidence == {"beam": 3}
    assert result.output_affected is False


def test_apply_uses_candidate_only_on_success_and_falls_back_on_failure() -> None:
    applied = run_asr_experiment(
        AsrExperimentPlan("candidate", "apply"),
        baseline="baseline",
        candidate_runner=lambda _params: ("candidate", {"ok": True}),
    )
    assert applied.selected == "candidate"
    assert applied.output_affected is True
    fallback = run_asr_experiment(
        AsrExperimentPlan("candidate", "apply"),
        baseline="baseline",
        candidate_runner=lambda _params: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    assert fallback.selected == "baseline"
    assert fallback.output_affected is False
    assert fallback.evidence["error_category"] == "RuntimeError"


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        AsrExperimentPlan("candidate", "automatic")
