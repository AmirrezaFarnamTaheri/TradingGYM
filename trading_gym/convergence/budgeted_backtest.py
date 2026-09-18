"""Budget-gated, lineage-producing backtest planning saga.

The workflow reserves compute/resource capacity before creating the existing
research-only backtest plan.  It then records deterministic lineage.  On a
post-reservation failure it cancels the queued experiment when possible and
releases the reservation.  It never commits spend, promotes a model, or grants
execution authority.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.budgets import (
    BudgetRepository,
    BudgetReservation,
    BudgetReservationCreate,
)
from trading_gym.convergence.workflows import (
    BacktestExperimentPlan,
    BacktestWorkflowRequest,
    ConvergenceWorkflowService,
)
from trading_gym.lineage import (
    LineageEdgeCreate,
    LineageNodeCreate,
    LineageNodeKind,
    LineageRelationship,
    LineageRepository,
    LineageSnapshot,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


class BudgetedBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backtest: BacktestWorkflowRequest
    budget_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    reservation_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
    ]
    amount: Decimal
    expires_in_seconds: int = Field(default=3600, ge=30, le=86_400)

    @model_validator(mode="after")
    def validate_amount(self) -> "BudgetedBacktestRequest":
        if (
            not self.amount.is_finite()
            or self.amount <= 0
            or self.amount > Decimal("1e30")
        ):
            raise ValueError("amount must be finite, positive, and bounded")
        return self


class BudgetedBacktestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: BacktestExperimentPlan
    reservation: BudgetReservation
    lineage: LineageSnapshot
    compensation_performed: bool = False
    budget_committed: bool = False
    promotion_authority_granted: bool = False
    execution_authority_granted: bool = False


class BudgetedBacktestCompensationError(RuntimeError):
    """A planning failure was followed by one or more failed compensations."""


class BudgetedBacktestWorkflow:
    def __init__(
        self,
        *,
        base: ConvergenceWorkflowService,
        budgets: BudgetRepository,
        lineage: LineageRepository,
    ) -> None:
        self.base = base
        self.budgets = budgets
        self.lineage = lineage

    def _lineage(self, plan: BacktestExperimentPlan, *, actor: str) -> LineageSnapshot:
        now = plan.manifest.created_at
        artifact_by_kind = {
            (item.kind, item.name): item for item in plan.manifest.artifacts
        }
        instrument_artifact = artifact_by_kind.get(
            ("configuration", plan.instrument_id)
        )
        if instrument_artifact is None:
            raise ValueError(
                "backtest manifest is missing instrument artifact evidence"
            )
        nodes = [
            LineageNodeCreate(
                node_id=f"strategy:{plan.strategy_id}:{plan.strategy_revision}",
                kind=LineageNodeKind.STRATEGY,
                sha256=plan.strategy_sha256,
                label=f"Strategy {plan.strategy_id} revision {plan.strategy_revision}",
                created_at=now,
                metadata={"revision": plan.strategy_revision},
            ),
            LineageNodeCreate(
                node_id=f"instrument:{hashlib.sha256(plan.instrument_id.encode()).hexdigest()[:24]}",
                kind=LineageNodeKind.OTHER,
                sha256=instrument_artifact.sha256,
                label=f"Instrument configuration {plan.instrument_id}",
                created_at=now,
                metadata={"instrument_id": plan.instrument_id},
            ),
            LineageNodeCreate(
                node_id=f"experiment:{plan.experiment.experiment_id}",
                kind=LineageNodeKind.EXPERIMENT,
                sha256=_sha(plan.experiment.model_dump(mode="json")),
                label=plan.experiment.name,
                created_at=plan.experiment.created_at,
                metadata={"experiment_type": plan.experiment.experiment_type},
            ),
            LineageNodeCreate(
                node_id=f"run:{plan.manifest.manifest_id}",
                kind=LineageNodeKind.RUN,
                sha256=plan.manifest.manifest_sha256,
                label=f"Run manifest {plan.manifest.manifest_id}",
                created_at=plan.manifest.created_at,
                metadata={"experiment_id": plan.experiment.experiment_id},
            ),
        ]
        snapshot = self.lineage.snapshot()
        for node in nodes:
            snapshot = self.lineage.add_node(
                node, expected_revision=snapshot.revision, actor=actor
            )
        run_id = nodes[3].node_id
        edges = [
            ("strategy", nodes[0].node_id, run_id, LineageRelationship.USES),
            ("instrument", nodes[1].node_id, run_id, LineageRelationship.USES),
            ("experiment", nodes[2].node_id, run_id, LineageRelationship.PRODUCES),
        ]
        for label, parent, child, relationship in edges:
            edge = LineageEdgeCreate(
                edge_id=f"edge:{hashlib.sha256(f'{parent}|{child}|{relationship.value}'.encode()).hexdigest()[:32]}",
                parent_node_id=parent,
                child_node_id=child,
                relationship=relationship,
                created_at=now,
                actor=actor,
                metadata={"source": "budgeted_backtest", "role": label},
            )
            snapshot = self.lineage.add_edge(edge, expected_revision=snapshot.revision)
        return snapshot

    def plan(
        self, request: BudgetedBacktestRequest, *, actor: str
    ) -> BudgetedBacktestPlan:
        reservation = self.budgets.reserve(
            BudgetReservationCreate(
                reservation_id=request.reservation_id,
                budget_id=request.budget_id,
                amount=request.amount,
                expires_in_seconds=request.expires_in_seconds,
                idempotency_key=f"backtest-budget:{request.backtest.idempotency_key}",
                actor=actor,
                subject=f"backtest:{request.backtest.idempotency_key}",
                metadata={"strategy_id": request.backtest.strategy_id},
            )
        )
        plan: BacktestExperimentPlan | None = None
        try:
            plan = self.base.plan_backtest(request.backtest, actor=actor)
            lineage = self._lineage(plan, actor=actor)
            return BudgetedBacktestPlan(
                plan=plan, reservation=reservation, lineage=lineage
            )
        except Exception as original:
            failures: list[str] = []
            try:
                current = self.budgets.get_reservation(reservation.reservation_id)
                if current.state.value == "reserved":
                    self.budgets.release(
                        current.reservation_id,
                        expected_revision=current.revision,
                        actor=actor,
                        reason="budgeted backtest planning failed",
                    )
            except Exception as exc:
                failures.append(f"budget_release:{type(exc).__name__}")
            if plan is not None:
                try:
                    current_experiment = self.base.experiments.get(
                        plan.experiment.experiment_id
                    )
                    if current_experiment.state.value not in {
                        "succeeded",
                        "failed",
                        "cancelled",
                    }:
                        self.base.experiments.cancel(
                            current_experiment.experiment_id,
                            expected_revision=current_experiment.revision,
                            reason="budgeted backtest lineage failed",
                            actor=actor,
                        )
                except Exception as exc:
                    failures.append(f"experiment_cancel:{type(exc).__name__}")
            if failures:
                try:
                    self.base.ledger.append(
                        event_id=f"budgeted-backtest:{request.reservation_id}:compensation-failed",
                        event="workflow.compensation_failed",
                        actor=actor,
                        subject=request.reservation_id,
                        details={
                            "original_error_type": type(original).__name__,
                            "failures": failures,
                            "execution_authority_granted": False,
                        },
                    )
                except Exception as exc:
                    failures.append(f"audit_append:{type(exc).__name__}")
                raise BudgetedBacktestCompensationError(
                    f"planning failed ({type(original).__name__}); compensation degraded: {', '.join(failures)}"
                ) from original
            raise
