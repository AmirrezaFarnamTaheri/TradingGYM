"""Compact, decision-time context for fast advisory System-1 models.

Chat completions are stateless. Each call needs current financial facts,
source freshness, risk posture, and optionally verified memory. Never send
raw tool output, unbounded history, or future observations to the model.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Callable, Mapping

MAX_CONTEXT_CHARS = 4_000
_MAX_RETURNS = 12
_MAX_EXPERIENCES = 3
_MAX_CYCLES = 4
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,79}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _finite(value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid market/account context")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("invalid market/account context")
    return number


def context_tags(context: Any) -> tuple[str, ...]:
    symbol = str(context.symbol).lower()
    if not _TAG_RE.fullmatch(symbol):
        raise ValueError("invalid market symbol")
    returns = tuple(_finite(v) for v in context.recent_returns[:8])
    regime = "chop"
    if len(returns) >= 2:
        momentum = sum(returns[:4])
        if momentum > 0.006:
            regime = "uptrend"
        elif momentum < -0.006:
            regime = "downtrend"
    return (f"symbol:{symbol}", f"regime:{regime}")


def _memory_digest(source: Any, tags: tuple[str, ...]) -> dict[str, Any]:
    """Obtain a bounded advisory digest, never raw journal entries."""
    result = source.advice(tags, k=_MAX_EXPERIENCES)
    value = result.to_dict() if hasattr(result, "to_dict") else result
    if not isinstance(value, Mapping):
        raise ValueError("invalid retrieved memory digest")
    net = _finite(value.get("net_signal", 0))
    if not -1 <= net <= 1:
        raise ValueError("invalid memory support signal")
    digest: dict[str, Any] = {
        "net_signal": net,
        "warnings": [],
        "confirmations": [],
    }
    for name in ("warnings", "confirmations"):
        records = value.get(name, ())
        if not isinstance(records, (list, tuple)):
            raise ValueError("invalid memory entries")
        for record in records[:_MAX_EXPERIENCES]:
            if not isinstance(record, Mapping):
                raise ValueError("invalid memory entries")
            lesson, support = record.get("lesson"), record.get("support")
            if not isinstance(lesson, str) or type(support) is not int or support < 1:
                raise ValueError("invalid memory evidence")
            digest[name].append({"lesson": lesson[:160], "support": support})
    return digest


def _working_digest(memory: Any) -> dict[str, Any]:
    cycles = []
    for item in memory.recent(_MAX_CYCLES):
        if item.outcome is None or item.pnl_quote is None:
            continue  # unverified/unsettled outcomes never count as evidence
        cycles.append(
            {
                "outcome": str(item.outcome)[:30],
                "pnl_quote": _finite(item.pnl_quote),
            }
        )
    return {
        "loss_streak": int(memory.loss_streak()),
        "recent_realized_pnl": _finite(memory.recent_pnl(_MAX_CYCLES)),
        "settled_cycles": cycles,
    }


def build_system1_packet(
    context: Any,
    *,
    working_memory: Any = None,
    experience_memory: Any = None,
    now: Callable[[], float] = time.time,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """Build valid bounded JSON; never slice away required market or risk facts.

    Historical/offline evaluation never sees experience written later
    (hindsight). Persistent retrieval runs only with proven live freshness.
    """
    if type(max_chars) is not int or not 512 <= max_chars <= 16_000:
        raise ValueError("invalid system1 context budget")
    symbol = str(context.symbol)
    if not symbol or len(symbol) > 48 or any(c.isspace() for c in symbol):
        raise ValueError("invalid market symbol")
    market = {
        "symbol": symbol,
        "last_close": _finite(context.last_close, positive=True),
        "prev_close": (
            _finite(context.prev_close, positive=True)
            if context.prev_close is not None
            else None
        ),
        "recent_returns": [
            _finite(v) for v in context.recent_returns[:_MAX_RETURNS]
        ],
    }
    raw_features = getattr(context, "market_features", {}) or {}
    if not isinstance(raw_features, Mapping) or len(raw_features) > 32:
        raise ValueError("invalid named market indicators")
    indicators: dict[str, float] = {}
    for raw_name, value in sorted(raw_features.items()):
        if not isinstance(raw_name, str):
            raise ValueError("invalid named market indicators")
        name = raw_name.lower()
        if not _TAG_RE.fullmatch(name):
            raise ValueError("invalid named market indicators")
        indicators[name] = _finite(value)
    if indicators:
        market["normalized_indicators"] = indicators
    account = {
        "equity": _finite(context.equity, positive=True),
        "position_units": _finite(context.position_units),
        "margin_level": (
            _finite(context.margin_level)
            if context.margin_level is not None
            else None
        ),
        "drawdown": (
            _finite(context.drawdown_from_peak)
            if context.drawdown_from_peak is not None
            else None
        ),
    }
    if account["drawdown"] is not None and not 0 <= account["drawdown"] <= 1:
        raise ValueError("invalid account drawdown")
    evidence = getattr(context, "market_evidence", {}) or {}
    if not isinstance(evidence, Mapping):
        raise ValueError("invalid market provenance")
    live = getattr(context, "is_live", False)
    if type(live) is not bool:
        raise ValueError("invalid live context flag")
    freshness: dict[str, Any] = {
        "status": "historical" if not live else "unverified"
    }
    if live and evidence:
        timestamp = evidence.get("bar_open_unix")
        interval = evidence.get("bar_seconds")
        digest = evidence.get("source_sha256")
        if (
            type(timestamp) is not int
            or type(interval) is not int
            or interval < 60
            or interval > 604800
            or not isinstance(digest, str)
            or not _HASH_RE.fullmatch(digest)
        ):
            raise PermissionError("invalid_live_market_provenance")
        age = float(now()) - (timestamp + interval)
        # A future close cannot become "verified_closed_bar", even within
        # a clock-skew tolerance. Wait for the actual observed close.
        if not math.isfinite(age) or age < 0 or age > max(300, interval * 3):
            raise PermissionError("stale_live_market_provenance")
        freshness = {
            "status": "verified_closed_bar",
            "bar_open_unix": timestamp,
            "bar_seconds": interval,
            "age_seconds": max(0.0, round(age, 2)),
            "source_sha256": digest,
        }
    if live and freshness["status"] != "verified_closed_bar":
        raise PermissionError("unverified_live_market_freshness")
    limits = getattr(context, "risk_limits", {}) or {}
    if not isinstance(limits, Mapping) or len(limits) > 20:
        raise ValueError("invalid risk context")
    risk: dict[str, float] = {}
    for name, value in sorted(limits.items()):
        if not isinstance(name, str) or not _TAG_RE.fullmatch(name):
            raise ValueError("invalid risk context")
        risk[name] = _finite(value)

    packet: dict[str, Any] = {
        "schema": "system1.market.v1",
        "authority": "advisory_only; never executes orders",
        "freshness": freshness,
        "market": market,
        "account": account,
        "risk_constraints": risk,
    }
    if working_memory is not None:
        packet["working_memory"] = _working_digest(working_memory)
    if experience_memory is not None and live:
        packet["experience_digest"] = _memory_digest(
            experience_memory, context_tags(context)
        )

    def encode() -> str:
        return json.dumps(
            packet, sort_keys=True, separators=(",", ":"), allow_nan=False
        )

    result = encode()
    # Shed optional evidence in a fixed order; required fields always stay.
    if len(result) > max_chars:
        packet.pop("experience_digest", None)
        result = encode()
    if len(result) > max_chars:
        packet.pop("working_memory", None)
        result = encode()
    if len(result) > max_chars:
        packet["market"].pop("normalized_indicators", None)
        result = encode()
    if len(result) > max_chars:
        packet["market"]["recent_returns"] = (
            packet["market"]["recent_returns"][:4]
        )
        result = encode()
    if len(result) > max_chars:
        raise ValueError("system1 essential context exceeds budget")
    return result
