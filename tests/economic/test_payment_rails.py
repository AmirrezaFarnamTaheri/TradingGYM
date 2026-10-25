"""Smoke tests for the economic-actions plane (evidence-only by design).

These pin the fail-closed guarantees of the payment rails and x402 challenge
handling: the target normalises and budgets payment metadata but never resolves
secrets, signs, or broadcasts. See docs/15_Federated_Tools_and_Economic_Actions.md.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_gym.execution.economic.payment_rails import (
    PaymentOperation,
    PaymentRailPolicy,
    evaluate_payment_rail,
    normalise_lightning_invoice,
    normalise_usdc_transfer,
)
from trading_gym.execution.economic.x402 import (
    build_economic_action_plan,
    parse_payment_challenge,
)


class TestUSDCNormalisation:
    def test_valid_transfer_normalises_to_unsigned_operation(self) -> None:
        op = normalise_usdc_transfer(
            {"amount": "12.50", "recipient": "0xabc", "expiry": "2026-12-01"}
        )
        assert isinstance(op, PaymentOperation)
        assert op.kind == "usdc_transfer"
        assert op.amount == "12.50"
        assert op.executed is False

    @pytest.mark.parametrize("secret", ["mnemonic", "private_key", "seed"])
    def test_secret_material_is_rejected(self, secret: str) -> None:
        with pytest.raises(ValueError, match="secret material"):
            normalise_usdc_transfer({"amount": "1", secret: "..."})

    def test_non_positive_amount_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="amount"):
            normalise_usdc_transfer({"amount": "0", "recipient": "0xabc"})

    def test_authority_expanding_policy_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="authority expanding"):
            normalise_usdc_transfer(
                {"amount": "1", "recipient": "0xabc"},
                PaymentRailPolicy(allow_execution=True),
            )


class TestLightningInvoice:
    def test_valid_invoice_hashes_without_paying(self) -> None:
        result = normalise_lightning_invoice("lnbcrt1qabcdefg", amount="100")
        assert result["paid"] is False
        assert result["amount"] == "100"
        assert len(result["invoice_sha256"]) == 64

    def test_malformed_invoice_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="bolt11"):
            normalise_lightning_invoice("not-an-invoice")


class TestPaymentRailEvaluation:
    def test_evidence_is_human_review_only(self) -> None:
        result = evaluate_payment_rail({"invoice": "x", "amount": "1"})
        assert result["ready_for_human_review"] is True
        assert result["executed"] is False
        assert result["signed"] is False


class TestX402Challenge:
    def test_challenge_parses_unsigned(self) -> None:
        result = parse_payment_challenge(
            {"amount": "5", "expires_at": "2026-12-01T00:00:00Z"}
        )
        assert Decimal(result["amount"]) == Decimal("5")
        assert result["signed"] is False
        assert result["broadcast"] is False

    def test_challenge_requires_expiry(self) -> None:
        with pytest.raises(ValueError, match="expiry required"):
            parse_payment_challenge({"amount": "5"})

    def test_challenge_requires_positive_amount(self) -> None:
        with pytest.raises(ValueError, match="amount"):
            parse_payment_challenge({"amount": "0", "expires_at": "2026-12-01"})

    def test_plan_never_grants_signing_or_broadcast_authority(self) -> None:
        plan = build_economic_action_plan(
            {"amount": "5", "expires_at": "2026-12-01T00:00:00Z"},
            budget_available="1000",
        )
        assert plan["reserved"] is True
        assert plan["signing_authority"] is False
        assert plan["broadcast_authority"] is False

    def test_plan_reports_unaffordable_action_without_authority(self) -> None:
        plan = build_economic_action_plan(
            {"amount": "5", "expires_at": "2026-12-01T00:00:00Z"},
            budget_available="1",
        )
        assert plan["reserved"] is False
        assert plan["signing_authority"] is False
