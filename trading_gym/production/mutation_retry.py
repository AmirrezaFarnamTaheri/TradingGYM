"""Automatic, replay-safe retry for governed live mutations.

The Aban client deliberately never retries mutating calls: a naive retry can
double an order after an ambiguous network failure.  This module adds the
missing piece — *safe* automatic mutation retry:

1. Every logical mutation keeps ONE stable idempotency key for all attempts.
   ``LiveExecutionCoordinator`` returns the cached receipt when a key is
   re-submitted, so if the first attempt actually reached the venue and only
   the response was lost, the retry observes the original outcome instead of
   duplicating the order.
2. Only explicitly transient failures are retried (bounded attempts,
   exponential backoff with jitter).  Validation, authorization, consent and
   emergency-halt errors abort immediately.
3. A durable outbox journal records every attempt before submission and its
   outcome afterwards; intents whose outcome stayed ambiguous are surfaced by
   :func:`scan_outbox` so operators can reconcile them.  The journal is
   evidence, not authority: nothing is ever re-submitted from disk.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from filelock import FileLock

from trading_gym.common.persistence import append_jsonl, resolve_data_home

MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY_S = 0.5
DEFAULT_MAX_DELAY_S = 8.0
DEFAULT_JITTER_FRACTION = 0.25


class MutationOutcomeUnknown(RuntimeError):
    """A mutation whose delivery outcome cannot be proven locally.

    Raised by venue transports when a request may have reached the venue but
    the result is unobservable (e.g. read timeout after send, malformed
    response).  Automatic retry of such a mutation is only safe when the
    venue adapter declares ``supports_idempotent_replay``; otherwise it must
    escalate to reconciliation.
    """


@dataclass(frozen=True)
class MutationRetryPolicy:
    """Bounded retry budget for one class of governed venue mutations."""

    max_attempts: int = 3
    base_delay_s: float = DEFAULT_BASE_DELAY_S
    max_delay_s: float = DEFAULT_MAX_DELAY_S
    jitter_fraction: float = DEFAULT_JITTER_FRACTION
    # Exceptions that are provably safe to replay under a stable idempotency
    # key.  Anything unknown is treated as terminal.
    retryable_exceptions: tuple[type[BaseException], ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not 1 <= self.max_attempts <= MAX_ATTEMPTS
        ):
            raise ValueError("max_attempts must be between 1 and 5")
        if not 0 < self.base_delay_s <= 60:
            raise ValueError("base_delay_s must be in (0, 60]")
        if not self.max_delay_s >= self.base_delay_s:
            raise ValueError("max_delay_s must be >= base_delay_s")
        if not 0 <= self.jitter_fraction <= 1:
            raise ValueError("jitter_fraction must be within [0, 1]")

    def is_retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, self.retryable_exceptions)


def delay_for_attempt(
    policy: MutationRetryPolicy, attempt: int, *, rng: random.Random | None = None
) -> float:
    """Exponential backoff capped at ``max_delay_s`` with multiplicative jitter."""
    if attempt < 1:
        raise ValueError("attempt numbering starts at 1")
    rng = rng or random.Random()
    raw = min(policy.max_delay_s, policy.base_delay_s * (2 ** (attempt - 1)))
    spread = raw * policy.jitter_fraction
    return float(max(0.0, raw - spread) + (2 * spread * rng.random()))


OUTBOX_SCHEMA_VERSION = 1
OUTBOX_STATUSES = frozenset({"pending", "committed", "ambiguous", "terminal"})


class MutationOutboxIntegrityError(RuntimeError):
    """The journal cannot safely establish which venue mutations are unresolved."""


class MutationOutbox:
    """Append-only JSONL journal of governed mutation attempts.

    One append per state transition: ``pending`` is fsynced before sending an
    order, followed by a distinct durable outcome record. Reading the journal
    verifies each record instead of silently hiding potentially live orders.
    """

    def __init__(self, path: Path | None = None):
        self._explicit_path = path

    @property
    def path(self) -> Path:
        return self._explicit_path or (
            resolve_data_home() / "production" / "mutation_outbox.jsonl"
        )

    def record_pending(
        self,
        *,
        idempotency_key: str,
        proposal_id: str,
        operation: str,
        payload_sha256: str,
    ) -> None:
        entry: dict[str, str | int] = {
            "schema_version": OUTBOX_SCHEMA_VERSION,
            "status": "pending",
            "idempotency_key": idempotency_key,
            "proposal_id": proposal_id,
            "operation": operation,
            "payload_sha256": payload_sha256,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._append(entry)

    def record_outcome(
        self,
        *,
        idempotency_key: str,
        proposal_id: str,
        operation: str,
        payload_sha256: str,
        outcome: str,
        detail: str = "",
    ) -> None:
        if outcome not in OUTBOX_STATUSES or outcome == "pending":
            raise ValueError("outcome must be committed, ambiguous or terminal")
        entry: dict[str, str | int] = {
            "schema_version": OUTBOX_SCHEMA_VERSION,
            "status": outcome,
            "idempotency_key": idempotency_key,
            "proposal_id": proposal_id,
            "operation": operation,
            "payload_sha256": payload_sha256,
            "detail": detail[:200],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._append(entry)

    def _append(self, entry: dict[str, str | int]) -> None:
        # This helper holds an inter-process lock, flushes and fsyncs the line.
        # An unflushed "pending" record must never precede venue contact.
        append_jsonl(self.path, entry)

    def latest_by_key(self) -> dict[str, dict[str, str]]:
        """Verify journal records and return the newest state for each key.

        A malformed line may represent a partially written intent for a real
        order. Skipping it would silently declare that intent nonexistent.
        Raise instead so the operator must reconcile the venue state.
        """
        latest: dict[str, dict[str, str]] = {}
        path = self.path
        with FileLock(str(path) + ".lock"):
            if path.is_symlink():
                raise MutationOutboxIntegrityError("mutation outbox is a symlink")
            if not path.exists():
                return latest
            with path.open("r", encoding="utf-8") as handle:
                for number, line in enumerate(handle, start=1):
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise MutationOutboxIntegrityError(
                            f"malformed mutation outbox record at line {number}"
                        ) from exc
                    if not isinstance(entry, dict):
                        raise MutationOutboxIntegrityError(
                            f"non-object mutation outbox record at line {number}"
                        )
                    required = (
                        "idempotency_key", "proposal_id", "operation",
                        "payload_sha256", "recorded_at",
                    )
                    if (
                        entry.get("schema_version") != OUTBOX_SCHEMA_VERSION
                        or entry.get("status") not in OUTBOX_STATUSES
                        or any(
                            not isinstance(entry.get(field), str)
                            or not entry[field]
                            for field in required
                        )
                    ):
                        raise MutationOutboxIntegrityError(
                            f"invalid mutation outbox record at line {number}"
                        )
                    key = entry["idempotency_key"]
                    previous = latest.get(key)
                    if previous is not None and any(
                        previous[field] != entry[field]
                        for field in ("proposal_id", "operation", "payload_sha256")
                    ):
                        raise MutationOutboxIntegrityError(
                            f"idempotency-key collision at line {number}"
                        )
                    latest[key] = entry
        return latest


def scan_outbox(outbox: MutationOutbox) -> list[dict[str, str]]:
    """Return intents whose last known state is still unresolved."""
    unresolved = []
    for entry in sorted(
        outbox.latest_by_key().values(), key=lambda item: item.get("recorded_at", "")
    ):
        if entry.get("status") in {"pending", "ambiguous"}:
            unresolved.append(entry)
    return unresolved


@dataclass(frozen=True)
class RetryOutcome:
    receipt: object
    attempts: int
    succeeded_on_retry: bool = False
    delays: tuple[float, ...] = field(default_factory=tuple)


def execute_mutation_with_retry(
    submit: Callable[[], object],
    *,
    policy: MutationRetryPolicy,
    on_pending: Callable[[], None] | None = None,
    on_committed: Callable[[object], None] | None = None,
    on_ambiguous: Callable[[BaseException], None] | None = None,
    on_terminal: Callable[[BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    allow_unknown_replay: bool = False,
) -> RetryOutcome:
    """Run ``submit`` with bounded, replay-safe retries.

    ``submit`` must be bound to one fixed idempotency key. Unknown outcomes
    are replayed only when the *upstream venue* demonstrably deduplicates the
    same operation and key; process-local coordinator caching is insufficient.
    The callbacks map onto the outbox journal lifecycle.
    """
    delays: list[float] = []
    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        if attempt == 1 and on_pending is not None:
            on_pending()
        try:
            receipt = submit()
        except Exception as exc:
            # An ambiguous venue outcome is not a failed order. Never convert
            # it into a terminal rejection and release reserved capital.
            unknown = isinstance(exc, MutationOutcomeUnknown)
            may_retry = policy.is_retryable(exc) and (
                not unknown or allow_unknown_replay
            )
            if may_retry and attempt < policy.max_attempts:
                delay = delay_for_attempt(policy, attempt, rng=rng)
                delays.append(delay)
                sleep(delay)
                last_exc = exc
                continue
            if unknown:
                if on_ambiguous is not None:
                    on_ambiguous(exc)
            elif on_terminal is not None:
                # Exhausted *pre-send* retries do not create an ambiguous
                # broker order. A terminal operation is safe to release.
                on_terminal(exc)
            raise
        else:
            # The venue has accepted the mutation, but the local commit path
            # can still fail (durability, accounting, storage exhaustion).
            # Never convert that failure into a terminal order rejection or
            # free the reservation: subsequent mutations must reconcile.
            if on_committed is not None:
                try:
                    on_committed(receipt)
                except Exception as exc:
                    local_unknown = MutationOutcomeUnknown(
                        "local_post_submission_commit_unverified"
                    )
                    if on_ambiguous is not None:
                        try:
                            on_ambiguous(local_unknown)
                        except Exception:
                            # Keep the original local failure observable;
                            # the durable pre-submission pending entry remains
                            # authoritative when outcome journaling also fails.
                            pass
                    raise local_unknown from exc
            return RetryOutcome(
                receipt=receipt,
                attempts=attempt,
                succeeded_on_retry=attempt > 1,
                delays=tuple(delays),
            )
    raise RuntimeError("retry loop exited unexpectedly") from last_exc


def new_mutation_idempotency_key() -> str:
    """One key per logical mutation, stable across all of its retries."""
    return uuid.uuid4().hex


class StabilityAwareBackoff:
    """Escalating delays that de-escalate only after proven stability.

    Absorbed refinement: reconnect/backoff policies that never reset keep
    punishing a healthy stream after one bad episode (kraken `backoff.rs:14-44`
    session-stability reset).  After ``stability_threshold`` consecutive
    successes the attempt counter returns to 1; any failure resets successes
    and escalates from there.
    """

    def __init__(
        self,
        policy: MutationRetryPolicy,
        *,
        stability_threshold: int = 5,
        rng: random.Random | None = None,
    ) -> None:
        if stability_threshold < 1:
            raise ValueError("stability_threshold must be >= 1")
        self._policy = policy
        self._threshold = stability_threshold
        self._rng = rng or random.Random()
        self._attempt = 1
        self._success_streak = 0

    def next_delay(self) -> float:
        delay = delay_for_attempt(self._policy, self._attempt, rng=self._rng)
        self._attempt += 1
        return delay

    def record_success(self) -> None:
        self._success_streak += 1
        if self._success_streak >= self._threshold:
            self._attempt = 1

    def record_failure(self) -> None:
        self._success_streak = 0
