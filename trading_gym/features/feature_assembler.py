"""
Feature Assembler
=================
High-performance, Numba-accelerated feature engineering.
Ensures ZERO lookahead bias.

The Feature Assembler converts raw OHLCV data into predictive signals for the RL Agent.
It uses Numba for JIT compilation to speed up loop-heavy calculations like RSI and Volatility.

Core Features:
1. **Log Returns:** Used instead of simple returns for additive properties.
2. **Volatility:** Rolling standard deviation of log returns, essential for risk parity.
3. **RSI:** Relative Strength Index, a momentum oscillator measuring speed and change of price movements.
4. **Microstructure:** Estimates spread proxies from High-Low range.
5. **Sector Encoding:** Feeds the agent categorical context (e.g., "This is a Meme coin").

Zero-to-Hero Note:
We use `cache=True` in Numba decorators to cache compiled machine code on disk, reducing startup time
on subsequent runs.
"""

import logging
import os
import numpy as np
import pandas as pd
from numba import njit
from trading_gym.common.config import FeatureConfig
from trading_gym.venue.asset_registry import get_asset_sector, is_shortable

logger = logging.getLogger(__name__)

# Check if we are in a test environment to potentially disable caching or fastmath
IS_TEST_ENV = os.getenv("PYTEST_CURRENT_TEST") is not None


