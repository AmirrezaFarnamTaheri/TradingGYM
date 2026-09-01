# Developer Guidelines

## 💻 Coding Standards

We enforce strict standards to maintain "Production-Grade" quality.

### 1. Type Hints
**Strictly Required.** Every function argument and return value must be typed.
```python
# ✅ Good
def calculate_profit(entry: Decimal, exit: Decimal) -> Decimal:
    return exit - entry

# ❌ Bad
def calculate_profit(entry, exit):
    return exit - entry
```

### 2. Decimal Math
Never use `float` for money. Use `decimal.Decimal`.
*   **Why?** To avoid `0.1 + 0.2 = 0.30000000000000004`.
*   **Rule:** Convert inputs to String first: `Decimal(str(float_val))`.

### 3. Linting & Formatting
We use `ruff` and `black`.
```bash
# Run before committing
poetry run ruff check . --fix
poetry run black .
```

### 4. Documentation
*   **Docstrings:** Every public class and method must have a Google-style docstring.
*   **Comments:** Explain *why*, not *what*.

## 🧪 Testing Strategy

We aim for >95% code coverage.

### 1. Unit Tests (`tests/unit/`)
*   **Scope:** Single class or function.
*   **Speed:** Fast (<10ms).
*   **Mocking:** Mock all external dependencies (API, Filesystem, Network).

### 2. Integration Tests (`tests/integration/`)
*   **Scope:** Interaction between components (e.g., Env + Engine).
*   **Speed:** Slower.
*   **Realism:** Use real file I/O or in-memory DBs where possible.

### 3. Property-Based Tests (`hypothesis`)
*   **Scope:** Mathematical invariants in `ExecutionEngine`.
*   **Example:** "For all inputs, Slippage must be >= 0".

### 4. Running Tests
```bash
# Run full suite
poetry run pytest

# Run with coverage
poetry run pytest --cov=trading_gym
```

## 🤝 Contribution Workflow

1.  **Fork & Branch:** Create a feature branch (e.g., `feat/new-indicator`).
2.  **Implement:** Write code + tests.
3.  **Verify:** Run `scripts/run_full_suite.sh`.
4.  **PR:** Submit Pull Request. CI must pass.

## 📂 Project Structure

*   `trading_gym/`: The core library.
    *   `env/`: Gymnasium environment.
    *   `execution/`: Financial engine.
    *   `features/`: Data pipeline.
    *   `adapters/`: Exchange connectors.
*   `apps/`: CLI applications (Entry points).
    *   `trainer/`: RL training.
    *   `simulator/`: Backtesting.
    *   `live/`: Live execution.
*   `config/`: YAML configuration files.
*   `tests/`: Test suite.
*   `docs/`: Documentation & Wiki.

## ⚙️ Configuration Reference (`config/app.yaml`)

### `env` - Environment
*   **`symbol`**: Trading pair (e.g., "BTCUSDT").
*   **`quote_ccy`**: Quote currency (e.g., "USDT").
*   **`leverage`**:
    *   **`max_leverage`**: Max long leverage (float).
    *   **`warning_margin_ratio`**: Warning threshold.
    *   **`liquidation_margin_ratio`**: Liquidation threshold.
*   **`shorts`**:
    *   **`enabled`**: Enable/disable short selling.
    *   **`allowed_symbols`**: Whitelist of shortable assets.
    *   **`borrow_caps`**: Max short position size per asset.
*   **`costs`**:
    *   **`taker_fee_bps`**: Taker fee in basis points.
    *   **`max_spread_bps`**: Max spread allowed before skipping a step.

### `data` - Pipeline
*   **`lake_dir`**: Path to Parquet data lake.
*   **`features_dir`**: Path to processed features.
*   **`symbols`**: List of symbols to process.
*   **`timeframes`**: List of timeframes (e.g., ["5m", "1h"]).

### `training` - RL Pipeline
*   **`algo`**: Algorithm name (e.g., "PPO").
*   **`total_timesteps`**: Total training steps.
*   **`n_envs`**: Number of parallel environments.
*   **`ppo`**: PPO-specific hyperparameters (learning rate, batch size, etc.).
*   **`device`**: "cpu" or "cuda".

### `simulator` - Backtesting
*   **`steps`**: Number of steps to simulate.

### `paper_trader` - Live/Paper
*   **`loop_interval_seconds`**: Frequency of the trading loop.

### `mlflow` - Tracking
*   **`enable`**: Enable MLflow logging.
*   **`run_name`**: Experiment name.
