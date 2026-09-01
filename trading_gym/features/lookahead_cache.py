"""Lookahead-keyed feature cache.

Absorbed mechanism: pybroker keys model caches on the model's lookahead so
artifacts can never be reused across leakage regimes (`model.py:650-663`).
Ported as a strict namespace store: every entry lives under an explicit
``lookahead`` bucket, and fetching through a different bucket is a hard error —
the failure mode this exists to prevent is a silently reused artifact trained
with future data.
"""

from __future__ import annotations

from typing import Any


class LookaheadMismatch(KeyError):
    """A cache hit exists for this key but under a different lookahead."""

    def __init__(self, namespace: str, key: str, stored_lookahead: int):
        self.namespace = namespace
        self.key = key
        self.stored_lookahead = stored_lookahead
        super().__init__(
            f"lookahead-mismatch: {namespace}/{key} is cached with "
            f"lookahead={stored_lookahead}, requested a different value"
        )


class LookaheadKeyedCache:
    """Strict per-lookahead object cache (typed, in-memory, non-persistent)."""

    def __init__(self) -> None:
        # {namespace: {lookahead: {key: value}}}
        self._store: dict[str, dict[int, dict[str, Any]]] = {}

    def put(self, *, namespace: str, key: str, value: Any, lookahead: int) -> None:
        if not namespace or not key:
            raise ValueError("namespace and key are required")
        if isinstance(lookahead, bool) or not isinstance(lookahead, int) or lookahead < 0:
            raise ValueError("lookahead must be a non-negative integer")
        buckets = self._store.setdefault(namespace, {})
        existing = {
            la: bucket for la, bucket in buckets.items() if key in bucket and la != lookahead
        }
        if existing:
            raise LookaheadMismatch(namespace, key, next(iter(existing)))
        buckets.setdefault(lookahead, {})[key] = value

    def get(self, *, namespace: str, key: str, lookahead: int) -> Any:
        buckets = self._store.get(namespace, {})
        if lookahead in buckets and key in buckets[lookahead]:
            return buckets[lookahead][key]
        other = [la for la, bucket in buckets.items() if key in bucket]
        if other:
            raise LookaheadMismatch(namespace, key, other[0])
        raise KeyError(f"{namespace}/{key}")

    def has(self, *, namespace: str, key: str, lookahead: int) -> bool:
        try:
            self.get(namespace=namespace, key=key, lookahead=lookahead)
            return True
        except KeyError:
            return False
