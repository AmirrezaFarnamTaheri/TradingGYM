"""Closed-world primitives for reconstructed convergence assurances."""

from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Iterable
import hashlib
import json
import re

HEX64 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_AUTHORITY = (
    "sign",
    "broadcast",
    "execute",
    "mutate",
    "payment",
    "trade",
    "order_submission",
    "credential_resolution",
    "network_fetch",
    "wallet_secret",
    "private_key",
    "mnemonic",
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, RecordModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("evidence must be a mapping")


def _freeze(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): _freeze(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return tuple(_freeze(x) for x in v)
    return v


class RecordModel:
    """Pydantic-like immutable mapping holder used for recovered contracts."""

    def __init__(self, **data: Any):
        object.__setattr__(self, "_data", _freeze(data))

    def __getattr__(self, n: str) -> Any:
        try:
            return self._data[n]
        except KeyError:
            raise AttributeError(n)

    def model_dump(self, mode: str | None = None) -> dict[str, Any]:
        return json.loads(json.dumps(self._data, default=str))

    @classmethod
    def model_validate(cls, value: Any):
        return value if isinstance(value, cls) else cls(**as_mapping(value))


class PolicyModel(RecordModel):
    pass


@dataclass(frozen=True)
class AssuranceReport:
    capability: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_sha256: str = ""
    ready_for_human_review: bool = False
    mutation_performed: bool = False
    authority_granted: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def report_sha256(self) -> str:
        return sha256_payload(self.model_dump(exclude_hash=True))

    def model_dump(
        self, mode: str | None = None, exclude_hash: bool = False
    ) -> dict[str, Any]:
        d = {
            "capability": self.capability,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "evidence_sha256": self.evidence_sha256,
            "ready_for_human_review": self.ready_for_human_review,
            "mutation_performed": False,
            "authority_granted": False,
            "details": self.details,
        }
        if not exclude_hash:
            d["report_sha256"] = sha256_payload(d)
        return d


def evaluate_contract(
    capability: str,
    evidence: Any,
    *,
    required: Iterable[str] = (),
    max_items: int = 10000,
    extra_blockers: Iterable[str] = (),
) -> AssuranceReport:
    data = as_mapping(evidence)
    blockers = list(extra_blockers)
    warnings = []
    if len(data) > max_items:
        blockers.append("evidence_too_large")
    for k in required:
        if k not in data or data[k] in (None, "", [], {}):
            blockers.append(f"missing:{k}")
    for k, v in data.items():
        lk = k.lower()
        if (
            lk.endswith("_sha256")
            and v not in (None, "")
            and not HEX64.fullmatch(str(v))
        ):
            blockers.append(f"invalid_sha256:{k}")
        if any(x in lk for x in FORBIDDEN_AUTHORITY) and v is True:
            blockers.append(f"forbidden_authority:{k}")
        if any(
            x in lk for x in ("private_key", "mnemonic", "seed_phrase", "api_secret")
        ) and v not in (None, "", False):
            blockers.append(f"secret_material:{k}")
    return AssuranceReport(
        capability=capability,
        blockers=tuple(sorted(set(blockers))),
        warnings=tuple(warnings),
        evidence_sha256=sha256_payload(data),
        ready_for_human_review=not blockers,
        details={"keys": sorted(data)},
    )


def require_decimal(value: Any, label: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} invalid")
    if not d.is_finite():
        raise ValueError(f"{label} non-finite")
    return d
