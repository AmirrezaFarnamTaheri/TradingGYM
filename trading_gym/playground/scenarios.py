"""AXIOM-1 playground scenarios (ADR-031): deterministic market overlays.

A scenario is the playground's experiment unit: one canonical data window
plus an ordered list of declarative overlays plus a seed. Overlays are
pure functions over the window frame — they never mutate the input — and
every overlay result preserves the OHLCV invariants the rest of the
platform relies on (``strategy_lab.evaluator.validate_market_frame``).

Overlay params are declarative data (``build_overlay``), so a scenario
file is data, not code. Where a parameter is randomised, each Monte-Carlo
repetition derives its own numpy Generator from the scenario seed
(``SeedSequence(seed).spawn``), so repetitions are reproducible from the
scenario definition alone.

Attribution support: every overlay knows which columns it touches
(``Overlay.touched_columns``), letting the runner attribute metric deltas
to individual overlays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from trading_gym.strategy_lab.evaluator import validate_market_frame

__all__ = [
    "Overlay",
    "OverlayError",
    "Scenario",
    "ScenarioWindow",
    "apply_overlays",
    "build_overlay",
    "gap_injection",
    "liquidity_drought",
    "stale_feed",
]

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

_OHLC = ("open", "high", "low", "close")


class OverlayError(ValueError):
    """A scenario, window, or overlay is malformed or violates invariants."""


def _resolve_start(frame: pd.DataFrame, start: int | str | "pd.Timestamp") -> int:
    """Resolve an overlay anchor to an integer index position.

    Accepts an integer position, an ISO timestamp string, or a
    ``pd.Timestamp`` (which must match a bar exactly).
    """

    if isinstance(start, pd.Timestamp):
        if start.tzinfo is None:
            raise OverlayError("overlay start timestamp must be timezone-aware")
        position = int(frame.index.searchsorted(start))
        if position >= len(frame) or frame.index[position] != start:
            raise OverlayError(f"overlay start timestamp {start} is not a bar")
        return position
    if isinstance(start, bool) or not isinstance(start, (int, str)):
        raise OverlayError(f"overlay start must be int or timestamp, got {start!r}")
    if isinstance(start, int):
        if start < 0 or start >= len(frame):
            raise OverlayError(
                f"overlay start index {start} outside frame of {len(frame)} bars"
            )
        return start
    timestamp = pd.Timestamp(start)
    if timestamp.tzinfo is None:
        raise OverlayError("overlay start timestamp must be timezone-aware")
    position = int(frame.index.searchsorted(timestamp))
    if position >= len(frame) or frame.index[position] != timestamp:
        raise OverlayError(f"overlay start timestamp {start!r} is not a bar")
    return position


def _validate_result(frame: pd.DataFrame, base: pd.DataFrame) -> None:
    validate_market_frame(frame)
    if len(frame) != len(base):
        raise OverlayError("overlay changed the row count")
    if not frame.index.equals(base.index):
        raise OverlayError("overlay changed the DatetimeIndex")


def gap_injection(
    frame: pd.DataFrame,
    *,
    kind: str,
    magnitude_pct: float,
    recovery_bars: int,
    start: int | str,
) -> pd.DataFrame:
    """Inject a price shock at ``start`` that linearly recovers over bars.

    ``kind`` is one of ``flash_crash`` (close-led dip with widened band),
    ``gap_up``/``gap_down`` (open gaps with proportional band). The
    recovery is linear in bar index and reaches the base path exactly at
    ``start + recovery_bars``.
    """

    if kind not in ("flash_crash", "gap_up", "gap_down"):
        raise OverlayError(f"unknown gap kind {kind!r}")
    if not isinstance(magnitude_pct, (int, float)) or isinstance(magnitude_pct, bool):
        raise OverlayError("magnitude_pct must be numeric")
    if kind == "flash_crash" and magnitude_pct >= 0:
        raise OverlayError("flash_crash magnitude_pct must be negative")
    if kind in ("gap_up", "gap_down") and magnitude_pct == 0:
        raise OverlayError("gap magnitude_pct must be non-zero")
    if isinstance(recovery_bars, bool) or not isinstance(recovery_bars, int):
        raise OverlayError("recovery_bars must be an integer")
    if recovery_bars < 1:
        raise OverlayError("recovery_bars must be >= 1")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise OverlayError("gap_injection needs a non-empty frame")

    base = frame
    out = frame.copy()
    position = _resolve_start(out, start)
    scale = float(magnitude_pct) / 100.0

    span = min(recovery_bars + 1, len(out) - position)
    decay = np.linspace(1.0, 0.0, num=span)
    if kind == "flash_crash":
        factor = 1.0 + scale * decay
        for column in _OHLC:
            out.iloc[position : position + span, out.columns.get_loc(column)] *= factor
    else:
        open_scale = 1.0 + scale * decay
        close_scale = 1.0 + scale * decay * 0.5
        for column, series_scale in (("open", open_scale), ("close", close_scale)):
            out.iloc[position : position + span, out.columns.get_loc(column)] *= series_scale
        # keep the envelope consistent: recompute the band around OHLC
        inner_low = out.iloc[position : position + span][["open", "close", "low"]].min(axis=1)
        inner_high = out.iloc[position : position + span][["open", "close", "high"]].max(axis=1)
        out.iloc[position : position + span, out.columns.get_loc("high")] = inner_high
        out.iloc[position : position + span, out.columns.get_loc("low")] = inner_low

    _validate_result(out, base)
    return out


def stale_feed(
    frame: pd.DataFrame,
    *,
    duration_bars: int,
    start: int | str,
) -> pd.DataFrame:
    """Freeze the feed: rows after ``start`` repeat the anchor row values.

    Perception-robustness overlay: a strategy must notice it is trading
    on a frozen book and stand down. The anchor bar itself keeps its
    values; the next ``duration_bars - 1`` rows become copies of it.
    """

    if isinstance(duration_bars, bool) or not isinstance(duration_bars, int):
        raise OverlayError("duration_bars must be an integer")
    if duration_bars < 1:
        raise OverlayError("duration_bars must be >= 1")
    base = frame
    out = frame.copy()
    position = _resolve_start(out, start)
    span = min(duration_bars, len(out) - position)
    anchor = out.iloc[position].copy()
    out.iloc[position + 1 : position + span] = anchor
    _validate_result(out, base)
    return out


def liquidity_drought(
    frame: pd.DataFrame,
    *,
    depth_multiplier: float,
    start: int | str,
    duration_bars: int | None = None,
) -> pd.DataFrame:
    """Scale volume down inside a window (execution-layer overlay)."""

    if not isinstance(depth_multiplier, (int, float)) or isinstance(
        depth_multiplier, bool
    ):
        raise OverlayError("depth_multiplier must be numeric")
    if not 0.0 < float(depth_multiplier) <= 1.0:
        raise OverlayError("depth_multiplier must be within (0, 1]")
    base = frame
    out = frame.copy()
    position = _resolve_start(out, start)
    end = len(out) if duration_bars is None else min(len(out), position + int(duration_bars))
    if end <= position:
        raise OverlayError("liquidity window is empty")
    out.iloc[position:end, out.columns.get_loc("volume")] *= float(depth_multiplier)
    _validate_result(out, base)
    return out


@dataclass(frozen=True)
class Overlay:
    """One declarative overlay bound to its pure application function.

    ``Overlay.apply(frame)`` applies with stored params; ``draw_params``
    derives per-repetition values for randomized parameters.
    """

    type: str
    params: Mapping[str, Any]
    fn: Callable[..., pd.DataFrame]
    touched_columns: tuple[str, ...]
    randomize: tuple[str, ...] = ()
    ranges: Mapping[str, tuple[float, float]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.ranges is None:
            object.__setattr__(self, "ranges", {})

    def draw_params(self, generator: np.random.Generator) -> dict[str, Any]:
        """Draw randomized parameters for one Monte-Carlo repetition."""

        if not self.randomize:
            return dict(self.params)
        drawn = dict(self.params)
        for name in self.randomize:
            low, high = self.ranges[name]
            drawn[name] = float(generator.uniform(low, high))
        return drawn

    def apply(self, frame: pd.DataFrame, **overrides: Any) -> pd.DataFrame:
        """Apply with stored (optionally overridden) params to a frame."""

        params = dict(self.params)
        params.update(overrides)
        try:
            return self.fn(frame, **params)
        except TypeError as exc:
            raise OverlayError(f"overlay {self.type!r} params invalid: {exc}") from exc


_OVERLAY_REGISTRY: dict[str, dict[str, Any]] = {
    "gap_injection": {
        "fn": gap_injection,
        "columns": _OHLC,
    },
    "stale_feed": {
        "fn": stale_feed,
        "columns": ("open", "high", "low", "close", "volume", "spread_bps"),
    },
    "liquidity_drought": {
        "fn": liquidity_drought,
        "columns": ("volume",),
    },
}


def build_overlay(spec: Mapping[str, Any]) -> Overlay:
    """Parse one overlay declaration into a validated :class:`Overlay`."""

    if not isinstance(spec, Mapping) or "type" not in spec:
        raise OverlayError("overlay declaration must carry a 'type'")
    overlay_type = str(spec["type"])
    if overlay_type not in _OVERLAY_REGISTRY:
        raise OverlayError(f"unknown overlay type {overlay_type!r}")
    params = dict(spec.get("params") or {})
    if not isinstance(params, dict):
        raise OverlayError("overlay params must be a mapping")
    randomize = tuple(params.get("randomize") or ())
    ranges: dict[str, tuple[float, float]] = {}
    raw_ranges = params.get("ranges") or {}
    if not isinstance(raw_ranges, Mapping):
        raise OverlayError("overlay ranges must be a mapping")
    for name, low_high in raw_ranges.items():
        ranges[str(name)] = (float(low_high[0]), float(low_high[1]))
    # "<param>_range": [low, high] shorthand (roadmap yaml convention)
    for key, value in params.items():
        if key.endswith("_range"):
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise OverlayError(f"{key} must be a [low, high] pair")
            if len(value) != 2:
                raise OverlayError(f"{key} must be a [low, high] pair")
            ranges[key[: -len("_range")]] = (float(value[0]), float(value[1]))
    for name in randomize:
        if name not in ranges:
            raise OverlayError(f"randomized param {name!r} needs a range")
    clean_params = {
        key: value
        for key, value in params.items()
        if key not in ("randomize", "ranges") and not key.endswith("_range")
    }
    entry = _OVERLAY_REGISTRY[overlay_type]
    return Overlay(
        type=overlay_type,
        params=clean_params,
        fn=entry["fn"],
        touched_columns=tuple(entry["columns"]),
        randomize=randomize,
        ranges=ranges,
    )


def apply_overlays(
    frame: pd.DataFrame, overlays: Sequence[Overlay], generator: np.random.Generator
) -> pd.DataFrame:
    """Apply overlays in declaration order; each validates the result."""

    current = frame.copy()
    for overlay in overlays:
        drawn = overlay.draw_params(generator)
        try:
            current = overlay.fn(current, **drawn)
        except TypeError as exc:
            raise OverlayError(
                f"overlay {overlay.type!r} params invalid: {exc}"
            ) from exc
    return current


@dataclass(frozen=True)
class ScenarioWindow:
    """A canonical, validated data window for playground runs."""

    symbol: str
    timeframe: str
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        if self.timeframe not in _TIMEFRAME_SECONDS:
            raise OverlayError(f"unknown timeframe {self.timeframe!r}")
        if not isinstance(self.frame, pd.DataFrame) or len(self.frame) < 2:
            raise OverlayError("scenario window needs at least 2 bars")
        if not isinstance(self.frame.index, pd.DatetimeIndex):
            raise OverlayError("scenario window needs a DatetimeIndex")
        if self.frame.index.tz is None:
            raise OverlayError("scenario window index must be timezone-aware")
        validate_market_frame(self.frame)

    @property
    def bar_seconds(self) -> int:
        return _TIMEFRAME_SECONDS[self.timeframe]


@dataclass(frozen=True)
class Scenario:
    """One playground experiment: window + overlays + seed + repetitions."""

    scenario_id: str
    window: ScenarioWindow
    overlays: tuple[Overlay, ...]
    seed: int
    repetitions: int

    @classmethod
    def parse(cls, spec: Mapping[str, Any]) -> "Scenario":
        if not isinstance(spec, Mapping):
            raise OverlayError("scenario must be a mapping")
        if "scenario_id" not in spec or not str(spec["scenario_id"]).strip():
            raise OverlayError("scenario needs a non-empty scenario_id")
        window_spec = spec.get("window")
        if not isinstance(window_spec, Mapping):
            raise OverlayError("scenario needs a window mapping")
        window = ScenarioWindow(
            symbol=str(window_spec.get("symbol", "BTCUSDT")),
            timeframe=str(window_spec.get("timeframe", "5m")),
            frame=window_spec["frame"],
        )
        overlays = tuple(
            build_overlay(item) for item in (spec.get("overlays") or ())
        )
        seed = spec.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise OverlayError("scenario seed must be an integer")
        repetitions = spec.get("repetitions", 1)
        if isinstance(repetitions, bool) or not isinstance(repetitions, int):
            raise OverlayError("repetitions must be an integer")
        if not 1 <= repetitions <= 10_000:
            raise OverlayError("repetitions must be within [1, 10000]")
        return cls(
            scenario_id=str(spec["scenario_id"]),
            window=window,
            overlays=overlays,
            seed=seed,
            repetitions=repetitions,
        )

    def _generator(self, repetition: int) -> np.random.Generator:
        seed_seq = np.random.SeedSequence(self.seed).spawn(self.repetitions)[repetition]
        return np.random.default_rng(seed_seq)

    def stressed_frame(self, repetition: int = 0) -> pd.DataFrame:
        """The window after one repetition's overlay draws."""

        return apply_overlays(
            self.window.frame, self.overlays, self._generator(repetition)
        )

    def stressed_frames(self) -> list[pd.DataFrame]:
        """All repetitions' stressed windows (Monte-Carlo input set)."""

        return [self.stressed_frame(index) for index in range(self.repetitions)]
