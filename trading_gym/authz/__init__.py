"""AXIOM-1 delegated capability grants (ADR-028).

Public surface: scope grammar, grant model + evaluator, hash-chained
ledger. See ``docs/architecture/ADR-028-wildcard-capability-grants.md``.
"""

from trading_gym.authz.grants import (
    ConstraintViolation,
    Grant,
    GrantDecision,
    GrantLedger,
    GrantLedgerIntegrityError,
    GrantRequest,
    GrantState,
    ScopeDenied,
)
from trading_gym.authz.scopes import (
    RESERVED_FIRST_SEGMENTS,
    RESERVED_SCOPES,
    ScopeError,
    ScopePattern,
    ScopeSet,
    ScopeToken,
)

__all__ = [
    "ConstraintViolation",
    "Grant",
    "GrantDecision",
    "GrantLedger",
    "GrantLedgerIntegrityError",
    "GrantRequest",
    "GrantState",
    "ScopeDenied",
    "ScopeError",
    "ScopePattern",
    "ScopeSet",
    "ScopeToken",
    "RESERVED_FIRST_SEGMENTS",
    "RESERVED_SCOPES",
]
