"""Versioned canonical instrument registry with alias-collision protection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from filelock import FileLock

from trading_gym.core.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.core.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.market.models import InstrumentSpec


class InstrumentConflict(RuntimeError):
    pass


def _key(value: str) -> str:
    return value.strip().upper()


class InstrumentRegistry:
    MAX_INSTRUMENTS = 100_000

    def __init__(
        self,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "instruments.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".registry.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {"schema_version": 1, "revision": 0, "instruments": {}, "aliases": {}},
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported instrument registry schema")
        for field in ("instruments", "aliases"):
            if not isinstance(document.get(field), dict):
                raise ValueError(f"instrument registry {field} must be an object")
        if len(document["instruments"]) > self.MAX_INSTRUMENTS:
            raise ValueError("instrument registry record limit exceeded")
        ensure_audit_outbox(document)
        return document

    def audit_pending(self) -> int:
        with self.lock:
            return len(self._load()["audit_outbox"])

    def reconcile_audit(self) -> dict[str, int]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {"delivered": delivered, "pending": len(document["audit_outbox"])}

    def list(self, *, active_only: bool = False) -> list[InstrumentSpec]:
        with self.lock:
            document = self._load()
        values = [
            InstrumentSpec.model_validate(item)
            for item in document["instruments"].values()
        ]
        if active_only:
            values = [item for item in values if item.active]
        return sorted(
            values, key=lambda item: (item.venue, item.symbol, item.instrument_id)
        )

    def revision(self) -> int:
        with self.lock:
            return int(self._load()["revision"])

    def resolve(self, identifier: str, *, venue: str | None = None) -> InstrumentSpec:
        with self.lock:
            document = self._load()
        candidate = _key(identifier)
        instrument_id = (
            candidate
            if candidate in document["instruments"]
            else document["aliases"].get(candidate)
        )
        if instrument_id is None and venue:
            venue_key = _key(venue)
            matches = [
                item_id
                for item_id, raw in document["instruments"].items()
                if raw["venue"] == venue_key and raw["symbol"] == candidate
            ]
            instrument_id = matches[0] if len(matches) == 1 else None
        if instrument_id is None:
            raise KeyError(identifier)
        return InstrumentSpec.model_validate(document["instruments"][instrument_id])

    def save(
        self,
        spec: InstrumentSpec,
        *,
        expected_revision: int | None = None,
        actor: str = "api-operator",
    ) -> dict[str, Any]:
        with self.lock:
            document = self._load()
            current_revision = int(document["revision"])
            if expected_revision is not None and expected_revision != current_revision:
                raise InstrumentConflict(
                    f"expected registry revision {expected_revision}, found {current_revision}"
                )
            if (
                spec.instrument_id not in document["instruments"]
                and len(document["instruments"]) >= self.MAX_INSTRUMENTS
            ):
                raise InstrumentConflict("instrument registry record limit reached")
            owner_by_alias = dict(document["aliases"])
            identifiers = {spec.instrument_id, spec.symbol, *spec.aliases}
            for identifier in identifiers:
                owner = owner_by_alias.get(_key(identifier))
                if owner is not None and owner != spec.instrument_id:
                    raise InstrumentConflict(
                        f"identifier {identifier!r} already belongs to {owner}"
                    )
            previous = document["instruments"].get(spec.instrument_id)
            if previous:
                old = InstrumentSpec.model_validate(previous)
                for identifier in {old.symbol, *old.aliases}:
                    document["aliases"].pop(_key(identifier), None)
            document["instruments"][spec.instrument_id] = spec.model_dump(mode="json")
            for identifier in {spec.symbol, *spec.aliases}:
                document["aliases"][_key(identifier)] = spec.instrument_id
            document["revision"] = current_revision + 1
            queue_audit_event(
                document,
                event_id=f"instrument:{spec.instrument_id}:revision:{document['revision']}:saved",
                event="instrument.saved",
                actor=actor,
                subject=spec.instrument_id,
                details={
                    "venue": spec.venue,
                    "symbol": spec.symbol,
                    "active": spec.active,
                    "revision": document["revision"],
                },
            )
            atomic_write_json(self.path, document)
            result = {
                "revision": document["revision"],
                "instrument": spec.model_dump(mode="json"),
            }
        self.reconcile_audit()
        return result
