"""Cross-broker order-status normalization for TradingGYM venue adapters.

Donor provenance: lumibot ``lumibot/entities/order.py`` (evidence window
120-165; definitions verified at lines 114-162) carries a hand-written
``VALID_STATUS`` list plus a ``STATUS_ALIAS_MAP`` that folds broker-specific
raw statuses (``"apicancelled"``, ``"pending_new"``, ``"rejected_by_exchange"``,
...) onto one small vocabulary before anything downstream inspects an order.
This module ports that idea target-natively: **every venue adapter calls
:func:`normalize_status` right before persisting order state**, so the
execution, accounting and research planes observe a single
:class:`CanonicalOrderStatus` vocabulary instead of a zoo of per-venue strings.

Matching rules: case-insensitive, surrounding whitespace stripped, and
separator-insensitive (``-``, ``_`` and space are interchangeable word
separators), so ``"Part-Filled"``, ``"partially_filled"`` and
``"PARTIALLY FILLED"`` resolve identically. Unknown raw strings never raise --
they degrade to :attr:`CanonicalOrderStatus.UNKNOWN`, mirroring lumibot's
``"unknown"`` sentinel.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "STATUS_ALIASES",
    "CanonicalOrderStatus",
    "is_terminal",
    "normalize_status",
]


class CanonicalOrderStatus(str, Enum):
    """The single order-status vocabulary shared by every TradingGYM plane."""

    PENDING_NEW = "pending_new"  # accepted by gateway, not yet live at the venue
    OPEN = "open"  # live/working at the venue
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"  # unresolved raw status; adapters must persist this, never raise


#: Per-venue lookup tables mapping lowercase raw venue status strings onto
#: :class:`CanonicalOrderStatus`. Keys are stored in their natural spelling;
#: :func:`normalize_status` folds case/separators on both sides before lookup.
#: A venue whose key is absent from this dict falls back to ``"generic"``.
STATUS_ALIASES: dict[str, dict[str, CanonicalOrderStatus]] = {
    # Abantether REST order statuses. "new"/"pending" mean created-but-not-yet-
    # live, hence PENDING_NEW; "processing" is an actively worked order.
    "abantether": {
        "new": CanonicalOrderStatus.PENDING_NEW,
        "pending": CanonicalOrderStatus.PENDING_NEW,
        "open": CanonicalOrderStatus.OPEN,
        "processing": CanonicalOrderStatus.OPEN,
        "partially_filled": CanonicalOrderStatus.PARTIALLY_FILLED,
        "filled": CanonicalOrderStatus.FILLED,
        "cancelled": CanonicalOrderStatus.CANCELLED,
        "rejected": CanonicalOrderStatus.REJECTED,
        "expired": CanonicalOrderStatus.EXPIRED,
    },
    # OKX v5 order states: named states plus the numeric-string codes used by
    # the private orders channel ("-1" canceled, "0" live, "1" partially
    # filled, "2" fully filled); "effectively_canceled" is OKX's IOC/FOK
    # residual-cancel state.
    "okx": {
        "live": CanonicalOrderStatus.OPEN,
        "partially_filled": CanonicalOrderStatus.PARTIALLY_FILLED,
        "effectively_canceled": CanonicalOrderStatus.CANCELLED,
        "canceled": CanonicalOrderStatus.CANCELLED,
        "filled": CanonicalOrderStatus.FILLED,
        "-1": CanonicalOrderStatus.CANCELLED,
        "0": CanonicalOrderStatus.OPEN,
        "1": CanonicalOrderStatus.PARTIALLY_FILLED,
        "2": CanonicalOrderStatus.FILLED,
    },
    # Broker-agnostic synonyms (FIX-style: OrdStatus New(0) = accepted and
    # working). Covers the common spellings seen across equities/crypto venues;
    # deliberately omits transitional states (e.g. "cancelling") so they land
    # on UNKNOWN instead of pretending to be terminal.
    "generic": {
        "working": CanonicalOrderStatus.OPEN,
        "open": CanonicalOrderStatus.OPEN,
        "new": CanonicalOrderStatus.OPEN,
        "submitted": CanonicalOrderStatus.OPEN,
        "part-filled": CanonicalOrderStatus.PARTIALLY_FILLED,
        "partially-filled": CanonicalOrderStatus.PARTIALLY_FILLED,
        "partially_filled": CanonicalOrderStatus.PARTIALLY_FILLED,
        "fill": CanonicalOrderStatus.FILLED,
        "filled": CanonicalOrderStatus.FILLED,
        "canceled": CanonicalOrderStatus.CANCELLED,
        "cancelled": CanonicalOrderStatus.CANCELLED,
        "cancel": CanonicalOrderStatus.CANCELLED,
        "expired": CanonicalOrderStatus.EXPIRED,
        "rejected": CanonicalOrderStatus.REJECTED,
        "rejected_by_exchange": CanonicalOrderStatus.REJECTED,
    },
}

def _fold_key(value: str) -> str:
    """Fold a status token to its lookup form.

    Lowercases, strips surrounding whitespace, and normalizes ``-`` and space
    to ``_`` so separators are interchangeable. Applied symmetrically to table
    keys and incoming raw strings; ``"-1"`` folds to ``"_1"`` on both sides and
    therefore still matches.
    """
    return value.strip().lower().replace("-", "_").replace(" ", "_")


#: Folded-key projection of :data:`STATUS_ALIASES` used for O(1) lookups;
#: derived once at import so the public tables stay human-readable.
_FOLDED_ALIASES: dict[str, dict[str, CanonicalOrderStatus]] = {
    venue: {_fold_key(raw): status for raw, status in table.items()}
    for venue, table in STATUS_ALIASES.items()
}

_GENERIC_KEY = "generic"

#: Exactly these canonical states end an order's lifecycle.
_TERMINAL_STATUSES: frozenset[CanonicalOrderStatus] = frozenset(
    {
        CanonicalOrderStatus.FILLED,
        CanonicalOrderStatus.CANCELLED,
        CanonicalOrderStatus.REJECTED,
        CanonicalOrderStatus.EXPIRED,
    }
)


def normalize_status(raw: str, *, venue: str = "generic") -> CanonicalOrderStatus:
    """Map a raw venue status string onto :class:`CanonicalOrderStatus`.

    This is the integration point for venue adapters: call it before persisting
    any order-state transition so downstream planes share one vocabulary.

    Args:
        raw: Raw status string exactly as reported by the venue. May carry
            arbitrary casing, surrounding whitespace, or ``-``/``_``/space
            separators. ``None``, non-string values, and empty/blank strings
            yield :attr:`CanonicalOrderStatus.UNKNOWN`.
        venue: Venue whose alias table to consult (case-insensitive). Unknown
            venue names fall back to the ``"generic"`` table.

    Returns:
        The canonical status. Unresolvable inputs return
        :attr:`CanonicalOrderStatus.UNKNOWN` -- this function never raises on
        bad input.
    """
    if not isinstance(raw, str):
        return CanonicalOrderStatus.UNKNOWN
    folded = _fold_key(raw)
    if not folded:
        return CanonicalOrderStatus.UNKNOWN

    venue_key = venue.strip().lower() if isinstance(venue, str) else ""
    table = _FOLDED_ALIASES.get(venue_key)
    if table is None:
        table = _FOLDED_ALIASES[_GENERIC_KEY]
    return table.get(folded, CanonicalOrderStatus.UNKNOWN)


def is_terminal(status: CanonicalOrderStatus) -> bool:
    """Return ``True`` iff ``status`` ends an order's lifecycle.

    Terminal states are exactly FILLED, CANCELLED, REJECTED and EXPIRED;
    PENDING_NEW, OPEN, PARTIALLY_FILLED and UNKNOWN are not terminal.
    """
    return status in _TERMINAL_STATUSES
