from __future__ import annotations

from trading_gym.production.mutation_retry import MutationOutcomeUnknown
from trading_gym.adapters.enhanced_aban_client import OrderSide
from trading_gym.production.venues import VenueOrderIntent

try:  # pragma: no cover - requests is a declared hard dependency
    from requests import exceptions as _requests_exceptions

    _PRE_SEND_FAILURES: tuple[type[BaseException], ...] = (
        _requests_exceptions.ConnectTimeout,
    )
except Exception:  # pragma: no cover
    _PRE_SEND_FAILURES = ()

# Operations whose replay cannot double a venue side effect even when the
# first attempt's outcome is unknown (worst case: an idempotent no-op error).
_IDEMPOTENT_OPERATIONS = frozenset({"order.cancel"})


def operation_is_idempotent(operation: str) -> bool:
    """Whether replaying ``operation`` is safe without outcome knowledge."""
    return operation in _IDEMPOTENT_OPERATIONS


class AbanTransport:
    """Single typed dispatch seam from production authority to the SDK.

    Rate scheduling lives in ``EnhancedAbanClient``.  This layer deliberately
    does not add a second limiter or retry policy.

    Failure classification for automatic mutation retry:

    * Local validation problems are raised before any network call and keep
      their original ``ValueError`` type — they are terminal.
    * Only failures with a proven pre-send contract (requests ConnectTimeout)
      can be automatically replayed. The broad ConnectionError class includes
      failures after submission, so it must be treated as ambiguous.
    * Anything else leaves the delivery outcome unprovable, so it is wrapped
      in :class:`MutationOutcomeUnknown`; the coordinator records the key as
      ambiguous and refuses replay unless the venue deduplicates.
    """

    def __init__(self, client):
        self.client = client

    def submit_order(self, intent: VenueOrderIntent):
        # Validate local inputs *outside* the transport exception boundary.
        # An SDK ValueError/TypeError may result from parsing a response after
        # a successful venue-side order and is NOT proof of non-submission.
        self._validate_local_intent(intent)
        try:
            return self._dispatch(intent)
        except _PRE_SEND_FAILURES:
            # The request never reached the venue; a retry cannot duplicate.
            raise
        except Exception as exc:
            if operation_is_idempotent(intent.operation):
                # Cancelling an already-cancelled/filled order has no venue
                # side effect, so an ambiguous outcome is still safe to replay.
                raise exc
            raise MutationOutcomeUnknown(
                f"outcome unknown after {intent.operation} with key {intent.idempotency_key}"
            ) from exc

    @staticmethod
    def _validate_local_intent(intent: VenueOrderIntent) -> None:
        if intent.operation not in {"spot.market", "short.open", "short.close", "order.cancel"}:
            raise ValueError(f"unsupported Aban operation: {intent.operation}")
        if intent.operation == "spot.market":
            OrderSide(intent.side)
        if intent.operation in {"short.close", "order.cancel"} and not intent.reference_id:
            raise ValueError(f"{intent.operation} requires reference_id")

    def _dispatch(self, intent: VenueOrderIntent):
        operation = intent.operation
        if operation == "spot.market":
            return self.client.place_market_order(
                OrderSide(intent.side),
                intent.base_symbol,
                intent.quote_symbol,
                intent.quantity,
            )
        if operation == "short.open":
            return self.client.create_short_sell(
                intent.base_symbol,
                intent.quote_symbol,
                intent.quantity,
            )
        if operation == "short.close":
            if not intent.reference_id:
                raise ValueError("short.close requires reference_id")
            amount = intent.quantity if intent.quantity > 0 else None
            return self.client.close_short_position(intent.reference_id, amount)
        if operation == "order.cancel":
            if not intent.reference_id:
                raise ValueError("order.cancel requires reference_id")
            return self.client.cancel_order(intent.reference_id)
        raise ValueError(f"unsupported Aban operation: {operation}")
