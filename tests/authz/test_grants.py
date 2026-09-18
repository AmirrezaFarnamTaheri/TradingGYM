"""AXIOM-1 authz grants (ADR-028): evaluation and hash-chained ledger.

Contract:
- A grant binds a principal to grantable scope patterns plus numeric
  constraints; evaluation is deny-overrides-allow and fail-closed.
- TTL expiry is evaluated against an injectable clock.
- Constraint breaches latch the grant ``revoked-breach`` (drawdown checks read
  caller-supplied state, not their own).
- Every mutation is journaled through a hash-chained JSONL ledger which
  verifies its own chain on load and refuses tampered files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_gym.authz.grants import (
    ConstraintViolation,
    GrantLedger,
    GrantLedgerIntegrityError,
    GrantRequest,
    GrantState,
    ScopeDenied,
)
from trading_gym.authz.scopes import ScopeError


TEST_WINDOW = "2026-09-01T00:00:00Z"


def make_request(**overrides: object) -> GrantRequest:
    base: dict[str, object] = {
        "principal": "agent-axiom1",
        "scopes": ("trade.paper.**",),
        "ttl_hours": 720,
        "issued_at": TEST_WINDOW,
        "constraints": (),
        "issued_by": "operator-1",
    }
    base.update(overrides)
    return GrantRequest(**base)  # type: ignore[arg-type]


class TestGrantLifecycle:
    def test_active_grant_has_id_and_expiry(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request())
        assert grant.state is GrantState.ACTIVE
        assert grant.grant_id
        assert grant.expires_at == "2026-10-01T00:00:00Z"

    def test_ttl_hours_must_be_positive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            GrantLedger.open(tmp_path / "grants.jsonl").issue(
                make_request(ttl_hours=0)
            )

    def test_revoke_is_terminal(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request())
        revoked = ledger.revoke(grant.grant_id, actor="operator-1")
        assert revoked.state is GrantState.REVOKED
        with pytest.raises(ValueError):
            ledger.revoke(grant.grant_id, actor="operator-1")

    def test_revoke_unknown_grant_fails(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(KeyError):
            ledger.revoke("gw-does-not-exist", actor="operator-1")


class TestLedgerEvaluation:
    def test_check_allows_across_principal_grants(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        ledger.issue(make_request(scopes=("trade.paper.**",)))
        other = make_request(principal="agent-other", scopes=("research.**",))
        ledger.issue(other)

        allowed = ledger.check("agent-axiom1", "trade.paper.order.submit")
        assert allowed.allowed
        denied = ledger.check("agent-other", "trade.paper.order.submit")
        assert not denied.allowed
        assert denied.reason == "no_matching_scope"

    def test_check_rejects_unknown_principal(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        decision = ledger.check("agent-nobody", "trade.paper.order.submit")
        assert not decision.allowed

    def test_require_returns_grant_or_raises(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(scopes=("trade.paper.**",)))
        found = ledger.require("agent-axiom1", "trade.paper.order")
        assert found.grant_id == grant.grant_id
        with pytest.raises(ScopeDenied):
            ledger.require("agent-axiom1", "trade.live.order")


class TestEvaluation:
    def test_scope_match_allows_action(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(scopes=("trade.paper.**",)))
        decision = grant.evaluate("trade.paper.order.submit")
        assert decision.allowed
        assert decision.matched_scope == "trade.paper.**"

    def test_unmatched_scope_denied(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(scopes=("trade.paper.**",)))
        decision = grant.evaluate("trade.live.order.submit")
        assert not decision.allowed
        assert decision.reason == "no_matching_scope"

    def test_expired_grant_denies_fail_closed(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(ttl_hours=1))
        decision = grant.evaluate(
            "trade.paper.order.submit", now="2026-09-01T02:00:00Z"
        )
        assert not decision.allowed
        assert decision.reason == "grant_expired"

    def test_expired_grant_denies_on_default_clock_path(self, tmp_path: Path) -> None:
        """The default clock must be the present, not issued_at. Comparing
        issued_at < expires_at is tautologically true, so omitting ``now`` used
        to keep an expired grant active on every production call path."""
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(issued_at=TEST_WINDOW, ttl_hours=1))
        # No explicit now: evaluated against the wall clock.
        decision = grant.evaluate("trade.paper.order.submit")
        assert not decision.allowed
        assert decision.reason == "grant_expired"

    def test_revoked_grant_denies(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request())
        grant = ledger.revoke(grant.grant_id, actor="operator-1")
        decision = grant.evaluate("trade.paper.order.submit")
        assert not decision.allowed
        assert decision.reason == "grant_revoked"

    def test_constraint_ceiling_enforced(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(
            make_request(
                constraints=(("max_notional_quote", 500),),
            )
        )
        decision = grant.evaluate(
            "trade.paper.order.submit", constraints={"max_notional_quote": 499}
        )
        assert decision.allowed
        denied = grant.evaluate(
            "trade.paper.order.submit", constraints={"max_notional_quote": 501}
        )
        assert not denied.allowed
        assert denied.reason == "constraint_max_notional_quote"

    def test_breach_latches_revoked_breach(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(
            make_request(constraints=(("max_notional_quote", 500),))
        )
        breach = grant.evaluate(
            "trade.paper.order.submit",
            constraints={"max_notional_quote": 1000},
            breach=True,
        )
        assert not breach.allowed
        refreshed = ledger.get(grant.grant_id)
        assert refreshed.state is GrantState.REVOKED_BREACH

    def test_non_numeric_constraint_value_rejected(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(
            make_request(constraints=(("max_notional_quote", "500"),))
        )
        with pytest.raises(ConstraintViolation):
            grant.evaluate(
                "trade.paper.order.submit",
                constraints={"max_notional_quote": 100},
            )


class TestGrantLedgerIntegrity:
    def test_reload_preserves_state(self, tmp_path: Path) -> None:
        path = tmp_path / "grants.jsonl"
        ledger = GrantLedger.open(path)
        grant = ledger.issue(make_request())
        ledger.revoke(grant.grant_id, actor="operator-1")

        reopened = GrantLedger.open(path)
        assert reopened.get(grant.grant_id).state is GrantState.REVOKED

    def test_tampered_record_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "grants.jsonl"
        ledger = GrantLedger.open(path)
        grant = ledger.issue(make_request())

        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["principal"] = "agent-impersonator"
        lines[0] = json.dumps(record, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(GrantLedgerIntegrityError):
            GrantLedger.open(path)
        assert ledger.get(grant.grant_id).state is GrantState.ACTIVE

    def test_truncated_tail_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "grants.jsonl"
        ledger = GrantLedger.open(path)
        grant = ledger.issue(make_request())
        ledger.revoke(grant.grant_id, actor="operator-1")

        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text(lines[0] + "\n", encoding="utf-8")

        with pytest.raises(GrantLedgerIntegrityError):
            GrantLedger.open(path)

    def test_ledger_rejects_ungrantable_scope(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ScopeError):
            ledger.issue(make_request(scopes=("governance.proposal.approve",)))

    def test_ledger_rejects_empty_scope_list(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ScopeError):
            ledger.issue(make_request(scopes=()))
