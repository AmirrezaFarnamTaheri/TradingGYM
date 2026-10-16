"""Read-only, PIT joint admission for historical factor data sufficiency.

A factor needing fundamentals of an index constituent must satisfy BOTH
known-date membership and filing availability at the same as-of instant.
This reports data readiness only, never tradability, positive alpha or grants.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from trading_gym.market.fundamental_asof import (
    FundamentalFact,
    FundamentalQuery,
    assess_fundamentals_asof,
)
from trading_gym.market.universe_asof import (
    UniverseMembershipEvent,
    assess_universe_asof,
)


def assess_factor_research_readiness(
    *,
    fundamental_facts: Sequence[FundamentalFact],
    fundamental_queries: Sequence[FundamentalQuery],
    membership_events: Sequence[UniverseMembershipEvent],
    universe_id: str,
    instruments: Sequence[str],
    as_of_unix: int,
    max_period_age_seconds: int = 550 * 86400,
) -> dict[str, Any]:
    """Combine independent evidence blockers using conservative conjunction."""
    universe = assess_universe_asof(
        events=membership_events, universe_id=universe_id,
        instruments=instruments, as_of_unix=as_of_unix,
    )
    demand = set(instruments)
    if any(
        not isinstance(query, FundamentalQuery)
        or query.instrument_id not in demand
        for query in fundamental_queries
    ):
        raise ValueError("factor query instrument outside historical universe request")
    fundamentals = assess_fundamentals_asof(
        facts=fundamental_facts, queries=fundamental_queries,
        as_of_unix=as_of_unix, max_period_age_seconds=max_period_age_seconds,
    )
    blockers = set(universe["blockers"]) | set(fundamentals["blockers"])
    for item in universe["members"]:
        if item["state"] == "excluded":
            blockers.add(f"not_universe_member:{item['instrument_id']}")
        elif item["state"] != "included":
            blockers.add(f"membership_unknown:{item['instrument_id']}")
    result: dict[str, Any] = {
        "schema": "tradinggym.factor-research-readiness.v1",
        "universe_id": universe_id,
        "as_of_unix": as_of_unix,
        "universe_sha256": universe["report_sha256"],
        "fundamentals_sha256": fundamentals["report_sha256"],
        "member_count": len(universe["members"]),
        "filing_query_count": len(fundamentals["facts"]),
        "blockers": sorted(blockers),
        "ready_for_research": not blockers,
        "survivorship_bias_not_independently_ruled_out": True,
        "source_authenticity_verified": False,
        "factor_authority_granted": False,
        "execution_authority_granted": False,
        "mutation_performed": False,
    }
    result["report_sha256"] = hashlib.sha256(
        json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return result
