"""RL absorption tests: episode registry, forward outcomes, ideal-fill oracle."""

import numpy as np
import pytest

from trading_gym.rl.episode_registry import (
    DuplicateEpisodeError,
    EpisodeRegistry,
    make_episode_spec,
)
from trading_gym.rl.forward_outcomes import forward_outcome, ideal_fill_oracle

SHA = "a" * 64


# ---------------------------------------------------------------------------
# Episode registry
# ---------------------------------------------------------------------------


def test_spec_key_is_content_addressed_and_deduped():
    registry = EpisodeRegistry()
    spec_a = make_episode_spec(
        source_sha256=SHA, start_index=0, end_index=500, event_type="gap_up_open"
    )
    assert registry.register(spec_a) is True
    same = make_episode_spec(
        source_sha256=SHA, start_index=0, end_index=500, event_type="gap_up_open"
    )
    assert registry.register(same) is False
    with pytest.raises(DuplicateEpisodeError):
        registry.register_or_raise(same)
    assert len(registry) == 1


def test_key_changes_with_bounds_event_type_and_meta():
    base = dict(source_sha256=SHA, start_index=0, end_index=100)
    k1 = make_episode_spec(event_type="gap_up_open", **base).key
    k2 = make_episode_spec(
        event_type="gap_up_open", source_sha256=SHA, start_index=1, end_index=101
    ).key
    k3 = make_episode_spec(
        event_type="limit_down_close", source_sha256=SHA, start_index=0, end_index=100
    ).key
    k4 = make_episode_spec(
        event_type="gap_up_open",
        source_sha256=SHA,
        start_index=0,
        end_index=100,
        meta={"note": "x"},
    ).key
    assert len({k1, k2, k3, k4}) == 4


def test_by_event_type_grouping():
    registry = EpisodeRegistry()
    bounds = [(0, 50), (50, 100), (100, 150)]
    events = ["gap_up_open", "limit_down_close", "gap_up_open"]
    for et, (a, b) in zip(events, bounds):
        registry.register_or_raise(
            make_episode_spec(
                source_sha256=SHA,
                start_index=a,
                end_index=b,
                event_type=et,
            )
        )
    assert len(registry.by_event_type("gap_up_open")) == 2
    assert len(registry.by_event_type("limit_down_close")) == 1


def test_spec_validation():
    with pytest.raises(ValueError):
        make_episode_spec(
            source_sha256="short", start_index=0, end_index=10, event_type="x"
        )
    with pytest.raises(ValueError):
        make_episode_spec(source_sha256=SHA, start_index=5, end_index=5, event_type="x")
    with pytest.raises(ValueError):
        make_episode_spec(
            source_sha256=SHA, start_index=0, end_index=10, event_type="Bad Type!"
        )


# ---------------------------------------------------------------------------
# Forward outcome labeling
# ---------------------------------------------------------------------------


def _arrays(mid, high=None, low=None):
    mid_a = np.asarray(mid, dtype=float)
    high_a = np.asarray(high, dtype=float) if high is not None else mid_a.copy()
    low_a = np.asarray(low, dtype=float) if low is not None else mid_a.copy()
    return mid_a, high_a, low_a


def test_forward_outcome_take_profit_early_exit_long():
    mid = [100.0, 105.0, 110.0, 111.0, 112.0]
    mid_a, high_a, low_a = _arrays(mid)
    out = forward_outcome(
        prices_mid=mid_a,
        prices_high=high_a,
        prices_low=low_a,
        entry_index=0,
        entry_price=100.0,
        side="long",
        take_profit_price=109.5,
        horizon_bars=10,
    )
    assert out.hit_take_profit and not out.hit_stop_loss
    assert out.bars_scanned == 2  # resolved on the bar at 110
    assert out.horizon_return == pytest.approx(0.10)


def test_forward_outcome_stop_loss_short_side():
    mid = [100.0, 106.0, 107.0]
    mid_a, high_a, low_a = _arrays(mid)
    out = forward_outcome(
        prices_mid=mid_a,
        prices_high=high_a,
        prices_low=low_a,
        entry_index=0,
        entry_price=100.0,
        side="short",
        stop_loss_price=105.5,
        horizon_bars=10,
    )
    assert out.hit_stop_loss and not out.hit_take_profit
    assert out.worst_adverse_excursion <= -5.0 + 1e-9


def test_forward_outcome_excursions_are_signed_correctly_for_shorts():
    mid = [100.0, 95.0, 90.0]
    mid_a, high_a, low_a = _arrays(mid)
    out = forward_outcome(
        prices_mid=mid_a,
        prices_high=high_a,
        prices_low=low_a,
        entry_index=0,
        entry_price=100.0,
        side="short",
        horizon_bars=5,
    )
    # Short trade that never goes underwater: least favourable point (95) is
    # still profitable, so the worst signed excursion stays positive.
    assert out.best_favorable_excursion == pytest.approx(10.0)
    assert out.worst_adverse_excursion == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Ideal-fill oracle (per-bar reposition upper bound)
# ---------------------------------------------------------------------------


def test_oracle_rising_series_compounds_every_bar():
    close = [100.0, 101.0, 102.02]  # +1% each step
    high = [c * 1.01 for c in close]
    low = [c * 0.99 for c in close]
    profit = ideal_fill_oracle(
        prices_close=np.array(close),
        prices_high=np.array(high),
        prices_low=np.array(low),
    )
    # Each bar captures max(1, high/prev_close) = 1.01*1.01 ≈ high/prev.
    expected = (high[1] / close[0]) * (high[2] / close[1])
    assert profit == pytest.approx(expected)


def test_oracle_falling_series_is_flat_without_shorts():
    close = [100.0, 99.0, 98.0]
    high = [c for c in close]
    low = [c * 0.99 for c in close]
    profit = ideal_fill_oracle(
        prices_close=np.array(close),
        prices_high=np.array(high),
        prices_low=np.array(low),
        allow_short=False,
    )
    # long factor per bar: max(1, high[t]/close[t-1]) = max(1, <1) = 1
    assert profit == pytest.approx(1.0)


def test_oracle_short_mode_profits_from_falling_series():
    close = [100.0, 99.0, 98.0]
    high = close
    low = [c * 0.98 for c in close]
    profit = ideal_fill_oracle(
        prices_close=np.array(close),
        prices_high=np.array(high),
        prices_low=np.array(low),
        allow_short=True,
    )
    assert profit > 1.0


def test_oracle_rejects_invalid_envelope():
    with pytest.raises(ValueError):
        ideal_fill_oracle(
            prices_close=np.array([100.0, 100.0]),
            prices_high=np.array([100.0, 100.0]),
            prices_low=np.array([101.0, 100.0]),  # low > high
        )
