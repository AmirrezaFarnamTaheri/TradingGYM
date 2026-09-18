"""Grammar-bounded strategy mutation and the new template families (ADR-034).

Two things the forge depends on and the ``strategy.propose_variants`` tool
exposes directly:

**Mutation is bounded by the grammar, not by convention.** Variants are
built through ``StrategySpec``, so an out-of-range period, an unknown
parameter, or a MACD with ``fast >= slow`` cannot be produced — it would
raise at construction. Mutation is a pure function of
(spec, count, seed): deterministic, duplicate-free, and structure
preserving (which indicators exist and which operators compare them are
*structural* changes under ADR-033 and are never jittered).

**The new families are evaluable, not decorative.** Each added template
validates, computes over a real frame, and produces both entries and
exits — a family that can never enter is a stub, and the eye-exam style
assertions here are what stop one from landing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.strategy_variants import (
    MAX_PERIOD,
    MIN_PERIOD,
    mutation_material,
    propose_variants,
    validate_range,
)
from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.models import (
    Condition,
    ConditionGroup,
    IndicatorSpec,
    Operand,
    Operator,
    StrategySpec,
)
from trading_gym.strategy_lab.templates import template_catalogue
from trading_gym.strategy_lab.validation import validate_strategy_spec

#: Families added in ADR-034 (the round-7 strategy development work).
NEW_FAMILIES = (
    "atr_volatility_breakout",
    "obv_trend_confirmation",
    "bollinger_squeeze_expansion",
    "macd_histogram_pullback",
)


def trending_frame(n: int = 600, seed: int = 11) -> pd.DataFrame:
    """A frame with trend, chop, and volatility clustering."""

    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-01T00:00:00Z", periods=n, freq="15min", tz="UTC")
    drift = np.concatenate(
        [
            np.full(n // 3, 0.0008),
            np.full(n // 3, -0.0002),
            np.full(n - 2 * (n // 3), 0.0005),
        ]
    )
    volatility = np.where(np.arange(n) % 120 < 40, 0.004, 0.0015)
    close = 30_000 * np.exp(np.cumsum(rng.normal(drift, volatility)))
    open_ = np.roll(close, 1)
    open_[0] = 30_000.0
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 6e-4, n))),
            "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 6e-4, n))),
            "close": close,
            "volume": rng.uniform(10, 900, n),
            "spread_bps": np.full(n, 2.0),
        },
        index=index,
    )


def sma_seed() -> StrategySpec:
    return StrategySpec(
        name="seed",
        timeframe="5m",
        indicators=[
            IndicatorSpec(alias="fast", name="sma", params={"period": 5}),
            IndicatorSpec(alias="slow", name="sma", params={"period": 20}),
        ],
        entry=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="fast"),
                    operator=Operator.CROSSES_ABOVE,
                    right=Operand(kind="indicator", value="slow"),
                )
            ],
        ),
        exit=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="fast"),
                    operator=Operator.CROSSES_BELOW,
                    right=Operand(kind="indicator", value="slow"),
                )
            ],
        ),
    )


class TestMutationBounds:
    def test_every_period_stays_inside_the_range(self) -> None:
        for variant in propose_variants(sma_seed(), count=24, seed=3):
            for indicator in variant.indicators:
                period = indicator.params["period"]
                assert isinstance(period, int)
                assert MIN_PERIOD <= period <= MAX_PERIOD

    def test_a_narrow_range_is_respected(self) -> None:
        variants = propose_variants(sma_seed(), count=8, seed=5, mutation_range=(4, 9))
        assert variants
        for variant in variants:
            for indicator in variant.indicators:
                assert 4 <= int(indicator.params["period"]) <= 9

    def test_invalid_ranges_are_rejected(self) -> None:
        for bad in ((10, 10), (0, 20), (5, MAX_PERIOD + 1), (20, 5)):
            with pytest.raises(ValueError):
                validate_range(bad)  # type: ignore[arg-type]
        assert validate_range((3, 30)) == (3, 30)

    def test_count_is_bounded(self) -> None:
        with pytest.raises(ValueError):
            propose_variants(sma_seed(), count=0)
        with pytest.raises(ValueError):
            propose_variants(sma_seed(), count=65)

    def test_macd_fast_stays_below_slow(self) -> None:
        seed = StrategySpec(
            name="macd-seed",
            indicators=[
                IndicatorSpec(
                    alias="line",
                    name="macd",
                    params={"fast": 8, "slow": 21, "signal": 5},
                )
            ],
            entry=ConditionGroup(
                conditions=[
                    Condition(
                        left=Operand(kind="indicator", value="line"),
                        operator=Operator.CROSSES_ABOVE,
                        right=Operand(kind="constant", value=0.0),
                    )
                ]
            ),
            exit=ConditionGroup(
                conditions=[
                    Condition(
                        left=Operand(kind="indicator", value="line"),
                        operator=Operator.CROSSES_BELOW,
                        right=Operand(kind="constant", value=0.0),
                    )
                ]
            ),
        )
        variants = propose_variants(seed, count=32, seed=9)
        assert variants
        for variant in variants:
            params = variant.indicators[0].params
            assert int(params["fast"]) < int(params["slow"])

    def test_constants_never_flip_sign(self) -> None:
        seed = StrategySpec(
            name="rsi-seed",
            indicators=[IndicatorSpec(alias="rsi", name="rsi", params={"period": 14})],
            entry=ConditionGroup(
                conditions=[
                    Condition(
                        left=Operand(kind="indicator", value="rsi"),
                        operator=Operator.LT,
                        right=Operand(kind="constant", value=30.0),
                    )
                ]
            ),
            exit=ConditionGroup(
                conditions=[
                    Condition(
                        left=Operand(kind="indicator", value="rsi"),
                        operator=Operator.GT,
                        right=Operand(kind="constant", value=70.0),
                    )
                ]
            ),
        )
        for variant in propose_variants(seed, count=24, seed=13):
            assert float(variant.entry.conditions[0].right.value) > 0
            assert float(variant.exit.conditions[0].right.value) > 0

    def test_structure_is_preserved(self) -> None:
        seed = sma_seed()
        for variant in propose_variants(seed, count=12, seed=17):
            assert [item.name for item in variant.indicators] == [
                item.name for item in seed.indicators
            ]
            assert [item.alias for item in variant.indicators] == [
                item.alias for item in seed.indicators
            ]
            assert [condition.operator for condition in variant.entry.conditions] == [
                condition.operator for condition in seed.entry.conditions
            ]
            assert variant.risk == seed.risk
            assert variant.timeframe == seed.timeframe


class TestMutationDeterminism:
    def test_same_seed_same_variants(self) -> None:
        first = propose_variants(sma_seed(), count=6, seed=21)
        second = propose_variants(sma_seed(), count=6, seed=21)
        assert [item.model_dump_json() for item in first] == [
            item.model_dump_json() for item in second
        ]

    def test_different_seeds_diverge(self) -> None:
        first = propose_variants(sma_seed(), count=6, seed=21)
        second = propose_variants(sma_seed(), count=6, seed=22)
        assert [item.model_dump_json() for item in first] != [
            item.model_dump_json() for item in second
        ]

    def test_variants_are_distinct_and_exclude_the_seed(self) -> None:
        seed = sma_seed()
        variants = propose_variants(seed, count=16, seed=31)
        materials = [mutation_material(item) for item in variants]
        assert len(set(materials)) == len(materials)
        assert mutation_material(seed) not in materials

    def test_an_unmutable_seed_yields_fewer_rather_than_padding(self) -> None:
        """A seed pinned to a one-value range admits no distinct mutation."""

        seed = StrategySpec(
            name="pinned",
            indicators=[IndicatorSpec(alias="sma", name="sma", params={"period": 4})],
            entry=ConditionGroup(
                conditions=[
                    Condition(
                        left=Operand(kind="field", value="close"),
                        operator=Operator.GT,
                        right=Operand(kind="indicator", value="sma"),
                    )
                ]
            ),
            exit=ConditionGroup(
                conditions=[
                    Condition(
                        left=Operand(kind="field", value="close"),
                        operator=Operator.LT,
                        right=Operand(kind="indicator", value="sma"),
                    )
                ]
            ),
        )
        variants = propose_variants(seed, count=8, seed=3, mutation_range=(4, 5))
        assert len(variants) <= 2
        assert len({mutation_material(item) for item in variants}) == len(variants)


class TestNewTemplateFamilies:
    def test_the_new_families_are_registered(self) -> None:
        catalogue = template_catalogue()
        for key in NEW_FAMILIES:
            assert key in catalogue, f"missing family: {key}"

    @pytest.mark.parametrize("key", NEW_FAMILIES)
    def test_family_passes_static_validation(self, key: str) -> None:
        report = validate_strategy_spec(template_catalogue()[key])
        assert report.valid, getattr(report, "errors", report)

    @pytest.mark.parametrize("key", NEW_FAMILIES)
    def test_family_produces_entries_and_exits(self, key: str) -> None:
        signals = evaluate_strategy(trending_frame(), template_catalogue()[key])
        assert {"entry_signal", "exit_signal", "position_intent"}.issubset(
            signals.columns
        )
        assert int(signals["entry_signal"].sum()) > 0, "family never enters"
        assert int(signals["exit_signal"].sum()) > 0, "family never exits"
        assert set(signals["position_intent"].unique()).issubset({-1, 0, 1})

    @pytest.mark.parametrize("key", NEW_FAMILIES)
    def test_family_is_mutable_within_the_grammar(self, key: str) -> None:
        variants = propose_variants(template_catalogue()[key], count=4, seed=7)
        assert variants
        for variant in variants:
            StrategySpec.model_validate(variant.model_dump(mode="json"))

    @pytest.mark.parametrize("key", NEW_FAMILIES)
    def test_family_evaluation_is_deterministic(self, key: str) -> None:
        spec = template_catalogue()[key]
        frame = trending_frame()
        first = evaluate_strategy(frame, spec)["position_intent"].to_numpy()
        second = evaluate_strategy(frame, spec)["position_intent"].to_numpy()
        assert np.array_equal(first, second)

    def test_the_catalogue_has_unique_names_and_tags_every_family(self) -> None:
        catalogue = template_catalogue()
        names = [spec.name for spec in catalogue.values()]
        assert len(set(names)) == len(names)
        for key, spec in catalogue.items():
            assert "template" in spec.tags, f"{key} is not tagged as a template"
            assert spec.description, f"{key} has no description"
