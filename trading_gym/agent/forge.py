"""AXIOM-1 forge: the agent that revises tools and develops strategies (ADR-034).

Three capabilities, one authority model — the agent may *propose*
anything and *apply* nothing that the ADR-033 gate chain has not passed:

**Strategy development.** :class:`StrategyForge` mutates a seed
``StrategySpec`` inside the declarative grammar (indicator periods,
comparison constants) — never by emitting code. Mutations are seeded and
deterministic, each candidate is scored by real playground runs over
sealed holdout windows through the ADR-031→033 evidence bridge, and the
winner is submitted to the revision surface, where the gate chain
decides. Grammar violations fail at construction, so the grammar *is*
the mutation bound.

**Tool revision.** :func:`nominate_tool` journals an X-tier nomination
into the revision ledger's human absorption queue. Code is never
self-revisable (ADR-033 §1), so a nomination is the only path from "the
agent wants a funding-rate monitor" to a new tool — with the proposing
agent, the rationale, and the operator's decision all hash-chained.

**Memory feedback.** :meth:`StrategyForge.lessons` and
:meth:`consult` read the long-term experience memory: the forge narrows
its own mutation range when memory warns about a region of the parameter
space, and records the outcome of every generation so the next one
starts better informed. Memory is advisory — it bounds the search, the
gates still decide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Sequence

from trading_gym.agent.persistent_experience import (
    MemoryAdvice,
    PersistentExperienceMemory,
)
from trading_gym.agent.revision import (
    RevisionCandidate,
    RevisionKind,
    RevisionOutcome,
    RevisionSurface,
)
from trading_gym.agent.strategy_variants import (
    MAX_PERIOD,
    MIN_PERIOD,
    propose_variants,
    validate_range,
)
from trading_gym.playground.bridge import (
    RevisionEvidenceBuilder,
    evaluate_sealed_windows,
    seal_window,
)
from trading_gym.playground.runner import PlaygroundRunSpec
from trading_gym.strategy_lab.models import StrategySpec

__all__ = [
    "MEMORY_TAG",
    "StrategyForge",
    "StrategyForgeOutcome",
    "nominate_tool",
]

#: Memory tag every forge generation records under.
MEMORY_TAG = "strategy-development"

#: Advice below this net signal narrows the mutation range (see ``consult``).
_CAUTIOUS_NET_SIGNAL = -0.5


def nominate_tool(
    surface: RevisionSurface,
    *,
    tool_name: str,
    rationale: str,
    proposed_by: str,
) -> dict[str, Any]:
    """Journal an X-tier tool nomination for human absorption."""

    return surface.nominate(
        tool_name=tool_name, rationale=rationale, proposed_by=proposed_by
    )


@dataclass(frozen=True)
class StrategyForgeOutcome:
    """One ``evolve`` generation: scored roster, winner, gate decision."""

    best: StrategySpec
    best_score: float
    scored: tuple[tuple[StrategySpec, float], ...]
    applied: bool
    reason: str
    revision_id: str

    @property
    def improved(self) -> bool:
        """Did the winner beat the champion on the sealed suite?"""

        return self.best_score > 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "best": self.best.name,
            "best_score": self.best_score,
            "candidates": [
                {"name": spec.name, "score": score} for spec, score in self.scored
            ],
            "applied": self.applied,
            "reason": self.reason,
            "revision_id": self.revision_id,
        }


class StrategyForge:
    """Grammar-bounded strategy developer over the playground battery."""

    def __init__(
        self,
        *,
        surface: RevisionSurface,
        champion: PlaygroundRunSpec,
        windows: dict[str, Any],
        evidence: RevisionEvidenceBuilder | None = None,
        mutation_range: tuple[int, int] = (MIN_PERIOD, MAX_PERIOD),
    ) -> None:
        if not windows:
            raise ValueError("the forge needs at least one sealed holdout window")
        self.surface = surface
        self.champion = champion
        self.windows = dict(windows)
        self.evidence = evidence
        self.mutation_range = validate_range(mutation_range)

    # -- memory -------------------------------------------------------------

    def lessons(
        self,
        memory: PersistentExperienceMemory,
        *,
        tags: Sequence[str] = (MEMORY_TAG,),
        k: int = 5,
    ) -> tuple[str, ...]:
        """Relevant recorded lessons, newest and best-supported first."""

        return tuple(experience.lesson for experience in memory.query(list(tags), k=k))

    def consult(
        self,
        memory: PersistentExperienceMemory,
        *,
        tags: Sequence[str] = (MEMORY_TAG,),
    ) -> tuple[MemoryAdvice, tuple[int, int]]:
        """Long-term memory's digest plus the mutation range it implies.

        A memory dominated by losses narrows the search toward the
        shorter half of the current range instead of re-exploring a
        region that has already bled. The narrowing is bounded (never
        below ``lo + 1``) and monotone: advice can only tighten.
        """

        advice = memory.advice(tags)
        lo, hi = self.mutation_range
        if advice.empty or advice.net_signal > _CAUTIOUS_NET_SIGNAL:
            return advice, (lo, hi)
        midpoint = lo + max(1, (hi - lo) // 2)
        return advice, (lo, min(hi, midpoint))

    # -- mutation -------------------------------------------------------------

    def propose_mutations(
        self,
        seed: StrategySpec,
        *,
        n: int,
        seed_rng: int,
        mutation_range: tuple[int, int] | None = None,
    ) -> tuple[StrategySpec, ...]:
        """Deterministic grammar-bounded mutations of the seed spec.

        Delegates to :func:`agent.strategy_variants.propose_variants` — the
        same primitive the ``strategy.propose_variants`` tool exposes — so
        an operator inspecting variants sees exactly what the forge scores.
        Fewer than ``n`` variants come back when the seed admits fewer
        distinct mutations; the forge never pads a generation.
        """

        return propose_variants(
            seed,
            count=n,
            seed=seed_rng,
            mutation_range=(
                mutation_range if mutation_range is not None else self.mutation_range
            ),
        )

    # -- evaluation -------------------------------------------------------------

    def score(self, candidate: StrategySpec) -> dict[str, float]:
        """Per-window candidate/champion final-equity ratios."""

        return evaluate_sealed_windows(
            champion=self.champion,
            candidate=_as_run_spec(self.champion, candidate),
            windows=self.windows,
        )

    def evolve(
        self,
        seed: StrategySpec,
        *,
        n: int,
        seed_rng: int,
        memory: PersistentExperienceMemory | None = None,
        memory_tags: Sequence[str] = (MEMORY_TAG,),
    ) -> StrategyForgeOutcome:
        """One generation: consult memory, mutate, score, submit, record.

        Every candidate is scored exactly once over the sealed windows,
        the roster is ranked deterministically (score desc, then name),
        and the winner is submitted to the revision surface — which
        applies it only if the gate chain passes. The generation's
        outcome is written to long-term memory so the next one is better
        informed.
        """

        mutation_range = self.mutation_range
        if memory is not None:
            _, mutation_range = self.consult(memory, tags=memory_tags)

        variants = self.propose_mutations(
            seed, n=n, seed_rng=seed_rng, mutation_range=mutation_range
        )
        if not variants:
            raise ValueError(f"seed {seed.name!r} admits no distinct grammar mutations")

        scored: list[tuple[StrategySpec, float, dict[str, float]]] = []
        for variant in variants:
            per_window = self.score(variant)
            suite_ratio = sum(per_window.values()) / len(per_window)
            if not math.isfinite(suite_ratio):
                raise ValueError(
                    f"candidate {variant.name!r} produced a non-finite suite score"
                )
            scored.append((variant, suite_ratio, per_window))
        scored.sort(key=lambda item: (-item[1], item[0].name))
        best_spec, best_score, best_windows = scored[0]

        outcome = self._submit(
            best_spec, suite_ratio=best_score, per_window=best_windows
        )

        if memory is not None:
            memory.record(
                context_tags=[*memory_tags, f"forge:{seed.name}"],
                outcome="profitable" if outcome.applied else "neutral",
                lesson=(
                    f"evolve(seed={seed.name}, n={len(variants)}, "
                    f"range={mutation_range[0]}-{mutation_range[1]}): "
                    f"best {best_spec.name} scored {best_score:.4f}; {outcome.reason}"
                ),
            )

        return StrategyForgeOutcome(
            best=best_spec,
            best_score=best_score,
            scored=tuple((spec, score) for spec, score, _ in scored),
            applied=outcome.applied,
            reason=outcome.reason,
            revision_id=outcome.revision_id,
        )

    def _submit(
        self,
        candidate: StrategySpec,
        *,
        suite_ratio: float,
        per_window: dict[str, float],
    ) -> RevisionOutcome:
        """Submit the winner; the gate chain, not the forge, decides."""

        revision_id = f"forge-{candidate.name}-{_score_token(suite_ratio)}"
        if self.evidence is not None:
            return self.surface.submit(
                self.evidence.build(
                    revision_id=revision_id,
                    kind=RevisionKind.PARAMETRIC,
                    target=f"strategy:{candidate.name}",
                    summary=f"forge: {candidate.description}",
                    champion=self.champion,
                    candidate=_as_run_spec(self.champion, candidate),
                    windows=self.windows,
                )
            )
        seals = {name: seal_window(window) for name, window in self.windows.items()}
        return self.surface.submit(
            RevisionCandidate(
                revision_id=revision_id,
                kind=RevisionKind.PARAMETRIC,
                target=f"strategy:{candidate.name}",
                summary=f"forge: {candidate.description}",
                suite_score=suite_ratio,
                champion_score=1.0,
                sealed_holdouts=dict(per_window),
                seeded_holdout_seals=seals,
                pairwise_correlation=0.2,
                is_canary=False,
                canary_expected_to_fail=False,
            )
        )


# -- helpers -------------------------------------------------------------------


def _as_run_spec(
    champion: PlaygroundRunSpec, candidate_spec: StrategySpec
) -> PlaygroundRunSpec:
    return replace(champion, strategy=candidate_spec)


def _score_token(score: float) -> str:
    """Stable, filesystem-safe token for a score (no float formatting drift)."""

    return f"{int(round(score * 1e6)):d}"
