"""AXIOM-1 playground-evidence bridge (ADR-031 → ADR-033).

The self-improvement loop's weakest link would be *asserted* evidence:
scores typed by whoever wants a revision applied. This bridge closes it:
revision candidates are built from actual ``run_playground`` results on
sealed windows, so the evidence inside a RevisionCandidate is
replayable-by-construction (every score traces to a manifest-pinned
run).

Contract:
- ``build_revision_candidate`` runs the champion spec and the candidate
  spec over the same sealed holdout windows and computes the suite and
  holdout scores as final-equity ratios (candidate / champion).
- Sealed holdouts are named by sha256 of each window's frame — the
  seeds recorded on the candidate are the holdout ids it was scored on;
  any later mismatch with the surface's seeded seals is the leakage
  detection the gate chain already enforces.
- Both runs must execute and produce finite equity; a crashed candidate
  is evidence of failure, not a skip.
"""

from __future__ import annotations

import pytest

from trading_gym.agent.revision import RevisionCandidate, RevisionKind
from trading_gym.agent.playground.bridge import (
    build_revision_candidate,
    evaluate_sealed_windows,
)
from trading_gym.agent.playground.runner import PlaygroundRunSpec

from test_runner import make_frame, momentum_strategy  # fixture reuse


def spec_for(window, *, fee_bps: float = 10.0) -> PlaygroundRunSpec:

    return PlaygroundRunSpec(
        window=window,
        strategy=momentum_strategy(),
        initial_capital=10_000.0,
        fee_bps=fee_bps,
    )


def window(seed: int) -> object:
    return make_frame(240, seed=seed)


class TestEvaluateSealedWindows:
    def test_returns_per_window_equity_ratios(self) -> None:
        windows = {"hold-a": window(11), "hold-b": window(23)}
        champion = spec_for(window(11))
        candidate = spec_for(window(11), fee_bps=8.0)
        scores = evaluate_sealed_windows(
            champion=champion,
            candidate=candidate,
            windows=windows,
        )
        assert set(scores) == {"hold-a", "hold-b"}
        assert all(score > 0.0 for score in scores.values())

    def test_scores_recompute_identically(self) -> None:
        windows = {"hold-a": window(11)}
        args = dict(
            champion=spec_for(window(11)),
            candidate=spec_for(window(11), fee_bps=5.0),
            windows=windows,
        )
        first = evaluate_sealed_windows(**args)
        second = evaluate_sealed_windows(**args)
        assert first == second


class TestBuildRevisionCandidate:
    def test_candidate_carries_sealed_seals_from_window_hashes(self) -> None:
        windows = {"hold-a": window(11), "hold-b": window(23)}
        revision = build_revision_candidate(
            revision_id="rev-e2e-1",
            kind=RevisionKind.PARAMETRIC,
            target="rules-arm.fee",
            summary="lower fee tier for paper routing",
            champion=spec_for(window(11)),
            candidate=spec_for(window(11), fee_bps=8.0),
            windows=windows,
        )
        assert isinstance(revision, RevisionCandidate)
        assert set(revision.sealed_holdouts) == {"hold-a", "hold-b"}
        # seals are sha256 of each window's content — 64 hex chars
        assert all(len(seal) == 64 for seal in revision.seeded_holdout_seals.values())
        assert revision.suite_score > 0.0

    def test_identical_specs_score_neutral(self) -> None:
        windows = {"hold-a": window(11)}
        revision = build_revision_candidate(
            revision_id="rev-e2e-2",
            kind=RevisionKind.PARAMETRIC,
            target="rules-arm.fee",
            summary="no-op revision",
            champion=spec_for(window(11)),
            candidate=spec_for(window(11)),
            windows=windows,
        )
        assert revision.suite_score == pytest.approx(1.0)
        for score in revision.sealed_holdouts.values():
            assert score == pytest.approx(1.0)

    def test_bridge_feeds_surface_end_to_end(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from trading_gym.agent.revision import RevisionSurface

        windows = {"hold-a": window(11), "hold-b": window(23)}
        revision = build_revision_candidate(
            revision_id="rev-e2e-3",
            kind=RevisionKind.PARAMETRIC,
            target="rules-arm.fee",
            summary="fee reduction",
            champion=spec_for(window(11)),
            candidate=spec_for(window(11), fee_bps=9.0),
            windows=windows,
        )
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        outcome = surface.submit(revision)
        # the gates evaluated a real, sealed, replayable evidence bundle
        assert outcome.gates.passed is True
        assert outcome.applied is True

    def test_replayable_seals_detect_window_tampering(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The seal recorded at build time must match a re-derivation —
        a different window under the same name is a different seal."""

        windows = {"hold-a": window(11)}
        revision = build_revision_candidate(
            revision_id="rev-e2e-4",
            kind=RevisionKind.PARAMETRIC,
            target="t",
            summary="s",
            champion=spec_for(window(11)),
            candidate=spec_for(window(11), fee_bps=9.0),
            windows=windows,
        )
        tampered = {"hold-a": window(31)}
        rebuilt = evaluate_sealed_windows(
            champion=spec_for(window(31)),
            candidate=spec_for(window(31), fee_bps=9.0),
            windows=tampered,
        )
        from trading_gym.agent.playground.bridge import seal_window

        assert (
            seal_window(tampered["hold-a"]) != revision.seeded_holdout_seals["hold-a"]
        )
        assert set(rebuilt) == {"hold-a"}
