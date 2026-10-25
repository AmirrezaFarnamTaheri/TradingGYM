"""AXIOM-1 authz scope grammar (ADR-028): parsing, wildcards, matching.

Contract:
- Segments are lowercase ``[a-z0-9_-]``, ``*`` matches exactly one segment,
  ``**`` must be terminal and matches zero or more remaining segments.
- Concrete scopes (the thing being checked) may never contain wildcards.
- Grantable patterns never start with a reserved namespace or ``**``.
"""

from __future__ import annotations

import pytest

from trading_gym.core.authz.scopes import (
    ScopeError,
    ScopePattern,
    ScopeSet,
    ScopeToken,
    RESERVED_FIRST_SEGMENTS,
    RESERVED_SCOPES,
)


class TestScopeTokenParsing:
    def test_parses_simple_scope(self) -> None:
        tok = ScopeToken.parse("trade.live.btcusdt.order.submit")
        assert tok.segments == ("trade", "live", "btcusdt", "order", "submit")
        assert tok.text == "trade.live.btcusdt.order.submit"

    def test_allows_digits_underscore_hyphen(self) -> None:
        tok = ScopeToken.parse("trade.paper.btc_usdt-2024.order")
        assert tok.segments == ("trade", "paper", "btc_usdt-2024", "order")

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "trade..submit",
            " trade.live",
            "trade.live ",
            "Trade.live",
            "trade.LIVE",
            "trade;drop",
            "trade\u00e9",
            "trade|live",
            "tr*de",
            "trade.li*e",
            "a" * 65,
            ".".join(["s"] * 17),
        ],
    )
    def test_rejects_invalid_scope_text(self, bad: str) -> None:
        with pytest.raises(ScopeError):
            ScopeToken.parse(bad)

    def test_single_star_is_full_segment_anywhere(self) -> None:
        assert ScopeToken.parse("trade.*.order").segments == ("trade", "*", "order")
        assert ScopeToken.parse("*.live").segments == ("*", "live")

    def test_double_star_terminal_only(self) -> None:
        assert ScopeToken.parse("trade.**").segments == ("trade", "**")
        with pytest.raises(ScopeError):
            ScopeToken.parse("trade.**.order")
        with pytest.raises(ScopeError):
            ScopeToken.parse("**.order")


class TestScopePatternMatching:
    def test_literal_pattern_requires_exact_match(self) -> None:
        pattern = ScopePattern.parse("trade.live.order.submit")
        assert pattern.matches("trade.live.order.submit")
        assert not pattern.matches("trade.live.order")
        assert not pattern.matches("trade.live.order.submit.cancel")

    def test_single_star_matches_exactly_one_segment(self) -> None:
        pattern = ScopePattern.parse("trade.*.order.submit")
        assert pattern.matches("trade.paper.order.submit")
        assert pattern.matches("trade.live.order.submit")
        assert not pattern.matches("trade.order.submit")
        assert not pattern.matches("trade.paper.extra.order.submit")

    def test_double_star_matches_zero_or_more_remaining(self) -> None:
        pattern = ScopePattern.parse("trade.**")
        assert pattern.matches("trade")
        assert pattern.matches("trade.paper")
        assert pattern.matches("trade.paper.order.submit")
        assert not pattern.matches("trading.paper")

    def test_star_and_double_star_compose(self) -> None:
        pattern = ScopePattern.parse("trade.*.order.**")
        assert pattern.matches("trade.paper.order.submit")
        assert pattern.matches("trade.paper.order")
        assert not pattern.matches("trade.paper")

    def test_concrete_scope_must_be_wildcard_free(self) -> None:
        pattern = ScopePattern.parse("trade.**")
        with pytest.raises(ScopeError):
            pattern.matches("trade.*.order")
        with pytest.raises(ScopeError):
            pattern.matches("trade.**")

    def test_shorter_concrete_fails_without_double_star(self) -> None:
        pattern = ScopePattern.parse("trade.live.order")
        assert not pattern.matches("trade.live")


class TestGrantability:
    def test_reserved_namespaces_exist(self) -> None:
        assert {"emergency", "governance", "wallet", "authz"} <= RESERVED_FIRST_SEGMENTS
        assert "audit.seal" in RESERVED_SCOPES

    def test_reserved_first_segment_is_not_grantable(self) -> None:
        for text in (
            "governance.proposal.approve",
            "wallet.withdraw",
            "authz.grant.issue",
            "emergency.halt",
        ):
            assert not ScopePattern.parse(text).is_grantable()

    def test_catch_all_is_not_grantable(self) -> None:
        assert not ScopePattern.parse("**").is_grantable()

    def test_audit_seal_reserved_deep_scope(self) -> None:
        assert not ScopePattern.parse("audit.seal").is_grantable()
        # unrelated audit reads may be granted
        assert ScopePattern.parse("audit.read.**").is_grantable()

    def test_wildcard_covering_reserved_scope_is_not_grantable(self) -> None:
        # The text-exact check alone admits these: ``audit.*`` and ``audit.**``
        # both match ``audit.seal``, so granting them would delegate the
        # reserved capability under a wildcard.
        assert not ScopePattern.parse("audit.*").is_grantable()
        assert not ScopePattern.parse("audit.**").is_grantable()
        # A wildcard that does not reach the reserved scope stays grantable.
        assert ScopePattern.parse("audit.read.*").is_grantable()

    def test_normal_scopes_are_grantable(self) -> None:
        for text in ("trade.paper.**", "trade.live.btcusdt.order", "research.**"):
            assert ScopePattern.parse(text).is_grantable()


class TestScopeSet:
    def test_match_returns_none_when_nothing_matches(self) -> None:
        scope_set = ScopeSet.parse(("trade.paper.**",))
        assert scope_set.match("trade.live.order") is None

    def test_most_specific_pattern_wins(self) -> None:
        scope_set = ScopeSet.parse(("trade.**", "trade.paper.order"))
        matched = scope_set.match("trade.paper.order")
        assert matched is not None
        assert matched.text == "trade.paper.order"

    def test_first_segment_literal_is_enforced_for_grantability(self) -> None:
        with pytest.raises(ScopeError):
            ScopeSet.parse(("**",))


class TestHighRiskNewDelegation:
    @pytest.mark.parametrize(
        "pattern",
        (
            "trade.**",
            "trade.*.order.submit",
            "trade.live.**",
            "trade.live.btcusdt.order.*",
            "production.jobs.*",
            "economic.x402.**",
            "x402.payment.*",
            "payments.**",
            "venue.order.**",
            "research.production.*",
        ),
    )
    def test_rejects_high_risk_wildcards_for_new_issuance(self, pattern: str) -> None:
        assert not ScopePattern.parse(pattern).is_issuable()

    @pytest.mark.parametrize(
        "pattern",
        (
            "trade.paper.**",
            "research.**",
            "audit.read.*",
            "playground.**",
            "trade.live.btcusdt.order.submit",
            "economic.x402.payment.execute",
        ),
    )
    def test_paper_research_and_explicit_economic_scopes_allowed(
        self, pattern: str
    ) -> None:
        assert ScopePattern.parse(pattern).is_issuable()

    def test_legacy_broad_patterns_remain_parseable_for_revoke(self) -> None:
        old = ScopeSet.parse(("trade.live.**",))
        assert old.match("trade.live.order.submit") is not None
        assert not old.patterns[0].is_issuable()
