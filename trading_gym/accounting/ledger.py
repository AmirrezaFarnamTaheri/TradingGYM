"""Crash-safe, hash-chained portfolio accounting and reconciliation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from filelock import FileLock

from trading_gym.accounting.models import (
    AccountSnapshot,
    AccountingEvent,
    AccountingEventType,
    FillSide,
    ObservedAccountState,
    PositionState,
    ReconciliationDifference,
    ReconciliationReport,
    ReconciliationTolerance,
)
from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
    verify_outbox_digest,
)
from trading_gym.common.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.common.security import (
    bounded_json_dumps,
    confined_child,
    redact_secrets,
)
from trading_gym.governance.ledger import AuditLedger

MAX_EVENTS = 100_000
MAX_POSITIONS = 10_000
MAX_CURRENCIES = 200


class AccountingConflict(RuntimeError):
    """The requested mutation conflicts with durable accounting state."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _decimal(value: Any) -> Decimal:
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite() or abs(result) > Decimal("1e30"):
        raise ValueError("accounting value must be finite and bounded")
    return result


def _sign(value: Decimal) -> Decimal:
    return Decimal("1") if value > 0 else Decimal("-1") if value < 0 else Decimal("0")


def _currency(value: Any) -> str:
    currency = str(value).upper()
    if not re.fullmatch(r"[A-Z0-9]{2,20}", currency):
        raise ValueError("currency must contain 2-20 uppercase alphanumeric characters")
    return currency


