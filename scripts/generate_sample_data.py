#!/usr/bin/env python3
"""
Generate Synthetic Market Data

This script generates realistic synthetic cryptocurrency market data
for testing and development purposes.
"""

import argparse
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def generate_price_series(
    initial_price: float,
    n_bars: int,
    drift: float = 0.0001,
    volatility: float = 0.02,
    seed: int = 42,
) -> np.ndarray:
    """
    Generate realistic price series using geometric Brownian motion.

    Args:
        initial_price: Starting price
        n_bars: Number of price bars to generate
        drift: Daily drift (trend)
        volatility: Daily volatility
        seed: Random seed for reproducibility

    Returns:
        Array of prices
    """
    np.random.seed(seed)

    # Generate random returns
    returns = np.random.normal(drift, volatility, n_bars)

    # Apply geometric Brownian motion
    price_series = initial_price * np.exp(np.cumsum(returns))

    return price_series


def generate_volume_series(
    n_bars: int, mean_volume: float = 1000000, volatility: float = 0.3, seed: int = 42
) -> np.ndarray:
    """Generate realistic volume series."""
    np.random.seed(seed + 1)

    # Log-normal distribution for volume
    volumes = np.random.lognormal(
        mean=np.log(mean_volume), sigma=volatility, size=n_bars
    )

    return volumes


def generate_ohlcv_bars(
    prices: np.ndarray, volumes: np.ndarray, intrabar_volatility: float = 0.005
) -> pd.DataFrame:
    """
    Generate OHLCV bars from price series.

    Args:
        prices: Close prices
        volumes: Volume series
        intrabar_volatility: Volatility within each bar

    Returns:
        DataFrame with OHLC columns
    """
    n_bars = len(prices)

    # Generate high/low within each bar
    highs = prices * (1 + np.abs(np.random.normal(0, intrabar_volatility, n_bars)))
    lows = prices * (1 - np.abs(np.random.normal(0, intrabar_volatility, n_bars)))

    # Ensure high >= close and low <= close
    highs = np.maximum(highs, prices)
    lows = np.minimum(lows, prices)

    # Open is close of previous bar (with some noise)
    opens = np.roll(prices, 1)
    opens[0] = prices[0]
    opens = opens * (1 + np.random.normal(0, intrabar_volatility / 2, n_bars))

    # Ensure bars are valid (low <= open/close <= high)
    lows = np.minimum(lows, np.minimum(opens, prices))
    highs = np.maximum(highs, np.maximum(opens, prices))

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": prices, "volume": volumes}
    )


def add_market_microstructure(
    df: pd.DataFrame, spread_bps_mean: float = 20, spread_bps_std: float = 10
) -> pd.DataFrame:
    """Add market microstructure features."""

    # Bid-ask spread
    df["spread_bps"] = np.abs(
        np.random.normal(spread_bps_mean, spread_bps_std, len(df))
    )
    df["spread_bps"] = df["spread_bps"].clip(lower=5)  # Min 5 bps

    # Buy/sell capacity (liquidity)
    df["buy_max"] = df["volume"] * df["close"] * np.random.uniform(0.8, 1.2, len(df))
    df["sell_max"] = df["volume"] * df["close"] * np.random.uniform(0.8, 1.2, len(df))

    # Buy and sell prices
    half_spread = (df["spread_bps"] / 10000) * df["close"] / 2
    df["buy_price"] = df["close"] + half_spread
    df["sell_price"] = df["close"] - half_spread

    return df


def generate_timeframe_data(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    timeframe: str,
    initial_price: float,
    output_dir: Path,
) -> None:
    """
    Generate data for a specific timeframe and save to parquet.

    Args:
        symbol: Trading symbol (e.g., 'BTC')
        start_date: Start date
        end_date: End date
        timeframe: Timeframe (e.g., '5m', '1h')
        initial_price: Starting price
        output_dir: Output directory for parquet files
    """
    # Parse timeframe
    if timeframe.endswith("m"):
        freq = f"{timeframe[:-1]}min"
        minutes = int(timeframe[:-1])
    elif timeframe.endswith("h"):
        freq = f"{timeframe[:-1]}H"
        minutes = int(timeframe[:-1]) * 60
    elif timeframe.endswith("d"):
        freq = f"{timeframe[:-1]}D"
        minutes = int(timeframe[:-1]) * 1440
    else:
        raise ValueError(f"Invalid timeframe: {timeframe}")

    # Generate timestamp index
    timestamps = pd.date_range(start=start_date, end=end_date, freq=freq)
    n_bars = len(timestamps)

    print(f"Generating {n_bars} bars for {symbol} {timeframe}...")

    # Generate price and volume series
    prices = generate_price_series(
        initial_price=initial_price,
        n_bars=n_bars,
        drift=0.0001 * (minutes / 1440),  # Scale drift by timeframe
        volatility=0.02 * np.sqrt(minutes / 1440),  # Scale volatility
    )

    volumes = generate_volume_series(
        n_bars=n_bars,
        mean_volume=1000000 * (minutes / 5),  # More volume for longer timeframes
        volatility=0.3,
    )

    # Generate OHLCV data
    df = generate_ohlcv_bars(prices, volumes)
    df.index = timestamps

    # Add market microstructure
    df = add_market_microstructure(df)

    # Add symbol prefix to columns
    df.columns = [f"{symbol}_{col}" for col in df.columns]

    # Add timestamp column
    df["timestamp"] = df.index

    # Create output directory
    partition_dir = output_dir / symbol / timeframe
    partition_dir.mkdir(parents=True, exist_ok=True)

    # Save to parquet
    output_file = partition_dir / f"data_{start_date.strftime('%Y_%m')}.parquet"
    df.to_parquet(output_file, index=False)

    print(f"✓ Saved {len(df)} bars to {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic market data")
    parser.add_argument("--symbol", default="BTC", help="Trading symbol")
    parser.add_argument(
        "--timeframe", default="5m", help="Timeframe (e.g., 1m, 5m, 1h)"
    )
    parser.add_argument(
        "--days", type=int, default=30, help="Number of days to generate"
    )
    parser.add_argument("--output-dir", default="lake", help="Output directory")
    parser.add_argument(
        "--initial-price",
        type=float,
        default=1_250_000_000,
        help="Initial price in IRT",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")

    args = parser.parse_args()

    # Set random seed
    np.random.seed(args.seed)

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)

    # Generate data
    output_dir = Path(args.output_dir)
    generate_timeframe_data(
        symbol=args.symbol,
        start_date=start_date,
        end_date=end_date,
        timeframe=args.timeframe,
        initial_price=args.initial_price,
        output_dir=output_dir,
    )

    print(
        f"\n✓ Successfully generated {args.days} days of {args.symbol} {args.timeframe} data"
    )
    print(f"  Location: {output_dir / args.symbol / args.timeframe}")


if __name__ == "__main__":
    main()
