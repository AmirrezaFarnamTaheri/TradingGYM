from __future__ import annotations

from typing import Iterator, Sequence, Tuple

import numpy as np

from trading_gym.core.config import load_app_config

# Re-exported so callers can import the loader here; the canonical definition
# lives in trading_gym.core.config.
__all__ = ["TimeSeriesSplit", "load_app_config"]


class TimeSeriesSplit:
    """Simple anchored train/test splitter."""

    def __init__(self, n_splits: int, train_fraction: float = 0.8) -> None:
        if not 0 < train_fraction < 1:
            raise ValueError("train_fraction must be between 0 and 1")
        if n_splits < 1:
            raise ValueError("n_splits must be positive")
        self.n_splits = n_splits
        self.train_fraction = train_fraction

    def split(self, data: Sequence[object]) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
        indices = np.arange(len(data))
        train_end = int(len(data) * self.train_fraction)
        if train_end <= 0:
            raise ValueError("train_fraction too small for available data")
        window = max((len(data) - train_end) // self.n_splits, 1)

        for i in range(self.n_splits):
            start = 0
            stop = train_end + i * window
            test_start = stop
            test_end = min(len(data), stop + window)
            if test_start >= len(data):
                break
            yield indices[start:stop], indices[test_start:test_end]
