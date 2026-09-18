"""Historical Abantether market-data downloader with transactional lake writes."""

from __future__ import annotations

import logging
import math
import os
import re
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import typer
from filelock import FileLock

from apps.lake.manifest import write_daily_merkle, write_partition_manifest
from trading_gym.adapters.enhanced_aban_client import EnhancedAbanClient
from trading_gym.common.persistence import atomic_write_text
from trading_gym.common.security import confined_child
from trading_gym.data.data_loader import (
    _normalise_timestamp_index,
    validate_market_frame,
)

app = typer.Typer(no_args_is_help=True)
logger = logging.getLogger(__name__)

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.=-]{0,63}$")
_TIMEFRAME_RE = re.compile(r"^(?P<count>[1-9][0-9]{0,3})(?P<unit>[mhd])$")
_MAX_BATCH_CANDLES = 5_000
_MAX_DOWNLOAD_DAYS = 3_650


def _safe_component(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not _COMPONENT_RE.fullmatch(normalized) or normalized in {".", ".."}:
        raise ValueError(f"Unsafe {label}")
    return normalized


def _timeframe_seconds(value: str) -> int:
    match = _TIMEFRAME_RE.fullmatch(value)
    if not match:
        raise ValueError("timeframe must use a bounded form such as 1m, 5m, 1h, or 1d")
    count = int(match.group("count"))
    unit = match.group("unit")
    seconds = count * {"m": 60, "h": 3_600, "d": 86_400}[unit]
    if seconds > 31 * 86_400:
        raise ValueError("timeframe exceeds 31 days")
    return seconds


def _fsync_directory(path: Path) -> None:
    # Directory fsync is a POSIX durability primitive. Windows has no
    # directory file descriptor to open, so the returned handle is
    # invalid and closing it raised EBADF, failing every batch save; off
    # POSIX the step is a no-op because os.replace is already atomic.
    if os.name != "posix":
        return
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class Poller:
    """Fetch and durably merge bounded OHLCV batches into the lake."""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        client: EnhancedAbanClient,
        data_dir: str | Path = "trading_gym/data/lake",
        manifests_dir: str | Path | None = None,
        *,
        default_spread_bps: float = 20.0,
    ) -> None:
        self.symbol = _safe_component(symbol, "symbol")
        self.timeframe = _safe_component(timeframe, "timeframe")
        self.timeframe_seconds = _timeframe_seconds(self.timeframe)
        if not isinstance(client, EnhancedAbanClient) and not hasattr(
            client, "get_klines"
        ):
            raise TypeError("client must provide get_klines")
        if not math.isfinite(default_spread_bps) or default_spread_bps < 0:
            raise ValueError("default_spread_bps must be finite and non-negative")
        self.client = client
        root = Path(data_dir).expanduser()
        if root.exists() and root.is_symlink():
            raise ValueError("data_dir must not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir = root.resolve(strict=True)
        manifest_root = (
            Path(manifests_dir).expanduser()
            if manifests_dir
            else self.data_dir.parent / "manifests"
        )
        if manifest_root.exists() and manifest_root.is_symlink():
            raise ValueError("manifests_dir must not be a symlink")
        manifest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.manifests_dir = manifest_root.resolve(strict=True)
        self.default_spread_bps = float(default_spread_bps)

    def fetch_batch(self, start_ts: int, end_ts: int) -> pd.DataFrame:
        """Fetch one bounded, complete, validated interval ``[start, end]``."""
        if isinstance(start_ts, bool) or isinstance(end_ts, bool):
            raise TypeError("timestamps must be integer epoch seconds")
        start_ts, end_ts = int(start_ts), int(end_ts)
        if start_ts <= 0 or end_ts <= start_ts:
            raise ValueError("end_ts must be greater than a positive start_ts")
        expected = math.ceil((end_ts - start_ts) / self.timeframe_seconds) + 1
        if expected > _MAX_BATCH_CANDLES:
            raise ValueError("requested batch exceeds candle limit")

        logger.info(
            "Fetching historical batch symbol=%s timeframe=%s start=%d end=%d",
            self.symbol,
            self.timeframe,
            start_ts,
            end_ts,
        )
        try:
            data = self.client.get_klines(
                symbol=self.symbol,
                interval=self.timeframe,
                start=start_ts,
                end=end_ts,
                limit=min(_MAX_BATCH_CANDLES, expected),
            )
        except Exception as exc:
            logger.error(
                "Historical batch request failed error_type=%s", type(exc).__name__
            )
            raise
        if not data:
            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "spread_bps",
                ]
            )
        if not isinstance(data, list) or len(data) > _MAX_BATCH_CANDLES:
            raise ValueError("Kline response is invalid or unbounded")
        for row in data:
            if not isinstance(row, (list, tuple)) or len(row) != 6:
                raise ValueError("Kline response rows must contain exactly six values")

        frame = pd.DataFrame(
            data,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        raw_ts = pd.to_numeric(frame["timestamp"], errors="raise").astype(np.float64)
        if not np.isfinite(raw_ts.to_numpy()).all():
            raise ValueError("Kline timestamps must be finite")
        # Support the two common API encodings without guessing other units.
        seconds = np.where(raw_ts >= 1e12, raw_ts / 1000.0, raw_ts)
        frame["timestamp"] = pd.to_datetime(seconds, unit="s", utc=True, errors="raise")
        frame["spread_bps"] = self.default_spread_bps
        frame = _normalise_timestamp_index(frame, source="Abantether response")
        frame, _, _ = validate_market_frame(
            frame,
            symbol=self.symbol,
            timeframe=self.timeframe,
            default_spread_bps=self.default_spread_bps,
        )
        # Resolution-independent epoch seconds: pandas 3 may build second- or
        # microsecond-resolution indexes, so normalise through datetime64[s].
        epoch = (
            frame.index.tz_convert("UTC")
            .to_numpy()
            .astype("datetime64[s]")
            .astype(np.int64)
        )
        if (epoch < start_ts).any() or (epoch > end_ts).any():
            raise ValueError(
                "Kline response contains timestamps outside the requested interval"
            )
        if len(frame) > expected:
            raise ValueError("Kline response exceeds the requested interval capacity")
        return frame

    def _partition(self) -> Path:
        partition = confined_child(
            self.data_dir,
            self.symbol,
            self.timeframe,
            allow_symlinks=False,
        )
        partition.mkdir(parents=True, exist_ok=True, mode=0o700)
        if partition.is_symlink() or not partition.is_dir():
            raise ValueError("partition path is unsafe")
        return partition

    def _merge_day(self, destination: Path, incoming: pd.DataFrame) -> pd.DataFrame:
        frames = [incoming]
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("existing partition artifact is unsafe")
            try:
                existing = pd.read_parquet(destination)
            except Exception as exc:
                raise ValueError(
                    "existing partition artifact cannot be validated"
                ) from exc
            frames.insert(
                0, _normalise_timestamp_index(existing, source=destination.name)
            )
        merged = pd.concat(frames, axis=0, ignore_index=False, copy=False)
        merged, _, _ = validate_market_frame(
            merged,
            symbol=self.symbol,
            timeframe=self.timeframe,
            default_spread_bps=self.default_spread_bps,
        )
        return merged

    def save_batch(self, frame: pd.DataFrame) -> list[Path]:
        """Atomically merge a batch and refresh exact partition manifests.

        A durable ``.integrity-pending`` marker is created before the first
        replacement.  The strict loader rejects the partition while that marker
        exists, so a crash cannot expose changed parquet bytes under stale
        manifests as trusted data.
        """
        if frame.empty:
            return []
        admitted = _normalise_timestamp_index(frame, source="poller batch")
        admitted, _, _ = validate_market_frame(
            admitted,
            symbol=self.symbol,
            timeframe=self.timeframe,
            default_spread_bps=self.default_spread_bps,
        )
        partition = self._partition()
        lock_path = partition / ".write.lock"
        marker = partition / ".integrity-pending"
        written: list[Path] = []
        with FileLock(str(lock_path)):
            operation_id = uuid.uuid4().hex
            atomic_write_text(marker, operation_id + "\n", max_bytes=128)
            try:
                dates = admitted.index.tz_convert("UTC").date
                for date in sorted(set(dates)):
                    day_frame = admitted.loc[dates == date].copy()
                    destination = confined_child(
                        partition,
                        f"part_{date.isoformat()}.parquet",
                        allow_symlinks=False,
                    )
                    merged = self._merge_day(destination, day_frame)
                    temp = partition / f".{destination.name}.{operation_id}.tmp.parquet"
                    try:
                        merged.reset_index(drop=True).to_parquet(temp, index=False)
                        if temp.is_symlink() or not temp.is_file():
                            raise ValueError(
                                "parquet writer did not create a regular file"
                            )
                        with temp.open("r+b") as handle:
                            os.fsync(handle.fileno())
                        os.replace(temp, destination)
                        _fsync_directory(partition)
                    finally:
                        if temp.exists():
                            temp.unlink()
                    written.append(destination)

                with FileLock(str(self.manifests_dir / ".manifest.lock")):
                    manifest_path = (
                        self.manifests_dir
                        / f"{self.symbol}__{self.timeframe}.manifest.json"
                    )
                    root_path = self.manifests_dir / "daily.merkle.json"
                    write_partition_manifest(
                        partition,
                        manifest_path,
                        partition_id=f"{self.symbol}/{self.timeframe}",
                    )
                    write_daily_merkle(self.manifests_dir, root_path)
                marker.unlink()
                _fsync_directory(partition)
            except Exception:
                # Marker deliberately remains to make incomplete integrity
                # publication visible to every strict reader.
                raise

        logger.info(
            "Saved and manifested %d rows symbol=%s timeframe=%s files=%d",
            len(admitted),
            self.symbol,
            self.timeframe,
            len(written),
        )
        return written


@app.command()
def download(
    symbol: str = typer.Option(..., help="Symbol (for example BTC)"),
    timeframe: str = typer.Option("5m", help="Timeframe such as 5m or 1h"),
    start_date: str = typer.Option(..., help="Inclusive start date YYYY-MM-DD"),
    end_date: str = typer.Option(..., help="Exclusive end date YYYY-MM-DD"),
    api_key: Optional[str] = typer.Option(None, envvar="ABAN_API_KEY", help="API key"),
    data_dir: Path = typer.Option(Path("trading_gym/data/lake"), help="Lake root"),
    manifests_dir: Optional[Path] = typer.Option(
        None, help="Integrity manifest directory"
    ),
) -> None:
    """Download complete daily batches and stop on the first failed interval."""
    logging.basicConfig(level=logging.INFO)
    if not api_key or not api_key.strip():
        raise typer.BadParameter("ABAN_API_KEY is required", param_hint="--api-key")
    try:
        start = pd.Timestamp(start_date, tz="UTC")
        end = pd.Timestamp(end_date, tz="UTC")
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter("dates must be valid ISO dates") from exc
    if end <= start:
        raise typer.BadParameter("end_date must be after start_date")
    if (end - start).days > _MAX_DOWNLOAD_DAYS:
        raise typer.BadParameter(f"download range exceeds {_MAX_DOWNLOAD_DAYS} days")

    poller = Poller(
        symbol,
        timeframe,
        EnhancedAbanClient(api_key=api_key),
        data_dir,
        manifests_dir,
    )
    current = start
    while current < end:
        next_boundary = min(current + pd.Timedelta(days=1), end)
        frame = poller.fetch_batch(
            int(current.timestamp()), int(next_boundary.timestamp())
        )
        if frame.empty:
            raise RuntimeError(
                f"Provider returned no candles for {current.isoformat()} to {next_boundary.isoformat()}"
            )
        poller.save_batch(frame)
        current = next_boundary


if __name__ == "__main__":
    app()
