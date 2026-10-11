"""Bounded multi-provider market-data routing with provenance and failover."""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Literal, Protocol

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.circuit_breaker import CircuitBreaker, CircuitOpenError
from trading_gym.common.rate_limit import RateLimitExceeded, SlidingWindowLimiter
from trading_gym.strategy_lab.evaluator import REQUIRED_COLUMNS, validate_market_frame

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class MarketDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str = Field(
        min_length=3,
        max_length=120,
        pattern=r"^[A-Z0-9][A-Z0-9._:/-]{2,119}$",
    )
    timeframe: str = Field(pattern=r"^(1m|3m|5m|15m|30m|1h|4h|1d|1w)$")
    start: datetime
    end: datetime
    max_rows: int = Field(default=2_000_000, ge=1, le=2_000_000)

    @model_validator(mode="after")
    def validate_window(self) -> "MarketDataRequest":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("market-data timestamps must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("market-data end must be after start")
        expected = (
            int(
                (self.end - self.start).total_seconds()
                // _TIMEFRAME_SECONDS[self.timeframe]
            )
            + 1
        )
        if expected > self.max_rows:
            raise ValueError(
                f"requested window implies approximately {expected} rows, above max_rows={self.max_rows}"
            )
        return self


class MarketDataProvider(Protocol):
    def fetch(self, request: MarketDataRequest) -> pd.DataFrame: ...


@dataclass(frozen=True)
class ProviderRegistration:
    provider_id: str
    adapter: MarketDataProvider
    priority: int
    limiter: SlidingWindowLimiter
    circuit: CircuitBreaker


@dataclass(frozen=True)
class MarketDataEnvelope:
    provider_id: str
    fetched_at: datetime
    frame: pd.DataFrame
    row_count: int
    expected_rows: int
    completeness: float
    content_sha256: str
    warnings: tuple[str, ...]
    attempted_providers: tuple[str, ...]
    from_cache: bool = False


@dataclass
class _CacheRecord:
    expires_at: float
    envelope: MarketDataEnvelope
    bytes_estimate: int


@dataclass
class _InflightRequest:
    event: threading.Event = field(default_factory=threading.Event)
    envelope: MarketDataEnvelope | None = None
    error: Exception | None = None


class DataProviderRouter:
    def __init__(
        self,
        *,
        cache_ttl_seconds: float = 30.0,
        max_cache_entries: int = 1024,
        max_cache_bytes: int = 256 * 1024 * 1024,
        max_inflight_requests: int = 128,
        inflight_wait_seconds: float = 10.0,
        max_providers: int = 64,
        routing_mode: Literal["fast", "balanced", "quality"] = "balanced",
        target_completeness: float = 0.95,
    ) -> None:
        if not math.isfinite(cache_ttl_seconds) or cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be finite and non-negative")
        if (
            not isinstance(max_cache_bytes, int)
            or isinstance(max_cache_bytes, bool)
            or not 0 <= max_cache_bytes <= 4 * 1024**3
        ):
            raise ValueError("max_cache_bytes must be between 0 and 4GiB")
        if (
            not isinstance(max_inflight_requests, int)
            or isinstance(max_inflight_requests, bool)
            or not 1 <= max_inflight_requests <= 10_000
        ):
            raise ValueError("max_inflight_requests must be between 1 and 10000")
        if not math.isfinite(inflight_wait_seconds) or not 0 < inflight_wait_seconds <= 120:
            raise ValueError("inflight_wait_seconds must be finite and positive")
        if not 1 <= max_cache_entries <= 100_000:
            raise ValueError("max_cache_entries must be between 1 and 100000")
        if not 1 <= max_providers <= 1_000:
            raise ValueError("max_providers must be between 1 and 1000")
        if routing_mode not in {"fast", "balanced", "quality"}:
            raise ValueError("routing_mode must be fast, balanced or quality")
        if not math.isfinite(target_completeness) or not 0 <= target_completeness <= 1:
            raise ValueError("target_completeness must be finite and between 0 and 1")
        self.routing_mode = routing_mode
        self.target_completeness = target_completeness
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cache_entries = max_cache_entries
        self.max_cache_bytes = max_cache_bytes
        self.max_inflight_requests = max_inflight_requests
        self.inflight_wait_seconds = inflight_wait_seconds
        self.max_providers = max_providers
        self._providers: dict[str, ProviderRegistration] = {}
        self._cache: OrderedDict[str, _CacheRecord] = OrderedDict()
        self._cache_bytes = 0
        self._inflight: dict[str, _InflightRequest] = {}
        self._lock = threading.Lock()

    def register(
        self,
        provider_id: str,
        adapter: MarketDataProvider,
        *,
        priority: int = 100,
        requests_per_minute: int = 60,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("provider_id must be a safe 1-80 character identifier")
        if not 1 <= requests_per_minute <= 100_000:
            raise ValueError("requests_per_minute must be between 1 and 100000")
        with self._lock:
            if provider_id in self._providers:
                raise ValueError(f"provider already registered: {provider_id}")
            if len(self._providers) >= self.max_providers:
                raise ValueError("market-data provider limit reached")
            self._providers[provider_id] = ProviderRegistration(
                provider_id=provider_id,
                adapter=adapter,
                priority=priority,
                limiter=SlidingWindowLimiter(
                    capacity=requests_per_minute, window_seconds=60.0
                ),
                circuit=CircuitBreaker(
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                ),
            )

    @staticmethod
    def _cache_key(
        request: MarketDataRequest, *, routing_mode: str = "balanced", target_completeness: float = 0.95
    ) -> str:
        # The same bars may be insufficient for a stricter quality setting.
        # Never serve a fast-mode partial result as a quality-mode cache hit.
        identity = (
            request.model_dump_json()
            + "|"
            + routing_mode
            + "|"
            + repr(target_completeness)
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_frame(frame: pd.DataFrame) -> str:
        canonical = (
            frame.loc[:, list(REQUIRED_COLUMNS)]
            .to_csv(
                index=True,
                lineterminator="\n",
                date_format="%Y-%m-%dT%H:%M:%S.%f%z",
            )
            .encode("utf-8")
        )
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _normalise(
        frame: pd.DataFrame,
        request: MarketDataRequest,
    ) -> tuple[pd.DataFrame, tuple[str, ...]]:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("provider must return a pandas DataFrame")
        # Reject oversized results before copying a potentially huge frame.
        if len(frame) > request.max_rows:
            raise ValueError(
                f"provider returned {len(frame)} rows, above {request.max_rows}"
            )
        output = frame.copy()
        warnings: list[str] = []
        if not isinstance(output.index, pd.DatetimeIndex):
            raise ValueError("market-data frame must use a DatetimeIndex")
        if output.index.tz is None:
            raise ValueError("market-data DatetimeIndex must be timezone-aware")
        output = output.loc[
            (output.index >= request.start) & (output.index <= request.end)
        ]
        if output.index.has_duplicates:
            duplicate_count = int(output.index.duplicated(keep="last").sum())
            output = output.loc[~output.index.duplicated(keep="last")]
            warnings.append(f"removed {duplicate_count} duplicate timestamps")
        if not output.index.is_monotonic_increasing:
            output = output.sort_index()
            warnings.append("sorted non-monotonic timestamps")
        validate_market_frame(output)
        return output, tuple(warnings)

    @staticmethod
    def _fetch_and_normalise(
        provider: ProviderRegistration,
        request: MarketDataRequest,
    ) -> tuple[pd.DataFrame, tuple[str, ...]]:
        raw = provider.adapter.fetch(request)
        return DataProviderRouter._normalise(raw, request)

    def fetch(
        self,
        request: MarketDataRequest,
        *,
        routing_mode: Literal["fast", "balanced", "quality"] | None = None,
        target_completeness: float | None = None,
    ) -> MarketDataEnvelope:
        """Coalesce equal reads and isolate each caller from cached frame edits.

        Routing can be selected per request without mutating the router's
        default policy; cache identity includes the selected quality contract.
        """
        mode = self.routing_mode if routing_mode is None else routing_mode
        target = (
            self.target_completeness
            if target_completeness is None
            else target_completeness
        )
        if mode not in {"fast", "balanced", "quality"}:
            raise ValueError("routing_mode must be fast, balanced or quality")
        if not math.isfinite(target) or not 0 <= target <= 1:
            raise ValueError("target_completeness must be finite and between 0 and 1")
        key = self._cache_key(
            request, routing_mode=mode, target_completeness=target
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached.expires_at > time.monotonic():
                self._cache.move_to_end(key)
                cached_envelope = cached.envelope
            else:
                cached_envelope = None
                if cached:
                    self._cache_bytes -= self._cache.pop(key).bytes_estimate
                pending = self._inflight.get(key)
                owner = pending is None
                if owner:
                    if len(self._inflight) >= self.max_inflight_requests:
                        raise RuntimeError("market_data_concurrent_request_limit")
                    pending = _InflightRequest()
                    self._inflight[key] = pending
        if cached_envelope is not None:
            # Large DataFrame copies must not hold the shared routing lock.
            return replace(
                cached_envelope,
                frame=cached_envelope.frame.copy(deep=True),
                from_cache=True,
            )
        assert pending is not None
        if not owner:
            if not pending.event.wait(self.inflight_wait_seconds):
                raise TimeoutError("market_data_shared_request_timeout")
            if pending.error is not None:
                raise RuntimeError("market_data_shared_request_failed") from pending.error
            if pending.envelope is None:
                raise RuntimeError("market_data_shared_request_missing_result")
            return replace(
                pending.envelope, frame=pending.envelope.frame.copy(deep=True)
            )
        try:
            outcome = self._fetch_uncached(request, key, mode, target)
            pending.envelope = outcome
            return replace(outcome, frame=outcome.frame.copy(deep=True))
        except Exception as exc:
            pending.error = exc
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
                pending.event.set()

    def _store_cache(
        self, key: str, envelope: MarketDataEnvelope, *, ttl: float
    ) -> None:
        """Bound resident frame memory independently of the number of entries."""
        if ttl <= 0 or self.max_cache_bytes == 0:
            return
        size = int(envelope.frame.memory_usage(index=True, deep=True).sum())
        if size > self.max_cache_bytes:
            return
        snapshot = replace(envelope, frame=envelope.frame.copy(deep=True))
        with self._lock:
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous.bytes_estimate
            self._cache[key] = _CacheRecord(time.monotonic() + ttl, snapshot, size)
            self._cache_bytes += size
            while (
                len(self._cache) > self.max_cache_entries
                or self._cache_bytes > self.max_cache_bytes
            ):
                _, dropped = self._cache.popitem(last=False)
                self._cache_bytes -= dropped.bytes_estimate

    def _fetch_uncached(
        self,
        request: MarketDataRequest,
        key: str,
        mode: Literal["fast", "balanced", "quality"],
        target: float,
    ) -> MarketDataEnvelope:
        with self._lock:
            providers = sorted(
                self._providers.values(),
                key=lambda item: (item.priority, item.provider_id),
            )
        if not providers:
            raise RuntimeError("no market-data providers registered")

        attempted: list[str] = []
        failures: list[str] = []
        expected_rows = (
            int(
                (request.end - request.start).total_seconds()
                // _TIMEFRAME_SECONDS[request.timeframe]
            )
            + 1
        )
        best: MarketDataEnvelope | None = None
        for provider in providers:
            attempted.append(provider.provider_id)
            try:
                provider.limiter.acquire()
                frame, warnings = provider.circuit.call(
                    lambda: self._fetch_and_normalise(provider, request)
                )
                observed = len(frame)
                completeness = min(1.0, observed / max(1, expected_rows))
                if completeness < 0.95:
                    warnings = (
                        *warnings,
                        f"coverage is {completeness:.1%} of expected bars",
                    )
                envelope = MarketDataEnvelope(
                    provider_id=provider.provider_id,
                    fetched_at=datetime.now(timezone.utc),
                    frame=frame,
                    row_count=observed,
                    expected_rows=expected_rows,
                    completeness=completeness,
                    content_sha256=self._hash_frame(frame),
                    warnings=warnings,
                    attempted_providers=tuple(attempted),
                )
                if best is None or envelope.completeness > best.completeness:
                    best = envelope
                # Fast optimizes latency/provider cost. Balanced stops as soon
                # as coverage meets its target. Quality samples remaining
                # healthy providers unless coverage is already perfect.
                if (
                    mode == "fast"
                    or (mode == "balanced" and completeness >= target)
                    or (mode == "quality" and completeness == 1.0)
                ):
                    break
            except (
                CircuitOpenError,
                RateLimitExceeded,
                ValueError,
                TypeError,
                RuntimeError,
            ) as exc:
                failures.append(f"{provider.provider_id}:{type(exc).__name__}")
            except (
                Exception
            ) as exc:  # provider boundary: fail over but do not leak details
                failures.append(f"{provider.provider_id}:{type(exc).__name__}")
        if best is not None:
            notes = best.warnings
            if best.completeness < target:
                notes = (*notes, "coverage below configured quality target")
            best = replace(best, attempted_providers=tuple(attempted), warnings=notes)
            if self.cache_ttl_seconds > 0:
                # Incomplete historical data is rechecked rather than cached
                # for the entire healthy response TTL.
                ttl = self.cache_ttl_seconds
                if best.completeness < target:
                    ttl = min(ttl, 5.0)
                self._store_cache(key, best, ttl=ttl)
            return best
        raise RuntimeError("all market-data providers failed: " + " | ".join(failures))
