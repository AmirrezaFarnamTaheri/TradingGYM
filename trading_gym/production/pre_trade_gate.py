"""Advisory pre-trade gate producing an auditable payload.

Absorbed contract: vibe-trading-main(1)'s pre-trade risk gate returns a typed
verdict plus the complete, auditable ``checks`` payload on every rejection
(`execution/pre_trade_risk.py:74-190`) instead of a bare boolean.  Ported as a
pure evaluator over one candidate venue mutation.

Advisory by design: callers decide what a denial means.  The natural wiring is
inside ``LiveExecutionAdapter._mutate`` before the outbox record — denials are
cheap local checks and never reach the venue or the journal.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal

_OPERATIONS = frozenset({"spot.market", "short.open", "short.close", "order.cancel"})
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PreTradeGatePayload:
    gate_id: str
    operation: str
    allowed: bool
    checks: tuple[GateCheck, ...]
    denial_reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "operation": self.operation,
            "allowed": self.allowed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "denial_reasons": list(self.denial_reasons),
        }


def evaluate_pre_trade_gate(
    *,
    operation: str,
    quantity: Decimal,
    idempotency_key: str,
    reference_id: str = "",
) -> PreTradeGatePayload:
    """Run the cheap local checks every governed mutation must satisfy."""
    checks: list[GateCheck] = []

    op_known = operation in _OPERATIONS
    checks.append(
        GateCheck(
            name="operation_known",
            passed=op_known,
            detail=operation if op_known else f"unknown operation {operation!r}",
        )
    )

    quantity_ok = isinstance(quantity, Decimal) and quantity.is_finite()
    # Cancellation is quantity-free. A short close can be partial (positive
    # amount) or complete (zero); other orders require positive units.
    if operation == "order.cancel":
        quantity_ok = quantity_ok and quantity == 0
    elif operation == "short.close":
        quantity_ok = quantity_ok and quantity >= 0
    else:
        quantity_ok = quantity_ok and quantity > 0
    checks.append(
        GateCheck(name="quantity_valid", passed=quantity_ok, detail=str(quantity))
    )

    key_ok = bool(_KEY_RE.fullmatch(idempotency_key or ""))
    checks.append(
        GateCheck(
            name="idempotency_key_format",
            passed=key_ok,
            detail="ok" if key_ok else "expected 32-char lowercase hex",
        )
    )

    reference_required = operation in {"short.close", "order.cancel"}
    ref_ok = bool(reference_id) if reference_required else True
    checks.append(
        GateCheck(
            name="reference_present",
            passed=ref_ok,
            detail=reference_id if reference_required else "not-required",
        )
    )

    reasons = tuple(c.name for c in checks if not c.passed)
    gate_material = {
        "operation": operation,
        "quantity": str(quantity),
        "idempotency_key": idempotency_key,
        "reference_id": reference_id,
    }
    gate_id = hashlib.sha256(
        json.dumps(gate_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    return PreTradeGatePayload(
        gate_id=gate_id,
        operation=operation,
        allowed=not reasons,
        checks=tuple(checks),
        denial_reasons=reasons,
    )
