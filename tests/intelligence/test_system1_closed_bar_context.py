"""Source-separated, point-in-time market context for fast models."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from trading_gym.agent.intelligence.system1_context import build_system1_packet
from trading_gym.agent.intelligence.types import MarketContext
from trading_gym.agent.intelligence.wiring import build_market_context


def _env(*, completed=None):
    state = SimpleNamespace(
        live_mode=True,
        price=np.array([108.0]),
        equity=1200.0,
        position=0.25,
        t=0,
        price_history=[70.0, 150.0, 90.0, 101.0],
        equity_history=[1400.0, 1200.0],
        _margin_level=lambda _price: 2.0,
    )
    if completed is not None:
        state.latest_closed_bar_closes = completed
    return state


def test_live_market_uses_closed_bar_series_not_quote_update_returns():
    # The quote stream is deliberately inconsistent with the OHLCV bars.
    context = build_market_context(
        _env(completed=(100.0, 102.0, 104.0)), symbol="BTCUSDT"
    )
    assert context["last_close"] == 108.0  # current tradable mark
    assert context["prev_close"] == 104.0  # last fully closed bar
    assert context["recent_returns"] == pytest.approx(
        (104.0 / 102.0 - 1, 102.0 / 100.0 - 1)
    )
    assert context["return_basis"] == "completed_bars"


def test_old_live_env_labels_quote_returns_instead_of_claiming_candle_returns():
    context = build_market_context(_env(), symbol="BTCUSDT")
    assert context["return_basis"] == "mark_updates"
    assert context["recent_returns"][0] == pytest.approx(108 / 101 - 1)


def test_live_context_packet_names_its_price_and_return_sampling():
    data = build_market_context(_env(completed=(100.0, 101.0, 102.0)), symbol="BTCUSDT")
    data["market_evidence"] = {
        "bar_open_unix": 1000,
        "bar_seconds": 300,
        "source_sha256": "a" * 64,
    }
    packet = json.loads(build_system1_packet(MarketContext(**data), now=lambda: 1350))
    assert packet["market"]["price_basis"] == "live_mark_quote"
    assert packet["market"]["recent_returns_basis"] == "completed_bars"
    assert packet["market"]["last_close"] == 108.0
    assert packet["market"]["prev_close"] == 102.0


@pytest.mark.parametrize("bad", [(100.0, float("nan")), (100.0, -1.0)])
def test_nonfinite_or_negative_completed_bars_never_enter_context(bad):
    with pytest.raises(ValueError):
        build_market_context(_env(completed=bad), symbol="BTCUSDT")


def test_historical_context_packet_labels_source_as_historical():
    context = MarketContext(
        symbol="BTCUSDT",
        position_units=0,
        equity=1000,
        margin_level=None,
        last_close=103,
        prev_close=102,
        recent_returns=(0.01,),
        return_basis="historical_bars",
    )
    packet = json.loads(build_system1_packet(context))
    assert packet["market"]["recent_returns_basis"] == "historical_bars"
    assert packet["market"]["price_basis"] == "historical_close"


@pytest.mark.parametrize("basis", ["unspecified", "mark_updates", "historical_bars"])
def test_verified_live_bar_cannot_relabel_non_bar_returns(basis: str):
    context = MarketContext(
        symbol="BTCUSDT",
        position_units=0,
        equity=1000,
        margin_level=None,
        last_close=102,
        prev_close=101,
        recent_returns=(0.02, 0.01),
        return_basis=basis,
        is_live=True,
        market_evidence={
            "bar_open_unix": 1000,
            "bar_seconds": 300,
            "source_sha256": "a" * 64,
        },
    )
    with pytest.raises(PermissionError, match="live_completed_bar"):
        build_system1_packet(context, now=lambda: 1350)


def test_completed_bar_history_requires_a_prior_closed_candle():
    context = MarketContext(
        symbol="BTCUSDT",
        position_units=0,
        equity=1000,
        margin_level=None,
        last_close=102,
        prev_close=None,
        recent_returns=(),
        return_basis="completed_bars",
        is_live=True,
        market_evidence={
            "bar_open_unix": 1000,
            "bar_seconds": 300,
            "source_sha256": "a" * 64,
        },
    )
    with pytest.raises(PermissionError, match="live_completed_bar"):
        build_system1_packet(context, now=lambda: 1350)
