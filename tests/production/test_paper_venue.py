"""AXIOM-1 paper-deterministic venue (ADR-029 §6 composition).

Contract:
- ``PaperVenue`` implements the full ``VenueAdapter`` seam — the same
  protocol AbanTether implements — so the coordinator, mutation-retry
  journal, and reservation ledger treat it as just another venue.
- Deterministic: fills are exact (no slippage model), priced at the
  caller-supplied reference price, and the same idempotency key returns
  the identical receipt. Order book state lives in memory only; the
  venue cannot lose or duplicate an order.
- Replay-dedup capable: ``supports_idempotent_replay = True`` — a
  repeated submission with the same key returns the original receipt
  instead of double-filling, which lets the coordinator retry
  ambiguous outcomes safely (exactly the property live venues must
  prove).
- Emergency-halt honoring: after ``halt()``, every mutation raises
  ``EmergencyHaltError`` — the panic path is venue-agnostic.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_gym.execution.production.runtime import (
    EmergencyController,
    LiveExecutionCoordinator,
)
from trading_gym.execution.production.venues import VenueOrderIntent
from trading_gym.execution.production.paper_venue import PaperVenue


def make_intent(
    key: str = "intent-0001",
    operation: str = "spot.market",
    quantity: str = "0.5",
) -> VenueOrderIntent:
    return VenueOrderIntent(
        venue="paper",
        operation=operation,
        base_symbol="BTC",
        quote_symbol="USDT",
        side="buy",
        quantity=Decimal(quantity),
        idempotency_key=key,
    )


class TestPaperVenue:
    def test_submit_fills_at_reference_price(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        receipt = venue.submit(make_intent())
        assert receipt["status"] == "filled"
        assert receipt["price"] == Decimal("50000")
        assert receipt["quantity"] == Decimal("0.5")

    def test_same_key_returns_identical_receipt(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        first = venue.submit(make_intent())
        second = venue.submit(make_intent())
        assert first == second
        assert venue.order_count == 1, "replay must not double-fill"

    def test_different_keys_are_separate_orders(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        venue.submit(make_intent(key="k1"))
        venue.submit(make_intent(key="k2"))
        assert venue.order_count == 2

    def test_unknown_symbol_rejected_before_book(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        unknown = VenueOrderIntent(
            venue="paper",
            operation="spot.market",
            base_symbol="SOL",
            quote_symbol="USDT",
            side="buy",
            quantity=Decimal("1"),
            idempotency_key="sol-1",
        )
        with pytest.raises(ValueError):
            venue.submit(unknown)
        assert venue.order_count == 0

    def test_halt_blocks_mutation(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        venue.halt()
        with pytest.raises(RuntimeError, match="halt"):
            venue.submit(make_intent())
        venue.resume()
        receipt = venue.submit(make_intent())
        assert receipt["status"] == "filled"

    def test_cancel_operation(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        receipt = venue.submit(make_intent(key="c1"))
        cancel_intent = VenueOrderIntent(
            venue="paper",
            operation="order.cancel",
            base_symbol="BTC",
            quote_symbol="USDT",
            side="buy",
            quantity=Decimal("0"),
            idempotency_key="c2",
            reference_id=str(receipt["order_id"]),
        )
        cancel = venue.submit(cancel_intent)
        assert cancel["status"] == "cancelled"
        assert cancel["reference_id"] == receipt["order_id"]

    def test_replay_dedup_flag_is_true(self) -> None:
        assert PaperVenue.supports_idempotent_replay is True


class TestCoordinatorWithPaperVenue:
    def _consent(self) -> object:
        from trading_gym.execution.live_trading import LiveConsent

        return LiveConsent(
            actor="agent-axiom1",
            scope="live.execute",
            expires_at="2026-12-01T00:00:00Z",
            nonce="nonce-1",
        )

    def test_execute_through_coordinator(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        coordinator = LiveExecutionCoordinator(
            {"paper": venue}, emergency_controller=EmergencyController()
        )
        intent = make_intent()
        consent = self._consent()
        authorization = coordinator.bind_authorization(
            intent,
            consent=consent,  # type: ignore[arg-type]
            proposal_id="prop-paper-1",
        )
        result = coordinator.execute(
            intent, authorization, consent=consent  # type: ignore[arg-type]
        )
        assert result["result"]["status"] == "filled"

    def test_replay_after_execute_returns_cached_receipt(self) -> None:
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        coordinator = LiveExecutionCoordinator(
            {"paper": venue}, emergency_controller=EmergencyController()
        )
        intent = make_intent()
        authorization = coordinator.bind_authorization(
            intent,
            consent=self._consent(),  # type: ignore[arg-type]
            proposal_id="prop-paper-1",
        )
        first = coordinator.execute(
            intent, authorization, consent=self._consent()  # type: ignore[arg-type]
        )
        second = coordinator.execute(
            intent, authorization, consent=self._consent()  # type: ignore[arg-type]
        )
        assert first == second
        assert venue.order_count == 1

    def test_emergency_stop_blocks_execution(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        controller = EmergencyController(path=tmp_path / "emergency.json")
        venue = PaperVenue(reference_prices={"BTCUSDT": Decimal("50000")})
        coordinator = LiveExecutionCoordinator(
            {"paper": venue}, emergency_controller=controller
        )
        intent = make_intent()
        consent = self._consent()
        authorization = coordinator.bind_authorization(
            intent,
            consent=consent,  # type: ignore[arg-type]
            proposal_id="prop-paper-1",
        )
        controller.transition(
            halt=True,
            reason="panic",
            expected_revision=0,
            actor="operator-1",
            approver="operator-2",
        )
        with pytest.raises(PermissionError, match="emergency_halt"):
            coordinator.execute(
                intent, authorization, consent=consent  # type: ignore[arg-type]
            )
