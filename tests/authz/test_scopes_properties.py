"""Property-based tests for the AXIOM-1 authz scope grammar (ADR-028).

Invariants held under structured generation:
- A concrete scope is matched by every wildcard relaxation of itself.
- A pattern never matches outside its first literal segment.
- Most-specific-wins: a more specific pattern beats a catch-all.
- ``**`` is equivalent to an unbounded run of ``*`` segments.
"""

from __future__ import annotations

from hypothesis import given, settings
import pytest
from hypothesis import strategies as st

from trading_gym.authz.scopes import (
    RESERVED_FIRST_SEGMENTS,
    RESERVED_SCOPES,
    ScopeError,
    ScopePattern,
    ScopeSet,
)

SAFE_SEGMENT_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_-"

#: Namespaces a grantable pattern may never name. The property tests below
#: assert matching/specificity *grammar* invariants, not grantability, so the
#: generated namespaces must stay out of reserved space — otherwise
#: ``ScopeSet.parse`` correctly raises ScopeError and the property is
#: measuring the wrong thing.
_RESERVED_NAMESPACES = RESERVED_FIRST_SEGMENTS | {
    scope.split(".", 1)[0] for scope in RESERVED_SCOPES
}


def not_reserved(segment: str) -> bool:
    return segment not in _RESERVED_NAMESPACES


segments = st.lists(
    st.text(alphabet=SAFE_SEGMENT_CHARS, min_size=1, max_size=8).filter(not_reserved),
    min_size=1,
    max_size=5,
    unique=True,
).map(tuple)


def concrete(segs: tuple[str, ...]) -> str:
    return ".".join(segs)


class TestScopeGrammarProperties:
    @settings(max_examples=200, deadline=None)
    @given(segments)
    def test_literal_pattern_matches_only_itself(self, segs: tuple[str, ...]) -> None:
        scope = concrete(segs)
        assert ScopePattern.parse(scope).matches(scope)
        other = concrete(tuple(reversed(segs)))
        if other != scope:
            assert not ScopePattern.parse(scope).matches(other) or concrete(
                tuple(reversed(segs))
            ) == concrete(segs)

    @settings(max_examples=200, deadline=None)
    @given(segments)
    def test_double_star_is_universal_suffix(self, segs: tuple[str, ...]) -> None:
        scope = concrete(segs)
        head = segs[0]
        pattern = ScopePattern.parse(head + ".**")
        assert pattern.matches(scope)

    @settings(max_examples=200, deadline=None)
    @given(segments)
    def test_first_segment_literal_anchors_matching(
        self, segs: tuple[str, ...]
    ) -> None:
        scope = concrete(segs)
        decoy = "zz" + scope  # different first segment
        pattern = ScopePattern.parse(segs[0] + ".**")
        assert pattern.matches(scope)
        if not decoy.startswith(segs[0] + "."):
            assert not pattern.matches(decoy)

    @settings(max_examples=100, deadline=None)
    @given(segments)
    def test_most_specific_wins(self, segs: tuple[str, ...]) -> None:
        if len(segs) < 2:
            return
        scope = concrete(segs)
        specific = concrete(segs)
        general = segs[0] + ".**"
        scope_set = ScopeSet.parse((general, specific))
        matched = scope_set.match(scope)
        assert matched is not None
        assert matched.text == specific

    @settings(max_examples=100, deadline=None)
    @given(segments)
    def test_single_star_matches_one_segment(self, segs: tuple[str, ...]) -> None:
        if len(segs) < 3:
            return
        scope = concrete(segs)
        pattern_text = ".".join([segs[0], "*"] + list(segs[2:]))
        pattern = ScopePattern.parse(pattern_text)
        assert pattern.matches(scope)
        shorter = concrete(segs[:2])
        assert not pattern.matches(shorter)
        longer = concrete(segs + ("x",))
        assert not pattern.matches(longer)

    @settings(max_examples=100, deadline=None)
    @given(segments)
    def test_wildcard_in_concrete_scope_raises(self, segs: tuple[str, ...]) -> None:
        pattern = ScopePattern.parse(segs[0] + ".**")
        for bad in (segs[0] + ".*", segs[0] + ".**"):
            with pytest.raises(ScopeError):
                pattern.matches(bad)
