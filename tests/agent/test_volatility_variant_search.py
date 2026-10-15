"""Volatility percentile lookback is part of the bounded strategy-search space."""

from trading_gym.agent.strategy_variants import (
    mutation_material,
    propose_variants,
)
from trading_gym.strategy_lab.templates import template_catalogue
from trading_gym.strategy_lab.validation import validate_strategy_spec


def _volatility_lookback(strategy):
    return next(
        int(item.params["lookback"])
        for item in strategy.indicators
        if item.name == "realized_volatility"
    )


def test_volatility_lookback_is_research_mutable_without_shortening_horizon_to_50():
    original = template_catalogue()["volatility_expansion_breakout"]
    source_lookback = _volatility_lookback(original)
    assert source_lookback == 100
    variants = propose_variants(original, count=16, seed=11)
    assert variants
    values = {_volatility_lookback(item) for item in variants}
    assert values != {source_lookback}
    assert all(2 <= horizon <= 10_000 for horizon in values)
    assert any(horizon > 50 for horizon in values)
    assert all(validate_strategy_spec(item).valid for item in variants)
    assert _volatility_lookback(original) == source_lookback


def test_lookback_mutations_are_deterministic_and_do_not_clone_seed():
    original = template_catalogue()["volatility_expansion_breakout"]
    a = propose_variants(original, count=8, seed=121)
    b = propose_variants(original, count=8, seed=121)
    assert [mutation_material(item) for item in a] == [
        mutation_material(item) for item in b
    ]
    assert len({mutation_material(item) for item in a}) == len(a)
    assert mutation_material(original) not in {
        mutation_material(item) for item in a
    }
