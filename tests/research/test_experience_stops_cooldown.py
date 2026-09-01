"""Experience memory, dynamic stops, re-entry cooldown tests."""

import numpy as np
import pytest

from trading_gym.agent.experience_memory import ExperienceMemory
from trading_gym.risk.dynamic_stops import atr_series, dynamic_stop, ratchet_take_profit
from trading_gym.risk.reentry_cooldown import CooldownConfig, ReentryCooldown


# ---------------------------------------------------------------------------
# Experience memory (ReasoningBank absorption)
# ---------------------------------------------------------------------------


def test_record_and_query_by_tag_overlap_with_recency():
    mem = ExperienceMemory()
    mem.record(context_tags=["btc", "stop_loss"], outcome="loss",
               lesson="tighten stop in high vol", created_at=1000.0)
    mem.record(context_tags=["eth", "take_profit"], outcome="profitable",
               lesson="trail wins", created_at=2000.0)
    hits = mem.query(["btc", "stop_loss"], now=2500.0)
    assert hits and hits[0].lesson.startswith("tighten")


def test_memory_is_bounded_and_never_grows_past_max():
    mem = ExperienceMemory(max_entries=3)
    for i in range(6):
        mem.record(context_tags=[f"tag{i}"], outcome="neutral", lesson=f"l{i}")
    assert len(mem) == 3


def test_empty_tags_rejected_and_json_roundtrip():
    mem = ExperienceMemory()
    with pytest.raises(ValueError):
        mem.record(context_tags=["  "], outcome="neutral", lesson="x")
    entry = mem.record(context_tags=["gold"], outcome="neutral", lesson="g")
    restored = ExperienceMemory.from_json(mem.to_json())
    assert restored.query(["gold"])[0].memory_id == entry.memory_id


def test_query_without_overlap_returns_empty():
    mem = ExperienceMemory()
    mem.record(context_tags=["gold"], outcome="neutral", lesson="g")
    assert mem.query(["silver"]) == []


# ---------------------------------------------------------------------------
# Dynamic stops + ratchet TP (FenixAI/AITD absorptions)
# ---------------------------------------------------------------------------


def test_atr_series_wilder_smoothing_basic():
    highs = [11, 11, 12, 12, 13]
    lows = [10, 10, 10, 11, 11]
    closes = [10.5, 10.5, 11.5, 11.2, 12.4]
    out = atr_series(highs, lows, closes, period=2)
    assert len(out) == len(closes)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] > 0


def test_dynamic_stop_respects_timeframe_floor():
    # Tiny ATR: floor should dominate.
    stop = dynamic_stop(side="long", entry_price=100.0, atr_value=0.01,
                        multiplier=1.0, timeframe_floor=0.8)
    assert stop == pytest.approx(99.2)
    short = dynamic_stop(side="short", entry_price=100.0, atr_value=0.01,
                         multiplier=1.0, timeframe_floor=0.8)
    assert short == pytest.approx(100.8)


def test_dynamic_stop_validation():
    with pytest.raises(ValueError):
        dynamic_stop(side="hold", entry_price=100.0, atr_value=1.0)
    with pytest.raises(ValueError):
        dynamic_stop(side="long", entry_price=-1.0, atr_value=1.0)


def test_ratchet_take_profit_only_advances_long():
    tiers = [101.0, 103.0, 105.0]
    # Price at 102 banks only the 101 tier.
    locked, tp = ratchet_take_profit(
        side="long", entry_price=100.0, current_price=102.0,
        previously_locked=100.0, tiers=tiers,
    )
    assert (locked, tp) == (101.0, 101.0)
    # Price runs to 104: next tier banks; protection never retreats.
    locked2, tp2 = ratchet_take_profit(
        side="long", entry_price=100.0, current_price=104.0,
        previously_locked=locked, tiers=tiers,
    )
    assert (locked2, tp2) == (103.0, 103.0)
    # Pullback below the lock cannot un-bank it.
    locked3, _ = ratchet_take_profit(
        side="long", entry_price=100.0, current_price=102.0,
        previously_locked=locked2, tiers=tiers,
    )
    assert locked3 == locked2


def test_ratchet_take_profit_short_direction():
    tiers = [97.0, 96.0, 95.0]
    locked, tp = ratchet_take_profit(
        side="short", entry_price=100.0, current_price=96.5,
        previously_locked=100.0, tiers=tiers,
    )
    assert (locked, tp) == (97.0, 97.0)
    locked2, _ = ratchet_take_profit(
        side="short", entry_price=100.0, current_price=95.5,
        previously_locked=locked, tiers=tiers,
    )
    assert locked2 == 96.0



# ---------------------------------------------------------------------------
# Re-entry cooldown (prism-insight absorption)
# ---------------------------------------------------------------------------


def test_cooldown_asymmetric_by_exit_kind():
    cfg = CooldownConfig(bars_after_stop_loss=12, bars_after_take_profit=3,
                         bars_after_manual_exit=1)
    cd = ReentryCooldown(config=cfg)
    cd.on_exit("stop_loss", bar_index=0)
    for _ in range(12):
        if cd.may_enter():
            break
        cd.on_bar()
    assert cd.may_enter()

    cd2 = ReentryCooldown(config=cfg)
    cd2.on_exit("take_profit", bar_index=0)
    steps = 0
    while not cd2.may_enter():
        cd2.on_bar()
        steps += 1
    assert steps == 3 < 12


def test_cooldown_unknown_kind_rejected():
    cd = ReentryCooldown()
    with pytest.raises(ValueError):
        cd.on_exit("vibes", bar_index=0)
