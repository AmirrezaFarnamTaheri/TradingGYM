"""Regression tests for the final low-severity hardening sweep.

Each test pins one specific defect fixed in this pass:
  - a torn final write in the grant/autonomy/revision journals must surface as
    the module's own integrity error, not a bare JSONDecodeError;
  - a future timestamp must not pass the data-freshness gate;
  - a NaN previously_locked must not silently disable the stop ratchet;
  - a suppressed CRITICAL alert must leave a log trace, not just a counter;
  - submit_deposit must apply the same input discipline as submit_withdrawal.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_gym.agent.autonomy_ladder import (
    AutonomyGovernor,
    AutonomyLedgerIntegrityError,
    PromotionEvidence,
)
from trading_gym.agent.revision import (
    RevisionCandidate,
    RevisionKind,
    RevisionLedgerIntegrityError,
    RevisionSurface,
)
from trading_gym.authz.grants import (
    GrantLedger,
    GrantLedgerIntegrityError,
    GrantRequest,
)
from trading_gym.monitoring.alerting import (
    AlertLevel,
    AlertManager,
    SendOutcome,
)
from trading_gym.monitoring.health import check_data_freshness
from trading_gym.risk.dynamic_stops import ratchet_take_profit


def _tear_final_line(path: Path) -> None:
    """Truncate the last record, exactly as a crash mid-append would leave it."""
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1][: max(1, len(lines[-1]) // 2)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _grant_writer(path: Path) -> None:
    ledger = GrantLedger(path)
    ledger.issue(
        GrantRequest(
            principal="alpha",
            scopes=("trade",),
            ttl_hours=24,
            issued_at="2026-09-01T00:00:00Z",
        )
    )


def _autonomy_writer(path: Path) -> None:
    AutonomyGovernor.open(path).promote(
        PromotionEvidence(
            days_in_current_level=30,
            decisions_at_level=500,
            constraint_breaches=0,
            journal_valid=True,
            slippage_within_tolerance=True,
        )
    )


def _revision_writer(path: Path) -> None:
    RevisionSurface.open(path).submit(
        RevisionCandidate(
            revision_id="rev-001",
            kind=RevisionKind.PARAMETRIC,
            target="rules-arm.thresholds",
            summary="raise defence threshold",
            suite_score=1.02,
            champion_score=1.0,
            sealed_holdouts={"holdout-a": 1.03, "holdout-b": 1.03},
            seeded_holdout_seals={"holdout-a": "a" * 64, "holdout-b": "b" * 64},
            pairwise_correlation=0.2,
            is_canary=False,
            canary_expected_to_fail=False,
        )
    )


@pytest.mark.parametrize(
    ("writer", "opener", "integrity_error"),
    [
        (
            _grant_writer,
            lambda path: GrantLedger(path),
            GrantLedgerIntegrityError,
        ),
        (
            _autonomy_writer,
            lambda path: AutonomyGovernor.open(path, verify=False),
            AutonomyLedgerIntegrityError,
        ),
        (
            _revision_writer,
            lambda path: RevisionSurface.open(path, verify=False),
            RevisionLedgerIntegrityError,
        ),
    ],
)
def test_torn_journal_line_raises_the_module_integrity_error(
    tmp_path: Path, writer, opener, integrity_error
) -> None:
    path = tmp_path / "journal.jsonl"
    writer(path)
    assert path.exists(), "the writer must leave at least one record"

    _tear_final_line(path)

    # Before the fix this raised json.decoder.JSONDecodeError — detected
    # corruption presenting as a code bug instead of the integrity error that
    # callers catch to trigger repair.
    with pytest.raises(integrity_error, match="invalid JSON"):
        opener(path)


# ---------------------------------------------------------------------------
# Freshness gate rejects the future
# ---------------------------------------------------------------------------


def test_future_timestamp_is_not_fresh() -> None:
    import time

    now = time.time()
    assert check_data_freshness(now - 5) is True
    assert check_data_freshness(now - 120) is False
    # A feed clock running ahead, or a replay with future timestamps, must
    # not pass the gate.
    assert check_data_freshness(now + 60) is False
    assert check_data_freshness(now + 3600) is False


# ---------------------------------------------------------------------------
# NaN previously_locked cannot silently disable the ratchet
# ---------------------------------------------------------------------------


def test_nan_previously_locked_is_rejected() -> None:
    with pytest.raises(ValueError, match="previously_locked"):
        ratchet_take_profit(
            side="long",
            entry_price=100.0,
            current_price=200.0,
            tiers=[110.0, 120.0, 130.0],
            previously_locked=float("nan"),
        )


def test_ratchet_still_advances_on_finite_input() -> None:
    locked, take_profit = ratchet_take_profit(
        side="long",
        entry_price=100.0,
        current_price=125.0,
        tiers=[110.0, 120.0, 130.0],
        previously_locked=110.0,
    )
    assert (locked, take_profit) == (120.0, 120.0)


# ---------------------------------------------------------------------------
# A suppressed CRITICAL leaves a log trace
# ---------------------------------------------------------------------------


class _SkippingChannel:
    def __init__(self) -> None:
        self.name = "SkippingChannel"

    def send_with_outcome(self, alert) -> SendOutcome:
        return SendOutcome.SKIPPED


def test_suppressed_critical_alert_is_logged(caplog) -> None:
    channel = _SkippingChannel()
    manager = AlertManager(channels=[channel])
    with caplog.at_level(logging.WARNING):
        delivered = manager.send_alert(
            title="drawdown breach",
            message="equity fell past the daily floor",
            level=AlertLevel.CRITICAL,
        )

    assert delivered == 0
    # The stat key is the channel class name, per the manager's convention.
    assert manager.get_stats()[f"{type(channel).__name__}_skipped"] == 1
    assert any(
        "Critical alert suppressed by channel" in record.message
        and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_suppressed_non_critical_alert_stays_silent(caplog) -> None:
    channel = _SkippingChannel()
    manager = AlertManager(channels=[channel])
    with caplog.at_level(logging.WARNING):
        manager.send_alert(
            title="routine",
            message="informational only",
            level=AlertLevel.INFO,
        )

    assert not any(
        "Critical alert suppressed" in record.message for record in caplog.records
    )


# ---------------------------------------------------------------------------
# submit_deposit applies the same input discipline as submit_withdrawal
# ---------------------------------------------------------------------------


def test_submit_deposit_rejects_unsafe_coin_and_hash() -> None:
    # The venue SDK is not installed here; the validation we pin runs before
    # any SDK call, so patching the client class is enough to construct it.
    with patch("trading_gym.adapters.enhanced_aban_client.AbantetherClient"):
        from trading_gym.adapters.enhanced_aban_client import EnhancedAbanClient

        client = EnhancedAbanClient(api_key="k" * 40)
        with pytest.raises(ValueError, match="coin"):
            client.submit_deposit(coin="A" * 100, tx_hash="abc123")
        with pytest.raises(ValueError, match="tx_hash"):
            client.submit_deposit(coin="BTC", tx_hash="\x00\n\r drop tables")
