"""Automatic mutation retry: replay-safety, classification and journal tests."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from time import sleep

import pytest

from trading_gym.production.emergency import EmergencyController
from trading_gym.production.mutation_retry import (
    MutationOutcomeUnknown,
    MutationOutbox,
    MutationOutboxIntegrityError,
    MutationRetryPolicy,
    delay_for_attempt,
    execute_mutation_with_retry,
    scan_outbox,
)
from trading_gym.production.runtime import LiveExecutionCoordinator
from axiomforge.live_trading import LiveConsent
from trading_gym.production.venues import VenueAdapter, VenueOrderIntent


class TransientNetworkError(RuntimeError):
    pass


class TerminalValidationError(ValueError):
    pass


def _policy(max_attempts: int = 3) -> MutationRetryPolicy:
    return MutationRetryPolicy(
        max_attempts=max_attempts,
        base_delay_s=0.01,
        max_delay_s=0.05,
        jitter_fraction=0.0,
        retryable_exceptions=(
            TransientNetworkError,
            MutationOutcomeUnknown,
            ConnectionError,
            TimeoutError,
        ),
    )


# ---------------------------------------------------------------------------
# Policy validation and backoff
# ---------------------------------------------------------------------------


def test_policy_rejects_out_of_range_attempts():
    with pytest.raises(ValueError):
        MutationRetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        MutationRetryPolicy(max_attempts=6)
    with pytest.raises(ValueError):
        MutationRetryPolicy(max_attempts=True)


def test_backoff_is_exponential_capped_and_deterministic_with_rng():
    policy = _policy()
    delays = [
        delay_for_attempt(policy, attempt, rng=__import__("random").Random(7))
        for attempt in (1, 2, 3, 4)
    ]
    assert delays[1] >= delays[0]
    assert all(delay <= policy.max_delay_s + 1e-9 for delay in delays)


# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------


def test_retry_succeeds_after_transient_failures_and_records_delays():
    attempts = {"n": 0}

    def submit():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientNetworkError("connection reset")
        return "receipt"

    sleeps: list[float] = []
    outcome = execute_mutation_with_retry(submit, policy=_policy(), sleep=sleeps.append)
    assert outcome.receipt == "receipt"
    assert outcome.attempts == 3
    assert outcome.succeeded_on_retry is True
    assert len(outcome.delays) == 2 and len(sleeps) == 2


def test_terminal_error_aborts_without_retry():
    calls = {"n": 0}

    def submit():
        calls["n"] += 1
        raise TerminalValidationError("bad quantity")

    terminal = []
    with pytest.raises(TerminalValidationError):
        execute_mutation_with_retry(
            submit,
            policy=_policy(),
            on_terminal=lambda exc: terminal.append(exc),
            sleep=lambda _: None,
        )
    assert calls == {"n": 1}
    assert len(terminal) == 1


def test_unknown_exception_class_is_not_retried():
    calls = {"n": 0}

    def submit():
        calls["n"] += 1
        raise RuntimeError("unclassified failure")

    with pytest.raises(RuntimeError):
        execute_mutation_with_retry(submit, policy=_policy(), sleep=lambda _: None)
    assert calls == {"n": 1}


def test_budget_exhaustion_marks_outcome_ambiguous():
    def submit():
        raise MutationOutcomeUnknown("lost acknowledgement")

    ambiguous = []
    with pytest.raises(MutationOutcomeUnknown):
        execute_mutation_with_retry(
            submit,
            policy=_policy(max_attempts=2),
            on_ambiguous=ambiguous.append,
            sleep=lambda _: None,
            allow_unknown_replay=True,
        )
    assert len(ambiguous) == 1


def test_unknown_outcome_does_not_retry_or_release_on_default_policy():
    calls = []
    ambiguous = []
    terminal = []

    def submit():
        calls.append(True)
        raise MutationOutcomeUnknown("broker may have received this order")

    with pytest.raises(MutationOutcomeUnknown):
        execute_mutation_with_retry(
            submit,
            policy=_policy(max_attempts=5),
            on_ambiguous=ambiguous.append,
            on_terminal=terminal.append,
            sleep=lambda _: None,
        )
    assert len(calls) == 1
    assert len(ambiguous) == 1
    assert not terminal


def test_presend_exhaustion_releases_rather_than_recording_ambiguous():
    ambiguous, terminal = [], []
    with pytest.raises(TransientNetworkError):
        execute_mutation_with_retry(
            lambda: (_ for _ in ()).throw(TransientNetworkError("pre-send")),
            policy=_policy(max_attempts=2),
            on_ambiguous=ambiguous.append,
            on_terminal=terminal.append,
            sleep=lambda _: None,
        )
    assert len(terminal) == 1
    assert not ambiguous


# ---------------------------------------------------------------------------
# Coordinator integration: replay safety under a stable idempotency key
# ---------------------------------------------------------------------------


class CountingVenue(VenueAdapter):
    """Venue double that records submissions and can simulate failure modes."""

    name = "counting"

    def __init__(self, *, replay_capable: bool = False):
        self.calls: list[VenueOrderIntent] = []
        self.script: list[object] = []
        if replay_capable:
            self.supports_idempotent_replay = True

    def submit(self, intent: VenueOrderIntent):
        self.calls.append(intent)
        action = (
            self.script.pop(0)
            if self.script
            else {"order_id": f"venue-{len(self.calls)}"}
        )
        if isinstance(action, BaseException):
            raise action
        return action


def _consent() -> "LiveConsent":
    return LiveConsent(
        actor="operator",
        scope="live.execute",
        expires_at="2099-01-01T00:00:00+00:00",
        nonce=f"mutation-retry-{next(_nonce_counter)}",
    )


import itertools  # noqa: E402

_nonce_counter = itertools.count(1)


def _coordinator_intent(key: str) -> VenueOrderIntent:
    return VenueOrderIntent(
        venue="counting",
        operation="spot.market",
        base_symbol="BTC",
        quote_symbol="USDT",
        side="buy",
        quantity=Decimal("0.5"),
        idempotency_key=key,
        reference_id="",
    )


def _bound_submit(
    coordinator: LiveExecutionCoordinator, intent: VenueOrderIntent, consent
):
    def submit():
        authorization = coordinator.bind_authorization(
            intent, consent=consent, proposal_id="p-1"
        )
        return coordinator.execute(intent, authorization, consent=consent)

    return submit


def test_presend_failure_is_retried_and_does_not_taint_the_key():
    consent = _consent()
    venue = CountingVenue()
    venue.script = [ConnectionError("connection refused before send")]
    coordinator = LiveExecutionCoordinator({"counting": venue})
    intent = _coordinator_intent("presend-key-1")

    outcome = execute_mutation_with_retry(
        _bound_submit(coordinator, intent, consent),
        policy=_policy(),
        sleep=lambda _: None,
    )

    assert outcome.attempts == 2 and outcome.succeeded_on_retry is True
    assert len(venue.calls) == 2
    assert outcome.receipt["result"]["order_id"] == "venue-2"


def test_unknown_outcome_on_replay_capable_venue_is_auto_retried():
    consent = _consent()
    # Replay-capable venue: the first POST landed but the response was lost;
    # the venue deduplicates the retried key and returns the original order.
    venue = CountingVenue(replay_capable=True)
    venue.script = [
        MutationOutcomeUnknown("read timeout after send"),
        {"order_id": "venue-dedup-1"},
    ]
    coordinator = LiveExecutionCoordinator({"counting": venue})
    intent = _coordinator_intent("replay-key-1")

    outcome = execute_mutation_with_retry(
        _bound_submit(coordinator, intent, consent),
        policy=_policy(),
        sleep=lambda _: None,
        allow_unknown_replay=True,  # only the replay-capable test venue
    )

    assert len(venue.calls) == 2  # two HTTP attempts...
    assert outcome.receipt["result"]["order_id"] == "venue-dedup-1"  # ...one order.


def test_unknown_outcome_without_replay_support_escalates_fail_closed():
    consent = _consent()
    venue = CountingVenue()  # no supports_idempotent_replay
    venue.script = [MutationOutcomeUnknown("read timeout after send")]
    coordinator = LiveExecutionCoordinator({"counting": venue})
    intent = _coordinator_intent("escalate-key-1")

    terminal: list[BaseException] = []
    ambiguous: list[BaseException] = []
    with pytest.raises(MutationOutcomeUnknown):
        execute_mutation_with_retry(
            _bound_submit(coordinator, intent, consent),
            policy=_policy(max_attempts=3),
            on_terminal=terminal.append,
            on_ambiguous=ambiguous.append,
            sleep=lambda _: None,
        )

    assert len(venue.calls) == 1
    assert coordinator.ambiguous_keys() == ("escalate-key-1",)
    assert not terminal
    assert len(ambiguous) == 1


def test_emergency_halt_is_never_retried():
    controller = EmergencyController(path=None)
    coordinator = LiveExecutionCoordinator(
        {"counting": CountingVenue()}, emergency_controller=controller
    )

    class HaltedController(EmergencyController):
        def snapshot(self):  # noqa: D102 - test double
            from trading_gym.production.emergency import EmergencyState

            return EmergencyState(
                halted=True, revision=1, reason="test", actor="a", approver="b"
            )

    coordinator.emergency_controller = HaltedController()

    intent = _coordinator_intent("halted-key-1")
    consent = _consent()

    with pytest.raises(PermissionError):
        execute_mutation_with_retry(
            _bound_submit(coordinator, intent, consent),
            policy=_policy(),
            sleep=lambda _: None,
        )


def test_ambiguous_key_requires_human_reconciliation_before_reuse():
    consent = _consent()
    venue = CountingVenue()
    venue.script = [MutationOutcomeUnknown("read timeout after send")]
    coordinator = LiveExecutionCoordinator({"counting": venue})
    intent = _coordinator_intent("reconcile-key-1")

    submit = _bound_submit(coordinator, intent, consent)
    with pytest.raises(MutationOutcomeUnknown):
        submit()

    assert coordinator.ambiguous_keys() == ("reconcile-key-1",)
    with pytest.raises(PermissionError):
        submit()

    assert coordinator.mark_reconciled("reconcile-key-1") is True
    assert coordinator.mark_reconciled("reconcile-key-1") is False
    assert coordinator.ambiguous_keys() == ()
    # Clearing an unknown outcome does not authorize replaying the old key.
    with pytest.raises(PermissionError, match="reconciled_key_cannot_be_resubmitted"):
        submit()
    assert len(venue.calls) == 1


def test_same_idempotency_key_with_other_payload_is_rejected():
    venue = CountingVenue()
    coordinator = LiveExecutionCoordinator({"counting": venue})
    consent = _consent()
    original = _coordinator_intent("collision-key")
    original_receipt = _bound_submit(coordinator, original, consent)()
    changed = replace(original, quantity=Decimal("1.25"))

    with pytest.raises(PermissionError, match="idempotency_key_reused"):
        _bound_submit(coordinator, changed, consent)()
    assert len(venue.calls) == 1
    assert (
        original_receipt["payload_sha256"]
        != coordinator.bind_authorization(
            changed, consent=consent, proposal_id="p-1"
        ).payload_sha256
    )


def test_simultaneous_same_key_submits_only_once():
    class SlowVenue(CountingVenue):
        def submit(self, intent):
            sleep(0.04)
            return super().submit(intent)

    venue = SlowVenue()
    coordinator = LiveExecutionCoordinator({"counting": venue})
    consent = _consent()
    intent = _coordinator_intent("concurrent-key")
    barrier = Barrier(9)

    def submit():
        barrier.wait(timeout=5)
        return _bound_submit(coordinator, intent, consent)()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(submit) for _ in range(8)]
        barrier.wait(timeout=5)
        receipts = [f.result(timeout=5) for f in futures]
    assert len(venue.calls) == 1
    assert all(receipt == receipts[0] for receipt in receipts)


# ---------------------------------------------------------------------------
# Outbox journal
# ---------------------------------------------------------------------------


def test_outbox_journal_lifecycle(tmp_path):
    outbox = MutationOutbox(path=tmp_path / "outbox.jsonl")
    outbox.record_pending(
        idempotency_key="key-a",
        proposal_id="p-1",
        operation="spot.market",
        payload_sha256="deadbeef",
    )
    outbox.record_pending(
        idempotency_key="key-b",
        proposal_id="p-1",
        operation="short.open",
        payload_sha256="feedface",
    )
    outbox.record_outcome(
        idempotency_key="key-b",
        proposal_id="p-1",
        operation="short.open",
        payload_sha256="feedface",
        outcome="committed",
    )
    unresolved = scan_outbox(outbox)
    keys = {entry["idempotency_key"] for entry in unresolved}
    assert keys == {"key-a"}
    latest = outbox.latest_by_key()
    assert latest["key-b"]["status"] == "committed"


def test_outbox_rejects_invalid_outcome(tmp_path):
    outbox = MutationOutbox(path=tmp_path / "outbox.jsonl")
    with pytest.raises(ValueError):
        outbox.record_outcome(
            idempotency_key="k",
            proposal_id="p",
            operation="spot.market",
            payload_sha256="x",
            outcome="pending",
        )


def test_outbox_scan_rejects_corrupt_line_instead_of_hiding_an_order(tmp_path):
    path = tmp_path / "outbox.jsonl"
    good = json.dumps(
        {
            "schema_version": 1,
            "status": "pending",
            "idempotency_key": "k-ok",
            "proposal_id": "p",
            "operation": "spot.market",
            "payload_sha256": "x",
            "recorded_at": "2026-08-24T00:00:00+00:00",
        }
    )
    path.write_text("{broken json\n" + good + "\n", encoding="utf-8")
    with pytest.raises(MutationOutboxIntegrityError, match="line 1"):
        scan_outbox(MutationOutbox(path=path))


def test_outbox_rejects_colliding_mutation_payloads(tmp_path):
    outbox = MutationOutbox(path=tmp_path / "outbox.jsonl")
    outbox.record_pending(
        idempotency_key="duplicate",
        proposal_id="p1",
        operation="spot.market",
        payload_sha256="hash-a",
    )
    outbox.record_outcome(
        idempotency_key="duplicate",
        proposal_id="p2",
        operation="spot.market",
        payload_sha256="hash-b",
        outcome="committed",
    )
    with pytest.raises(MutationOutboxIntegrityError, match="collision"):
        outbox.latest_by_key()


def test_independent_outbox_writers_share_process_lock(tmp_path):
    path = tmp_path / "outbox.jsonl"

    def record(index):
        MutationOutbox(path=path).record_pending(
            idempotency_key=f"key-{index}",
            proposal_id="p",
            operation="spot.market",
            payload_sha256=f"hash-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(40)))
    assert len(MutationOutbox(path=path).latest_by_key()) == 40


def test_outbox_fsyncs_intent_before_return(tmp_path, monkeypatch):
    import os
    from trading_gym.common import persistence

    real_fsync = os.fsync
    fsynced = []

    def observed_fsync(fd):
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(persistence.os, "fsync", observed_fsync)
    MutationOutbox(path=tmp_path / "outbox.jsonl").record_pending(
        idempotency_key="durable",
        proposal_id="p",
        operation="spot.market",
        payload_sha256="hash",
    )
    assert fsynced, "pending mutation must reach an fsync before venue contact"


def test_committed_venue_operation_with_failed_local_commit_is_ambiguous():
    """A broker submission can succeed while local durability fails."""
    attempts, ambiguous, terminal = [], [], []

    def submit():
        attempts.append("sent")
        return {"order": "at-venue"}

    def cannot_commit(_receipt):
        raise OSError("storage failed after broker accepted")

    with pytest.raises(
        MutationOutcomeUnknown, match="local_post_submission_commit_unverified"
    ):
        execute_mutation_with_retry(
            submit,
            policy=_policy(),
            on_committed=cannot_commit,
            on_ambiguous=ambiguous.append,
            on_terminal=terminal.append,
            sleep=lambda _: None,
        )
    assert attempts == ["sent"]
    assert len(ambiguous) == 1 and not terminal


def test_outbox_rejects_terminal_record_without_durable_intent(tmp_path):
    outbox = MutationOutbox(path=tmp_path / "outbox.jsonl")
    outbox.record_outcome(
        idempotency_key="missing-intent",
        proposal_id="p1",
        operation="spot.market",
        payload_sha256="hash",
        outcome="committed",
    )
    with pytest.raises(MutationOutboxIntegrityError, match="preceding pending"):
        scan_outbox(outbox)


def test_outbox_rejects_resurrection_of_resolved_intent(tmp_path):
    outbox = MutationOutbox(path=tmp_path / "outbox.jsonl")
    kwargs = dict(
        idempotency_key="same-key",
        proposal_id="p1",
        operation="spot.market",
        payload_sha256="hash",
    )
    outbox.record_pending(**kwargs)
    outbox.record_outcome(**kwargs, outcome="terminal")
    outbox.record_pending(**kwargs)
    with pytest.raises(MutationOutboxIntegrityError, match="invalid transition"):
        scan_outbox(outbox)


def test_outbox_rejects_unterminated_or_oversize_journal_lines(tmp_path):
    path = tmp_path / "outbox.jsonl"
    entry = dict(
        schema_version=1,
        status="pending",
        idempotency_key="test",
        proposal_id="p1",
        operation="spot.market",
        payload_sha256="hash",
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    path.write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(MutationOutboxIntegrityError, match="unterminated"):
        scan_outbox(MutationOutbox(path=path))

    from trading_gym.common.persistence import MAX_JSONL_RECORD_BYTES

    path.write_bytes(b"x" * (MAX_JSONL_RECORD_BYTES + 10) + b"\n")
    with pytest.raises(MutationOutboxIntegrityError, match="exceeds"):
        scan_outbox(MutationOutbox(path=path))



def test_atomic_claim_prevents_concurrent_live_intents(tmp_path):
    """Independent workers cannot both clear the same outbox preflight."""
    path = tmp_path / "shared_outbox.jsonl"

    def claim(index):
        try:
            MutationOutbox(path).claim_pending(
                idempotency_key=f"live-{index}",
                proposal_id="proposal-1",
                operation="spot.market",
                payload_sha256=f"hash-{index}",
            )
        except PermissionError as exc:
            return f"denied:{exc}"
        return f"accepted:{index}"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(claim, range(2)))
    assert sum(value.startswith("accepted:") for value in results) == 1
    assert sum(
        value == "denied:unresolved_mutations_require_reconciliation"
        for value in results
    ) == 1

    # A proven terminal failure frees the slot, without resurrecting the
    # original idempotency key or allowing an unverified pending request.
    latest = MutationOutbox(path).latest_by_key()
    assert len(latest) == 1
    key, entry = next(iter(latest.items()))
    with pytest.raises(MutationOutboxIntegrityError, match="already_recorded"):
        MutationOutbox(path).claim_pending(
            idempotency_key=key,
            proposal_id=entry["proposal_id"],
            operation=entry["operation"],
            payload_sha256=entry["payload_sha256"],
        )
    MutationOutbox(path).record_outcome(
        idempotency_key=key,
        proposal_id=entry["proposal_id"],
        operation=entry["operation"],
        payload_sha256=entry["payload_sha256"],
        outcome="terminal",
    )
    MutationOutbox(path).claim_pending(
        idempotency_key="next-logical-operation",
        proposal_id="proposal-1",
        operation="spot.market",
        payload_sha256="new-hash",
    )
    assert MutationOutbox(path).latest_by_key()["next-logical-operation"][
        "status"
    ] == "pending"


def test_atomic_claim_rejects_corrupt_prior_outbox(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"truncated":true}', encoding="utf-8")
    with pytest.raises(MutationOutboxIntegrityError, match="unterminated"):
        MutationOutbox(path).claim_pending(
            idempotency_key="fresh",
            proposal_id="proposal",
            operation="spot.market",
            payload_sha256="sha",
        )
    assert path.read_text(encoding="utf-8") == '{"truncated":true}'


def test_atomic_claim_keeps_cancellation_possible_during_unknown_outcome(tmp_path):
    outbox = MutationOutbox(tmp_path / "outbox.jsonl")
    outbox.claim_pending(
        idempotency_key="unknown-buy",
        proposal_id="proposal",
        operation="spot.market",
        payload_sha256="hash-buy",
    )
    outbox.claim_pending(
        idempotency_key="cancel-pending-order",
        proposal_id="proposal",
        operation="order.cancel",
        payload_sha256="hash-cancel",
    )
    assert len(scan_outbox(outbox)) == 2


@pytest.mark.parametrize("invalid", [3.0, True, 0, -1, 6, float("nan")])
def test_mutation_attempt_budget_must_be_bounded_integer(invalid):
    with pytest.raises((ValueError, TypeError)):
        MutationRetryPolicy(max_attempts=invalid)


@pytest.mark.parametrize("invalid", [float("inf"), float("nan"), -1.0, 0.0])
def test_mutation_retry_max_delay_must_be_finite_and_positive(invalid):
    with pytest.raises(ValueError):
        MutationRetryPolicy(max_delay_s=invalid)

class _MaximumRandom:
    def random(self):
        return 1.0


def test_retry_delay_never_exceeds_absolute_maximum():
    policy = MutationRetryPolicy(
        base_delay_s=8, max_delay_s=8, jitter_fraction=0.25
    )
    assert delay_for_attempt(policy, 1, rng=_MaximumRandom()) <= 8


@pytest.mark.parametrize("invalid", [True, 1.5, 0, -1])
def test_retry_attempt_number_must_be_positive_integer(invalid):
    policy = MutationRetryPolicy()
    with pytest.raises(ValueError):
        delay_for_attempt(policy, invalid)
