"""Bounded market data routing, coalesced reads and per-call quality contracts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time

import pandas as pd
import pytest

from trading_gym.market.data_router import DataProviderRouter, MarketDataRequest


def request(day=0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
    return MarketDataRequest(
        instrument_id="BINANCE:BTCUSDT",
        timeframe="5m",
        start=start,
        end=start + timedelta(minutes=15),
        max_rows=4,
    )


def frame(req, *, rows=4):
    return pd.DataFrame(
        {
            "open": [100.0] * rows,
            "high": [101.0] * rows,
            "low": [99.0] * rows,
            "close": [100.0] * rows,
            "volume": [1000.0] * rows,
        },
        index=pd.date_range(req.start, periods=rows, freq="5min", tz="UTC"),
    )


class Provider:
    def __init__(self, *, gate=None, entered=None, fails=False, rows=4):
        self.calls = 0
        self.guard = threading.Lock()
        self.gate = gate
        self.entered = entered
        self.fails = fails
        self.rows = rows

    def fetch(self, req):
        with self.guard:
            self.calls += 1
        if self.entered:
            self.entered.set()
        if self.gate:
            assert self.gate.wait(3)
        if self.fails:
            raise OSError("provider disconnected")
        return frame(req, rows=self.rows)


def test_equal_concurrent_reads_share_one_provider_call():
    entered, release = threading.Event(), threading.Event()
    provider = Provider(gate=release, entered=entered)
    router = DataProviderRouter(cache_ttl_seconds=30)
    router.register("primary", provider, requests_per_minute=1)
    barrier = threading.Barrier(6)

    def fetch(_):
        barrier.wait(timeout=3)
        return router.fetch(request())

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, i) for i in range(6)]
        assert entered.wait(2)
        time.sleep(0.06)
        assert provider.calls == 1
        release.set()
        outputs = [future.result(timeout=4) for future in futures]
    assert provider.calls == 1
    assert all(out.row_count == 4 for out in outputs)
    outputs[0].frame.iloc[0, 0] = 999
    assert outputs[1].frame.iloc[0, 0] == 100
    assert router.fetch(request()).from_cache is True


def test_shared_request_timeout_does_not_duplicate_upstream_calls():
    entered, release = threading.Event(), threading.Event()
    provider = Provider(gate=release, entered=entered)
    router = DataProviderRouter(inflight_wait_seconds=0.025)
    router.register("primary", provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(router.fetch, request())
        assert entered.wait(2)
        with pytest.raises(TimeoutError, match="market_data_shared_request_timeout"):
            router.fetch(request())
        assert provider.calls == 1
        release.set()
        assert future.result(timeout=4).row_count == 4


def test_distinct_inflight_budget_preserves_existing_work():
    entered, release = threading.Event(), threading.Event()
    provider = Provider(gate=release, entered=entered)
    router = DataProviderRouter(max_inflight_requests=1)
    router.register("primary", provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(router.fetch, request(0))
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match="market_data_concurrent_request_limit"):
            router.fetch(request(1))
        assert provider.calls == 1
        release.set()
        assert future.result(timeout=4).row_count == 4


def test_failed_leader_unblocks_waiters_and_new_request_recovers():
    entered, release = threading.Event(), threading.Event()
    provider = Provider(gate=release, entered=entered, fails=True)
    router = DataProviderRouter(cache_ttl_seconds=0)
    router.register("primary", provider, failure_threshold=3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        lead = pool.submit(router.fetch, request())
        assert entered.wait(2)
        follower = pool.submit(router.fetch, request())
        time.sleep(0.04)
        release.set()
        with pytest.raises(RuntimeError, match="all market-data providers failed"):
            lead.result(timeout=4)
        with pytest.raises(RuntimeError, match="market_data_shared_request_failed"):
            follower.result(timeout=4)
    assert provider.calls == 1
    provider.fails = False
    assert router.fetch(request()).row_count == 4
    assert provider.calls == 2


def test_cache_byte_budget_evicts_oldest_entry():
    normalized, _ = DataProviderRouter._normalise(frame(request()), request())
    one_frame = int(normalized.memory_usage(index=True, deep=True).sum())
    provider = Provider()
    router = DataProviderRouter(max_cache_bytes=one_frame + 1)
    router.register("primary", provider)
    assert router.fetch(request(0)).row_count == 4
    assert router.fetch(request(0)).from_cache is True
    assert provider.calls == 1
    assert router.fetch(request(1)).row_count == 4
    assert router._cache_bytes <= one_frame + 1
    assert len(router._cache) == 1
    assert router.fetch(request(0)).from_cache is False
    assert provider.calls == 3


def test_oversized_frame_is_returned_but_not_cached():
    provider = Provider()
    router = DataProviderRouter(max_cache_bytes=10)
    router.register("primary", provider)
    assert router.fetch(request()).row_count == 4
    assert router._cache_bytes == 0
    assert not router._cache
    assert router.fetch(request()).row_count == 4
    assert provider.calls == 2


def test_excessive_rows_rejected_before_pandas_copy():
    class CopyForbiddenFrame(pd.DataFrame):
        def copy(self, *args, **kwargs):
            raise AssertionError("large frame must not be copied")

    class OversizedProvider:
        def fetch(self, req):
            return CopyForbiddenFrame(index=range(5))

    router = DataProviderRouter(cache_ttl_seconds=0)
    router.register("oversized", OversizedProvider())
    with pytest.raises(RuntimeError, match="all market-data providers failed"):
        router.fetch(request())


def test_per_call_fast_and_quality_modes_never_share_lower_quality_cache():
    sparse, full = Provider(rows=2), Provider(rows=4)
    router = DataProviderRouter(cache_ttl_seconds=60, routing_mode="fast")
    router.register("sparse", sparse, priority=1)
    router.register("full", full, priority=2)
    first = router.fetch(request())
    assert first.provider_id == "sparse"
    assert first.completeness == 0.5
    second = router.fetch(request(), routing_mode="quality")
    assert second.provider_id == "full"
    assert second.completeness == 1.0
    assert full.calls == 1
    assert router.fetch(request(), routing_mode="quality").from_cache
    assert router.fetch(request(), routing_mode="fast").from_cache


@pytest.mark.parametrize(
    "policy",
    [
        {"max_cache_bytes": -1},
        {"max_cache_bytes": 4 * 1024**3 + 1},
        {"max_inflight_requests": 0},
        {"inflight_wait_seconds": 0.0},
        {"inflight_wait_seconds": float("nan")},
        {"cache_ttl_seconds": float("inf")},
    ],
)
def test_invalid_cache_and_concurrency_budgets_are_rejected(policy):
    with pytest.raises(ValueError):
        DataProviderRouter(**policy)


def test_invalid_per_call_routing_never_contacts_a_provider():
    provider = Provider()
    router = DataProviderRouter()
    router.register("primary", provider)
    with pytest.raises(ValueError, match="routing_mode"):
        router.fetch(request(), routing_mode="invalid")
    with pytest.raises(ValueError, match="target_completeness"):
        router.fetch(request(), target_completeness=float("nan"))
    assert provider.calls == 0


def test_registering_healthy_provider_invalidates_stale_quality_cache():
    class Sparse(Provider):
        def fetch(self, req):
            self.calls += 1
            return frame(req, rows=2)

    sparse, full = Sparse(), Provider()
    router = DataProviderRouter(cache_ttl_seconds=60, routing_mode="quality")
    router.register("sparse", sparse, priority=1)
    assert router.fetch(request()).completeness == 0.5
    assert router.fetch(request()).from_cache
    router.register("full", full, priority=2)
    improved = router.fetch(request())
    assert improved.provider_id == "full"
    assert improved.completeness == 1.0
    assert not improved.from_cache
    assert full.calls == 1


def test_old_inflight_topology_cannot_poison_new_provider_cache():
    class Sparse(Provider):
        def __init__(self):
            super().__init__()
            self.enter = threading.Event()
            self.release = threading.Event()

        def fetch(self, req):
            self.calls += 1
            if self.calls == 1:
                self.enter.set()
                assert self.release.wait(3)
            return frame(req, rows=2)

    sparse, full = Sparse(), Provider()
    router = DataProviderRouter(cache_ttl_seconds=60, routing_mode="quality")
    router.register("sparse", sparse, priority=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old_request = pool.submit(router.fetch, request())
        assert sparse.enter.wait(2)
        router.register("full", full, priority=2)
        new_request = router.fetch(request())
        assert new_request.provider_id == "full"
        assert new_request.completeness == 1.0
        sparse.release.set()
        assert old_request.result(timeout=4).completeness == 0.5
    assert router.fetch(request()).from_cache
    assert full.calls == 1
