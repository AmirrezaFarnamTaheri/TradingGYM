"""Evidence-only model challenge and stability analysis."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChallengeRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: Annotated[str, Field(min_length=1, max_length=160)]
    seed: int = Field(ge=0, le=2**32 - 1)
    regime: Annotated[str, Field(min_length=1, max_length=100)]
    annual_return: float
    sharpe: float
    maximum_drawdown: float = Field(ge=0.0, le=1.0)
    turnover: float = Field(ge=0.0, le=1_000_000.0)
    baseline_annual_return: float
    completed: bool = True

    @model_validator(mode="after")
    def finite(self) -> "ChallengeRun":
        values = (self.annual_return, self.sharpe, self.maximum_drawdown, self.turnover, self.baseline_annual_return)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("challenge metrics must be finite")
        return self


class ModelChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: Annotated[str, Field(min_length=1, max_length=200)]
    model_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    runs: list[ChallengeRun] = Field(min_length=1, max_length=100_000)
    minimum_unique_seeds: int = Field(default=3, ge=1, le=10_000)
    minimum_regimes: int = Field(default=2, ge=1, le=10_000)
    minimum_median_excess_return: float = -1.0
    maximum_return_dispersion: float = Field(default=1.0, ge=0.0, le=1000.0)
    maximum_drawdown: float = Field(default=0.5, ge=0.0, le=1.0)
    maximum_failure_fraction: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def unique(self) -> "ModelChallengeRequest":
        ids = [run.run_id for run in self.runs]
        if len(ids) != len(set(ids)):
            raise ValueError("run_id values must be unique")
        pairs = [(run.seed, run.regime) for run in self.runs]
        if len(pairs) != len(set(pairs)):
            raise ValueError("seed/regime challenge pairs must be unique")
        limits = (self.minimum_median_excess_return, self.maximum_return_dispersion)
        if any(not math.isfinite(value) for value in limits):
            raise ValueError("challenge limits must be finite")
        return self


class ModelChallengeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: str
    completed_runs: int
    failed_runs: int
    unique_seeds: int
    regimes: list[str]
    median_excess_return: float | None
    return_dispersion: float | None
    worst_drawdown: float | None
    median_sharpe: float | None
    median_turnover: float | None
    blockers: list[str]
    warnings: list[str]
    ready_for_model_risk_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    promotion_authority_granted: bool = False
    deployment_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_model_challenge(request: ModelChallengeRequest) -> ModelChallengeReport:
    completed = [run for run in request.runs if run.completed]
    failed = len(request.runs) - len(completed)
    blockers: list[str] = []
    warnings: list[str] = []
    seeds = {run.seed for run in completed}
    regimes = sorted({run.regime for run in completed})
    failure_fraction = failed / len(request.runs)
    if len(seeds) < request.minimum_unique_seeds:
        blockers.append(f"insufficient_unique_seeds:{len(seeds)}")
    if len(regimes) < request.minimum_regimes:
        blockers.append(f"insufficient_regimes:{len(regimes)}")
    if failure_fraction > request.maximum_failure_fraction:
        blockers.append(f"challenge_failure_fraction:{failure_fraction:.6f}")
    median_excess = dispersion = worst_drawdown = median_sharpe = median_turnover = None
    if completed:
        returns = [run.annual_return for run in completed]
        excess = [run.annual_return - run.baseline_annual_return for run in completed]
        median_excess = statistics.median(excess)
        dispersion = statistics.pstdev(returns) if len(returns) > 1 else 0.0
        worst_drawdown = max(run.maximum_drawdown for run in completed)
        median_sharpe = statistics.median(run.sharpe for run in completed)
        median_turnover = statistics.median(run.turnover for run in completed)
        if median_excess < request.minimum_median_excess_return:
            blockers.append("median_excess_return_below_minimum")
        if dispersion > request.maximum_return_dispersion:
            blockers.append("return_dispersion_exceeded")
        if worst_drawdown > request.maximum_drawdown:
            blockers.append("maximum_drawdown_exceeded")
        regime_returns: dict[str, list[float]] = {}
        for run in completed:
            regime_returns.setdefault(run.regime, []).append(run.annual_return - run.baseline_annual_return)
        for regime, values in sorted(regime_returns.items()):
            if statistics.median(values) < 0:
                warnings.append(f"negative_median_excess_in_regime:{regime}")
    else:
        blockers.append("no_completed_challenge_runs")
    payload = {
        "request": request.model_dump(mode="json"),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ModelChallengeReport(
        model_id=request.model_id,
        completed_runs=len(completed),
        failed_runs=failed,
        unique_seeds=len(seeds),
        regimes=regimes,
        median_excess_return=median_excess,
        return_dispersion=dispersion,
        worst_drawdown=worst_drawdown,
        median_sharpe=median_sharpe,
        median_turnover=median_turnover,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        ready_for_model_risk_review=not blockers,
        report_sha256=digest,
    )
