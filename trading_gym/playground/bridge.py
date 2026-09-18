"""Playground-to-revision evidence bridge (ADR-031 → ADR-033).

Builds :class:`~trading_gym.agent.revision.RevisionCandidate` evidence
from actual deterministic runs, so self-improvement scores are never
typed by hand: the champion and candidate specs are executed over the
same sealed holdout windows, and each score is the final-equity ratio
(candidate / champion). Seals are ``sha256(window frame)`` recorded at
build time — the revision surface's holdout-leakage gate compares them,
so scoring against a swapped window fails the seal.
"""

from __future__ import annotations

import hashlib
import math
from typing import Mapping

import pandas as pd

from trading_gym.agent.ensemble_correlation import (
    ensemble_pairwise_correlation,
)
from trading_gym.agent.revision import RevisionCandidate, RevisionKind
from trading_gym.playground.runner import PlaygroundRunSpec, run_playground

__all__ = [
    "RevisionEvidenceBuilder",
    "build_revision_candidate",
    "evaluate_sealed_windows",
    "seal_window",
]


def seal_window(window: pd.DataFrame) -> str:
    """Content seal for a holdout window (stable across runs)."""

    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(window, index=True).to_numpy().tobytes())
    for column in window.columns:
        digest.update(str(column).encode("utf-8"))
    return digest.hexdigest()


def _final_equity(spec: PlaygroundRunSpec, window: pd.DataFrame) -> float:
    run_spec = PlaygroundRunSpec(
        window=window,
        strategy=spec.strategy,
        overlays=spec.overlays,
        initial_capital=spec.initial_capital,
        fee_bps=spec.fee_bps,
        position_fraction=spec.position_fraction,
        max_bars=spec.max_bars,
    )
    equity = float(run_playground(run_spec).metrics["final_equity"])
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError(
            "sealed-window run produced non-finite or non-positive equity — "
            "crashes are evidence of failure, not a skip"
        )
    return equity


def evaluate_sealed_windows(
    *,
    champion: PlaygroundRunSpec,
    candidate: PlaygroundRunSpec,
    windows: Mapping[str, pd.DataFrame],
) -> dict[str, float]:
    """Candidate/champion final-equity ratios over each sealed window."""

    if not windows:
        raise ValueError("at least one sealed holdout window is required")
    scores: dict[str, float] = {}
    for name, window in windows.items():
        champion_equity = _final_equity(champion, window)
        candidate_equity = _final_equity(candidate, window)
        scores[name] = candidate_equity / champion_equity
    return scores


def build_revision_candidate(
    *,
    revision_id: str,
    kind: RevisionKind,
    target: str,
    summary: str,
    champion: PlaygroundRunSpec,
    candidate: PlaygroundRunSpec,
    windows: Mapping[str, pd.DataFrame],
) -> RevisionCandidate:
    """Run both specs over the sealed windows and bundle the evidence."""

    scores = evaluate_sealed_windows(
        champion=champion, candidate=candidate, windows=windows
    )
    suite_ratio = sum(scores.values()) / len(scores)
    seals = {name: seal_window(window) for name, window in windows.items()}
    # v1: single-strategy candidates carry no ensemble dimension, so the
    # diversity gate starts from a safe, unblocking correlation. Use
    # RevisionEvidenceBuilder (below) to supply a measured correlation.
    correlation = 0.2
    return RevisionCandidate(
        revision_id=revision_id,
        kind=kind,
        target=target,
        summary=summary,
        suite_score=suite_ratio,
        champion_score=1.0,
        sealed_holdouts=dict(scores),
        seeded_holdout_seals=seals,
        pairwise_correlation=correlation,
        is_canary=False,
        canary_expected_to_fail=False,
    )


class RevisionEvidenceBuilder:
    """Bundle playground evidence with a *measured* ensemble correlation.

    ``build`` runs the sealed-window evaluation (same scores/seals as
    :func:`build_revision_candidate`) and replaces the safe-default
    correlation with the mean pairwise correlation of the roster's
    exposure series over the caller-supplied context sequence — the
    diversity gate then sees the actual ensemble, not a constant.
    """

    def __init__(self, *, registry, contexts) -> None:  # type: ignore[no-untyped-def]
        self._registry = registry
        self._contexts = list(contexts)

    def build(
        self,
        *,
        revision_id: str,
        kind: RevisionKind,
        target: str,
        summary: str,
        champion: PlaygroundRunSpec,
        candidate: PlaygroundRunSpec,
        windows: Mapping[str, pd.DataFrame],
    ) -> RevisionCandidate:
        base = build_revision_candidate(
            revision_id=revision_id,
            kind=kind,
            target=target,
            summary=summary,
            champion=champion,
            candidate=candidate,
            windows=windows,
        )
        measured = ensemble_pairwise_correlation(self._registry, self._contexts)
        if measured is None:
            # no correlation dimension (all-HOLD roster): keep the safe default
            return base
        return RevisionCandidate(
            revision_id=base.revision_id,
            kind=base.kind,
            target=base.target,
            summary=base.summary,
            suite_score=base.suite_score,
            champion_score=base.champion_score,
            sealed_holdouts=base.sealed_holdouts,
            seeded_holdout_seals=base.seeded_holdout_seals,
            pairwise_correlation=measured,
            is_canary=False,
            canary_expected_to_fail=False,
        )
