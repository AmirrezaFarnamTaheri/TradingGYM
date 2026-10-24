"""Fail-closed metadata screening for optional native crypto evidence.

This does not recover the absent gm-crypto-rs donor or attest that a native
binary is constant-time. Passing only means a human can start reviewing an
externally supplied and independently verified build record.
"""

from dataclasses import dataclass
import re


_SOURCE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class NativeCryptoManifest:
    source_sha256: str
    compiler: str | None = None
    vectors_executed: int = 0
    built: bool = False


def evaluate_crypto_evidence(evidence):
    m = (
        evidence
        if isinstance(evidence, NativeCryptoManifest)
        else NativeCryptoManifest(**evidence)
    )
    blockers: list[str] = []
    if not isinstance(m.source_sha256, str) or not _SOURCE_SHA256.fullmatch(
        m.source_sha256
    ):
        blockers.append("invalid_source_sha256")
    if type(m.built) is not bool:  # noqa: E721
        blockers.append("invalid_build_evidence")
    elif not m.built:
        blockers.append("native_build_missing")
    if not isinstance(m.compiler, str) or not m.compiler.strip():
        blockers.append("compiler_required")
    if type(m.vectors_executed) is not int or m.vectors_executed <= 0:  # noqa: E721
        blockers.append("no_executed_vectors")
    return {
        "blockers": blockers,
        "ready_for_human_review": not blockers,
        "constant_time_claim": False,
        "audit_claim": False,
    }
