import pytest
from trading_gym.agent.intelligence.context_intervention import (
    ContextAdvice,
    ContextAdviceCache,
)
from trading_gym.agent.intelligence.decision_journal import DecisionJournal
from trading_gym.agent.intelligence.types import ActionKind, DecisionSignal, UncertainDecision

SIGNAL = DecisionSignal(
    "local-rl", ActionKind.LONG, 2, 0.7, "baseline", source_id="rl-bundle"
)


def cache(tmp_path, clock=lambda: 10):
    j = DecisionJournal(tmp_path / "trace.db", clock=clock)
    j.record_decision(
        decision_id="d",
        run_id="run",
        account_id="paper",
        mode="paper",
        configured_backend="rl",
        signal=SIGNAL,
        effective_target=2,
        observed_at=5,
        context_sha256="a" * 64,
        model_bundle_id="bundle",
    )
    return ContextAdviceCache(j)


def advice(**kwargs):
    values = dict(
        advice_id="a",
        source_id="native-provider",
        model_id="jev-pinned",
        scope_id="BTC-paper",
        input_sha256="b" * 64,
        input_cutoff_at=5,
        received_at=10,
        expires_at=20,
        max_abs_leverage=0.5,
    )
    values.update(kwargs)
    return ContextAdvice(**values)


def test_record_only_and_restriction_preserve_actual_source(tmp_path):
    c = cache(tmp_path)
    assert c.record(decision_id="d", advice=advice())
    assert not c.record(decision_id="d", advice=advice())
    assert (
        c.evaluate(
            SIGNAL, source_id="native-provider", scope_id="BTC-paper", as_of=11
        ).signal
        is SIGNAL
    )
    out = c.evaluate(
        SIGNAL,
        source_id="native-provider",
        scope_id="BTC-paper",
        as_of=11,
        mode="restriction_only",
    )
    assert (
        out.signal.target_leverage == 0.5
        and out.signal.backend == "local-rl"
        and out.signal.source_id == "rl-bundle"
    )


def test_expiry_and_revocation_do_not_restore_unrestricted_target(tmp_path):
    c = cache(tmp_path)
    c.record(decision_id="d", advice=advice())
    with pytest.raises(UncertainDecision):
        c.evaluate(
            SIGNAL,
            source_id="native-provider",
            scope_id="BTC-paper",
            as_of=20,
            mode="restriction_only",
        )
    c.revoke(decision_id="d", advice_id="a", revoked_at=12)
    with pytest.raises(UncertainDecision):
        c.latest(source_id="native-provider", scope_id="BTC-paper", as_of=13)
    assert (
        c.latest(source_id="native-provider", scope_id="BTC-paper", as_of=11).advice_id
        == "a"
    )


def test_delayed_record_does_not_affect_prior_decisions(tmp_path):
    c = cache(tmp_path, clock=lambda: 15)
    c.record(decision_id="d", advice=advice())
    with pytest.raises(UncertainDecision):
        c.latest(source_id="native-provider", scope_id="BTC-paper", as_of=14)
    assert (
        c.latest(source_id="native-provider", scope_id="BTC-paper", as_of=16).model_id
        == "jev-pinned"
    )


def test_large_cap_never_scales_candidate_up(tmp_path):
    c = cache(tmp_path)
    c.record(decision_id="d", advice=advice(max_abs_leverage=10))
    assert (
        c.evaluate(
            SIGNAL,
            source_id="native-provider",
            scope_id="BTC-paper",
            as_of=11,
            mode="restriction_only",
        ).signal.target_leverage
        == 2
    )
