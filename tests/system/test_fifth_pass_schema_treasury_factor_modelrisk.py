from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from trading_gym.core.contracts import (
    FieldContract,
    SchemaCompatibilityRequest,
    SchemaContract,
    evaluate_schema_compatibility,
)
from trading_gym.rl.experiments import (
    ChallengeRun,
    ModelChallengeRequest,
    evaluate_model_challenge,
)
from trading_gym.execution.risk import (
    FactorCovariance,
    FactorPosition,
    FactorRiskRequest,
    evaluate_factor_risk,
)
from trading_gym.execution.treasury import (
    LiquidityForecastRequest,
    TreasuryBalance,
    TreasuryCashflow,
    TreasuryFxRate,
    evaluate_liquidity_forecast,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def _base_schema(version: int = 1) -> SchemaContract:
    return SchemaContract(
        contract_id="market.candle",
        version=version,
        fields=[
            FieldContract(
                name="ts",
                data_type="datetime",
                required=True,
                nullable=False,
                semantic="closed candle time",
            ),
            FieldContract(
                name="close",
                data_type="decimal",
                required=True,
                nullable=False,
                semantic="close price",
            ),
            FieldContract(
                name="venue",
                data_type="string",
                required=False,
                nullable=True,
                semantic="venue code",
                enum_values=("A", "B"),
            ),
        ],
        primary_key=("ts",),
        event_time_field="ts",
    )


def test_schema_compatibility_accepts_optional_addition_and_binds_versions():
    current = _base_schema()
    candidate = _base_schema(2).model_copy(
        update={
            "fields": [
                *_base_schema(2).fields,
                FieldContract(
                    name="volume",
                    data_type="decimal",
                    required=False,
                    nullable=True,
                    semantic="base volume",
                ),
            ]
        }
    )
    report = evaluate_schema_compatibility(
        SchemaCompatibilityRequest(current=current, candidate=candidate)
    )
    assert report.compatible
    assert report.current_sha256 != report.candidate_sha256
    assert "field_added:volume" in report.warnings
    assert not report.registration_authority_granted


def test_schema_compatibility_rejects_required_addition_type_and_key_changes():
    current = _base_schema()
    candidate = SchemaContract(
        contract_id=current.contract_id,
        version=2,
        fields=[
            FieldContract(
                name="ts",
                data_type="datetime",
                required=True,
                nullable=False,
                semantic="closed candle time",
            ),
            FieldContract(
                name="close",
                data_type="number",
                required=True,
                nullable=False,
                semantic="close price",
            ),
            FieldContract(
                name="symbol",
                data_type="string",
                required=True,
                nullable=False,
                semantic="instrument",
            ),
        ],
        primary_key=("ts", "symbol"),
        event_time_field="ts",
    )
    report = evaluate_schema_compatibility(
        SchemaCompatibilityRequest(current=current, candidate=candidate)
    )
    assert not report.compatible
    assert any(item.startswith("type_changed:close") for item in report.backward_breaks)
    assert "required_field_added_without_default:symbol" in report.backward_breaks
    assert "primary_key_changed" in report.forward_breaks


def test_liquidity_forecast_projects_committed_and_probable_flows_without_transfer():
    report = evaluate_liquidity_forecast(
        LiquidityForecastRequest(
            evaluated_at=NOW,
            reporting_currency="GBP",
            balances=[
                TreasuryBalance(currency="GBP", available=Decimal("1000")),
                TreasuryBalance(currency="USD", available=Decimal("100")),
            ],
            cashflows=[
                TreasuryCashflow(
                    cashflow_id="settle-1",
                    value_date=date(2026, 7, 28),
                    currency="GBP",
                    amount=Decimal("-200"),
                    kind="settlement",
                    confidence="committed",
                    source_sha256="a" * 64,
                ),
                TreasuryCashflow(
                    cashflow_id="deposit-1",
                    value_date=date(2026, 8, 1),
                    currency="USD",
                    amount=Decimal("50"),
                    kind="deposit",
                    confidence="probable",
                    source_sha256="b" * 64,
                ),
                TreasuryCashflow(
                    cashflow_id="possible",
                    value_date=date(2026, 7, 28),
                    currency="GBP",
                    amount=Decimal("-999"),
                    kind="other",
                    confidence="possible",
                    source_sha256="c" * 64,
                ),
            ],
            fx_rates=[
                TreasuryFxRate(
                    currency="USD",
                    reporting_currency="GBP",
                    rate=Decimal("0.8"),
                    observed_at=NOW,
                )
            ],
            minimum_reporting_buffer=Decimal("500"),
        )
    )
    assert report.ready_for_treasury_review
    assert report.buckets[0].projected_by_currency["GBP"] == Decimal("800")
    assert report.buckets[1].projected_by_currency["USD"] == Decimal("150")
    assert report.buckets[1].reporting_value == Decimal("920.0")
    assert not report.transfer_authority_granted


def test_liquidity_forecast_fails_closed_on_missing_fx_and_currency_shortfall():
    report = evaluate_liquidity_forecast(
        LiquidityForecastRequest(
            evaluated_at=NOW,
            reporting_currency="GBP",
            balances=[TreasuryBalance(currency="EUR", available=Decimal("10"))],
            cashflows=[
                TreasuryCashflow(
                    cashflow_id="fee",
                    value_date=NOW.date(),
                    currency="EUR",
                    amount=Decimal("-20"),
                    kind="fee",
                    source_sha256="a" * 64,
                )
            ],
            horizon_days=(0,),
        )
    )
    assert not report.ready_for_treasury_review
    assert "fx_missing:EUR" in report.blockers
    assert "currency_shortfall:0:EUR" in report.blockers


def test_factor_risk_decomposes_variance_and_applies_limits():
    report = evaluate_factor_risk(
        FactorRiskRequest(
            portfolio_equity=1000,
            positions=[
                FactorPosition(
                    position_id="p1",
                    market_value=500,
                    factor_loadings={"market": 1.0, "value": 0.2},
                ),
                FactorPosition(
                    position_id="p2",
                    market_value=-100,
                    factor_loadings={"market": 0.5, "value": -0.5},
                ),
            ],
            covariance=FactorCovariance(
                factors=("market", "value"), matrix=[[0.04, 0.01], [0.01, 0.09]]
            ),
            maximum_factor_exposure={"market": 0.6, "value": 0.2},
            maximum_factor_volatility=0.2,
        )
    )
    assert report.allowed
    assert report.portfolio_variance >= 0
    assert (
        pytest.approx(
            sum(item.variance_contribution for item in report.contributions), abs=1e-12
        )
        == report.portfolio_variance
    )
    assert not report.execution_authority_granted


def test_factor_risk_rejects_non_psd_covariance_and_flags_exposure():
    with pytest.raises(ValueError, match="positive semidefinite"):
        FactorCovariance(factors=("a", "b"), matrix=[[1.0, 2.0], [2.0, 1.0]])
    report = evaluate_factor_risk(
        FactorRiskRequest(
            portfolio_equity=100,
            positions=[
                FactorPosition(
                    position_id="p", market_value=100, factor_loadings={"market": 1.0}
                )
            ],
            covariance=FactorCovariance(factors=("market",), matrix=[[0.04]]),
            maximum_factor_exposure={"market": 0.5},
            maximum_factor_volatility=0.1,
        )
    )
    assert not report.allowed
    assert "factor_exposure_exceeded:market" in report.violations
    assert "factor_volatility_exceeded" in report.violations


def _challenge_runs() -> list[ChallengeRun]:
    return [
        ChallengeRun(
            run_id="r1",
            seed=1,
            regime="trend",
            annual_return=0.15,
            sharpe=1.2,
            maximum_drawdown=0.10,
            turnover=2,
            baseline_annual_return=0.08,
        ),
        ChallengeRun(
            run_id="r2",
            seed=2,
            regime="trend",
            annual_return=0.12,
            sharpe=1.0,
            maximum_drawdown=0.12,
            turnover=2.2,
            baseline_annual_return=0.08,
        ),
        ChallengeRun(
            run_id="r3",
            seed=3,
            regime="range",
            annual_return=0.09,
            sharpe=0.8,
            maximum_drawdown=0.15,
            turnover=1.8,
            baseline_annual_return=0.05,
        ),
    ]


def test_model_challenge_requires_seed_regime_stability_without_promotion():
    report = evaluate_model_challenge(
        ModelChallengeRequest(
            model_id="model-1",
            model_sha256="d" * 64,
            runs=_challenge_runs(),
            minimum_median_excess_return=0.02,
            maximum_return_dispersion=0.1,
            maximum_drawdown=0.2,
        )
    )
    assert report.ready_for_model_risk_review
    assert report.unique_seeds == 3
    assert report.regimes == ["range", "trend"]
    assert not report.promotion_authority_granted


def test_model_challenge_blocks_failures_dispersion_and_drawdown():
    runs = _challenge_runs()
    runs[0] = runs[0].model_copy(update={"annual_return": 2.0, "maximum_drawdown": 0.8})
    runs[2] = runs[2].model_copy(update={"completed": False})
    report = evaluate_model_challenge(
        ModelChallengeRequest(
            model_id="model-1",
            model_sha256="d" * 64,
            runs=runs,
            minimum_unique_seeds=3,
            minimum_regimes=2,
            maximum_failure_fraction=0,
            maximum_return_dispersion=0.1,
            maximum_drawdown=0.2,
        )
    )
    assert not report.ready_for_model_risk_review
    joined = "\n".join(report.blockers)
    assert "insufficient_unique_seeds" in joined
    assert "insufficient_regimes" in joined
    assert "challenge_failure_fraction" in joined
    assert "return_dispersion_exceeded" in joined
    assert "maximum_drawdown_exceeded" in joined


def test_schema_classification_downgrade_is_a_veto_in_forward_mode():
    current = SchemaContract(
        contract_id="sensitive.contract",
        version=1,
        fields=[
            FieldContract(
                name="account_id",
                data_type="string",
                required=True,
                nullable=False,
                semantic="account",
                classification="restricted",
            )
        ],
        primary_key=("account_id",),
    )
    candidate = SchemaContract(
        contract_id="sensitive.contract",
        version=2,
        fields=[
            FieldContract(
                name="account_id",
                data_type="string",
                required=True,
                nullable=False,
                semantic="account",
                classification="public",
            )
        ],
        primary_key=("account_id",),
    )
    report = evaluate_schema_compatibility(
        SchemaCompatibilityRequest(
            current=current,
            candidate=candidate,
            mode="forward",
        )
    )
    assert not report.compatible
    assert (
        "classification_relaxed:account_id:restricted->public" in report.forward_breaks
    )
