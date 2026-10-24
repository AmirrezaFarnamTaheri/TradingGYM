"""Wildcard scope grammar for delegated capability grants (ADR-028).

A scope is a dotted sequence of lowercase segments. Segments are
``[a-z0-9_-]{1,64}``; a whole segment may be ``*`` (matches exactly one
segment) and the final segment may be ``**`` (matches zero or more
remaining segments). Concrete scope strings (the things being checked)
never contain wildcards.

Wildcards are matched segment-by-segment against a pre-tokenised pattern
list — user-supplied scope text is never compiled into a regular
expression, so matching is linear in segments and immune to ReDoS.

Reserved namespaces are rejected at grant time (not at evaluation time):
they are outside any principal's authority to delegate, so a grant that
names them is malformed rather than merely unauthorized.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

_SEGMENT_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

#: First segments that may never appear in a *grantable* pattern.
RESERVED_FIRST_SEGMENTS = frozenset({"emergency", "governance", "wallet", "authz"})

#: Fully-qualified scopes that are reserved regardless of namespace rules.
RESERVED_SCOPES = frozenset({"audit.seal"})

_WILDCARD = "*"
_DOUBLE_WILDCARD = "**"
_MAX_SEGMENTS = 16

# New standing grants must not confer blanket economic mutation authority.
# Legacy journal records remain parseable, so they can be audited/revoked.
_SENSITIVE_SEGMENTS = frozenset(
    {
        "live",
        "production",
        "withdrawal",
        "x402",
        "wallet",
        "capital",
    }
)
_SENSITIVE_DOMAINS = frozenset(
    {
        "economic",
        "payments",
        "payment",
        "broker",
        "venue",
    }
)


class ScopeError(ValueError):
    """A scope string or pattern is malformed or not grantable."""


@dataclass(frozen=True)
class ScopeToken:
    """A parsed scope (concrete or pattern)."""

    segments: tuple[str, ...]
    text: str

    @classmethod
    def parse(cls, text: str) -> "ScopeToken":
        if not isinstance(text, str):
            raise ScopeError("scope must be a string")
        if not text or text != text.strip():
            raise ScopeError(f"scope must be non-empty and trimmed: {text!r}")
        segments = tuple(text.split("."))
        if len(segments) > _MAX_SEGMENTS:
            raise ScopeError(f"scope exceeds {_MAX_SEGMENTS} segments: {text!r}")
        for segment in segments:
            if segment == _WILDCARD or segment == _DOUBLE_WILDCARD:
                continue
            if not _SEGMENT_RE.match(segment):
                raise ScopeError(f"invalid scope segment {segment!r} in {text!r}")
        if _DOUBLE_WILDCARD in segments[:-1]:
            raise ScopeError(f"'**' must be the terminal segment: {text!r}")
        return cls(segments=segments, text=text)

    def is_concrete(self) -> bool:
        return _WILDCARD not in self.segments and _DOUBLE_WILDCARD not in self.segments


@dataclass(frozen=True)
class ScopePattern:
    """A grantable wildcard pattern with segment-wise matching."""

    segments: tuple[str, ...]
    text: str

    @classmethod
    def parse(cls, text: str) -> "ScopePattern":
        token = ScopeToken.parse(text)
        return cls(segments=token.segments, text=token.text)

    def matches(self, scope: str) -> bool:
        """Return True when *scope* (a concrete scope) is covered.

        Raises ScopeError when *scope* itself contains wildcards — a
        caller asking "does this pattern match ``a.*``" has a bug, and
        fail-closed means raising, not silently returning False.
        """

        target = ScopeToken.parse(scope)
        if not target.is_concrete():
            raise ScopeError(f"scope to match must be concrete: {scope!r}")
        return self._matches_segments(target.segments)

    def _matches_segments(self, target: Sequence[str]) -> bool:
        def seg_eq(pattern_seg: str, value: str) -> bool:
            return pattern_seg == _WILDCARD or pattern_seg == value

        p, t = self.segments, tuple(target)
        if _DOUBLE_WILDCARD in p:
            head = p.index(_DOUBLE_WILDCARD)
            prefix, tail = p[:head], p[head + 1 :]
            if len(t) < len(prefix) + len(tail):
                return False
            return all(seg_eq(p[i], t[i]) for i in range(len(prefix))) and all(
                seg_eq(p[-(i + 1)], t[-(i + 1)]) for i in range(len(tail))
            )
        if len(p) != len(t):
            return False
        return all(seg_eq(seg, t[i]) for i, seg in enumerate(p))

    def is_grantable(self) -> bool:
        """A pattern is grantable when it stays out of reserved space.

        The first segment must be a literal non-reserved name: a leading
        ``*`` would silently cover reserved namespaces (``*.live`` matches
        ``governance.live``), so it is rejected at grant time.
        """

        if self.segments == (_DOUBLE_WILDCARD,):
            return False
        first = self.segments[0]
        if first == _WILDCARD or first == _DOUBLE_WILDCARD:
            return False
        if first in RESERVED_FIRST_SEGMENTS:
            return False
        if self.text in RESERVED_SCOPES:
            return False
        # A pattern that *covers* a reserved scope is as dangerous as the
        # reserved scope itself: ``audit.*`` matches ``audit.seal``, so granting
        # it would delegate a capability the principal does not hold. The check
        # above is text-exact and lets wildcards through. Reserved scopes are
        # concrete by construction, so ``matches`` cannot raise here.
        if any(self.matches(reserved) for reserved in RESERVED_SCOPES):
            return False
        return True

    def is_issuable(self) -> bool:
        """Policy for *new* delegations, stricter than legacy parseability.

        Only concrete names can delegate economic/live operations.
        Historical signed records retain their original grammar for
        verification and revocation; do not reinterpret their hashes.
        """
        if not self.is_grantable():
            return False
        if all(part not in (_WILDCARD, _DOUBLE_WILDCARD) for part in self.segments):
            return True
        if self.segments[0] in _SENSITIVE_DOMAINS:
            return False
        if any(part in _SENSITIVE_SEGMENTS for part in self.segments):
            return False
        # "trade.**" and "trade.*.order" could span live operations.
        # "trade.paper.**" is bounded to the non-funded paper namespace.
        if self.segments[0] == "trade":
            return len(self.segments) > 1 and self.segments[1] == "paper"
        return True

    def specificity(self) -> tuple[int, int]:
        """Sort key: more literal segments win; ``**`` patterns rank lowest.

        A pattern with more literal segments is more specific; between two
        patterns with equal literals, the one without ``**`` wins.
        """

        literals = sum(
            1 for s in self.segments if s not in (_WILDCARD, _DOUBLE_WILDCARD)
        )
        has_double = 1 if _DOUBLE_WILDCARD in self.segments else 0
        return (literals, -has_double)


@dataclass(frozen=True)
class ScopeSet:
    """An ordered collection of grantable patterns with specificity-ordered matching."""

    patterns: tuple[ScopePattern, ...]

    @classmethod
    def parse(cls, texts: Iterable[str]) -> "ScopeSet":
        patterns = [ScopePattern.parse(t) for t in texts]
        for pattern in patterns:
            if not pattern.is_grantable():
                raise ScopeError(f"pattern is not grantable: {pattern.text!r}")
        patterns.sort(key=lambda p: p.specificity(), reverse=True)
        return cls(patterns=tuple(patterns))

    def match(self, scope: str) -> ScopePattern | None:
        """Return the most specific covering pattern, or None.

        Raises ScopeError if *scope* is not a concrete scope string.
        """

        for pattern in self.patterns:
            if pattern.matches(scope):
                return pattern
        return None