class PortfolioLedger:
    def __init__(
        self,
        account_id: str,
        *,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        if not account_id or len(account_id) > 120:
            raise ValueError("account_id must be 1-120 characters")
        self.account_id = account_id
        if path is None:
            root = resolve_data_home() / "accounting" / "accounts"
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = confined_child(root, f"{account_id}.json", allow_symlinks=False)
        self.path = path
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".accounting.lock")
        # Chain-verification cache. _load() replays and re-validates every
        # event on each call, which is O(N) per mutation and O(N^2) across N
        # mutations. A document we verified and then wrote is still valid, so
        # cache the verdict keyed by file identity and skip the replay while
        # the file has not changed since.
        self._verified_signature: tuple[tuple[int, int], Any | None] | None = None

    def _genesis_hash(self, opening_cash: dict[str, str]) -> str:
        return hashlib.sha256(
            _canonical(
                {
                    "schema_version": 1,
                    "account_id": self.account_id,
                    "opening_cash": opening_cash,
                }
            )
        ).hexdigest()

    def _empty(self) -> dict[str, Any]:
        opening_cash: dict[str, str] = {}
        return {
            "schema_version": 1,
            "account_id": self.account_id,
            "revision": 0,
            "sequence": 0,
            "opening_cash": opening_cash,
            "cash": {},
            "fees": {},
            "positions": {},
            "events": [],
            "event_index": {},
            "chain_head": self._genesis_hash(opening_cash),
            "last_event_at": None,
            "audit_outbox": {},
        }

    def _load(self) -> dict[str, Any]:
        document = read_json(self.path, self._empty())
        if document.get("schema_version") != 1:
            raise ValueError("unsupported accounting schema")
        if document.get("account_id") != self.account_id:
            raise ValueError("accounting document belongs to another account")
        for field in ("opening_cash", "cash", "fees", "positions", "event_index"):
            if not isinstance(document.get(field), dict):
                raise ValueError(f"accounting {field} must be an object")
        if not isinstance(document.get("events"), list):
            raise ValueError("accounting events must be an array")
        if len(document["events"]) > MAX_EVENTS:
            raise ValueError("accounting event limit exceeded")
        if len(document["positions"]) > MAX_POSITIONS:
            raise ValueError("accounting position limit exceeded")
        if (
            len(document["cash"]) > MAX_CURRENCIES
            or len(document["fees"]) > MAX_CURRENCIES
        ):
            raise ValueError("accounting currency limit exceeded")
        ensure_audit_outbox(document)
        verify_outbox_digest(document)
        # Skip the O(N) chain replay when the document on disk is unchanged
        # since this instance last verified and wrote it: an append-only chain
        # cannot have become invalid in between. A signature miss (another
        # writer, or the first load) falls through to the full replay.
        if self._matches_verified(document):
            return document
        self._verify_chain_document(document)
        self._record_verified(document)
        return document

    def _matches_verified(self, document: dict[str, Any]) -> bool:
        """True when the on-disk file is unchanged since the last verified write.

        Pairs with :meth:`_record_verified`: the signature identifies the file
        bytes and the revision identifies the content we verified, so a
        concurrent writer cannot make a stale signature point at new content.
        """
        if self._verified_signature is None:
            return False
        try:
            stat = self.path.stat()
        except OSError:
            return False
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._verified_signature[0] != signature:
            return False
        return document.get("revision") == self._verified_signature[1]

    def _record_verified(self, document: dict[str, Any]) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            return
        self._verified_signature = (
            (stat.st_mtime_ns, stat.st_size),
            document.get("revision"),
        )

    @staticmethod
    def _event_fingerprint(event: AccountingEvent) -> str:
        payload = redact_secrets(event.model_dump(mode="json"))
        return hashlib.sha256(_canonical(payload)).hexdigest()

    @staticmethod
    def _record_hash(record: dict[str, Any]) -> str:
        unsigned = {key: value for key, value in record.items() if key != "record_hash"}
        return hashlib.sha256(_canonical(unsigned)).hexdigest()

    def _verify_chain_document(self, document: dict[str, Any]) -> None:
        opening_cash = {
            _currency(key): str(_decimal(value))
            for key, value in document["opening_cash"].items()
        }
        if len(opening_cash) != len(document["opening_cash"]):
            raise ValueError("opening cash contains duplicate normalised currencies")
        previous = self._genesis_hash(opening_cash)
        seen: set[str] = set()
        replay = self._empty()
        replay["opening_cash"] = opening_cash
        replay["cash"] = dict(opening_cash)
        replay["chain_head"] = previous
        for expected_sequence, record in enumerate(document["events"], start=1):
            if not isinstance(record, dict):
                raise ValueError("accounting event record must be an object")
            if record.get("sequence") != expected_sequence:
                raise ValueError("accounting event sequence is inconsistent")
            if record.get("previous_hash") != previous:
                raise ValueError("accounting event hash chain is broken")
            event_id = str(record.get("event_id", ""))
            if not event_id or event_id in seen:
                raise ValueError("accounting event identifiers must be unique")
            if self._record_hash(record) != record.get("record_hash"):
                raise ValueError("accounting event record hash mismatch")
            if document["event_index"].get(event_id) != record.get("payload_sha256"):
                raise ValueError("accounting event index is inconsistent")
            try:
                event = AccountingEvent.model_validate(record.get("payload"))
            except Exception as exc:
                raise ValueError("accounting event payload is invalid") from exc
            if event.event_id != event_id or event.event_type.value != record.get(
                "event_type"
            ):
                raise ValueError("accounting event payload does not match its record")
            if self._event_fingerprint(event) != record.get("payload_sha256"):
                raise ValueError("accounting event payload hash mismatch")
            self._apply_payload(replay, event)
            seen.add(event_id)
            previous = record["record_hash"]
        if document.get("sequence") != len(document["events"]):
            raise ValueError("accounting sequence does not match event count")
        if document.get("chain_head") != previous:
            raise ValueError("accounting chain head does not match events")
        for field in ("cash", "fees", "positions"):
            if replay[field] != document[field]:
                raise ValueError(
                    f"accounting materialized {field} does not match event replay"
                )

    def _append_event(
        self, document: dict[str, Any], event: AccountingEvent, *, actor: str
    ) -> None:
        payload = redact_secrets(event.model_dump(mode="json"))
        bounded_json_dumps(payload, max_bytes=256 * 1024)
        fingerprint = self._event_fingerprint(event)
        existing = document["event_index"].get(event.event_id)
        if existing is not None:
            if existing != fingerprint:
                raise AccountingConflict(
                    "event_id was reused for a different accounting event"
                )
            return
        if len(document["events"]) >= MAX_EVENTS:
            raise AccountingConflict("accounting event limit reached")

        self._apply_payload(document, event)
        sequence = int(document["sequence"]) + 1
        record = {
            "sequence": sequence,
            "event_id": event.event_id,
            "payload_sha256": fingerprint,
            "previous_hash": document["chain_head"],
            "occurred_at": event.occurred_at.astimezone(timezone.utc).isoformat(),
            "event_type": event.event_type.value,
            "payload": payload,
        }
        record["record_hash"] = self._record_hash(record)
        document["events"].append(record)
        document["event_index"][event.event_id] = fingerprint
        document["sequence"] = sequence
        document["revision"] = int(document["revision"]) + 1
        document["chain_head"] = record["record_hash"]
        document["last_event_at"] = record["occurred_at"]
        queue_audit_event(
            document,
            event_id=f"account:{self.account_id}:sequence:{sequence}:{event.event_id}",
            event=f"accounting.{event.event_type.value}",
            actor=actor,
            subject=self.account_id,
            details={
                "account_revision": document["revision"],
                "sequence": sequence,
                "event_id": event.event_id,
                "event_sha256": fingerprint,
                "chain_head": document["chain_head"],
            },
        )

    def create(
        self,
        initial_cash: dict[str, Decimal] | None = None,
        *,
        actor: str = "api-operator",
    ) -> AccountSnapshot:
        initial_cash = initial_cash or {}
        if len(initial_cash) > MAX_CURRENCIES:
            raise ValueError("too many initial cash currencies")
        normalised: dict[str, Decimal] = {}
        for key, value in initial_cash.items():
            currency = _currency(key)
            amount = _decimal(value)
            if currency in normalised:
                raise ValueError(
                    "initial cash contains a duplicate normalised currency"
                )
            if amount != 0:
                normalised[currency] = amount
        if any(value < 0 for value in normalised.values()):
            raise ValueError("initial cash must be non-negative")
        with self.lock:
            if self.path.exists():
                existing = self._load()
                if {
                    key: _decimal(value)
                    for key, value in existing["opening_cash"].items()
                } != normalised:
                    raise AccountingConflict(
                        "account already exists with different state"
                    )
                document = existing
            else:
                document = self._empty()
                document["opening_cash"] = {
                    key: str(value) for key, value in sorted(normalised.items())
                }
                document["cash"] = dict(document["opening_cash"])
                document["chain_head"] = self._genesis_hash(document["opening_cash"])
                queue_audit_event(
                    document,
                    event_id=f"account:{self.account_id}:created",
                    event="accounting.account_created",
                    actor=actor,
                    subject=self.account_id,
                    details={"currencies": sorted(normalised)},
                )
                self._write(document)
        self.reconcile_audit()
        return self.snapshot()

    def _write(self, document: dict[str, Any]) -> None:
        """Persist the document and record it as freshly verified.

        Every mutation funnels through here so the O(N) chain replay in
        ``_load`` is skipped on the next call while the file is unchanged.
        """
        atomic_write_json(self.path, document)
        self._record_verified(document)

    def apply(
        self, event: AccountingEvent, *, actor: str = "accounting-engine"
    ) -> AccountSnapshot:
        if event.account_id != self.account_id:
            raise ValueError("event account_id does not match ledger")
        with self.lock:
            document = self._load()
            before = int(document["revision"])
            self._append_event(document, event, actor=actor)
            if int(document["revision"]) == before:
                return self._snapshot_from(document)
            self._write(document)
            result = self._snapshot_from(document)
        self.reconcile_audit()
        return result

    def apply_events(
        self,
        events: list[AccountingEvent],
        *,
        actor: str = "accounting-engine",
        expected_revision: int | None = None,
    ) -> list[AccountSnapshot]:
        """Apply a sequence of events to this account in one locked pass.

        Semantically equivalent to calling ``apply`` once per event, but the
        chain is verified once and the document is written once instead of
        once per event.  Each event is still applied idempotently: an event
        whose ``event_id`` is already recorded is skipped (returning the same
        snapshot it would have produced) and never re-applied.
        """
        if not events:
            return []
        for event in events:
            if event.account_id != self.account_id:
                raise ValueError("event account_id does not match ledger")
        with self.lock:
            document = self._load()
            before = int(document["revision"])
            results: list[AccountSnapshot] = []
            if expected_revision is not None and before != expected_revision:
                raise AccountingConflict("account changed after execution sizing")
            for event in events:
                self._append_event(document, event, actor=actor)
                results.append(self._snapshot_from(document))
            if int(document["revision"]) != before:
                self._write(document)
        self.reconcile_audit()
        return results

    def _apply_payload(self, document: dict[str, Any], event: AccountingEvent) -> None:
        cash = {key: _decimal(value) for key, value in document["cash"].items()}
        fees = {key: _decimal(value) for key, value in document["fees"].items()}
        positions = document["positions"]

        if event.event_type is AccountingEventType.OBSERVATION:
            # Durable clock/provenance only; an observation never creates a
            # holding or changes cash, unlike an admitted fill or funding event.
            pass
        elif event.event_type is AccountingEventType.CASH_ADJUSTMENT:
            assert event.currency is not None and event.amount is not None
            cash[event.currency] = cash.get(event.currency, Decimal("0")) + event.amount
        elif event.event_type is AccountingEventType.FEE:
            assert event.currency is not None and event.amount is not None
            cash[event.currency] = cash.get(event.currency, Decimal("0")) - event.amount
            fees[event.currency] = fees.get(event.currency, Decimal("0")) + event.amount
        elif event.event_type is AccountingEventType.FUNDING:
            assert event.currency is not None and event.amount is not None
            cash[event.currency] = cash.get(event.currency, Decimal("0")) + event.amount
            if event.instrument_id and event.instrument_id in positions:
                raw = positions[event.instrument_id]
                raw["funding_pnl"] = str(
                    _decimal(raw.get("funding_pnl", 0)) + event.amount
                )
        elif event.event_type is AccountingEventType.MARK:
            assert event.instrument_id is not None and event.mark_price is not None
            if event.instrument_id not in positions:
                raise AccountingConflict("cannot mark an unknown position")
            raw = positions[event.instrument_id]
            quantity = _decimal(raw["quantity"])
            average = _decimal(raw["average_entry_price"])
            raw["mark_price"] = str(event.mark_price)
            raw["unrealized_pnl"] = str((event.mark_price - average) * quantity)
        elif event.event_type is AccountingEventType.FILL:
            assert (
                event.instrument_id
                and event.quote_currency
                and event.side
                and event.quantity
                and event.price
            )
            signed_quantity = (
                event.quantity if event.side is FillSide.BUY else -event.quantity
            )
            cash[event.quote_currency] = (
                cash.get(event.quote_currency, Decimal("0"))
                - signed_quantity * event.price
            )
            raw = positions.get(
                event.instrument_id,
                {
                    "instrument_id": event.instrument_id,
                    "quote_currency": event.quote_currency,
                    "quantity": "0",
                    "average_entry_price": "0",
                    "mark_price": None,
                    "realized_pnl": "0",
                    "unrealized_pnl": "0",
                    "funding_pnl": "0",
                },
            )
            if raw["quote_currency"] != event.quote_currency:
                raise AccountingConflict("instrument quote currency changed")
            old_quantity = _decimal(raw["quantity"])
            old_average = _decimal(raw["average_entry_price"])
            new_quantity = old_quantity + signed_quantity
            realized = _decimal(raw.get("realized_pnl", 0))
            if old_quantity == 0 or _sign(old_quantity) == _sign(signed_quantity):
                total_abs = abs(old_quantity) + abs(signed_quantity)
                new_average = (
                    (
                        abs(old_quantity) * old_average
                        + abs(signed_quantity) * event.price
                    )
                    / total_abs
                    if total_abs
                    else Decimal("0")
                )
            else:
                closed = min(abs(old_quantity), abs(signed_quantity))
                realized += closed * (event.price - old_average) * _sign(old_quantity)
                if new_quantity == 0:
                    new_average = Decimal("0")
                elif _sign(new_quantity) == _sign(old_quantity):
                    new_average = old_average
                else:
                    new_average = event.price
            raw["quantity"] = str(new_quantity)
            raw["average_entry_price"] = str(new_average)
            raw["realized_pnl"] = str(realized)
            mark = (
                _decimal(raw["mark_price"])
                if raw.get("mark_price") is not None
                else event.price
            )
            raw["mark_price"] = str(mark)
            raw["unrealized_pnl"] = str(
                (mark - new_average) * new_quantity if new_quantity else Decimal("0")
            )
            positions[event.instrument_id] = raw
            if event.fee_amount:
                assert event.fee_currency is not None
                cash[event.fee_currency] = (
                    cash.get(event.fee_currency, Decimal("0")) - event.fee_amount
                )
                fees[event.fee_currency] = (
                    fees.get(event.fee_currency, Decimal("0")) + event.fee_amount
                )
        else:  # pragma: no cover - exhaustive enum guard
            raise ValueError("unsupported accounting event type")

        if len(cash) > MAX_CURRENCIES or len(fees) > MAX_CURRENCIES:
            raise AccountingConflict("accounting currency limit reached")
        if len(positions) > MAX_POSITIONS:
            raise AccountingConflict("accounting position limit reached")
        document["cash"] = {key: str(value) for key, value in sorted(cash.items())}
        document["fees"] = {key: str(value) for key, value in sorted(fees.items())}
        document["positions"] = positions

    def event(self, event_id: str) -> AccountingEvent | None:
        """Read one verified immutable event for idempotent command recovery."""
        with self.lock:
            document = self._load()
            if event_id not in document["event_index"]:
                return None
            for record in document["events"]:
                if record["event_id"] == event_id:
                    return AccountingEvent.model_validate(record["payload"])
        raise AccountingConflict("event index references a missing record")

    def snapshot(self) -> AccountSnapshot:
        with self.lock:
            return self._snapshot_from(self._load())

    def _snapshot_from(self, document: dict[str, Any]) -> AccountSnapshot:
        return AccountSnapshot(
            account_id=self.account_id,
            revision=int(document["revision"]),
            sequence=int(document["sequence"]),
            cash={key: _decimal(value) for key, value in document["cash"].items()},
            fees={key: _decimal(value) for key, value in document["fees"].items()},
            positions={
                key: PositionState.model_validate(value)
                for key, value in document["positions"].items()
            },
            chain_head=str(document["chain_head"]),
            last_event_at=(
                datetime.fromisoformat(document["last_event_at"])
                if document.get("last_event_at")
                else None
            ),
        )

    def reconcile(
        self,
        observed: ObservedAccountState,
        tolerance: ReconciliationTolerance | None = None,
    ) -> ReconciliationReport:
        tolerance = tolerance or ReconciliationTolerance()
        snapshot = self.snapshot()
        differences: list[ReconciliationDifference] = []
        for currency in sorted(set(snapshot.cash) | set(observed.cash)):
            expected = snapshot.cash.get(currency, Decimal("0"))
            actual = observed.cash.get(currency, Decimal("0"))
            delta = actual - expected
            if abs(delta) > tolerance.absolute_cash:
                differences.append(
                    ReconciliationDifference(
                        kind="cash",
                        key=currency,
                        expected=expected,
                        observed=actual,
                        difference=delta,
                    )
                )
        expected_positions = {
            key: value.quantity for key, value in snapshot.positions.items()
        }
        for instrument_id in sorted(set(expected_positions) | set(observed.positions)):
            expected = expected_positions.get(instrument_id, Decimal("0"))
            actual = observed.positions.get(instrument_id, Decimal("0"))
            delta = actual - expected
            if abs(delta) > tolerance.absolute_quantity:
                differences.append(
                    ReconciliationDifference(
                        kind="position",
                        key=instrument_id,
                        expected=expected,
                        observed=actual,
                        difference=delta,
                    )
                )
        return ReconciliationReport(
            account_id=self.account_id,
            account_revision=snapshot.revision,
            matched=not differences,
            differences=differences,
        )

    def verify(self) -> dict[str, Any]:
        with self.lock:
            document = self._load()
            return {
                "valid": True,
                "account_id": self.account_id,
                "revision": int(document["revision"]),
                "events": len(document["events"]),
                "chain_head": document["chain_head"],
                "audit_pending": len(document["audit_outbox"]),
            }

    def audit_pending(self) -> int:
        with self.lock:
            return len(self._load()["audit_outbox"])

    def reconcile_audit(self) -> dict[str, int]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                self._write(document)
            return {"delivered": delivered, "pending": len(document["audit_outbox"])}


