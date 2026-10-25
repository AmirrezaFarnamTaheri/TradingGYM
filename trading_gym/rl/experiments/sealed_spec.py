"""Sealed experiment specifications.

Absorbed mechanism: kraken's sealed experiments bind the experiment name, the
spec hash and the data-tape hash together at seal time; later verification
rejects any mismatch as damage rather than silently re-running something else
(`experiment.rs:178-258`).

A sealed spec is content-addressed and immutable.  Sealing never executes
anything — this is identity plumbing for the scientific-reproduction plane.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,119}$")


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class SealedExperiment:
    name: str
    spec_sha256: str
    tape_sha256: str
    seal_id: str


def seal_experiment(
    *,
    name: str,
    spec: dict,
    tape_sha256: str,
) -> SealedExperiment:
    """Bind ``name`` + ``spec`` + ``tape_sha256`` into an immutable seal."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError("name must match ^[a-z0-9][a-z0-9._-]{2,119}$")
    if not isinstance(spec, dict):
        raise ValueError("spec must be a mapping/dict")
    if not re.fullmatch(r"^[0-9a-f]{64}$", str(tape_sha256)):
        raise ValueError("tape_sha256 must be a lowercase sha256 hex digest")
    spec_sha = hashlib.sha256(_canonical(spec)).hexdigest()
    seal_id = hashlib.sha256(
        _canonical(
            {
                "kind": "sealed-experiment-v1",
                "name": name,
                "spec_sha256": spec_sha,
                "tape_sha256": tape_sha256,
            }
        )
    ).hexdigest()
    return SealedExperiment(
        name=name,
        spec_sha256=spec_sha,
        tape_sha256=tape_sha256,
        seal_id=seal_id,
    )


class SealMismatch(RuntimeError):
    """Verification failed: the artifact does not match its seal."""


def verify_seal(
    sealed: SealedExperiment, *, name: str, spec: dict, tape_sha256: str
) -> None:
    """Raise :class:`SealMismatch` on any mismatch; return None on success.

    Name mismatches are damage, not a different experiment (kraken rule).
    """
    expected = seal_experiment(name=name, spec=spec, tape_sha256=tape_sha256)
    if expected.seal_id != sealed.seal_id:
        field_hits = []
        if expected.name != sealed.name:
            field_hits.append("name")
        if expected.spec_sha256 != sealed.spec_sha256:
            field_hits.append("spec")
        if expected.tape_sha256 != sealed.tape_sha256:
            field_hits.append("tape")
        detail = ",".join(field_hits) or "seal_id"
        raise SealMismatch(f"seal verification failed ({detail})")
