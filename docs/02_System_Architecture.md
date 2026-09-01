# System Architecture

The Trading Gym architecture is designed for modularity, robustness, and strict separation of concerns. It follows a **Hexagonal Architecture (Ports and Adapters)** pattern where the core logic is isolated from external inputs (API, Data) and outputs (Exchange, Logs).

## 1. Core Philosophy: "Sim == Prod"

The foundational principle of the Trading Gym is **"Sim == Prod"** (Simulation is identical to Production). This is not merely a guideline but a strict architectural constraint ensuring that the exact same logic that powers a backtest is deployed for live trading. This eliminates a major source of error in quantitative trading: discrepancies between theoretical performance and real-world results.

### How it Works: The Unified Pipeline

The `trading_gym.pipelines.environment` module provides a standardized pipeline for creating environment instances. This pipeline is the key to the "Sim == Prod" design.

1.  `load_environment_inputs(cfg: AppConfig, timeframe: str) -> Dict`: Loads all necessary data, including historical prices and pre-calculated features.
2.  `create_environment(cfg: AppConfig, all_data: Dict, live_mode: bool = False) -> MarginTradingEnv`: Constructs the `MarginTradingEnv` with the provided data and configuration.

*   **Simulation:** The environment is created with `live_mode=False` and operates on a fixed historical dataset.
*   **Live Trading:** The environment is created with `live_mode=True` and uses a ring buffer to process a sliding window of real-time data.

Crucially, the **`MarginTradingEnv` and `ExecutionEngine` are the same objects in both scenarios**, guaranteeing identical behavior.

## 2. High-Level Architecture

```mermaid
graph TD
    subgraph "External World"
        Market[Crypto Market]
        Aban[Abantether API]
    end

    subgraph "Application Layer (Apps)"
        Trainer[Trainer (RL Loop)]
        Sim[Simulator (Backtest)]
        Live[Live Trader]
        Paper[Paper Trader]
    end

    subgraph "Core Domain (Trading Gym)"
        Env[MarginTradingEnv]
        Engine[ExecutionEngine]
        Assembler[FeatureAssembler]
        Fusion[FeatureFusion]
    end

    subgraph "Infrastructure"
        Lake[Data Lake (Parquet)]
        Models[Model Registry]
        Logs[Metrics & Logs]
    end

    Market -->|Raw Data| Lake
    Lake -->|OHLCV| Assembler
    Assembler -->|Features| Fusion
    Fusion -->|Obs| Env
    Env -->|Action| Engine
    Engine -->|State Update| Env

    Live -->|Orders| Aban
    Aban -->|Fills| Live
    Aban <--> Market
```

## 3. Component Breakdown

The project is structured into three distinct layers:

### Layer 1: `apps/` - The Application Layer
These are the user-facing CLI tools that orchestrate workflows.

*   **`apps/trainer`**: Runs the PPO training loop using `stable_baselines3`. Manages parallel environments (`SubprocVecEnv`) and model checkpointing.
*   **`apps/simulator`**: Runs backtests on historical data using a trained model, generating equity curves and performance metrics.
*   **`apps/live`**: Connects to the real Abantether API. Maintains the "Heartbeat", listens for "Panic Signals", and synchronizes internal state with the real wallet.
*   **`apps/paper`**: A hybrid mode that connects to the live API for data but simulates execution internally.
*   **`apps/feature_pipe`**: Orchestrates the feature engineering pipeline, processing raw data from the lake into engineered features.

### Layer 2: `trading_gym/` - The Core Logic Layer
This is the reusable Python library containing the domain logic.

*   **`trading_gym/rl`**: Contains `MarginTradingEnv`, the Gymnasium-compliant environment that interfaces with the agent.
*   **`trading_gym/execution`**: Contains `ExecutionEngine`, the financial heart of the system. It calculates costs, updates balances, and enforces limits using `decimal.Decimal` precision.
*   **`trading_gym/features`**: Contains `FeatureAssembler` (Numba-accelerated feature calculation) and `FeatureFusion` (multi-timeframe alignment).
*   **`trading_gym/pipelines`**: High-level logic connecting components (e.g., `training.py` orchestrates environment creation).
*   **`trading_gym/data`**: `data_loader.py` handles loading data from the Parquet data lake.
*   **`trading_gym/margin`**: Implements borrow caps and margin calculations.
*   **`trading_gym/rate_limit`**: Provides a global, thread-safe rate limiter.

### Layer 3: `config/` - Configuration
*   **`config/app.yaml`**: The central configuration file defining environment parameters, training hyperparameters, and system settings.

## 4. Data Flow

1.  **Ingestion:** `apps/poller` downloads raw market data, storing it in the **Data Lake** (`trading_gym/data/lake/`) as partitioned Parquet files.
2.  **Processing:** `FeatureAssembler` transforms raw OHLCV data into normalized features.
3.  **Training:** The Agent interacts with `MarginTradingEnv`, which streams these features.
4.  **Inference (Live):** `LiveExecutionAdapter` fetches real-time data, passes it to `FeatureAssembler`, and the Agent predicts an action.
5.  **Execution (Live):** The action is converted to an order. `LiveExecutionAdapter` checks risk rules (Borrow Caps) and sends the order via `EnhancedAbanClient`.

## 5. Safety Mechanisms

*   **Circuit Breakers:** Pause trading if drawdowns exceed thresholds (e.g., -10% in 1 hour).
*   **Panic Protocol:** A file-based signal (`panic.signal`) that instantly kills processes and flattens positions.
*   **Zombie Pruner:** Startup logic that kills lingering Python processes to prevent resource leaks.
*   **Rate Limiters:** Token-bucket limiters ensure API compliance.
