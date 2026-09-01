"""Tests for the capability-degradation ladder (trading_gym.agent.degradation_ladder).

Donor provenance: ai-trading-agent-master(1) capability-degradation retry
ladder; Hyperliquid TS twin holdAll degraded mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pytest

from trading_gym.agent.degradation_ladder import (
    AttemptRecord,
    CapabilityMode,
    DegradationLadder,
    LadderOutcome,
)

SECRET_TOKEN = "sk-donor-secret-do-not-leak"


def _always_valid(value: object) -> bool:
    return True


def test_richest_first_ordering_honored() -> None:
    calls: List[str] = []

    def rich(context: dict) -> str:
        calls.append("rich")
        return f"rich:{context['symbol']}"

    def mid(context: dict) -> str:
        calls.append("mid")
        return "mid"

    def poor(context: dict) -> str:
        calls.append("poor")
        return "poor"

    ladder = DegradationLadder[str](
        [
            CapabilityMode("rich_structured", rich, lambda v: v.startswith("rich:"), "schema decode"),
            CapabilityMode("mid_text", mid, lambda v: bool(v), "plain text"),
            CapabilityMode("poor_hold", poor, lambda v: bool(v), "hold-all"),
        ]
    )

    outcome = ladder.run({"symbol": "BTC-USD"})

    assert isinstance(outcome, LadderOutcome)
    assert outcome.success is True
    assert outcome.value == "rich:BTC-USD"
    assert outcome.mode_used == "rich_structured"
    assert outcome.records == ()
    assert calls == ["rich"]  # poorer rungs never invoked when a richer one passes


def test_handler_exception_falls_through_to_next_mode() -> None:
    def boom(context: dict) -> int:
        raise RuntimeError("rich path exploded")

    ladder = DegradationLadder[int](
        [
            CapabilityMode("rich", boom, lambda v: v > 0),
            CapabilityMode("poor", lambda c: 42, lambda v: v > 0),
        ]
    )

    outcome = ladder.run({})

    assert outcome.success is True
    assert outcome.value == 42
    assert outcome.mode_used == "poor"
    assert len(outcome.records) == 1
    record = outcome.records[0]
    assert isinstance(record, AttemptRecord)
    assert record.mode == "rich"
    assert record.outcome == "error"
    assert record.detail == "RuntimeError"


def test_validator_false_falls_through_to_next_mode() -> None:
    seen: List[str] = []

    def bad(context: dict) -> int:
        seen.append("bad")
        return -1

    def good(context: dict) -> int:
        seen.append("good")
        return 7

    ladder = DegradationLadder[int](
        [
            CapabilityMode("bad", bad, lambda v: v > 0),
            CapabilityMode("good", good, lambda v: v > 0),
        ]
    )

    outcome = ladder.run({})

    assert outcome.success is True
    assert outcome.value == 7
    assert outcome.mode_used == "good"
    assert [(r.mode, r.outcome) for r in outcome.records] == [("bad", "invalid_output")]
    assert seen == ["bad", "good"]  # next mode actually attempted after rejection


def test_none_result_treated_as_invalid_output() -> None:
    ladder = DegradationLadder[str](
        [
            CapabilityMode("none_mode", lambda c: None, _always_valid),
            CapabilityMode("fallback", lambda c: "ok", lambda v: bool(v)),
        ]
    )

    outcome = ladder.run({})

    assert outcome.success is True
    assert outcome.value == "ok"
    assert [(r.mode, r.outcome, r.detail) for r in outcome.records] == [
        ("none_mode", "invalid_output", "handler returned None")
    ]


def test_all_modes_fail_returns_unsuccessful_with_complete_records_in_order() -> None:
    attempted: List[str] = []

    def raises(context: dict) -> int:
        attempted.append("a_raise")
        raise ValueError("nope")

    def none_mode(context: dict) -> int:
        attempted.append("b_none")
        return None  # type: ignore[return-value]

    def invalid_mode(context: dict) -> int:
        attempted.append("c_invalid")
        return -5

    ladder = DegradationLadder[int](
        [
            CapabilityMode("a_raise", raises, _always_valid),
            CapabilityMode("b_none", none_mode, _always_valid),
            CapabilityMode("c_invalid", invalid_mode, lambda v: v > 0),
        ]
    )

    outcome = ladder.run({"k": "v"})

    assert outcome.success is False
    assert outcome.value is None
    assert outcome.mode_used is None
    assert [r.mode for r in outcome.records] == ["a_raise", "b_none", "c_invalid"]
    assert [r.outcome for r in outcome.records] == ["error", "invalid_output", "invalid_output"]
    assert outcome.records[0].detail == "ValueError"
    assert attempted == ["a_raise", "b_none", "c_invalid"]


def test_empty_modes_raises_value_error() -> None:
    with pytest.raises(ValueError):
        DegradationLadder([])


def test_empty_mode_name_raises_value_error() -> None:
    with pytest.raises(ValueError):
        CapabilityMode(name="", handler=lambda c: 1, validator=_always_valid)


def test_single_mode_success_has_empty_records() -> None:
    ladder = DegradationLadder[int]([CapabilityMode("only", lambda c: 3, lambda v: v % 2 == 1)])

    outcome = ladder.run({"x": 1})

    assert outcome.success is True
    assert outcome.value == 3
    assert outcome.mode_used == "only"
    assert outcome.records == ()


def test_records_never_contain_exception_message_text() -> None:
    def leaky(context: dict) -> str:
        raise RuntimeError(f"upstream failed while using {SECRET_TOKEN}")

    ladder = DegradationLadder[str]([CapabilityMode("leaky", leaky, _always_valid)])

    outcome = ladder.run({})

    rendered_records = repr(outcome.records) + str(outcome.records)
    assert SECRET_TOKEN not in rendered_records
    assert SECRET_TOKEN not in outcome.records[0].detail
    assert outcome.records[0].detail == "RuntimeError"  # type name only


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float


def test_generic_type_works_with_dataclass_value() -> None:
    def book(context: dict) -> Quote:
        return Quote(bid=100.0, ask=100.5)

    def stale(context: dict) -> Quote:
        return Quote(bid=99.0, ask=99.25)

    ladder: DegradationLadder[Quote] = DegradationLadder(
        [
            CapabilityMode("level2_book", book, lambda q: q.bid < q.ask),
            CapabilityMode("stale_quote", stale, lambda q: q.ask > 0),
        ]
    )

    outcome = ladder.run({})

    assert isinstance(outcome.value, Quote)
    assert outcome.value == Quote(bid=100.0, ask=100.5)
    assert outcome.mode_used == "level2_book"
    assert outcome.success is True
