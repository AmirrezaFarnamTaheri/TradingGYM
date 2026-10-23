"""Existing import names survive cleanup of the intelligence assembly modules."""


def test_existing_intelligence_exports_keep_their_canonical_identity():
    from trading_gym.intelligence.factory import BackendRegistry
    from trading_gym.intelligence.registry import BackendRegistry as CanonicalRegistry
    from trading_gym.intelligence.llm_backend import MAX_CONTEXT_CHARS
    from trading_gym.intelligence.system1_context import (
        MAX_CONTEXT_CHARS as CanonicalLimit,
    )
    from trading_gym.intelligence.system1_backend import BackendUnavailable
    from trading_gym.intelligence.types import BackendUnavailable as CanonicalError

    assert BackendRegistry is CanonicalRegistry
    assert MAX_CONTEXT_CHARS == CanonicalLimit
    assert BackendUnavailable is CanonicalError
