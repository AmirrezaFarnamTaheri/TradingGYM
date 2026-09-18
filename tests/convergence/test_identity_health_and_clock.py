from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from trading_gym.governance.identity import actor_identity_health
from trading_gym.operations.clock import (
    ClockHealthRequest,
    ClockSample,
    evaluate_clock_health,
)


def test_identity_health_proves_role_coverage_and_distinct_approval_principals(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "alice": {
                    "secret": "alice-health-secret-0000000000000001",
                    "roles": ["proposer", "executor"],
                },
                "bob": {
                    "secret": "bob-health-secret-00000000000000002",
                    "roles": ["approver"],
                },
            }
        ),
    )
    report = actor_identity_health()
    assert report.valid is True
    assert report.actor_count == 2
    assert report.separation_of_duties_possible is True
    assert report.secrets_exposed is False


def test_identity_health_fails_when_one_principal_cannot_satisfy_four_eyes(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "alice": {
                    "secret": "alice-only-secret-00000000000000001",
                    "roles": ["proposer", "approver", "executor"],
                },
            }
        ),
    )
    report = actor_identity_health()
    assert report.valid is False
    assert "separation_of_duties_not_possible" in report.blockers


def sample(
    source: str,
    now: datetime,
    offset_ms: int,
    *,
    age_seconds: int = 0,
    authenticated: bool = True,
):
    captured = now - timedelta(seconds=age_seconds)
    reference = captured
    return ClockSample(
        source_id=source,
        captured_at=captured,
        local_time=reference + timedelta(milliseconds=offset_ms),
        reference_time=reference,
        round_trip_ms=20,
        authenticated_source=authenticated,
    )


def test_clock_health_uses_multiple_sources_and_bounds_offset_jitter_and_age() -> None:
    now = datetime.now(timezone.utc)
    report = evaluate_clock_health(
        ClockHealthRequest(
            evaluated_at=now,
            samples=[
                sample("ntp-a", now, 10),
                sample("ntp-b", now, 20),
                sample("ntp-a", now, 15),
            ],
        )
    )
    assert report.healthy is True
    assert report.source_count == 2
    assert report.execution_authority_granted is False

    degraded = evaluate_clock_health(
        ClockHealthRequest(
            evaluated_at=now,
            samples=[
                sample("ntp-a", now, 900),
                sample("ntp-b", now, -900),
                sample("ntp-a", now, 0, age_seconds=1000),
            ],
        )
    )
    assert degraded.healthy is False
    assert any(item.startswith("clock_offset_exceeded") for item in degraded.blockers)
    assert any(item.startswith("clock_jitter_exceeded") for item in degraded.blockers)
    assert any(
        item.startswith("stale_or_future_clock_samples") for item in degraded.blockers
    )
