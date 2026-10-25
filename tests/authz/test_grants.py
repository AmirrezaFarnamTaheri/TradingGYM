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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_gym.core.authz.grants import (
    ConstraintViolation,
    GrantLedger,
    GrantLedgerIntegrityError,
    GrantRequest,
    GrantState,
    ScopeDenied,
)
from trading_gym.core.authz.scopes import ScopeError

TEST_WINDOW = "2026-09-01T00:00:00Z"
# Deliberate, fixed decision clock: these grants expire on 2026-10-01.
# Keep expiry enforcement enabled while testing other grant semantics.
TEST_DECISION_TIME = "2026-09-02T00:00:00Z"


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
            GrantLedger.open(tmp_path / "grants.jsonl").issue(make_request(ttl_hours=0))

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

        allowed = ledger.check(
            "agent-axiom1", "trade.paper.order.submit", now=TEST_DECISION_TIME
        )
        assert allowed.allowed
        denied = ledger.check(
            "agent-other", "trade.paper.order.submit", now=TEST_DECISION_TIME
        )
        assert not denied.allowed
        assert denied.reason == "no_matching_scope"

    def test_check_rejects_unknown_principal(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        decision = ledger.check("agent-nobody", "trade.paper.order.submit")
        assert not decision.allowed

    def test_require_returns_grant_or_raises(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(scopes=("trade.paper.**",)))
        found = ledger.require(
            "agent-axiom1", "trade.paper.order", now=TEST_DECISION_TIME
        )
        assert found.grant_id == grant.grant_id
        with pytest.raises(ScopeDenied):
            ledger.require("agent-axiom1", "trade.live.order", now=TEST_DECISION_TIME)


class TestEvaluation:
    def test_scope_match_allows_action(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(scopes=("trade.paper.**",)))
        decision = grant.evaluate("trade.paper.order.submit", now=TEST_DECISION_TIME)
        assert decision.allowed
        assert decision.matched_scope == "trade.paper.**"

    def test_unmatched_scope_denied(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(scopes=("trade.paper.**",)))
        decision = grant.evaluate("trade.live.order.submit", now=TEST_DECISION_TIME)
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
        issued_at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        grant = ledger.issue(make_request(issued_at=issued_at, ttl_hours=1))
        # No explicit now: evaluated against the actual wall clock.
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
            "trade.paper.order.submit",
            now=TEST_DECISION_TIME,
            constraints={"max_notional_quote": 499},
        )
        assert decision.allowed
        denied = grant.evaluate(
            "trade.paper.order.submit",
            now=TEST_DECISION_TIME,
            constraints={"max_notional_quote": 501},
        )
        assert not denied.allowed
        assert denied.reason == "constraint_max_notional_quote"

    def test_breach_latches_revoked_breach(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(constraints=(("max_notional_quote", 500),)))
        breach = grant.evaluate(
            "trade.paper.order.submit",
            now=TEST_DECISION_TIME,
            constraints={"max_notional_quote": 1000},
            breach=True,
        )
        assert not breach.allowed
        refreshed = ledger.get(grant.grant_id)
        assert refreshed.state is GrantState.REVOKED_BREACH

    def test_non_numeric_constraint_value_rejected(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ConstraintViolation):
            ledger.issue(make_request(constraints=(("max_notional_quote", "500"),)))
        assert ledger.events() == ()

    def test_historical_non_numeric_ceiling_fails_on_direct_evaluation(
        self,
    ) -> None:
        # Signed historical records must remain parseable for revocation and
        # forensic review, despite being forbidden on new issuance.
        from trading_gym.core.authz.grants import Grant

        legacy = Grant.from_record(
            {
                "grant_id": "gw-ffffffffffff",
                "principal": "agent-axiom1",
                "scopes": ["trade.paper.**"],
                "state": GrantState.ACTIVE,
                "issued_at": TEST_WINDOW,
                "expires_at": "2026-10-01T00:00:00Z",
                "issued_by": "operator-1",
                "constraints": {"max_notional_quote": "500"},
            }
        )
        with pytest.raises(ConstraintViolation):
            legacy.evaluate(
                "trade.paper.order.submit",
                now=TEST_DECISION_TIME,
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


@pytest.mark.parametrize(
    "pattern",
    ("trade.**", "trade.live.*", "production.**", "x402.pay.*"),
)
def test_new_high_risk_wildcard_grants_are_rejected_without_writing_journal(
    tmp_path: Path, pattern: str
) -> None:
    ledger = GrantLedger.open(tmp_path / "grants.jsonl")
    with pytest.raises(ScopeError, match="wildcard"):
        ledger.issue(make_request(scopes=(pattern,)))
    assert ledger.events() == ()


def test_existing_legacy_grant_record_can_still_be_parsed() -> None:
    from trading_gym.core.authz.grants import Grant

    legacy = {
        "grant_id": "gw-111111111111",
        "principal": "agent-axiom1",
        "scopes": ["trade.live.**"],
        "state": "active",
        "issued_at": TEST_WINDOW,
        "expires_at": "2026-10-01T00:00:00Z",
        "issued_by": "operator-1",
    }
    loaded = Grant.from_record(legacy)
    assert loaded.scopes.patterns[0].text == "trade.live.**"
    assert not loaded.scopes.patterns[0].is_issuable()


class TestOverlappingGrantRiskControls:
    """Constraints bind matching authority, not an arbitrary first grant."""

    def test_omitted_observation_denied_even_on_direct_evaluate(
        self, tmp_path: Path
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(constraints=(("max_notional_quote", 500),)))
        decision = grant.evaluate("trade.paper.order.submit", now=TEST_DECISION_TIME)
        assert not decision.allowed
        assert decision.reason == "constraint_missing_max_notional_quote"
        assert not ledger.check(
            "agent-axiom1", "trade.paper.order.submit", now=TEST_DECISION_TIME
        ).allowed

    def test_deny_overrides_allow_even_when_unconstrained_grant_came_first(
        self, tmp_path: Path
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        ledger.issue(make_request())
        ledger.issue(make_request(constraints=(("max_notional_quote", 500),)))
        decision = ledger.check(
            "agent-axiom1",
            "trade.paper.order.submit",
            now=TEST_DECISION_TIME,
            constraints={"max_notional_quote": 501},
        )
        assert not decision.allowed
        assert decision.reason == "constraint_max_notional_quote"
        with pytest.raises(ScopeDenied):
            ledger.require(
                "agent-axiom1",
                "trade.paper.order.submit",
                now=TEST_DECISION_TIME,
                constraints={"max_notional_quote": 501},
            )

    def test_matching_restricted_and_unrestricted_grants_allow_safe_values(
        self, tmp_path: Path
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        ledger.issue(make_request())
        ledger.issue(make_request(constraints=(("max_notional_quote", 500),)))
        verdict = ledger.check(
            "agent-axiom1",
            "trade.paper.order.submit",
            now=TEST_DECISION_TIME,
            constraints={"max_notional_quote": 499},
        )
        assert verdict.allowed
        assert (
            ledger.require(
                "agent-axiom1",
                "trade.paper.order.submit",
                now=TEST_DECISION_TIME,
                constraints={"max_notional_quote": 499},
            ).grant_id
            == verdict.grant_id
        )

    def test_unrelated_scope_cannot_veto_authorized_action(
        self, tmp_path: Path
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        ledger.issue(
            make_request(
                scopes=("research.**",),
                constraints=(("max_drawdown_pct", 1),),
            )
        )
        ledger.issue(make_request())
        assert ledger.check(
            "agent-axiom1", "trade.paper.order.submit", now=TEST_DECISION_TIME
        ).allowed

    @pytest.mark.parametrize(
        "observed",
        [
            float("nan"),
            float("inf"),
            float("-inf"),
            True,
        ],
    )
    def test_invalid_observation_fails_closed_without_500(
        self, tmp_path: Path, observed: object
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(make_request(constraints=(("max_notional_quote", 500),)))
        with pytest.raises(ConstraintViolation):
            grant.evaluate(
                "trade.paper.order.submit",
                now=TEST_DECISION_TIME,
                constraints={"max_notional_quote": observed},
            )
        denial = ledger.check(
            "agent-axiom1",
            "trade.paper.order.submit",
            now=TEST_DECISION_TIME,
            constraints={"max_notional_quote": observed},
        )
        assert not denial.allowed
        assert denial.reason == "invalid_constraints"

    @pytest.mark.parametrize("ceiling", [float("nan"), float("inf")])
    def test_bad_ceiling_is_rejected_before_journal_append(
        self, tmp_path: Path, ceiling: float
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ConstraintViolation):
            ledger.issue(make_request(constraints=(("max_notional_quote", ceiling),)))
        assert ledger.events() == ()

    def test_future_dated_grant_cannot_be_used_before_issuance(
        self, tmp_path: Path
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(
            make_request(issued_at="2026-09-05T00:00:00Z", ttl_hours=24)
        )
        assert not grant.is_active(now=TEST_DECISION_TIME)
        early = grant.evaluate("trade.paper.order.submit", now=TEST_DECISION_TIME)
        assert not early.allowed
        assert early.reason == "grant_not_yet_valid"
        start = grant.evaluate("trade.paper.order.submit", now="2026-09-05T00:00:00Z")
        assert start.allowed

    def test_old_grant_handle_stops_authorizing_after_revocation(
        self, tmp_path: Path
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        old_handle = ledger.issue(make_request())
        assert old_handle.evaluate(
            "trade.paper.order.submit", now=TEST_DECISION_TIME
        ).allowed
        ledger.revoke(old_handle.grant_id, actor="operator-1")
        assert not old_handle.is_active(now=TEST_DECISION_TIME)
        decision = old_handle.evaluate(
            "trade.paper.order.submit", now=TEST_DECISION_TIME
        )
        assert not decision.allowed
        assert decision.reason == "grant_revoked"


class TestGrantAnchorCorruptionRegression:
    def test_full_journal_truncation_with_surviving_anchor_is_rejected(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "grants.jsonl"
        ledger = GrantLedger.open(path)
        ledger.issue(make_request())
        assert path.with_suffix(".jsonl.anchor").exists()
        path.write_text("", encoding="utf-8")
        with pytest.raises(GrantLedgerIntegrityError, match="truncation"):
            GrantLedger.open(path)

    def test_deleted_journal_with_surviving_anchor_is_rejected(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "grants.jsonl"
        ledger = GrantLedger.open(path)
        ledger.issue(make_request())
        path.unlink()
        with pytest.raises(GrantLedgerIntegrityError, match="truncation"):
            GrantLedger.open(path)

    @pytest.mark.parametrize("bad_anchor", ["{broken", "[]", "null", "true"])
    def test_malformed_anchor_yields_typed_integrity_failure(
        self, tmp_path: Path, bad_anchor: str
    ) -> None:
        path = tmp_path / "grants.jsonl"
        GrantLedger.open(path).issue(make_request())
        path.with_suffix(".jsonl.anchor").write_text(bad_anchor, encoding="utf-8")
        with pytest.raises(GrantLedgerIntegrityError):
            GrantLedger.open(path)

    def test_boolean_anchor_sequence_is_not_accepted_as_integer(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "grants.jsonl"
        GrantLedger.open(path).issue(make_request())
        anchor_path = path.with_suffix(".jsonl.anchor")
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["seq"] = True  # True == 1 in Python; must fail strict shape.
        anchor_path.write_text(json.dumps(anchor), encoding="utf-8")
        with pytest.raises(GrantLedgerIntegrityError):
            GrantLedger.open(path)

    def test_non_object_journal_record_is_integrity_error(self, tmp_path: Path) -> None:
        path = tmp_path / "grants.jsonl"
        path.write_text("null" + chr(10), encoding="utf-8")
        with pytest.raises(GrantLedgerIntegrityError):
            GrantLedger.open(path)

    def test_bounded_anchor_size_prevents_unbounded_json_read(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "grants.jsonl"
        GrantLedger.open(path).issue(make_request())
        path.with_suffix(".jsonl.anchor").write_text(" " * 8192, encoding="utf-8")
        with pytest.raises(GrantLedgerIntegrityError, match="size bound"):
            GrantLedger.open(path)

    def test_existing_valid_anchor_survives_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "grants.jsonl"
        first = GrantLedger.open(path)
        grant = first.issue(make_request())
        reopened = GrantLedger.open(path)
        assert reopened.verify()["entries"] == 1
        assert reopened.get(grant.grant_id).state == GrantState.ACTIVE


class TestGrantIssuanceConstraintValidation:
    @pytest.mark.parametrize(
        "ceiling",
        ["500", True, float("nan"), float("inf"), float("-inf"), -1],
    )
    def test_invalid_numeric_ceilings_never_enter_journal(
        self, tmp_path: Path, ceiling: object
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ConstraintViolation):
            ledger.issue(make_request(constraints=(("max_notional_quote", ceiling),)))
        assert ledger.events() == ()

    def test_duplicate_ceiling_names_rejected(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ConstraintViolation, match="duplicate"):
            ledger.issue(
                make_request(
                    constraints=(
                        ("max_notional_quote", 500),
                        ("max_notional_quote", 1000),
                    )
                )
            )

    def test_malformed_constraint_name_rejected(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ConstraintViolation, match="name"):
            ledger.issue(make_request(constraints=(("max_notional_quote!", 100),)))

    def test_boolean_ttl_must_not_be_treated_as_one_hour(self, tmp_path: Path) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        with pytest.raises(ValueError, match="ttl_hours"):
            ledger.issue(make_request(ttl_hours=True))

    def test_multiple_valid_numeric_ceilings_are_preserved(
        self, tmp_path: Path
    ) -> None:
        ledger = GrantLedger.open(tmp_path / "grants.jsonl")
        grant = ledger.issue(
            make_request(
                constraints=(
                    ("max_notional_quote", 500),
                    ("max_orders_per_hour", 10),
                )
            )
        )
        assert grant.constraints == {
            "max_notional_quote": 500,
            "max_orders_per_hour": 10,
        }
        assert grant.evaluate(
            "trade.paper.order.submit",
            now=TEST_DECISION_TIME,
            constraints={"max_notional_quote": 450, "max_orders_per_hour": 9},
        ).allowed


class TestConcurrentGrantJournalTransitions:
    def test_stale_second_revoke_is_rejected_before_append(
        self, tmp_path: Path
    ) -> None:
        journal = tmp_path / "grants.jsonl"
        first = GrantLedger.open(journal)
        grant = first.issue(make_request())
        second = GrantLedger.open(journal)

        first.revoke(grant.grant_id, actor="operator-1")
        with pytest.raises(ValueError, match="cannot revoke"):
            second.revoke(grant.grant_id, actor="operator-2")

        current = GrantLedger.open(journal)
        assert current.verify()["entries"] == 2
        assert current.get(grant.grant_id).state == GrantState.REVOKED

    def test_stale_breach_after_revoke_preserves_actual_state(
        self, tmp_path: Path
    ) -> None:
        journal = tmp_path / "grants.jsonl"
        first = GrantLedger.open(journal)
        grant = first.issue(make_request())
        second = GrantLedger.open(journal)

        first.revoke(grant.grant_id, actor="operator-1")
        second._latch_breach(grant.grant_id)

        current = GrantLedger.open(journal)
        assert current.verify()["entries"] == 2
        assert current.get(grant.grant_id).state == GrantState.REVOKED
        assert second.get(grant.grant_id).state == GrantState.REVOKED

    def test_two_stale_breach_latches_are_idempotent(self, tmp_path: Path) -> None:
        journal = tmp_path / "grants.jsonl"
        first = GrantLedger.open(journal)
        grant = first.issue(make_request())
        second = GrantLedger.open(journal)

        first._latch_breach(grant.grant_id)
        second._latch_breach(grant.grant_id)

        current = GrantLedger.open(journal)
        assert current.verify()["entries"] == 2
        assert current.get(grant.grant_id).state == GrantState.REVOKED_BREACH

    def test_colliding_issuer_handles_are_checked_under_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        journal = tmp_path / "grants.jsonl"
        first = GrantLedger.open(journal)
        second = GrantLedger.open(journal)
        monkeypatch.setattr(
            "trading_gym.core.authz.grants.uuid.uuid4",
            lambda: SimpleNamespace(hex="f" * 32),
        )

        grant = first.issue(make_request())
        with pytest.raises(ValueError, match="duplicate grant ID"):
            second.issue(make_request())

        current = GrantLedger.open(journal)
        assert current.verify()["entries"] == 1
        assert current.get(grant.grant_id).state == GrantState.ACTIVE

    def test_unrelated_concurrent_issuance_remains_valid(self, tmp_path: Path) -> None:
        journal = tmp_path / "grants.jsonl"
        first = GrantLedger.open(journal)
        second = GrantLedger.open(journal)
        first.issue(make_request())
        second.issue(make_request(principal="agent-other"))

        current = GrantLedger.open(journal)
        assert current.verify()["entries"] == 2
        assert len(current.active_grants(now=TEST_DECISION_TIME)) == 2
