"""Turnover-aware research is governed by the existing construction API."""

from __future__ import annotations

import math

import pytest

from trading_gym.institutional_os.portfolio_construction import (
    ConstructionMethod,
    PortfolioAssetInput,
    PortfolioConstructionRequest,
    construct_portfolio,
)


def request(*, penalty=0.0, risk_aversion=0.0, first_cap=1.0):
    return PortfolioConstructionRequest(
        method=ConstructionMethod.TURNOVER_AWARE,
        assets=[
            PortfolioAssetInput(
                asset_id="AAA",
                expected_return=0.01,
                volatility=0.1,
                maximum_weight=first_cap,
            ),
            PortfolioAssetInput(
                asset_id="BBB",
                expected_return=0.04,
                volatility=0.1,
            ),
        ],
        correlation_matrix=[[1.0, 0.0], [0.0, 1.0]],
        previous_weights={"AAA": 1.0, "BBB": 0.0},
        turnover_penalty=penalty,
        risk_aversion=risk_aversion,
    )


def test_same_api_adds_turnover_penalized_research_without_execution_rights():
    baseline = construct_portfolio(request())
    guarded = construct_portfolio(request(penalty=0.1))
    assert baseline.weights["BBB"] == pytest.approx(1.0, abs=1e-5)
    assert guarded.weights["AAA"] == pytest.approx(1.0, abs=1e-5)
    assert baseline.estimated_one_way_turnover == pytest.approx(1.0)
    assert guarded.estimated_one_way_turnover == pytest.approx(0.0, abs=1e-5)
    assert guarded.execution_authority_granted is False
    assert guarded.mutation_performed is False
    assert guarded.previous_weights_sha256 is not None
    assert len(guarded.previous_weights_sha256) == 64
    assert guarded.report_sha256 == construct_portfolio(
        request(penalty=0.1)
    ).report_sha256


def test_new_name_cap_forces_tracked_reallocation():
    # AAA is deliberately the higher-return asset in this fixture.
    req = request(penalty=1.0, first_cap=0.6)
    assets = list(req.assets)
    assets[0] = assets[0].model_copy(update={"expected_return": 0.04})
    assets[1] = assets[1].model_copy(update={"expected_return": 0.01})
    constrained = construct_portfolio(req.model_copy(update={"assets": assets}))
    assert constrained.weights["AAA"] == pytest.approx(0.6, abs=1e-5)
    assert constrained.estimated_one_way_turnover == pytest.approx(0.4, abs=1e-5)
    assert constrained.turnover_regularization == pytest.approx(0.8, abs=1e-5)


@pytest.mark.parametrize(
    "prior",
    [
        {"AAA": 0.9, "BBB": 0.0},
        {"AAA": 1.0, "BBB": -0.1},
        {"AAA": 1.0, "BBB": 0.0, "OFFBOOK": 0.0},
        {"AAA": math.nan, "BBB": 0.0},
        {"AAA": True, "BBB": 0.0},
        {"AAA": "1.0", "BBB": 0.0},
    ],
)
def test_invalid_or_hidden_prior_exposures_fail_closed(prior):
    with pytest.raises(ValueError):
        PortfolioConstructionRequest(
            **{**request().model_dump(), "previous_weights": prior}
        )


def test_prior_must_be_explicit_and_turnover_settings_cannot_be_ignored():
    payload = request().model_dump()
    payload["previous_weights"] = None
    with pytest.raises(ValueError):
        PortfolioConstructionRequest(**payload)
    payload = request().model_dump()
    payload["method"] = ConstructionMethod.EQUAL_WEIGHT
    with pytest.raises(ValueError):
        PortfolioConstructionRequest(**payload)


def test_existing_methods_keep_nullable_turnover_evidence():
    req = request().model_copy(
        update={"method": ConstructionMethod.EQUAL_WEIGHT,
                "previous_weights": None,
                "risk_aversion": 1.0,
                "turnover_penalty": 0.0}
    )
    result = construct_portfolio(req)
    assert result.estimated_one_way_turnover is None
    assert result.turnover_regularization is None
    assert result.objective_value is None
    assert result.previous_weights_sha256 is None


def test_large_turnover_problem_fails_before_solver_allocation():
    n = 65
    with pytest.raises(ValueError, match="64"):
        PortfolioConstructionRequest(
            assets=[
                PortfolioAssetInput(
                    asset_id=f"ASSET{i}",
                    expected_return=0.01,
                    volatility=0.1,
                )
                for i in range(n)
            ],
            correlation_matrix=[
                [1.0 if i == j else 0.0 for j in range(n)]
                for i in range(n)
            ],
            method=ConstructionMethod.TURNOVER_AWARE,
            previous_weights={
                f"ASSET{i}": 1.0 / n for i in range(n)
            },
        )
