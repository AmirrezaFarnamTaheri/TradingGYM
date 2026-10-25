"""Tests for cross-broker order-status normalization.

Covers the alias tables, the matching tolerance (case / whitespace /
separator-insensitive), unknown-input degradation, terminal-state detection,
and venue fallback to the generic table.
"""

from __future__ import annotations

import pytest

from trading_gym.market.adapters.order_status_normalization import (
    STATUS_ALIASES,
    CanonicalOrderStatus,
    is_terminal,
    normalize_status,
)

#: One parametrize case per (venue, raw-key) entry of every alias table.
_ALIAS_CASES = [
    pytest.param(venue, raw, expected, id=f"{venue}[{raw}]")
    for venue, table in STATUS_ALIASES.items()
    for raw, expected in table.items()
]


@pytest.mark.parametrize(("venue", "raw", "expected"), _ALIAS_CASES)
def test_every_alias_table_entry_normalizes_to_canonical(
    venue: str, raw: str, expected: CanonicalOrderStatus
) -> None:
    """Each entry of STATUS_ALIASES resolves to exactly its declared member."""
    assert normalize_status(raw, venue=venue) is expected


def test_alias_tables_contain_no_unknown_values() -> None:
    """Alias tables exist to resolve: no entry may map to UNKNOWN."""
    for venue, table in STATUS_ALIASES.items():
        assert (
            CanonicalOrderStatus.UNKNOWN not in table.values()
        ), f"{venue} alias table must not contain UNKNOWN values"


@pytest.mark.parametrize(
    ("raw", "venue", "expected"),
    [
        ("FILLED", "generic", CanonicalOrderStatus.FILLED),
        ("filled", "generic", CanonicalOrderStatus.FILLED),
        ("  filled  ", "generic", CanonicalOrderStatus.FILLED),
        ("\tPart-Filled\n", "generic", CanonicalOrderStatus.PARTIALLY_FILLED),
        ("PARTIALLY_FILLED", "generic", CanonicalOrderStatus.PARTIALLY_FILLED),
        ("partially filled", "generic", CanonicalOrderStatus.PARTIALLY_FILLED),
        ("Rejected_By_Exchange", "generic", CanonicalOrderStatus.REJECTED),
        (" Working ", "generic", CanonicalOrderStatus.OPEN),
        ("LIVE", "okx", CanonicalOrderStatus.OPEN),
        ("Effectively-Canceled", "okx", CanonicalOrderStatus.CANCELLED),
        ("-1", "okx", CanonicalOrderStatus.CANCELLED),
        ("Effectively Canceled", "okx", CanonicalOrderStatus.CANCELLED),
        ("Processing", "abantether", CanonicalOrderStatus.OPEN),
        ("NEW", "abantether", CanonicalOrderStatus.PENDING_NEW),
    ],
)
def test_matching_is_case_whitespace_and_separator_insensitive(
    raw: str, venue: str, expected: CanonicalOrderStatus
) -> None:
    """Casing, surrounding whitespace and -/_/space separators do not matter."""
    assert normalize_status(raw, venue=venue) is expected


@pytest.mark.parametrize(
    "raw",
    ["definitely_not_a_status", "???", "123abc", "", "   ", "\t\n"],
)
def test_garbage_and_blank_strings_degrade_to_unknown(raw: str) -> None:
    """Unresolvable strings return UNKNOWN instead of raising."""
    assert normalize_status(raw) is CanonicalOrderStatus.UNKNOWN


@pytest.mark.parametrize("raw", [None, 42, 3.14, b"filled", ["filled"]])
def test_non_string_inputs_degrade_to_unknown(raw: object) -> None:
    """None and non-string inputs return UNKNOWN instead of raising."""
    assert normalize_status(raw) is CanonicalOrderStatus.UNKNOWN  # type: ignore[arg-type]


def test_okx_specific_status_is_unknown_under_missing_venue() -> None:
    """Fallback consults only the generic table -- okx tokens stay UNKNOWN there."""
    assert (
        normalize_status("effectively_canceled", venue="kraken")
        is CanonicalOrderStatus.UNKNOWN
    )


def test_unknown_venue_falls_back_to_generic_table() -> None:
    """A venue missing from STATUS_ALIASES resolves via the generic table."""
    assert normalize_status("working", venue="kraken") is CanonicalOrderStatus.OPEN
    assert normalize_status("open", venue="KRAKEN") is CanonicalOrderStatus.OPEN
    # Default venue IS generic.
    assert normalize_status("working") is CanonicalOrderStatus.OPEN


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CanonicalOrderStatus.PENDING_NEW, False),
        (CanonicalOrderStatus.OPEN, False),
        (CanonicalOrderStatus.PARTIALLY_FILLED, False),
        (CanonicalOrderStatus.FILLED, True),
        (CanonicalOrderStatus.CANCELLED, True),
        (CanonicalOrderStatus.REJECTED, True),
        (CanonicalOrderStatus.EXPIRED, True),
        (CanonicalOrderStatus.UNKNOWN, False),
    ],
)
def test_is_terminal_exactly_on_four_terminal_states(
    status: CanonicalOrderStatus, expected: bool
) -> None:
    """is_terminal is True precisely for FILLED/CANCELLED/REJECTED/EXPIRED."""
    assert is_terminal(status) is expected
