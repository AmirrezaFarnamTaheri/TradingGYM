import pandas as pd
import numpy as np
from pathlib import Path


def generate_dummy_data():
    print("Generating dummy data for dry run...")
    lake_dir = Path("data/lake")
    symbol = "BTCUSDT"
    timeframe = "5m"

    partition_dir = lake_dir / symbol / timeframe
    partition_dir.mkdir(parents=True, exist_ok=True)

    dates = pd.date_range(start="2024-01-01", periods=1000, freq="5min")
    df = pd.DataFrame(
        {
            "open": np.random.uniform(40000, 42000, 1000),
            "high": np.random.uniform(40000, 42000, 1000),
            "low": np.random.uniform(40000, 42000, 1000),
            "close": np.random.uniform(40000, 42000, 1000),
            "volume": np.random.uniform(100, 1000, 1000),
        },
        index=dates,
    )

    # Ensure High is highest and Low is lowest
    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"] = df[["open", "close", "low"]].min(axis=1)

    output_path = partition_dir / "part-0.parquet"
    df.to_parquet(output_path)
    print(f"Saved dummy data to {output_path}")

    # Also generate feature data if needed, but the pipeline might expect raw only and then generate features?
    # trading_gym/pipelines/environment.py loads raw data.
    # But does it need features?
    # Let's check load_environment_inputs.


if __name__ == "__main__":
    generate_dummy_data()
