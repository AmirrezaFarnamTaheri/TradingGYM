from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, cast

import pandas as pd


@dataclass
class LocalCacheAdapter:
    """Serve last-known-good tickers from a cache directory.

    The adapter is intentionally read-only and only reads parquet snapshots produced
    by the primary poller. It acts as a safety net when the live venue becomes
    unavailable so downstream pipelines can keep running with slightly stale data.
    """

    cache_dir: Path
    max_age: Optional[timedelta] = timedelta(minutes=5)

    def fetch_tickers(
        self, symbols: Sequence[str] | None = None
    ) -> List[Dict[str, Any]]:
        latest = self._latest_snapshot_path()
        if latest is None:
            return []

        frame = pd.read_parquet(latest)
        if "fetch_timestamp_utc" in frame.columns:
            # Ensure cached data is not too stale if an age limit is configured.
            if self.max_age is not None:
                try:
                    ts = pd.to_datetime(frame["fetch_timestamp_utc"].iloc[0], utc=True)
                except (KeyError, ValueError, TypeError):
                    ts = None
                if ts is not None:
                    age = datetime.now(timezone.utc) - ts.to_pydatetime()
                    if age > self.max_age:
                        return []

        if symbols:
            upper = {s.upper() for s in symbols}
            symbol_col = frame.columns.intersection(["symbol", "market", "ticker"])
            if not symbol_col.empty:
                col = symbol_col[0]
                frame = frame[frame[col].str.upper().isin(upper)]

        return cast(List[Dict[str, Any]], frame.to_dict(orient="records"))

    def healthcheck(self) -> Dict[str, Any]:
        latest = self._latest_snapshot_path()
        if latest is None:
            return {"ok": False, "reason": "no_cache"}
        if self.max_age is not None:
            ts = self._timestamp_from_path(latest)
            if ts and datetime.now(timezone.utc) - ts > self.max_age:
                return {
                    "ok": False,
                    "reason": "stale_cache",
                    "age_seconds": (datetime.now(timezone.utc) - ts).total_seconds(),
                }
        return {"ok": True, "path": str(latest)}

    def _latest_snapshot_path(self) -> Optional[Path]:
        if not self.cache_dir.exists():
            return None
        candidates = sorted(self.cache_dir.glob("*.parquet"))
        return candidates[-1] if candidates else None

    @staticmethod
    def _timestamp_from_path(path: Path) -> Optional[datetime]:
        try:
            stem = path.stem
            ts_str = stem.split("_")[0:2]
            if not ts_str:
                return None
            ts = datetime.strptime("_".join(ts_str), "%Y%m%d_%H%M%S")
            return ts.replace(tzinfo=timezone.utc)
        except Exception:
            return None
