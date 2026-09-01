"""GO/NO-GO orchestrator and sealed-experiment spec tests."""

import pytest

from trading_gym.operations.validation_orchestrator import (
    Criterion,
    ValidationPhase,
    format_verdict,
    run_validation,
    verdict_to_json,
    VERDICT_GO,
    VERDICT_NO_GO,
)
from trading_gym.experiments.sealed_spec import (
    SealedExperiment,
    SealMismatch,
    seal_experiment,
    verify_seal,
)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def test_go_when_all_criteria_pass():
    phases = [
        ValidationPhase(
            name="sharpe",
            run=lambda: {"sharpe": 1.4},
            criteria=(
                Criterion(
                    "min_sharpe",
                    lambda: (True, "1.4>=1.0"),
                ),
            ),
        ),
    ]
    verdict = run_validation(phases)
    assert verdict.verdict == VERDICT_GO
    assert verdict.exit_code == 0
    assert not verdict.failures


def test_no_go_on_failed_criterion_and_isolated_crash():
    def boom():
        raise RuntimeError("exploding phase")

    phases = [
        ValidationPhase(
            name="drawdown",
            run=dict,
            criteria=(Criterion("max_dd_25", lambda: (False, "-0.41")),),
        ),
        ValidationPhase(name="chaos", run=boom, criteria=()),
    ]
    verdict = run_validation(phases)
    assert verdict.verdict == VERDICT_NO_GO
    assert verdict.exit_code == 1
    assert any("drawdown:max_dd_25" in f for f in verdict.failures)
    assert any("chaos:phase_crashed" in f for f in verdict.failures)
    chaos = next(p for p in verdict.phase_reports if p.name == "chaos")
    assert chaos.crashed and not chaos.passed


def test_phase_returning_non_dict_is_treated_as_crash():
    verdict = run_validation([ValidationPhase("bad", run=lambda: ["not-a-dict"])])
    assert verdict.verdict == VERDICT_NO_GO
    assert any("bad:phase_crashed" in f for f in verdict.failures)


def test_criterion_raising_counts_as_failure_not_crash():
    def bad_check():
        raise ZeroDivisionError

    verdict = run_validation(
        [ValidationPhase("p", run=dict, criteria=(Criterion("c", bad_check),))]
    )
    assert verdict.verdict == VERDICT_NO_GO
    assert any("criterion-raised" in f for f in verdict.failures)


def test_format_and_json_artifacts():
    phases = [ValidationPhase("ok", run=lambda: {"x": 1})]
    verdict = run_validation(phases)
    text = format_verdict(verdict)
    assert "verdict=GO" in text
    payload = verdict_to_json(verdict)
    assert '"verdict":"GO"' in payload.replace(": ", ":").replace('": "', '":"') or '"GO"' in payload


# ---------------------------------------------------------------------------
# Sealed experiment specs
# ---------------------------------------------------------------------------

SPEC = {"window": 20, "universe": ["A", "B"]}
TAPE = "a" * 64


def test_seal_binds_name_spec_and_tape():
    sealed = seal_experiment(name="exp-1", spec=SPEC, tape_sha256=TAPE)
    verify_seal(sealed, name="exp-1", spec=SPEC, tape_sha256=TAPE)  # no raise


def test_any_mismatch_raises_seal_mismatch_with_field_detail():
    sealed = seal_experiment(name="exp-1", spec=SPEC, tape_sha256=TAPE)

    with pytest.raises(SealMismatch) as name_err:
        verify_seal(sealed, name="exp-2", spec=SPEC, tape_sha256=TAPE)
    assert "name" in str(name_err.value)

    with pytest.raises(SealMismatch) as spec_err:
        verify_seal(sealed, name="exp-1", spec={**SPEC, "window": 21}, tape_sha256=TAPE)
    assert "spec" in str(spec_err.value)

    with pytest.raises(SealMismatch) as tape_err:
        verify_seal(sealed, name="exp-1", spec=SPEC, tape_sha256="b" * 64)
    assert "tape" in str(tape_err.value)


def test_spec_key_order_does_not_change_the_seal():
    s1 = seal_experiment(name="exp-1", spec={"a": 1, "b": 2}, tape_sha256=TAPE)
    s2 = seal_experiment(name="exp-1", spec={"b": 2, "a": 1}, tape_sha256=TAPE)
    assert s1.seal_id == s2.seal_id


def test_input_validation():
    with pytest.raises(ValueError):
        seal_experiment(name="Bad Name!", spec=SPEC, tape_sha256=TAPE)
    with pytest.raises(ValueError):
        seal_experiment(name="e1", spec="not-a-dict", tape_sha256=TAPE)
    with pytest.raises(ValueError):
        seal_experiment(name="e1", spec=SPEC, tape_sha256="nothex")


def test_sealed_experiment_is_immutable_frozen_dataclass():
    sealed = seal_experiment(name="exp-1", spec=SPEC, tape_sha256=TAPE)
    with pytest.raises(Exception):
        sealed.name = "tampered"
