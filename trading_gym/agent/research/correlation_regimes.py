"""Causal, read-only cross-asset correlation-regime research.

Adapts Vibe-Trading's pair-density and hysteresis method, but requires exact
UTC observation timestamps. Undefined correlations stay UNKNOWN rather than
being counted as uncorrelated. Never changes weights or authorizes trades.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
import math
import re

import numpy as np
import pandas as pd

_SHA = re.compile(r"^[0-9a-f]{64}$")
_ASSET = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,79}$")
_MAX_ASSETS = 12
_MAX_BARS = 3000


def _threshold(v: object, name: str) -> float:
    if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1:
        raise ValueError(f"invalid {name}")
    return float(v)


def _positive_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:  # noqa: E721
        raise ValueError(f"invalid {label}")
    return value


def _seal(data: object) -> str:
    return hashlib.sha256(
        json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def classify_density_timeline(
    densities: Sequence[float | None],
    *,
    smooth_window: int = 3,
    enter_threshold: float = 0.65,
    exit_threshold: float = 0.45,
) -> dict[str, list[float | str | None]]:
    """Trailing mean + Schmitt trigger; UNKNOWN never becomes 'diverse'.

    UNKNOWN bars are surfaced separately. The internal last-known regime is
    preserved only for future *known* observations, not published as current.
    """
    smooth = _positive_int(smooth_window, "smoothing window", 1, 250)
    enter = _threshold(enter_threshold, "enter threshold")
    exit_ = _threshold(exit_threshold, "exit threshold")
    if enter <= exit_:
        raise ValueError("exit_threshold must be below enter_threshold")
    if not isinstance(densities, (list, tuple)) or len(densities) > _MAX_BARS:
        raise ValueError("density series must be bounded")
    validated: list[float | None] = []
    for value in densities:
        validated.append(None if value is None else _threshold(value, "density"))
    smoothed: list[float | None] = []
    regimes: list[str] = []
    latent = False
    for i in range(len(validated)):
        window = validated[max(0, i + 1 - smooth) : i + 1]
        if len(window) < smooth or any(x is None for x in window):
            smoothed.append(None)
            regimes.append("unknown")
            continue
        value = math.fsum(window) / smooth  # fully trailing, never centered
        smoothed.append(value)
        if not latent and value >= enter:
            latent = True
        elif latent and value <= exit_:
            latent = False
        regimes.append("fused" if latent else "diverse")
    return {"smoothed": smoothed, "regime": regimes}


def correlation_regimes(
    returns: pd.DataFrame,
    *,
    source_sha256: str,
    as_of_unix: int,
    corr_window: int = 20,
    edge_threshold: float = 0.5,
    smooth_window: int = 3,
    enter_threshold: float = 0.65,
    exit_threshold: float = 0.45,
) -> dict:
    """Evaluate closed-bar aligned returns without fetching or inventing data.

    The source fingerprint is caller supplied, not external authentication.
    Pair correlations are unknown when any contributing asset lacks a valid
    rolling window. A fused episode describes correlation, not trade alpha.
    """
    if type(source_sha256) is not str or not _SHA.fullmatch(  # noqa: E721
        source_sha256
    ):
        raise ValueError("missing canonical source SHA-256")
    if type(as_of_unix) is not int or not 0 <= as_of_unix <= 10**11:  # noqa: E721
        raise ValueError("invalid as_of timestamp")
    window = _positive_int(corr_window, "correlation window", 3, 250)
    threshold = _threshold(edge_threshold, "edge threshold")
    if threshold <= 0:
        raise ValueError("edge threshold must be positive")
    if not isinstance(returns, pd.DataFrame):
        raise ValueError("returns must be a DataFrame")
    size, assets = returns.shape
    if not 3 <= size <= _MAX_BARS or not 2 <= assets <= _MAX_ASSETS:
        raise ValueError("return matrix outside row/asset bounds")
    if size * assets * assets * window > 12_000_000:
        raise ValueError("regime evaluation work budget exceeded")
    if not isinstance(returns.index, pd.DatetimeIndex):
        raise ValueError("market timestamps require datetime index")
    if returns.index.tz is None:
        raise ValueError("market timestamps must be timezone aware")
    if (
        returns.index.hasnans
        or not returns.index.is_unique
        or not returns.index.is_monotonic_increasing
    ):
        raise ValueError("market timestamps must be unique and increasing")
    if any(t.microsecond or t.nanosecond for t in returns.index):
        raise ValueError("market timestamps must be second-aligned")
    if int(returns.index[-1].value) > as_of_unix * 1_000_000_000:
        raise ValueError("future market return outside as_of")
    columns = list(returns.columns)
    if not all(
        type(x) is str and _ASSET.fullmatch(x) for x in columns  # noqa: E721
    ) or len(columns) != len(set(columns)):
        raise ValueError("invalid or duplicate market asset identity")
    matrix = returns.loc[:, sorted(columns)]
    try:
        values = matrix.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("nonfinite or nonnumeric market returns") from exc
    if (
        np.isinf(values).any()
        or (np.isfinite(values) & ((values < -1) | (values > 100))).any()
    ):
        raise ValueError("nonfinite or implausible market returns")
    observed = [int(t.timestamp()) for t in matrix.index]
    dense: list[float | None] = [None] * size
    counts: list[int | None] = [None] * size
    total_pairs = assets * (assets - 1) // 2
    mask = np.triu_indices(assets, k=1)
    for end in range(window, size + 1):
        block = values[end - window : end, :]
        if not np.isfinite(block).all():
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            pair = np.corrcoef(block, rowvar=False)[mask]
        if not np.isfinite(pair).all():
            continue  # undefined pairs are missing evidence, NOT non-edges
        count = int(np.count_nonzero(np.abs(pair) >= threshold))
        counts[end - 1] = count
        dense[end - 1] = count / total_pairs
    series = classify_density_timeline(
        dense,
        smooth_window=smooth_window,
        enter_threshold=enter_threshold,
        exit_threshold=exit_threshold,
    )
    # JSON null explicitly records unknown returns; never forward-fill them.
    material = [[None if math.isnan(x) else float(x) for x in row] for row in values]
    report = {
        "schema": "tradinggym.correlation-regimes.v1",
        "source_sha256": source_sha256,
        "input_sha256": _seal(
            {"asset_ids": sorted(columns), "timestamps": observed, "returns": material}
        ),
        "as_of_unix": as_of_unix,
        "timestamps_unix": observed,
        "edge_density": dense,
        "edge_count": counts,
        "regime": series["regime"],
        "smoothed_density": series["smoothed"],
        "unknown_bars": series["regime"].count("unknown"),
        "non_authoritative": True,
        "execution_authority_granted": False,
        "decision_quality_calibrated": False,
    }
    report["report_sha256"] = _seal(report)
    return report
