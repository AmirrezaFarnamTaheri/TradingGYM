"""Native cryptography evidence cannot qualify from assertions alone."""

from __future__ import annotations

import pytest

from trading_gym.core.crypto.assurance import (
    NativeCryptoManifest,
    evaluate_crypto_evidence,
)

GOOD_SHA = "a" * 64


def test_unbuilt_native_module_cannot_qualify_even_with_vectors() -> None:
    result = evaluate_crypto_evidence(
        NativeCryptoManifest(
            source_sha256=GOOD_SHA,
            compiler="rustc",
            vectors_executed=8,
            built=False,
        )
    )
    assert not result["ready_for_human_review"]
    assert "native_build_missing" in result["blockers"]


@pytest.mark.parametrize("source_sha", ["", "nonhex", "x" * 64, "a" * 63, None])
def test_native_evidence_needs_valid_source_hash(source_sha: object) -> None:
    result = evaluate_crypto_evidence(
        NativeCryptoManifest(
            source_sha256=source_sha,
            compiler="rustc",
            vectors_executed=8,
            built=True,
        )
    )
    assert not result["ready_for_human_review"]
    assert "invalid_source_sha256" in result["blockers"]


@pytest.mark.parametrize("vectors", [0, -1, True, 1.5, "5", None])
def test_native_vectors_must_be_positive_integer(vectors: object) -> None:
    result = evaluate_crypto_evidence(
        NativeCryptoManifest(
            source_sha256=GOOD_SHA,
            compiler="rustc",
            vectors_executed=vectors,
            built=True,
        )
    )
    assert not result["ready_for_human_review"]
    assert "no_executed_vectors" in result["blockers"]


@pytest.mark.parametrize("compiler", [None, "", "   "])
def test_native_evidence_needs_a_real_compiler_label(compiler: object) -> None:
    result = evaluate_crypto_evidence(
        NativeCryptoManifest(
            source_sha256=GOOD_SHA,
            compiler=compiler,
            vectors_executed=8,
            built=True,
        )
    )
    assert not result["ready_for_human_review"]
    assert "compiler_required" in result["blockers"]


def test_complete_metadata_is_human_review_only_not_security_audited() -> None:
    result = evaluate_crypto_evidence(
        NativeCryptoManifest(
            source_sha256=GOOD_SHA,
            compiler="rustc 1.82",
            vectors_executed=8,
            built=True,
        )
    )
    assert result == {
        "blockers": [],
        "ready_for_human_review": True,
        "constant_time_claim": False,
        "audit_claim": False,
    }


def test_non_boolean_built_flag_must_never_qualify() -> None:
    result = evaluate_crypto_evidence(
        NativeCryptoManifest(
            source_sha256=GOOD_SHA,
            compiler="rustc",
            vectors_executed=8,
            built=1,
        )
    )
    assert not result["ready_for_human_review"]
    assert "invalid_build_evidence" in result["blockers"]
