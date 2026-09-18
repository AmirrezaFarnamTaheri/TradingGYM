"""Evidence-gated challenger evaluation.

This command may advance an MLflow candidate to *Staging* for paper validation.
It deliberately cannot grant Production authority; production activation is owned
by the governed, integrity-checked model registry and requires a critical approval.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import typer

try:  # MLflow is an optional operational dependency in minimal audit environments.
    import mlflow
    from mlflow.tracking import MlflowClient
except ImportError:  # pragma: no cover - exercised when the optional stack is absent
    mlflow = None  # type: ignore[assignment]
    MlflowClient = None  # type: ignore[assignment,misc]

from trading_gym.common.config import load_app_config

run_walkforward: Callable[..., Any] | None = None

logger = logging.getLogger(__name__)
app = typer.Typer()
_MLFLOW_RUN_ID = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class CandidateAssessment:
    qualified_for_staging: bool
    champion_sharpe: float
    challenger_sharpe: float
    absolute_improvement: float
    relative_improvement: float | None
    champion_folds: int
    challenger_folds: int
    reasons: tuple[str, ...]


def _extract_sharpes(result: Any) -> list[float]:
    if isinstance(result, dict):
        values = [float(item.sharpe) for item in result.get("RLModel", [])]
    elif hasattr(result, "sharpe_values"):
        values = [float(value) for value in getattr(result, "sharpe_values")]
    elif hasattr(result, "sharpe_ratio"):
        value = float(getattr(result, "sharpe_ratio"))
        folds = int(getattr(result, "evaluation_folds", 1))
        values = [value] * max(1, folds)
    else:
        values = []
    return [value for value in values if math.isfinite(value)]


def assess_candidate(
    champion_results: Any,
    challenger_results: Any,
    *,
    min_folds: int = 3,
    min_absolute_improvement: float = 0.10,
    min_relative_improvement: float = 0.05,
) -> CandidateAssessment:
    """Apply a bounded, deterministic staging gate to walk-forward evidence."""

    if min_folds < 3:
        raise ValueError("min_folds must be at least 3")
    if not math.isfinite(min_absolute_improvement) or min_absolute_improvement < 0:
        raise ValueError("min_absolute_improvement must be finite and non-negative")
    if (
        not math.isfinite(min_relative_improvement)
        or not 0 <= min_relative_improvement <= 1
    ):
        raise ValueError("min_relative_improvement must be in [0, 1]")

    champion_values = _extract_sharpes(champion_results)
    challenger_values = _extract_sharpes(challenger_results)
    champion = (
        sum(champion_values) / len(champion_values) if champion_values else float("nan")
    )
    challenger = (
        sum(challenger_values) / len(challenger_values)
        if challenger_values
        else float("nan")
    )
    absolute = (
        challenger - champion
        if math.isfinite(champion) and math.isfinite(challenger)
        else float("nan")
    )
    relative = (
        absolute / abs(champion) if math.isfinite(absolute) and champion != 0 else None
    )

    reasons: list[str] = []
    if len(champion_values) < min_folds:
        reasons.append("insufficient champion walk-forward folds")
    if len(challenger_values) < min_folds:
        reasons.append("insufficient challenger walk-forward folds")
    if not math.isfinite(champion) or not math.isfinite(challenger):
        reasons.append("non-finite or missing Sharpe evidence")
    if math.isfinite(challenger) and challenger <= 0:
        reasons.append("challenger Sharpe must be positive")
    if math.isfinite(absolute) and absolute < min_absolute_improvement:
        reasons.append("absolute improvement threshold not met")
    if relative is not None and relative < min_relative_improvement:
        reasons.append("relative improvement threshold not met")

    return CandidateAssessment(
        qualified_for_staging=not reasons,
        champion_sharpe=champion,
        challenger_sharpe=challenger,
        absolute_improvement=absolute,
        relative_improvement=relative,
        champion_folds=len(champion_values),
        challenger_folds=len(challenger_values),
        reasons=tuple(reasons),
    )


def update(
    config_path: Path,
    champion_run_id: str,
    challenger_run_id: str,
    client: MlflowClient | None = None,
    _mlflow_load_model: Callable | None = None,
    timeframe: str = "5m",
    *,
    allow_unsafe_deserialization: bool = False,
    min_folds: int = 3,
    min_absolute_improvement: float = 0.10,
    min_relative_improvement: float = 0.05,
) -> CandidateAssessment:
    """Evaluate a challenger and, if qualified, advance it only to Staging."""

    logger.info("Starting governed model candidate evaluation")
    if _mlflow_load_model is None:
        if not allow_unsafe_deserialization:
            raise RuntimeError(
                "MLflow model deserialization is disabled by default; run this operation "
                "only in an isolated worker and explicitly acknowledge the risk"
            )
        for label, run_id in (
            ("champion", champion_run_id),
            ("challenger", challenger_run_id),
        ):
            if not _MLFLOW_RUN_ID.fullmatch(run_id):
                raise ValueError(
                    f"{label} run id must be 32 lowercase hexadecimal characters"
                )
        pyfunc = getattr(mlflow, "pyfunc", None) if mlflow is not None else None
        if pyfunc is None or not hasattr(pyfunc, "load_model"):
            raise RuntimeError("mlflow.pyfunc is unavailable; cannot load models")
        _mlflow_load_model = pyfunc.load_model

    cfg = load_app_config(config_path)
    if client is None:
        if MlflowClient is None:
            raise RuntimeError("MLflow is unavailable; cannot stage the candidate")
        client = MlflowClient()
    champion_model = _mlflow_load_model(f"runs:/{champion_run_id}/model")
    challenger_model = _mlflow_load_model(f"runs:/{challenger_run_id}/model")

    evaluator = run_walkforward
    if evaluator is None:
        from trading_gym.pipelines.evaluation import run_walkforward as evaluator

    def run(model: Any) -> Any:
        try:
            return evaluator(cfg, timeframe, [], model)
        except TypeError:
            return evaluator(cfg, model)

    champion_results = run(champion_model)
    challenger_results = run(challenger_model)
    assessment = assess_candidate(
        champion_results,
        challenger_results,
        min_folds=min_folds,
        min_absolute_improvement=min_absolute_improvement,
        min_relative_improvement=min_relative_improvement,
    )
    logger.info("Candidate assessment: %s", asdict(assessment))

    if not assessment.qualified_for_staging:
        logger.info(
            "Challenger retained as candidate: %s", "; ".join(assessment.reasons)
        )
        return assessment

    model_name = getattr(challenger_results, "model_name", None)
    model_version = getattr(challenger_results, "model_version", None)
    if model_name is None or model_version is None:
        raise RuntimeError("qualified challenger is missing MLflow model identity")
    client.transition_model_version_stage(
        name=str(model_name),
        version=str(model_version),
        stage="Staging",
    )
    logger.info(
        "Challenger advanced to Staging only; Production requires registry admission, "
        "paper validation, and a critical human approval"
    )
    return assessment


@app.command()
def main(
    config_path: Path = typer.Option(..., "--config", "-c"),
    champion_run_id: str = typer.Option(..., "--champion-run-id"),
    challenger_run_id: str = typer.Option(..., "--challenger-run-id"),
    timeframe: str = typer.Option("5m", "--timeframe", "-t"),
    min_folds: int = typer.Option(3, "--min-folds", min=3, max=1000),
    min_absolute_improvement: float = typer.Option(
        0.10, "--min-absolute-improvement", min=0
    ),
    min_relative_improvement: float = typer.Option(
        0.05, "--min-relative-improvement", min=0, max=1
    ),
    allow_unsafe_mlflow_deserialization: bool = typer.Option(
        False,
        "--allow-unsafe-mlflow-deserialization",
        help=(
            "Acknowledge that MLflow model loading may execute artifact-supplied code. "
            "Use only inside an isolated, least-privilege worker."
        ),
    ),
) -> None:
    try:
        assessment = update(
            config_path,
            champion_run_id,
            challenger_run_id,
            timeframe=timeframe,
            allow_unsafe_deserialization=allow_unsafe_mlflow_deserialization,
            min_folds=min_folds,
            min_absolute_improvement=min_absolute_improvement,
            min_relative_improvement=min_relative_improvement,
        )
        if not assessment.qualified_for_staging:
            raise typer.Exit(code=3)
    except typer.Exit:
        raise
    except Exception as exc:
        logger.error(
            "Model candidate evaluation failed error_type=%s", type(exc).__name__
        )
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