class AccountingRepository:
    """Account namespace and cross-account audit reconciliation."""

    MAX_ACCOUNTS = 10_000

    def __init__(
        self, root: Path | None = None, ledger: AuditLedger | None = None
    ) -> None:
        self.root = root or resolve_data_home() / "accounting" / "accounts"
        if self.root.exists() and self.root.is_symlink():
            raise ValueError("accounting root must not be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ledger = ledger or AuditLedger()

    def account(self, account_id: str) -> PortfolioLedger:
        return PortfolioLedger(
            account_id,
            path=confined_child(self.root, f"{account_id}.json", allow_symlinks=False),
            ledger=self.ledger,
        )

    def exists(self, account_id: str) -> bool:
        path = confined_child(self.root, f"{account_id}.json", allow_symlinks=False)
        return path.is_file() and not path.is_symlink()

    def create(
        self,
        account_id: str,
        initial_cash: dict[str, Decimal] | None = None,
        *,
        actor: str = "api-operator",
    ) -> AccountSnapshot:
        if (
            len(self.list_account_ids()) >= self.MAX_ACCOUNTS
            and not (self.root / f"{account_id}.json").exists()
        ):
            raise AccountingConflict("account limit reached")
        return self.account(account_id).create(initial_cash, actor=actor)

    def list_account_ids(self) -> list[str]:
        account_ids: list[str] = []
        for path in sorted(self.root.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            account_ids.append(path.stem)
            if len(account_ids) > self.MAX_ACCOUNTS:
                # AccountingConflict, not ValueError: this is a durable state
                # conflict callers can recognise, and every entry point that
                # lists accounts (create/audit_pending/reconcile_audit) must
                # surface it rather than a generic error.
                raise AccountingConflict("account limit exceeded")
        return account_ids

    def audit_pending(self) -> int:
        return sum(
            self.account(account_id).audit_pending()
            for account_id in self.list_account_ids()
        )

    def reconcile_audit(self) -> dict[str, int]:
        delivered = 0
        pending = 0
        for account_id in self.list_account_ids():
            status = self.account(account_id).reconcile_audit()
            delivered += status["delivered"]
            pending += status["pending"]
        return {"delivered": delivered, "pending": pending}
