from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from trading_gym.adapters.observation_recorder import (
    ObservationProfile,
    capture,
    persist,
    coverage,
)

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


class ReadOnlyFixture:
    def __init__(self):
        self.calls = []
        self.ticker = SimpleNamespace(
            symbol="BTC",
            buy_price=Decimal("101"),
            sell_price=Decimal("100"),
            buy_max=Decimal("5"),
            sell_max=Decimal("5"),
            active=True,
        )
        self.info = SimpleNamespace(
            symbol="BTC",
            exchange_fee=Decimal(".001"),
            usdt_min_trade=Decimal("10"),
            usdt_max_trade=Decimal("1000"),
            irt_min_trade=Decimal("100"),
            irt_max_trade=Decimal("10000"),
            irt_fee=Decimal("0"),
            tether_fee=Decimal("0"),
        )

    def get_ticker(self, symbol):
        self.calls.append(("ticker", symbol))
        return self.ticker

    def get_coin_info(self, symbol, side):
        self.calls.append(("limits", symbol, side.value))
        return self.info

    def __getattr__(self, name):
        raise AssertionError(f"unexpected non-read capability: {name}")


def profile():
    return ObservationProfile(
        "fixture-venue",
        "fixture-v1",
        "BTC",
        "USDT",
        mapping_reference="fixture-contract-v1",
        fee_unit="fraction",
    )


def observed(client=None, **kwargs):
    return capture(
        client or ReadOnlyFixture(),
        profile(),
        observed_quote_currency="USDT",
        source_event_time=kwargs.pop("source_event_time", NOW - timedelta(seconds=1)),
        event_time_source="fixture-event-time",
        now=lambda: NOW,
        monotonic=kwargs.pop("monotonic", lambda: 10.0),
        **kwargs,
    )


def test_current_adapter_unknown_timestamp_stays_unknown():
    client = ReadOnlyFixture()
    row = capture(client, profile(), now=lambda: NOW, monotonic=lambda: 10.0)
    assert row["source_event_at_utc"] is None
    assert row["event_time_source"] == "not_supplied_by_adapter"
    assert row["admission"] == "modeled_cost_required"
    assert "source_event_time_unknown" in row["reasons"]
    assert "quote_currency_unverified" in row["reasons"]
    assert client.calls == [
        ("ticker", "BTC"),
        ("limits", "BTC", "buy"),
        ("limits", "BTC", "sell"),
    ]


@pytest.mark.parametrize(
    "change, reason",
    [
        ("inverted", "invalid_or_inverted_quote"),
        ("nan", "invalid_or_inverted_quote"),
        ("missing-limit", "missing_or_invalid_limits:buy"),
        ("inactive", "market_inactive_or_unknown"),
        ("wrong-symbol", "instrument_mismatch"),
    ],
)
def test_invalid_market_fields_are_downgraded(change, reason):
    client = ReadOnlyFixture()
    if change == "inverted":
        client.ticker.sell_price = Decimal("102")
    if change == "nan":
        client.ticker.buy_price = Decimal("NaN")
    if change == "missing-limit":
        client.info.usdt_min_trade = None
    if change == "inactive":
        client.ticker.active = False
    if change == "wrong-symbol":
        client.ticker.symbol = "ETH"
    row = observed(client)
    assert row["admission"] == "modeled_cost_required" and reason in row["reasons"]


def test_stale_future_mismatched_units_and_delays_are_explicit():
    assert (
        "stale_quote"
        in observed(source_event_time=NOW - timedelta(seconds=31))["reasons"]
    )
    assert (
        "source_event_time_in_future"
        in observed(source_event_time=NOW + timedelta(seconds=1))["reasons"]
    )
    ticks = iter([0.0, 11.0])
    assert (
        "read_duration_exceeded" in observed(monotonic=lambda: next(ticks))["reasons"]
    )
    row = capture(
        ReadOnlyFixture(),
        profile(),
        observed_quote_currency="IRT",
        source_event_time=NOW,
        event_time_source="fixture",
        now=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    assert "quote_currency_mismatch" in row["reasons"]
    with pytest.raises(ValueError, match="provenance"):
        capture(ReadOnlyFixture(), profile(), source_event_time=NOW)


def test_coverage_admission_and_immutable_bounds(tmp_path):
    valid = observed()
    assert valid["admission"] == "research_observation"
    assert (
        valid["own_order_fills_observed"] is False and valid["live_authority"] is False
    )
    stale = observed(source_event_time=NOW - timedelta(seconds=100))
    summary = coverage([valid, stale])
    assert summary["admitted"] == 1 and summary["downgrade_reasons"] == {
        "stale_quote": 1
    }
    assert summary["admitted_spread_bps_median"] > 0
    path = persist(tmp_path, valid, max_records=2)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        persist(tmp_path, valid, max_records=2)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="record limit"):
        persist(tmp_path, stale, max_records=1)
