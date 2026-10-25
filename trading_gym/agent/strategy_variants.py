"""Grammar-bounded strategy mutation (ADR-034).

The mutation primitives the strategy forge and the
``strategy.propose_variants`` tool share. Kept separate from
``agent/forge.py`` because proposing variants needs nothing but the
grammar — no playground, no revision ledger — so the tool surface stays
cheap while the forge remains the only thing that can *submit* a
candidate.

Two invariants make this safe to expose:

- **The grammar is the bound.** Every variant is constructed through
  ``StrategySpec``, so a mutation violating the schema (unknown
  parameter, MACD fast ≥ slow, out-of-range period) raises at
  construction instead of producing an invalid spec somebody must reject
  later.
- **Deterministic and duplicate-free.** Variants are a pure function of
  (spec, count, seed); identical inputs give identical output, and a
  seed admitting fewer than ``count`` distinct mutations yields fewer
  variants rather than padding with duplicates.

What is mutated: integer indicator parameters (``period``, ``fast``,
``slow``, ``signal``, ``smooth``) and bounded historical ``lookback`` and numeric comparison constants in
the entry/exit conditions. Structure — which indicators exist, which
operators compare them — is *not* mutated: that is a structural (S-tier)
change under ADR-033 and needs replicated holdout evidence, not a
parametric jitter.
"""

from __future__ import annotations

import numpy as np

from trading_gym.agent.strategy_lab.models import (
    Condition,
    ConditionGroup,
    IndicatorSpec,
    Operand,
    StrategySpec,
)

__all__ = [
    "MAX_PERIOD",
    "MIN_PERIOD",
    "mutate_group",
    "mutate_indicators",
    "mutation_material",
    "propose_variants",
    "validate_range",
]

#: Inclusive bounds for mutated integer parameters. The grammar permits
#: 2..10_000; these keep candidates inside sane intraday horizons.
MIN_PERIOD = 2
MAX_PERIOD = 50

_PARAM_KEYS = ("period", "fast", "slow", "signal", "smooth", "lookback")


def validate_range(mutation_range: tuple[int, int]) -> tuple[int, int]:
    lo, hi = int(mutation_range[0]), int(mutation_range[1])
    if not MIN_PERIOD <= lo < hi <= MAX_PERIOD:
        raise ValueError(
            f"mutation_range must satisfy {MIN_PERIOD} <= lo < hi <= {MAX_PERIOD}"
        )
    return lo, hi


def mutation_material(spec: StrategySpec) -> str:
    """Identity of a candidate for de-duplication (name/description excluded)."""

    payload = spec.model_dump(mode="json")
    payload.pop("name", None)
    payload.pop("description", None)
    return repr(sorted(payload.items(), key=lambda item: item[0]))


def mutate_indicators(
    indicators: tuple[IndicatorSpec, ...] | list[IndicatorSpec],
    rng: np.random.Generator,
    lo: int,
    hi: int,
) -> list[IndicatorSpec]:
    """Jitter integer indicator parameters inside ``[lo, hi]``."""

    mutated: list[IndicatorSpec] = []
    for indicator in indicators:
        params = dict(indicator.params)
        for key in _PARAM_KEYS:
            if key not in params:
                continue
            current = int(params[key])
            span = max(2, current // 3)
            delta = int(rng.integers(-span, span + 1))
            # Historical percentile lookback is a separate research horizon:
            # do not collapse a 100-bar context to the intraday 2–50
            # threshold used for the ordinary short indicator periods.
            lower, upper = (2, 10_000) if key == "lookback" else (lo, hi)
            params[key] = max(lower, min(upper, current + delta))
        if indicator.name == "macd":
            params = _repair_macd(params, lo, hi)
        mutated.append(
            IndicatorSpec(alias=indicator.alias, name=indicator.name, params=params)
        )
    return mutated


def mutate_group(group: ConditionGroup, rng: np.random.Generator) -> ConditionGroup:
    """Jitter numeric constants inside a condition group.

    Thresholds move by ±10% of their magnitude (at least ±1 for
    oscillator-scale levels) and never change sign, so a "below 30"
    condition cannot silently become "below -30".
    """

    conditions: list[Condition] = []
    for condition in group.conditions:
        conditions.append(
            Condition(
                left=_mutate_operand(condition.left, rng),
                operator=condition.operator,
                right=_mutate_operand(condition.right, rng),
            )
        )
    return ConditionGroup(mode=group.mode, conditions=conditions)


def propose_variants(
    spec: StrategySpec,
    *,
    count: int,
    seed: int = 0,
    mutation_range: tuple[int, int] = (MIN_PERIOD, MAX_PERIOD),
) -> tuple[StrategySpec, ...]:
    """Deterministic grammar-bounded variants of ``spec``.

    Returns at most ``count`` distinct variants (the seed spec itself is
    excluded). ``count`` is bounded to 64 so a tool call cannot ask for
    an unbounded generation.
    """

    if count < 1:
        raise ValueError("count must be >= 1")
    if count > 64:
        raise ValueError("count must be <= 64")
    lo, hi = validate_range(mutation_range)
    rng = np.random.default_rng(int(seed))
    variants: list[StrategySpec] = []
    seen = {mutation_material(spec)}
    for _ in range(count * 12):
        if len(variants) >= count:
            break
        candidate = StrategySpec(
            name=f"{spec.name}-v{len(variants) + 1}",
            description=f"grammar variant {len(variants) + 1} of {spec.name}",
            timeframe=spec.timeframe,
            indicators=mutate_indicators(spec.indicators, rng, lo, hi),
            entry=mutate_group(spec.entry, rng),
            exit=mutate_group(spec.exit, rng),
            risk=spec.risk,
            tags=_variant_tags(spec.tags),
        )
        material = mutation_material(candidate)
        if material in seen:
            continue
        seen.add(material)
        variants.append(candidate)
    return tuple(variants)


# -- internals ----------------------------------------------------------------


def _variant_tags(tags: list[str]) -> list[str]:
    merged = [*tags, "variant"]
    # the grammar caps tags at 20; keep the newest and stay inside it
    return merged[-20:]


def _repair_macd(
    params: dict[str, float | int | str | bool], lo: int, hi: int
) -> dict[str, float | int | str | bool]:
    """The grammar requires MACD fast < slow; repair rather than fail."""

    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    if fast < slow:
        return params
    repaired = dict(params)
    if slow + 1 <= hi:
        repaired["slow"] = slow + 1 if slow >= fast else slow
        repaired["fast"] = min(fast, int(repaired["slow"]) - 1)
    else:
        repaired["slow"] = hi
        repaired["fast"] = max(lo, hi - 1)
    if int(repaired["fast"]) < lo:
        repaired["fast"] = lo
        repaired["slow"] = min(hi, lo + 1)
    return repaired


def _mutate_operand(operand: Operand, rng: np.random.Generator) -> Operand:
    if operand.kind != "constant":
        return operand
    value = float(operand.value)
    if value == 0.0:
        return operand
    magnitude = abs(value)
    step = max(1.0, magnitude * 0.1) if magnitude >= 1.0 else magnitude * 0.1
    delta = float(rng.uniform(-step, step))
    mutated = value + delta
    if (mutated > 0) != (value > 0):  # never flip the sign
        return operand
    return Operand(kind="constant", value=mutated)
