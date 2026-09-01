# Market Microstructure & Data

Data is the fuel of any quantitative system. Trading Gym implements a rigorous "Data Lake" architecture to ensure data integrity, reproducibility, and zero lookahead bias.

## 1. The Data Lake Structure

We organize data hierarchically using the filesystem. This simple structure allows for easy partitioning and parallel processing.

```
trading_gym/data/lake/
├── BTC/                 # Symbol
│   ├── 5m/              # Timeframe
│   │   ├── part-0001.parquet
│   │   ├── part-0002.parquet
│   │   └── ...
│   └── 1h/
└── ETH/
    └── ...
```

*   **Format:** Parquet (Snappy compressed).
*   **Why Parquet?**
    *   **Columnar:** Fast to read specific columns (e.g., just "Close" prices).
    *   **Typed:** Preserves schema (float64, int64, timestamp).
    *   **Compressed:** Saves disk space compared to CSV.

## 2. Data Schema

Every Parquet file in the lake must adhere to this schema:

| Column | Type | Description |
| :--- | :--- | :--- |
| `timestamp` | `datetime64[ns, UTC]` | Open time of the candle. |
| `open` | `float64` | Open price. |
| `high` | `float64` | High price. |
| `low` | `float64` | Low price. |
| `close` | `float64` | Close price. |
| `volume` | `float64` | Volume in base asset. |
| `spread_bps` | `float64` | Average bid-ask spread in basis points. |
| `buy_cap` | `float64` | Liquidity available on the buy side (depth). |
| `sell_cap` | `float64` | Liquidity available on the sell side (depth). |
| `volatility` | `float64` | An optional volatility hint from the exchange. |
| `is_missing` | `int8`    | A flag indicating if the data for this bar is missing. |

**Note on `buy_cap` / `sell_cap`:** These columns are crucial for the Slippage Model. If real depth data is missing, we estimate it using volume and volatility proxies.

## 3. Feature Engineering Pipeline

The feature engineering pipeline transforms raw time-series data into a rich, stationary feature set. It is designed to be robust, performant, and free of data leakage.

### 3.1 Feature Assembly (`FeatureAssembler`)

The `FeatureAssembler` class processes a single asset's dataframe. It uses **Numba** (`@njit`) to compile critical loops for C-speed performance.

**Key Features:**

*   **Log Returns:** `ln(P_t / P_{t-1})`. Additive and more normally distributed than simple returns.
*   **Volatility (vol20):** Rolling standard deviation of log returns over a 20-period window.
*   **Momentum:** Close price relative to a 50-period Exponential Moving Average (EMA).
*   **RSI:** Relative Strength Index (14-period), normalized to `[0, 1]`.
*   **Spread Ratio:** Bid-ask spread normalized by mid-price.
*   **Spread Velocity:** Rate of change of the spread ratio.
*   **Capacity Utilization:** Liquidity usage relative to a standard trade size.
*   **Sector Encoding:** One-hot encoding of asset class (e.g., L1, DeFi, Meme).

### 3.2 Multi-Timeframe Fusion (`FeatureFusion`)

The `FeatureFusion` module handles combining features from multiple timeframes (e.g., 5m, 1h, 4h) to give the agent "peripheral vision".

1.  **Alignment:** Higher timeframe features (e.g., 1h) are forward-filled to align with the base timeframe (5m) index.
2.  **Vectorized Attention:** An attention mechanism computes a weighted average of features across timeframes, allowing the model to dynamically focus on short-term or long-term signals.
3.  **Concatenation:** The fused vector is stacked with the base features to form the final observation tensor.

## 4. Preventing Lookahead Bias

Lookahead bias is the #1 killer of trading strategies. We prevent it by:

1.  **Lagging Features:** Indicators are calculated on `Close` prices. The agent decides at the *end* of the candle. The observation at time `T` uses features calculated up to `T`. The action executed at `T` affects PnL from `T` to `T+1`.
2.  **Strict Splitting:** Training/Validation/Test splits are always chronological. **Never random shuffle.**
3.  **Windowed Normalization:** Features are normalized (Z-Score) using a *rolling window* (e.g., last 30 days), not global dataset statistics.

## 5. Latency Simulation

In the real world, there is a delay between "Seeing the price" and "Getting the fill".
*   **Simulation:** Configurable `latency_steps` parameter (default 0).
*   **Reality:** The `LiveExecutionAdapter` naturally incurs network latency.
*   **Order Book Dynamics:** We assume "Taker" execution against the snapshot of the book. We do not simulate limit order queue position.
