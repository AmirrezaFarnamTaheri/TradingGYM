import random
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from trading_gym.contracts import (
    FieldContract,
    SchemaCompatibilityRequest,
    SchemaContract,
    evaluate_schema_compatibility,
)
from trading_gym.risk import (
    FactorCovariance,
    FactorPosition,
    FactorRiskRequest,
    evaluate_factor_risk,
)
from trading_gym.treasury import (
    LiquidityForecastRequest,
    TreasuryBalance,
    TreasuryCashflow,
    evaluate_liquidity_forecast,
)
from trading_gym.operations import (
    DependencyImpactRequest,
    ServiceDependencyNode,
    evaluate_dependency_impact,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def test_random_liquidity_forecasts_conserve_each_currency_cash():
    rng = random.Random(90210)
    for case in range(100):
        opening = Decimal(rng.randint(100, 10_000))
        flows = []
        expected = opening
        for index in range(rng.randint(0, 30)):
            amount = Decimal(rng.randint(-100, 100))
            expected += amount
            flows.append(
                TreasuryCashflow(
                    cashflow_id=f"{case}-{index}",
                    value_date=date(2026, 7, 28),
                    currency="GBP",
                    amount=amount,
                    kind="other",
                    source_sha256="a" * 64,
                )
            )
        report = evaluate_liquidity_forecast(
            LiquidityForecastRequest(
                evaluated_at=NOW,
                reporting_currency="GBP",
                balances=[TreasuryBalance(currency="GBP", available=opening)],
                cashflows=flows,
                horizon_days=(1,),
                minimum_reporting_buffer=Decimal("0"),
            )
        )
        assert report.buckets[0].projected_by_currency["GBP"] == expected
        assert report.buckets[0].reporting_value == expected
        assert report.ready_for_treasury_review is (expected >= 0)


def test_random_diagonal_factor_models_match_independent_variance():
    rng = random.Random(1904)
    for case in range(100):
        factors = tuple(f"f{i}" for i in range(rng.randint(1, 8)))
        variances = [rng.uniform(0.001, 0.2) for _ in factors]
        loadings = {factor: rng.uniform(-2, 2) for factor in factors}
        value = rng.uniform(-1000, 1000)
        equity = 2000.0
        report = evaluate_factor_risk(
            FactorRiskRequest(
                portfolio_equity=equity,
                positions=[
                    FactorPosition(
                        position_id=f"p-{case}",
                        market_value=value,
                        factor_loadings=loadings,
                    )
                ],
                covariance=FactorCovariance(
                    factors=factors,
                    matrix=[
                        [variances[i] if i == j else 0.0 for j in range(len(factors))]
                        for i in range(len(factors))
                    ],
                ),
                maximum_factor_volatility=1000,
            )
        )
        expected = sum(
            ((value / equity) * loadings[factor]) ** 2 * variances[i]
            for i, factor in enumerate(factors)
        )
        assert report.portfolio_variance == pytest.approx(
            expected, rel=1e-12, abs=1e-12
        )
        assert sum(
            item.variance_contribution for item in report.contributions
        ) == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_optional_schema_additions_remain_compatible_across_random_contracts():
    rng = random.Random(73)
    for case in range(100):
        base_fields = [
            FieldContract(
                name="id",
                data_type="string",
                required=True,
                nullable=False,
                semantic="identity",
            )
        ]
        for index in range(rng.randint(0, 12)):
            base_fields.append(
                FieldContract(
                    name=f"field_{index}",
                    data_type=rng.choice(["string", "integer", "number", "boolean"]),
                    required=False,
                    nullable=True,
                    semantic=f"field {index}",
                )
            )
        current = SchemaContract(
            contract_id=f"contract-{case}",
            version=1,
            fields=base_fields,
            primary_key=("id",),
        )
        candidate = SchemaContract(
            contract_id=current.contract_id,
            version=2,
            fields=[
                *base_fields,
                FieldContract(
                    name="new_optional",
                    data_type="string",
                    required=False,
                    nullable=True,
                    semantic="extension",
                ),
            ],
            primary_key=("id",),
        )
        report = evaluate_schema_compatibility(
            SchemaCompatibilityRequest(current=current, candidate=candidate)
        )
        assert report.compatible
        assert report.backward_breaks == []
        assert report.forward_breaks == []


def test_dependency_impact_matches_independent_chain_descendants():
    rng = random.Random(8080)
    for size in range(2, 80):
        nodes = []
        for index in range(size):
            dependencies = () if index == 0 else (f"s{rng.randrange(index)}",)
            nodes.append(
                ServiceDependencyNode(
                    service_id=f"s{index}",
                    criticality="low",
                    dependencies=dependencies,
                    capacity_units=100,
                    observed_load_units=20,
                    rollback_available=True,
                    owner_role="platform",
                )
            )
        report = evaluate_dependency_impact(
            DependencyImpactRequest(
                change_id=f"chain-{size}",
                services=nodes,
                directly_changed_services=("s0",),
                maximum_critical_blast_radius=0,
            )
        )
        expected = {"s0"}
        changed = True
        while changed:
            changed = False
            for node in nodes:
                if node.service_id not in expected and any(
                    dep in expected for dep in node.dependencies
                ):
                    expected.add(node.service_id)
                    changed = True
        assert set(report.impacted_services) == expected
        assert report.ready_for_change_review
