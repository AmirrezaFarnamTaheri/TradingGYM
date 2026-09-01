"""Strict, bounded loader for partitioned historical market data.

The loader is an admission boundary, not a best-effort convenience reader.  It
rejects unsafe paths, corrupt or partial shard sets, non-finite prices,
conflicting duplicate candles, invalid OHLC envelopes, and unbounded workloads.
Any synthetic spread backfill is explicit in DataFrame provenance metadata.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from trading_gym.common.security import confined_child

logger = logging.getLogger(__name__)

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.=-]{0,63}$")
_REQUIRED_OHLCV = ("open", "high", "low", "close", "volume")
_MAX_FILE_BYTES = 20 * 1024**3


def _validate_component(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not _COMPONENT_RE.fullmatch(normalized) or normalized in {".", ".."}:
        raise ValueError(f"Unsafe {label}")
    return normalized


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_timestamp_index(frame: pd.DataFrame, *, source: str) -> pd.DataFrame:
    if frame.columns.duplicated().any():
        raise ValueError(f"Duplicate columns in {source}")

    result = frame.copy()
    if "timestamp" in result.columns:
        timestamps = pd.to_datetime(result["timestamp"], utc=True, errors="raise")
    elif isinstance(result.index, pd.DatetimeIndex):
        timestamps = pd.to_datetime(result.index, utc=True, errors="raise")
    else:
        candidates = [
            column
            for column in result.columns
            if "timestamp" in str(column).lower() or str(column).lower() in {"time", "datetime"}
        ]
        if len(candidates) != 1:
            raise ValueError(f"A unique timestamp column is required in {source}")
        timestamps = pd.to_datetime(result[candidates[0]], utc=True, errors="raise")

    if timestamps.isna().any():
        raise ValueError(f"Null timestamps in {source}")
    result["timestamp"] = timestamps
    result.index = pd.DatetimeIndex(timestamps, name="timestamp")
    return result


def validate_market_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    default_spread_bps: float | None,
) -> tuple[pd.DataFrame, bool, int]:
    missing_ohlcv = set(_REQUIRED_OHLCV) - set(frame.columns)
    if missing_ohlcv:
        raise ValueError(
            f"Data for {symbol}/{timeframe} missing critical columns: {sorted(missing_ohlcv)}"
        )

    synthetic_spread = False
    if "spread_bps" not in frame.columns:
        if default_spread_bps is None:
            raise ValueError(f"Data for {symbol}/{timeframe} is missing spread_bps")
        if not np.isfinite(default_spread_bps) or default_spread_bps < 0:
            raise ValueError("default_spread_bps must be finite and non-negative")
        frame = frame.copy()
        frame["spread_bps"] = float(default_spread_bps)
        synthetic_spread = True

    numeric_columns = [*_REQUIRED_OHLCV, "spread_bps"]
    for column in numeric_columns:
        try:
            frame[column] = pd.to_numeric(frame[column], errors="raise").astype(np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Non-numeric {column} values in {symbol}/{timeframe}") from exc

    values = frame[numeric_columns].to_numpy(dtype=np.float64, copy=False)
    if not np.isfinite(values).all():
        raise ValueError(f"Non-finite market data in {symbol}/{timeframe}")
    if (frame[["open", "high", "low", "close"]].to_numpy() <= 0).any():
        raise ValueError(f"Prices must be positive in {symbol}/{timeframe}")
    if (frame[["volume", "spread_bps"]].to_numpy() < 0).any():
        raise ValueError(f"Volume and spread must be non-negative in {symbol}/{timeframe}")

    upper = frame[["open", "close", "low"]].max(axis=1)
    lower = frame[["open", "close", "high"]].min(axis=1)
    if (frame["high"] < upper).any() or (frame["low"] > lower).any():
        raise ValueError(f"Invalid OHLC envelope in {symbol}/{timeframe}")

    frame = frame.sort_index(kind="mergesort")
    duplicate_count = int(frame.index.duplicated(keep=False).sum())
    if duplicate_count:
        duplicate_rows = frame.loc[frame.index.duplicated(keep=False), numeric_columns]
        conflicts = duplicate_rows.groupby(level=0, sort=False).nunique(dropna=False)
        if (conflicts > 1).any(axis=None):
            raise ValueError(f"Conflicting duplicate timestamps in {symbol}/{timeframe}")
        frame = frame.loc[~frame.index.duplicated(keep="last")]

    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError(f"Chronology is invalid in {symbol}/{timeframe}")
    return frame, synthetic_spread, duplicate_count


def load_data_from_lake(
    lake_dir: Path,
    symbols: Tuple[str, ...],
    timeframes: Tuple[str, ...],
    *,
    default_spread_bps: float | None = 20.0,
    max_files_per_partition: int = 10_000,
    max_total_bytes_per_partition: int = 20 * 1024**3,
    max_rows_per_partition: int = 10_000_000,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Load complete, validated partitions from ``lake/symbol/timeframe``.

    All parquet files in a requested partition are authoritative.  Failure to
    read any shard rejects the partition; silently training on a partial shard
    set is not permitted.
    """
    root = Path(lake_dir).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"Data lake directory not found or unsafe: {root}")
    root = root.resolve(strict=True)
    if not symbols or not timeframes:
        raise ValueError("At least one symbol and timeframe are required")
    if not 1 <= max_files_per_partition <= 100_000:
        raise ValueError("max_files_per_partition must be between 1 and 100000")
    if not 1 <= max_total_bytes_per_partition <= 100 * 1024**3:
        raise ValueError("max_total_bytes_per_partition is outside the supported range")
    if not 1 <= max_rows_per_partition <= 100_000_000:
        raise ValueError("max_rows_per_partition is outside the supported range")

    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    for raw_symbol in symbols:
        symbol = _validate_component(raw_symbol, "symbol")
        if symbol in result:
            raise ValueError(f"Duplicate symbol request: {symbol}")
        result[symbol] = {}
        for raw_timeframe in timeframes:
            timeframe = _validate_component(raw_timeframe, "timeframe")
            if timeframe in result[symbol]:
                raise ValueError(f"Duplicate timeframe request: {symbol}/{timeframe}")
            try:
                partition_dir = confined_child(
                    root,
                    symbol,
                    timeframe,
                    must_exist=True,
                    allow_symlinks=False,
                )
            except FileNotFoundError as exc:
                raise FileNotFoundError(f"Missing partition: {symbol}/{timeframe}") from exc
            if partition_dir.is_symlink() or not partition_dir.is_dir():
                raise FileNotFoundError(f"Missing or unsafe partition: {symbol}/{timeframe}")

            pending_marker = partition_dir / ".integrity-pending"
            if pending_marker.exists() or pending_marker.is_symlink():
                raise ValueError(f"Partition integrity update is incomplete: {symbol}/{timeframe}")
            parquet_files = sorted(partition_dir.glob("*.parquet"), key=lambda item: item.name)
            if not parquet_files:
                raise ValueError(f"No parquet files found for {symbol}/{timeframe}")
            if len(parquet_files) > max_files_per_partition:
                raise ValueError(f"File-count limit exceeded for {symbol}/{timeframe}")

            total_bytes = 0
            frames: list[pd.DataFrame] = []
            hashes: dict[str, str] = {}
            row_count = 0
            for parquet_file in parquet_files:
                if parquet_file.is_symlink() or not parquet_file.is_file():
                    raise ValueError(f"Unsafe parquet member in {symbol}/{timeframe}")
                size = parquet_file.stat().st_size
                if size > _MAX_FILE_BYTES:
                    raise ValueError(f"Parquet shard exceeds per-file limit: {parquet_file.name}")
                total_bytes += size
                if total_bytes > max_total_bytes_per_partition:
                    raise ValueError(f"Byte limit exceeded for {symbol}/{timeframe}")
                try:
                    frame = pd.read_parquet(parquet_file)
                except Exception as exc:
                    logger.error(
                        "Parquet admission failed symbol=%s timeframe=%s file=%s error_type=%s",
                        symbol,
                        timeframe,
                        parquet_file.name,
                        type(exc).__name__,
                    )
                    raise ValueError(
                        f"Failed to load complete partition {symbol}/{timeframe}"
                    ) from exc
                if frame.empty:
                    raise ValueError(f"Empty parquet shard: {symbol}/{timeframe}/{parquet_file.name}")
                frame = _normalise_timestamp_index(frame, source=parquet_file.name)
                row_count += len(frame)
                if row_count > max_rows_per_partition:
                    raise ValueError(f"Row limit exceeded for {symbol}/{timeframe}")
                frames.append(frame)
                hashes[parquet_file.name] = _hash_file(parquet_file)

            combined = pd.concat(frames, axis=0, ignore_index=False, copy=False)
            combined, synthetic_spread, duplicate_count = validate_market_frame(
                combined,
                symbol=symbol,
                timeframe=timeframe,
                default_spread_bps=default_spread_bps,
            )
            combined.attrs.update(
                {
                    "schema": "ohlcv-v2",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "source_files": tuple(hashes),
                    "source_sha256": hashes,
                    "source_bytes": total_bytes,
                    "synthetic_spread_bps": synthetic_spread,
                    "default_spread_bps": float(default_spread_bps)
                    if synthetic_spread and default_spread_bps is not None
                    else None,
                    "deduplicated_rows": duplicate_count,
                }
            )
            logger.info(
                "Admitted %d rows for %s/%s from %d verified shards",
                len(combined),
                symbol,
                timeframe,
                len(parquet_files),
            )
            result[symbol][timeframe] = combined
    return result


