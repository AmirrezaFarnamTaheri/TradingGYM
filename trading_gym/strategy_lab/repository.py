"""Versioned, compare-and-swap strategy repository with durable audit intent."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.governance.ledger import AuditLedger
from trading_gym.strategy_lab.models import StrategySpec


class StrategyConflictError(RuntimeError):
    pass


_STRATEGY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


class StrategyRepository:
    MAX_STRATEGIES = 10_000
    MAX_VERSIONS_PER_STRATEGY = 1_000

    def __init__(
        self,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "strategies.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".strategies.lock")

    @staticmethod
    def _validate_identifier(strategy_id: str) -> str:
        if not _STRATEGY_ID.fullmatch(strategy_id):
            raise ValueError(
                "strategy_id must be 1-120 characters using letters, digits, dot, underscore, or hyphen"
            )
        return strategy_id

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {"schema_version": 1, "strategies": {}, "audit_outbox": {}},
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported strategy repository schema")
        strategies = document.get("strategies")
        if not isinstance(strategies, dict):
            raise ValueError("strategy repository strategies must be an object")
        if len(strategies) > self.MAX_STRATEGIES:
            raise ValueError("strategy repository record limit exceeded")
        for strategy_id, record in strategies.items():
            self._validate_identifier(strategy_id)
            if not isinstance(record, dict):
                raise ValueError("strategy record must be an object")
            versions = record.get("versions")
            if not isinstance(versions, list) or not versions:
                raise ValueError("strategy versions must be a non-empty array")
            if len(versions) > self.MAX_VERSIONS_PER_STRATEGY:
                raise ValueError("strategy version limit exceeded")
            revision = int(record.get("revision", 0))
            if revision != len(versions):
                raise ValueError("strategy revision does not match version history")
            expected = 1
            for version in versions:
                if not isinstance(version, dict) or int(version.get("revision", 0)) != expected:
                    raise ValueError("strategy version history is not contiguous")
                spec = StrategySpec.model_validate(version.get("spec"))
                if version.get("sha256") != self._hash(spec):
                    raise ValueError("strategy version hash mismatch")
                expected += 1
        ensure_audit_outbox(document)
        return document

    @staticmethod
    def _hash(spec: StrategySpec) -> str:
        return hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()

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

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            document = self._load()
        output = []
        for strategy_id, record in document["strategies"].items():
            latest = record["versions"][-1]
            output.append(
                {
                    "strategy_id": strategy_id,
                    "name": latest["spec"]["name"],
                    "revision": record["revision"],
                    "sha256": latest["sha256"],
                    "updated_at": latest["created_at"],
                }
            )
        return sorted(output, key=lambda item: item["updated_at"], reverse=True)

    def get(self, strategy_id: str) -> dict[str, Any]:
        strategy_id = self._validate_identifier(strategy_id)
        with self.lock:
            record = self._load()["strategies"].get(strategy_id)
        if record is None:
            raise KeyError(strategy_id)
        return record

    def _save_unlocked(
        self,
        document: dict[str, Any],
        spec: StrategySpec,
        *,
        strategy_id: str,
        expected_revision: int | None,
        actor: str,
        restored_from: int | None = None,
    ) -> dict[str, Any]:
        existing = document["strategies"].get(strategy_id)
        current_revision = int(existing["revision"]) if existing else 0
        if expected_revision is not None and expected_revision != current_revision:
            raise StrategyConflictError(
                f"expected revision {expected_revision}, found {current_revision}"
            )
        if existing and len(existing["versions"]) >= self.MAX_VERSIONS_PER_STRATEGY:
            raise StrategyConflictError("strategy version limit reached")
        if not existing and len(document["strategies"]) >= self.MAX_STRATEGIES:
            raise StrategyConflictError("strategy repository record limit reached")
        next_revision = current_revision + 1
        version = {
            "revision": next_revision,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor.strip()[:120],
            "sha256": self._hash(spec),
            "spec": spec.model_dump(mode="json"),
        }
        if restored_from is not None:
            version["restored_from_revision"] = restored_from
        if existing:
            existing["revision"] = next_revision
            existing["versions"].append(version)
        else:
            document["strategies"][strategy_id] = {
                "revision": 1,
                "versions": [version],
            }
        event = "strategy.restored" if restored_from is not None else "strategy.saved"
        queue_audit_event(
            document,
            event_id=f"strategy:{strategy_id}:revision:{next_revision}:{event.rsplit('.', 1)[-1]}",
            event=event,
            actor=actor,
            subject=strategy_id,
            details={
                "revision": next_revision,
                "sha256": version["sha256"],
                "restored_from_revision": restored_from,
            },
        )
        atomic_write_json(self.path, document)
        return {"strategy_id": strategy_id, **version}

    def save(
        self,
        spec: StrategySpec,
        *,
        strategy_id: str | None = None,
        expected_revision: int | None = None,
        actor: str = "operator",
    ) -> dict[str, Any]:
        identifier = self._validate_identifier(strategy_id or uuid4().hex)
        with self.lock:
            result = self._save_unlocked(
                self._load(),
                spec,
                strategy_id=identifier,
                expected_revision=expected_revision,
                actor=actor,
            )
        self.reconcile_audit()
        return result

    def restore(
        self,
        strategy_id: str,
        revision: int,
        *,
        expected_revision: int,
        actor: str = "operator",
    ) -> dict[str, Any]:
        strategy_id = self._validate_identifier(strategy_id)
        if revision < 1:
            raise ValueError("revision must be positive")
        with self.lock:
            document = self._load()
            record = document["strategies"].get(strategy_id)
            if record is None:
                raise KeyError(strategy_id)
            source = next(
                (item for item in record["versions"] if item["revision"] == revision),
                None,
            )
            if source is None:
                raise KeyError(f"revision {revision}")
            result = self._save_unlocked(
                document,
                StrategySpec.model_validate(source["spec"]),
                strategy_id=strategy_id,
                expected_revision=expected_revision,
                actor=actor,
                restored_from=revision,
            )
        self.reconcile_audit()
        return result
