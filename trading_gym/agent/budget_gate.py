"""AXIOM-1 budget gate (ADR-029 §4 wiring): real reservations for arms.

``BudgetGate`` is the ``budget_check`` seam of the tier-cascade planner,
backed by the platform's ``BudgetRepository``: an arm invocation reserves
its declared ``credits_per_call`` against the cycle budget, and commits
the reservation when the planner actually uses the proposal. Conflict,
exhaustion, or a missing budget return False — the arm benches — and a
failed reservation never aborts the cycle.

Zero-cost arms (α rules, δ guard) pass without touching the ledger at
all, so the always-on tier pays nothing bookkeeping-wise.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Protocol

from trading_gym.agent.arms.base import Arm
from trading_gym.core.budgets.ledger import BudgetRepository, BudgetReservationCreate

__all__ = ["BudgetGate", "BudgetGateProtocol"]


class BudgetGateProtocol(Protocol):
    """The planner's ``budget_check`` contract."""

    def __call__(self, arm: Arm) -> bool: ...


class BudgetGate:
    """Reserve-and-commit adapter over one BudgetRepository budget."""

    def __init__(
        self,
        repository: BudgetRepository,
        budget_id: str,
        *,
        actor: str = "axiom1-planner",
    ) -> None:
        self._repository = repository
        self._budget_id = budget_id
        self._actor = actor

    def __call__(self, arm: Arm) -> bool:
        credits = getattr(arm.cost, "credits_per_call", 0.0) or 0.0
        if credits <= 0:
            return True
        reservation_id = f"res-{uuid.uuid4().hex[:12]}"
        idempotency_key = f"axiom1-{arm.name}-{reservation_id}"
        try:
            snapshot = self._repository.get_budget(self._budget_id)
        except KeyError:
            return False
        if snapshot.hard_limit_exhausted:
            return False
        try:
            reservation = self._repository.reserve(
                BudgetReservationCreate(
                    reservation_id=reservation_id,
                    budget_id=self._budget_id,
                    amount=Decimal(str(credits)),
                    expires_in_seconds=120,
                    idempotency_key=idempotency_key,
                    actor=self._actor,
                    subject=f"arm:{arm.name}",
                )
            )
        except Exception:
            # BudgetConflict / validation / integrity failures deny the
            # arm this cycle — spend authority must never fail open.
            return False
        try:
            self._repository.commit(
                reservation.reservation_id,
                actual_amount=reservation.amount,
                expected_revision=reservation.revision,
                actor=self._actor,
            )
        except Exception:
            try:
                self._repository.release(
                    reservation.reservation_id,
                    expected_revision=reservation.revision,
                    actor=self._actor,
                    reason="budget-gate-commit-failed",
                )
            except Exception:
                pass  # expiry reconciles; the deny decision already stands
            return False
        return True