@njit(cache=not IS_TEST_ENV, nogil=True)
def calculate_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Relative Strength Index (RSI) implementation.

    Math:
    RSI = 100 - (100 / (1 + RS))
    RS = Average Gain / Average Loss

    Uses Wilder's Smoothing (Smoothed Moving Average).
    """
    delta = np.diff(prices)
    # Prepend 0 to match length
    delta = np.concatenate((np.array([0.0]), delta))

    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.zeros_like(prices)
    avg_loss = np.zeros_like(prices)

    # Initial SMA
    avg_gain[period] = np.mean(gain[1 : period + 1])
    avg_loss[period] = np.mean(loss[1 : period + 1])

    for i in range(period + 1, len(prices)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

    rsi = np.empty_like(prices)
    for i in range(len(prices)):
        if i < period:
            rsi[i] = 50.0
        elif avg_gain[i] <= 1e-12 and avg_loss[i] <= 1e-12:
            rsi[i] = 50.0
        elif avg_loss[i] <= 1e-12:
            rsi[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


@njit(cache=not IS_TEST_ENV, nogil=True)
def calculate_log_returns(prices: np.ndarray) -> np.ndarray:
    """
    Log returns: ln(p_t / p_{t-1})

    Why Log Returns?
    1. Additivity: r_total = r_1 + r_2 ... (Simpler for multi-period)
    2. Normality: Log returns are more normally distributed than simple returns.
    """
    res = np.zeros_like(prices)
    res[1:] = np.log(prices[1:] / np.maximum(prices[:-1], 1e-9))
    return res


@njit(cache=not IS_TEST_ENV, nogil=True)
def calculate_volatility(
    returns: np.ndarray, window: int = 20, annualization_factor: float = 1.0
) -> np.ndarray:
    """
    Rolling volatility of log returns.

    Args:
        returns: Array of log returns
        window: Lookback window for rolling calculation
        annualization_factor: sqrt(periods_per_year) for annualization.
            For 5-min bars: sqrt(252 * 288) ≈ 269.4
            Pass 1.0 for non-annualized (default for features).

    Returns:
        Rolling standard deviation, optionally annualized.

    Note:
        For RL features, we typically use non-annualized volatility
        as the agent learns relative magnitudes. Annualization is
        applied in evaluation/reporting contexts.
    """
    vol = np.zeros_like(returns)
    for i in range(window, len(returns)):
        vol[i] = np.std(returns[i - window : i]) * annualization_factor
    return vol


class FeatureAssembler:
    """
    The Engine Room of the Data Pipeline.
    Takes raw candles -> Returns clean, normalized features.
    """

    def __init__(self, config: FeatureConfig):
        self.cfg = config
        self.enabled_features = [
            "log_ret",
            "volatility",
            "rsi",
            "spread_bps",
            "sector_encoding",
            "is_shortable",
        ]

    def process(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Main entry point for feature generation.
        df must have: ['open', 'high', 'low', 'close', 'volume']
        """
        # 1. Validate Input. Feature generation must never turn corrupt source
        # values into plausible zero-filled signals.
        required_cols = ["close", "high", "low"]
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"Dataframe missing required columns: {required_cols}")
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("Feature input must use a DatetimeIndex")
        if not df.index.is_monotonic_increasing or not df.index.is_unique:
            raise ValueError("Feature input timestamps must be ordered and unique")
        rolling_window = int(self.cfg.rolling_window)
        if not 20 <= rolling_window <= 1_000_000:
            raise ValueError("rolling_window must be between 20 and 1000000")
        min_length = max(20, rolling_window // 10)
        if len(df) < min_length:
            raise ValueError(
                f"DataFrame too short for feature calculation: {len(df)} rows < {min_length} minimum"
            )
        critical = df[required_cols].to_numpy(dtype=np.float64, copy=False)
        if not np.isfinite(critical).all():
            raise ValueError(f"Non-finite critical values detected for {symbol}")
        if (critical <= 0).any():
            raise ValueError(f"Critical prices must be positive for {symbol}")
        if (df["high"].to_numpy() < df[["close", "low"]].max(axis=1).to_numpy()).any():
            raise ValueError(f"Invalid high-price envelope for {symbol}")
        if (df["low"].to_numpy() > df[["close", "high"]].min(axis=1).to_numpy()).any():
            raise ValueError(f"Invalid low-price envelope for {symbol}")

        # Ensure contiguous arrays for Numba
        prices = np.ascontiguousarray(df["close"].values, dtype=np.float64)
        highs = np.ascontiguousarray(df["high"].values, dtype=np.float64)
        lows = np.ascontiguousarray(df["low"].values, dtype=np.float64)

        # 2. Core Features (Numba)
        # Force copy to numpy array to prevent Numba/Pandas ABI conflicts
        log_rets = np.array(calculate_log_returns(prices))
        vol = np.array(calculate_volatility(log_rets, window=rolling_window // 10))
        rsi = np.array(calculate_rsi(prices, period=14))

        # 3. Microstructure Features (Vectorized)
        # Estimate spread if not provided (High - Low as proxy for volatility/spread)
        spread_proxy = np.array((highs - lows) / np.maximum(prices, 1e-9))

        # 4. Static/Meta Features
        sector = get_asset_sector(symbol)

        # Build dynamic sector map from ASSET_SECTORS values + standard ones
        # We'll map the unique string values to integers deterministically
        # Standard map for consistency with older logic
        base_map = {
            "OTHER": 0,
            "L1": 1,
            "MEME": 2,
            "AI": 3,
            "DEFI": 4,
            "ETF": 5,
            "STOCK": 6,
        }
        sector_code = base_map.get(sector, 0)

        # Shortable logic (Centralized)
        is_short_allowed = 1.0 if is_shortable(symbol) else 0.0

        # 5. Assemble
        features = pd.DataFrame(index=df.index)
        features["log_ret"] = log_rets
        features["volatility"] = vol
        features["rsi"] = rsi / 100.0  # Normalize 0-1
        features["spread_ratio"] = spread_proxy
        features["sector"] = sector_code
        features["can_short"] = is_short_allowed

        # 6. Validate output. Any NaN/Inf is a feature-engineering defect,
        # not a value to silently coerce into a tradable signal.
        output = features.astype(np.float32)
        if not np.isfinite(output.to_numpy(dtype=np.float32, copy=False)).all():
            raise ValueError(
                f"Feature generation produced non-finite values for {symbol}"
            )
        return output