def load_symbol_timeframe(
    lake_dir: Path,
    symbol: str,
    timeframe: str,
    **kwargs,
) -> pd.DataFrame:
    """Load one complete symbol/timeframe partition."""
    data = load_data_from_lake(lake_dir, (symbol,), (timeframe,), **kwargs)
    return data[_validate_component(symbol, "symbol")][_validate_component(timeframe, "timeframe")]


def get_available_partitions(lake_dir: Path) -> Dict[str, list[str]]:
    """Return safe regular-file partitions in either supported directory layout."""
    root = Path(lake_dir).expanduser()
    if root.is_symlink() or not root.is_dir():
        return {}
    root = root.resolve(strict=True)
    partitions: Dict[str, set[str]] = {}

    top_level = [path for path in sorted(root.iterdir()) if path.is_dir() and not path.is_symlink()]
    for first in top_level:
        try:
            first_name = _validate_component(first.name, "partition")
        except ValueError:
            continue
        for second in sorted(first.iterdir()):
            if not second.is_dir() or second.is_symlink():
                continue
            try:
                second_name = _validate_component(second.name, "partition")
            except ValueError:
                continue
            files = [
                path
                for path in second.glob("*.parquet")
                if path.is_file() and not path.is_symlink()
            ]
            if not files:
                continue
            # Production interpretation: first=symbol, second=timeframe.
            partitions.setdefault(first_name, set()).add(second_name)

    return {symbol: sorted(timeframes) for symbol, timeframes in sorted(partitions.items())}
