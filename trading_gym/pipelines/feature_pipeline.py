"""Deterministic, fail-closed feature artifact pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from filelock import FileLock
from tqdm import tqdm

from trading_gym.common.config import AppConfig
from trading_gym.common.persistence import (
    _fsync_directory,
    atomic_write_json,
    atomic_write_text,
)
from trading_gym.common.security import bounded_json_dumps, confined_child
from trading_gym.data.data_loader import (
    _normalise_timestamp_index,
    load_symbol_timeframe,
    validate_market_frame,
)
from trading_gym.features.feature_assembler import FeatureAssembler

logger = logging.getLogger(__name__)

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.=-]{0,63}$")
_MAX_SOURCE_BYTES = 20 * 1024**3
_MAX_COMBINATIONS = 10_000
_MAX_FEATURE_COLUMNS = 10_000
_MAX_FEATURE_ROWS = 100_000_000


def _safe_component(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not _COMPONENT_RE.fullmatch(normalized) or normalized in {".", ".."}:
        raise ValueError(f"Unsafe {label}")
    return normalized


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_legacy_frame(
    frame: pd.DataFrame, *, source: Path, symbol: str, timeframe: str
) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [str(column).strip().lower() for column in result.columns]
    if len(set(result.columns)) != len(result.columns):
        raise ValueError(f"Duplicate columns in legacy source {source.name}")

    timestamp_column = next(
        (
            name
            for name in ("timestamp", "datetime", "date", "time")
            if name in result.columns
        ),
        None,
    )
    if timestamp_column is not None:
        values = result[timestamp_column]
        if pd.api.types.is_numeric_dtype(values):
            numeric = pd.to_numeric(values, errors="raise").astype(np.float64)
            if not np.isfinite(numeric.to_numpy()).all():
                raise ValueError("Legacy timestamps must be finite")
            magnitude = (
                float(np.max(np.abs(numeric.to_numpy()))) if len(numeric) else 0.0
            )
            if magnitude >= 1e14:
                raise ValueError("Legacy timestamp unit is unsupported or ambiguous")
            unit = "ms" if magnitude >= 1e11 else "s"
            result["timestamp"] = pd.to_datetime(
                numeric, unit=unit, utc=True, errors="raise"
            )
        else:
            result["timestamp"] = pd.to_datetime(values, utc=True, errors="raise")
    elif isinstance(result.index, pd.DatetimeIndex):
        result["timestamp"] = pd.to_datetime(result.index, utc=True, errors="raise")
    else:
        raise ValueError(f"Legacy source {source.name} lacks an explicit timestamp")

    result = _normalise_timestamp_index(result, source=source.name)
    result, synthetic_spread, duplicate_count = validate_market_frame(
        result,
        symbol=symbol,
        timeframe=timeframe,
        default_spread_bps=20.0,
    )
    result.attrs.update(
        {
            "source_kind": "legacy-file",
            "source_path": source.name,
            "source_sha256": _sha256_file(source),
            "synthetic_spread_bps": synthetic_spread,
            "deduplicated_rows": duplicate_count,
        }
    )
    return result


def load_raw_data(
    symbol: str,
    timeframe: str,
    raw_dir: str | Path,
    lake_dir: str | Path,
) -> pd.DataFrame:
    """Load exactly one authoritative source and validate it as OHLCV.

    The current partitioned lake layout is preferred only when it is the sole
    matching source.  Coexisting legacy files are rejected as ambiguous rather
    than selected by an undocumented precedence order.
    """
    symbol = _safe_component(symbol, "symbol")
    timeframe = _safe_component(timeframe, "timeframe")
    raw_root = Path(raw_dir).expanduser()
    lake_root = Path(lake_dir).expanduser()
    for root, label in ((raw_root, "raw_dir"), (lake_root, "lake_dir")):
        if root.exists() and root.is_symlink():
            raise ValueError(f"{label} must not be a symlink")

    partition = lake_root / symbol / timeframe
    partition_present = (
        partition.is_dir()
        and not partition.is_symlink()
        and any(
            path.is_file() and not path.is_symlink()
            for path in partition.glob("*.parquet")
        )
    )

    candidates: list[Path] = []
    for root in (lake_root, raw_root):
        if not root.is_dir():
            continue
        root = root.resolve(strict=True)
        names = (
            f"{symbol}_{timeframe}.parquet",
            f"{symbol.lower()}_{timeframe}.parquet",
            f"{symbol}_{timeframe}.csv",
            f"{symbol.lower()}_{timeframe}.csv",
        )
        for name in names:
            candidate = confined_child(root, name, allow_symlinks=False)
            if candidate.exists():
                if candidate.is_symlink() or not candidate.is_file():
                    raise ValueError("Legacy data source must be a regular file")
                candidates.append(candidate)
        symbol_dir = confined_child(root, symbol, allow_symlinks=False)
        if symbol_dir.is_dir() and not symbol_dir.is_symlink():
            for suffix in ("parquet", "csv"):
                candidate = confined_child(
                    symbol_dir, f"{timeframe}.{suffix}", allow_symlinks=False
                )
                if candidate.exists():
                    if candidate.is_symlink() or not candidate.is_file():
                        raise ValueError("Legacy data source must be a regular file")
                    candidates.append(candidate)

    # Deduplicate path aliases after resolution.
    unique_candidates = sorted(
        {candidate.resolve(strict=True) for candidate in candidates}
    )
    source_count = int(partition_present) + len(unique_candidates)
    if source_count == 0:
        raise FileNotFoundError(f"No data source found for {symbol}/{timeframe}")
    if source_count > 1:
        raise ValueError(f"Ambiguous data sources found for {symbol}/{timeframe}")

    if partition_present:
        return load_symbol_timeframe(lake_root, symbol, timeframe)

    source = unique_candidates[0]
    if source.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError("Legacy data source exceeds the byte limit")
    try:
        if source.suffix.lower() == ".parquet":
            frame = pd.read_parquet(source)
        elif source.suffix.lower() == ".csv":
            frame = pd.read_csv(source)
        else:  # pragma: no cover - candidates are fixed above
            raise ValueError("Unsupported legacy source format")
    except Exception as exc:
        logger.error(
            "Legacy data read failed file=%s error_type=%s",
            source.name,
            type(exc).__name__,
        )
        raise ValueError(f"Unable to read legacy data source {source.name}") from exc
    if frame.empty:
        raise ValueError(f"Legacy data source is empty: {source.name}")
    return _normalise_legacy_frame(
        frame, source=source, symbol=symbol, timeframe=timeframe
    )


def _feature_config_hash(cfg: AppConfig) -> str:
    feature_config = (
        cfg.features.model_dump(mode="json")
        if hasattr(cfg.features, "model_dump")
        else vars(cfg.features)
    )
    encoded = bounded_json_dumps(feature_config, max_bytes=2 * 1024 * 1024).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _validate_features(
    features: pd.DataFrame, raw: pd.DataFrame, *, symbol: str, timeframe: str
) -> pd.DataFrame:
    if features.empty:
        raise ValueError(f"No features generated for {symbol}/{timeframe}")
    if len(features) != len(raw) or not features.index.equals(raw.index):
        raise ValueError(
            f"Feature rows/index diverge from source for {symbol}/{timeframe}"
        )
    if (
        len(features) > _MAX_FEATURE_ROWS
        or len(features.columns) > _MAX_FEATURE_COLUMNS
    ):
        raise ValueError(
            f"Feature artifact exceeds shape limits for {symbol}/{timeframe}"
        )
    if features.columns.duplicated().any():
        raise ValueError(f"Duplicate feature columns for {symbol}/{timeframe}")
    if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in features.dtypes):
        raise ValueError(f"Non-numeric feature columns for {symbol}/{timeframe}")
    if not np.isfinite(features.to_numpy(dtype=np.float64, copy=False)).all():
        raise ValueError(f"Non-finite feature values for {symbol}/{timeframe}")
    return features


def _publish_features(
    output: Path,
    features: pd.DataFrame,
    *,
    source: pd.DataFrame,
    config_hash: str,
    symbol: str,
    timeframe: str,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.exists() and (output.is_symlink() or not output.is_file()):
        raise ValueError("Feature output path is unsafe")
    marker = output.with_suffix(output.suffix + ".integrity-pending")
    receipt_path = output.with_suffix(output.suffix + ".manifest.json")
    lock = FileLock(str(output) + ".lock")
    operation_id = uuid.uuid4().hex
    with lock:
        atomic_write_text(marker, operation_id + "\n", max_bytes=128)
        temp = output.parent / f".{output.name}.{operation_id}.tmp.parquet"
        try:
            features.reset_index(names="timestamp").to_parquet(
                temp,
                compression="snappy",
                index=False,
            )
            if temp.is_symlink() or not temp.is_file():
                raise ValueError("Feature writer did not create a regular file")
            with temp.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temp, output)
            _fsync_directory(output.parent)
            digest = _sha256_file(output)
            receipt = {
                "schema_version": 1,
                "artifact": output.name,
                "sha256": digest,
                "size_bytes": output.stat().st_size,
                "rows": len(features),
                "columns": list(features.columns),
                "symbol": symbol,
                "timeframe": timeframe,
                "start": features.index.min().isoformat(),
                "end": features.index.max().isoformat(),
                "feature_config_sha256": config_hash,
                "source_sha256": source.attrs.get("source_sha256"),
                "source_kind": source.attrs.get("source_kind", "partitioned-lake"),
            }
            # Validate receipt JSON before committing it.
            json.loads(bounded_json_dumps(receipt, max_bytes=4 * 1024 * 1024))
            atomic_write_json(receipt_path, receipt)
            marker.unlink()
            _fsync_directory(output.parent)
            return receipt
        finally:
            if temp.exists():
                temp.unlink()


def run_feature_pipeline(
    cfg: AppConfig,
    symbols: Optional[list[str]] = None,
    *,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    """Generate every requested feature artifact or fail the run.

    ``continue_on_error`` exists for explicit diagnostics only.  The default
    prevents a partial feature directory from being mistaken for a complete run.
    """
    assembler = FeatureAssembler(cfg.features)
    features_root = Path(cfg.data.features_dir or "trading_gym/data").expanduser()
    if features_root.exists() and features_root.is_symlink():
        raise ValueError("features_dir must not be a symlink")
    features_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    features_root = features_root.resolve(strict=True)
    raw_dir = Path(cfg.data.raw_dir or "data/raw")
    lake_dir = Path(cfg.data.lake_dir or "data/lake")

    selected_symbols = [
        _safe_component(item, "symbol")
        for item in (symbols if symbols is not None else cfg.data.symbols)
    ]
    selected_timeframes = [
        _safe_component(item, "timeframe") for item in cfg.data.timeframes
    ]
    if not selected_symbols or not selected_timeframes:
        raise ValueError("Feature pipeline requires at least one symbol and timeframe")
    if len(set(selected_symbols)) != len(selected_symbols) or len(
        set(selected_timeframes)
    ) != len(selected_timeframes):
        raise ValueError("Duplicate symbol or timeframe in feature request")
    total = len(selected_symbols) * len(selected_timeframes)
    if total > _MAX_COMBINATIONS:
        raise ValueError("Feature combination limit exceeded")

    config_hash = _feature_config_hash(cfg)
    receipts: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with tqdm(total=total, desc="Generating Features") as progress:
        for symbol in selected_symbols:
            for timeframe in selected_timeframes:
                progress.set_description(f"Processing {symbol} {timeframe}")
                try:
                    raw = load_raw_data(symbol, timeframe, raw_dir, lake_dir)
                    features = _validate_features(
                        assembler.process(raw, symbol),
                        raw,
                        symbol=symbol,
                        timeframe=timeframe,
                    )
                    output = confined_child(
                        features_root,
                        f"{symbol}_{timeframe}_features.parquet",
                        allow_symlinks=False,
                    )
                    receipts.append(
                        _publish_features(
                            output,
                            features,
                            source=raw,
                            config_hash=config_hash,
                            symbol=symbol,
                            timeframe=timeframe,
                        )
                    )
                except Exception as exc:
                    logger.error(
                        "Feature pipeline combination failed symbol=%s timeframe=%s error_type=%s",
                        symbol,
                        timeframe,
                        type(exc).__name__,
                    )
                    failures.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "error_type": type(exc).__name__,
                        }
                    )
                    if not continue_on_error:
                        raise RuntimeError(
                            f"Feature pipeline failed for {symbol}/{timeframe}"
                        ) from exc
                finally:
                    progress.update(1)

    report = {
        "schema_version": 1,
        "requested": total,
        "published": len(receipts),
        "failed": len(failures),
        "feature_config_sha256": config_hash,
        "receipts": receipts,
        "failures": failures,
    }
    atomic_write_json(features_root / "feature-run.json", report)
    if failures:
        raise RuntimeError(f"Feature pipeline completed with {len(failures)} failures")
    return report


def generate_features_for_symbol(
    cfg: AppConfig, symbol: str, timeframe: str
) -> pd.DataFrame:
    """Generate validated in-memory features for one admitted source."""
    raw = load_raw_data(symbol, timeframe, cfg.data.raw_dir, cfg.data.lake_dir)
    return _validate_features(
        FeatureAssembler(cfg.features).process(raw, symbol),
        raw,
        symbol=symbol,
        timeframe=timeframe,
    )
