from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from axiomforge.live_trading import LiveConsent, validate_live_consent

from trading_gym.production.mutation_retry import MutationOutcomeUnknown
from trading_gym.production.venues import VenueOrderIntent
from trading_gym.production.emergency import EmergencyController


@dataclass(frozen=True)
class OperationAuthorization:
    proposal_id: str
    actor: str
    operation: str
    payload_sha256: str
    idempotency_key: str


def intent_payload(intent: VenueOrderIntent) -> dict[str, str]:
    return {
        "venue": intent.venue,
        "operation": intent.operation,
        "base_symbol": intent.base_symbol,
        "quote_symbol": intent.quote_symbol,
        "side": intent.side,
        "quantity": str(intent.quantity),
        "reference_id": intent.reference_id,
        "idempotency_key": intent.idempotency_key,
    }


def intent_sha256(intent: VenueOrderIntent) -> str:
    raw = json.dumps(intent_payload(intent), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


class LiveExecutionCoordinator:
    """Sole authority for live venue mutations.

    Session-level live consent is converted into a payload-bound authorization for
    each operation.  Venue adapters never receive an unapproved mutation.
    """

    def __init__(self, venues, *, emergency_controller: EmergencyController | None = None):
        self.venues = dict(venues)
        self.emergency_controller = emergency_controller
        self._receipts: dict[str, dict[str, object]] = {}
        # Keys whose submission outcome is unknown: the venue transport raised
        # after the request may have been delivered.  Retrying such a key is
        # only permitted when the venue itself replay-deduplicates.
        self._ambiguous_keys: set[str] = set()

    def bind_authorization(
        self,
        intent: VenueOrderIntent,
        *,
        consent: LiveConsent,
        proposal_id: str,
    ) -> OperationAuthorization:
        validate_live_consent(
            consent,
            actor=consent.actor,
            scope="live.execute",
        )
        if not proposal_id:
            raise PermissionError("proposal_id_required")
        return OperationAuthorization(
            proposal_id=proposal_id,
            actor=consent.actor,
            operation=intent.operation,
            payload_sha256=intent_sha256(intent),
            idempotency_key=intent.idempotency_key,
        )

    def execute(
        self,
        intent: VenueOrderIntent,
        authorization: OperationAuthorization,
        *,
        consent: LiveConsent,
    ) -> dict[str, object]:
        validate_live_consent(
            consent,
            actor=authorization.actor,
            scope="live.execute",
        )
        if self.emergency_controller is not None and self.emergency_controller.snapshot().halted:
            raise PermissionError("emergency_halt")
        if authorization.operation != intent.operation:
            raise PermissionError("operation_mismatch")
        digest = intent_sha256(intent)
        if authorization.payload_sha256 != digest:
            raise PermissionError("authorization_mismatch")
        if authorization.idempotency_key != intent.idempotency_key:
            raise PermissionError("idempotency_mismatch")
        if intent.venue not in self.venues:
            raise KeyError("unknown_venue")
        if intent.idempotency_key in self._receipts:
            return self._receipts[intent.idempotency_key]
        venue = self.venues[intent.venue]
        if intent.idempotency_key in self._ambiguous_keys and not getattr(
            venue, "supports_idempotent_replay", False
        ):
            # A prior attempt with this key has an unknown outcome and the
            # venue cannot deduplicate replays: resubmitting could double the
            # order.  Escalate to reconciliation instead of mutating.
            raise PermissionError("ambiguous_mutation_requires_reconciliation")

        try:
            result = venue.submit(intent)
        except MutationOutcomeUnknown:
            # The transport explicitly signals an unprovable outcome; other
            # failures (validation, provably pre-send network errors) do not
            # taint the key.
            self._ambiguous_keys.add(intent.idempotency_key)
            raise
        receipt: dict[str, object] = {
            "proposal_id": authorization.proposal_id,
            "actor": authorization.actor,
            "operation": authorization.operation,
            "payload_sha256": digest,
            "idempotency_key": intent.idempotency_key,
            "result": result,
            "reconciled": False,
        }
        self._ambiguous_keys.discard(intent.idempotency_key)
        self._receipts[intent.idempotency_key] = receipt
        return receipt

    def ambiguous_keys(self) -> tuple[str, ...]:
        """Keys with unprovable submission outcomes awaiting reconciliation."""
        return tuple(sorted(self._ambiguous_keys))

    def mark_reconciled(self, idempotency_key: str) -> bool:
        """Clear a reconciliation hold once an operator resolved the outcome.

        Returns True when a hold existed and was cleared.  This never mutates
        venue state; it only permits a fresh governed submission under the
        same idempotency key after human reconciliation.
        """
        if idempotency_key not in self._ambiguous_keys:
            return False
        self._ambiguous_keys.discard(idempotency_key)
        return True
