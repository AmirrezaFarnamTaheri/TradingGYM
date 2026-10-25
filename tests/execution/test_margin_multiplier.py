"""Wave-3 absorption tests: margin multiplier, stops, cooldowns, validation stats,
operational utilities, experience memory."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Contract-size / margin-rate multiplier (gym-mtsim absorption)
# ---------------------------------------------------------------------------


def _engine_with(multiplier: float):
    from trading_gym.core.config import (
        BreakerConfig,
        CapacityConfig,
        CostConfig,
        EnvConfig,
        FinancingConfig,
        LeverageConfig,
        LiquidityConfig,
        ShortsConfig,
        SlippageConfig,
        VenueDecimals,
    )
    from trading_gym.execution.execution_engine import ExecutionEngine

    financing = FinancingConfig(daily_pct=0.05, periods_per_day=288)
    leverage = LeverageConfig(
        min_leverage=1.0,
        max_leverage=20.0,
        max_short_leverage=5.0,
        warning_margin_ratio=0.1,
        liquidation_margin_ratio=0.03,
        financing=financing,
        contract_sizes={"BTCUSDT": multiplier},
        margin_rates={"BTCUSDT": 1.0},
    )
    cfg = EnvConfig(
        symbol="BTCUSDT",
        quote_ccy="USDT",
        decimals=VenueDecimals(price={"BTCUSDT": 2}, qty={"BTCUSDT": 6}),
        leverage=leverage,
        shorts=ShortsConfig(
            enabled=True, allowed_symbols=["BTCUSDT"], borrow_caps={"BTCUSDT": 100.0}
        ),
        costs=CostConfig(
            taker_fee_bps=5.0, dead_zone_equity_frac=0.001, max_spread_bps=50.0
        ),
        slippage=SlippageConfig(
            volatility_lambda=0.1,
            liquidity_lambda=0.1,
            stress_multiplier=1.0,
            volume_reference_fraction=0.01,
        ),
        liquidity=LiquidityConfig(default_quote_cap=10_000_000.0),
        capacity=CapacityConfig(conservative_default=10_000_000.0),
        breaker=BreakerConfig(max_loss_pct=99.0),
    )
    return ExecutionEngine(cfg)


def test_margin_multiplier_identity_by_default_matches_baseline():
    base = _engine_with(1.0)
    assert float(base._margin_multiplier) == pytest.approx(1.0)


def test_higher_contract_size_tightens_leverage_cap():
    small = _engine_with(1.0)
    big = _engine_with(4.0)
    equity = Decimal("10000")
    price = Decimal("100")
    limit = small._max_lev
    units_small = equity * limit / price
    units_big = equity * limit / (price * big._margin_multiplier)
    assert units_big * big._margin_multiplier == pytest.approx(float(units_small))
    assert float(units_big) < float(units_small)


def test_non_positive_multiplier_rejected():
    from trading_gym.execution.execution_engine import ExecutionEngine  # noqa: F401

    with pytest.raises(ValueError):
        _engine_with(0.0)
