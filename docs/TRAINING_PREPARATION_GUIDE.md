# Training Preparation Guide

This guide contains essential information and recommendations for preparing the **TraderAgentReinforcedGym** project for a successful training run.

## 1. The "Death Penalty" Recommendation

**Original Advice:** Keep the death penalty at `-100.0` for the initial training runs.

### Explanation & Deep Dive

In Reinforcement Learning (RL), the "Death Penalty" is a massive negative reward given to the agent when it reaches a terminal failure state—in this case, bankruptcy (`Equity <= 0`).

*   **Why -100.0?**
    The reward function in `trading_gym/rl/margin_trading_environment.py` calculates rewards based on **Log Returns** scaled by a factor of 100.
    *   A 1% gain in a step $\approx$ +1.0 reward.
    *   A 5% loss in a step $\approx$ -5.0 reward.
    *   Therefore, a penalty of **-100.0** is equivalent to an instantaneous **100% loss** of equity in a single 5-minute bar. This is a "catastrophic" signal to the neural network.

*   **The Risk of "Fear Paralysis":**
    If you increase this penalty too much (e.g., to `-10,000`), the agent might learn that *any* market exposure carries a risk of infinite pain. It will discover that the optimal policy to avoid `-10,000` pain is to **never trade** (staying in cash results in 0 reward, which is better than -10,000).
    *   *Goal:* You want the agent to fear bankruptcy, not fear trading.

*   **The Risk of "Gambling":**
    If you decrease it (e.g., to `-10`), the agent might treat bankruptcy as a minor fee. It might learn to take 20x leverage on every trade because the times it doubles its money (+70 reward) outweigh the times it goes bankrupt (-10 penalty).

### Actionable Insight

Monitor the `liquidation_rate` metric during training.

*   If `liquidation_rate > 10%` after 100k steps: **Increase** the penalty (make it -200).
*   If `n_trades == 0`: **Decrease** the penalty (make it -50).

---

## 2. Essential Edits Before Training

The following edits are mandatory to prevent crashes or silent failures.

### A. Critical Configuration Fix: `features.timeframes`

**Issue:** The `FeatureAssembler` iterates over the list of timeframes defined in the config. If empty, the agent receives an observation vector of zeros or missing data, making it "blind".

**Fix:** Define the base timeframe that matches your data in `config/app.yaml`.

```yaml
features:
  timeframes:
    - name: "tf_5m"        # Name must match what your pipeline expects
      bar_minutes: 5       # The duration of one candle
      roll_fill_cap: 0     # How many missing bars to backfill (0 = strict)
    - name: "tf_1h"
      bar_minutes: 60
      roll_fill_cap: 0
    - name: "tf_4h"
      bar_minutes: 240
      roll_fill_cap: 0
```

### B. Training Duration: `total_timesteps`

**Issue:** PPO is "sample inefficient". 100k steps is often insufficient for the agent to learn basic mechanics.

**Fix:** Increase `total_timesteps` to `1,000,000`.

### C. Environment Secrets: `.env`

**Issue:** The `EnhancedAbanClient` often checks for an API token at startup, even in simulation. Missing `.env` can cause `KeyError` or connection validation errors.

**Fix:** Ensure `.env` exists.
```bash
cp .env.example .env
```

### D. Data Ingestion: `data/lake`

**Issue:** The simulator reads historical data from disk. If `data/lake` is empty, the training script will crash.

**Fix:** Generate dummy data if real data is unavailable.
```bash
python scripts/dev_generate_dummy_data.py
```

---

## 3. Audit Fix Verification

The following critical issues have been verified as **FIXED** in the codebase.

### Split-Brain Fix

*   **Issue:** The system used to calculate borrow amounts *before* checking if the position size was legal, leading to phantom interest payments.
*   **Verification:** In `trading_gym/execution/execution_engine.py`, `_enforce_short_rules` is called **lines before** `borrowed_base` is calculated.

### Short Leverage Fix

*   **Issue:** Missing safety cap for short selling.
*   **Verification:** `config/app.yaml` explicitly contains `max_short_leverage: 5.0`.

### Financing Rate Fix (v4.2.1)

*   **Issue:** The financing rate was using `daily_pct` directly as a decimal (treating 0.05 as 5%) instead of converting from percentage (0.05% = 0.0005). This caused financing costs to be **100x too high**.
*   **Verification:** In `trading_gym/rl/margin_trading_environment.py`, the code now explicitly converts: `daily_rate_decimal = financing_cfg.daily_pct / 100.0`.

### Sharpe Ratio Fix (v4.2.1)

*   **Issue:** Sharpe ratio was calculated from scaled reward signals instead of actual equity returns.
*   **Verification:** In `trading_gym/pipelines/evaluation.py`, Sharpe is now calculated from percentage returns on equity: `returns = np.diff(equity_curve) / equity_curve[:-1]`.

---

## 4. Verdict: 1-minute (1m) vs 5-minute (5m) Candles

**Verdict: Do NOT include 1-minute (1m) candles.**

For the **TraderAgentReinforcedGym** project and its PPO architecture, adding 1-minute data will likely **degrade performance**.

### 1. The "Fee Trap" (The Mathematical Killer)

*   **Your Cost:** Taker Fee is `7.5 bps` (0.075%). Round-trip cost is **0.15%**.
*   **The 1m Problem:** In a typical 1-minute candle, price movement is often < 0.15%. Even with perfect prediction, the move is often smaller than the fee, resulting in a loss.
*   **The 5m Advantage:** 5-minute candles have larger volatility, allowing the "signal" to overpower the "cost".

### 2. The Signal-to-Noise Ratio

*   **On 1m:** RSI and Volatility are extremely "jittery", triggering false signals due to random microstructure noise.
*   **On 5m+:** Indicators smooth out noise, providing a cleaner signal for the RL agent.

### 3. Computational Bloat

*   **Data Size:** 1m data is 5x larger than 5m data.
*   **Training Speed:** Significantly slows down the `apps/trainer` loop for little value.

### Recommended Configuration

Stick to the **"Scalper with Context"** setup:
*   **5m:** Immediate Action (Sweet Spot)
*   **1h:** Trend Context
*   **4h:** Macro Regime

---

## 5. GPU Configuration Profiles

The project supports multiple GPU profiles for different hardware configurations:

### A. RTX 3050 / Low VRAM (4-8GB)

Use `config/training_rtx3050.yaml` for consumer GPUs with limited VRAM:

```bash
python -m apps.trainer.main train --config config/training_rtx3050.yaml
```

**Key Optimizations:**
- `n_envs: 2` (reduced parallelism)
- `batch_size: 64` (small batches)
- `n_steps: 1024` (shorter rollouts)
- `gradient_accumulation: 2` (simulates 128 batch)
- `use_amp: true` (critical for memory)
- Attention disabled to save memory

**Estimated Training Time:** ~2-4 hours for 500K steps

### B. Kaggle T4 / Cloud GPU (16GB)

Use `config/training_kaggle.yaml` for cloud GPUs like Tesla T4:

```bash
python -m apps.trainer.main train --config config/training_kaggle.yaml
```

**Key Optimizations:**
- `n_envs: 16` (maximum parallelism)
- `batch_size: 4096` (large batches)
- `n_steps: 8192` (long rollouts)
- `gamma: 0.995` (longer horizon)
- Full asset universe

**Estimated Training Time:** ~6-8 hours for 10M steps

### C. Dual Workspace Strategy

For serious training, use both environments:

1. **Local RTX 3050:** Quick iteration, debugging, hyperparameter search
2. **Kaggle T4:** Full production training runs

**Workflow:**
```
1. Develop & debug locally (RTX 3050, 100K steps)
2. Transfer code to Kaggle
3. Run full training (T4, 10M steps)
4. Download trained model
5. Evaluate locally
```

---

## 6. ⚠️ CRITICAL: Dual-Process Consistency Warnings

When using different configurations for local development vs. cloud training, you **must** be aware of potential inconsistencies that can silently break your models.

### 6.1 Symbol Set Consistency

**Problem:** Training on different symbol subsets creates incompatible models.

| Scenario | Risk Level | Consequence |
|----------|------------|-------------|
| Train locally on BTC/ETH, deploy on full universe | 🔴 **HIGH** | Model hasn't learned dynamics of other assets |
| Train on Kaggle with full set, evaluate locally with subset | 🟡 **MEDIUM** | Evaluation may be misleading |
| Same symbols everywhere | 🟢 **SAFE** | Consistent behavior |

**The Root Cause:**
- **Feature Statistics:** Normalization (mean/std) is computed per training run
- **Market Dynamics:** BTC behaves differently than DOGE/PEPE (volatility, correlation)
- **Policy Generalization:** PPO learns symbol-specific patterns

**Solution:** Use the **same symbol list** for both local and Kaggle configs when training production models.

```yaml
# CONSISTENT symbol list for both configs
data:
  symbols:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
    - DOGEUSDT
```

### 6.2 Safe vs. Unsafe Dual-Process Patterns

**✅ SAFE Patterns:**

| Local (RTX 3050) | Kaggle (T4) | Purpose |
|------------------|-------------|---------|
| Hyperparameter search (small symbols) | Final training (same symbols) | Find optimal LR, batch_size |
| Debugging code (any symbols) | Production training | Fix bugs before long runs |
| Backtesting trained model | Training | Validate model performance |

**❌ UNSAFE Patterns:**

| Local (RTX 3050) | Kaggle (T4) | Problem |
|------------------|-------------|---------|
| Train on BTC only | Deploy on 30 symbols | Model can't generalize |
| Different `gamma` values | Different `gamma` values | Inconsistent value estimates |
| Different `rolling_window` | Different `rolling_window` | Feature dimension mismatch |

### 6.3 Configuration Synchronization Checklist

Before switching between local and cloud, verify these are **identical**:

```
✅ data.symbols (or superset relationship clear)
✅ features.rolling_window
✅ features.timeframes
✅ env.leverage settings
✅ env.costs settings
✅ training.ppo.gamma
✅ training.ppo.gae_lambda
```

**May differ (memory optimization only):**
```
⚡ training.n_envs
⚡ training.ppo.batch_size
⚡ training.ppo.n_steps
⚡ training.ppo.gradient_accumulation
⚡ features.attention.enabled (if memory constrained)
```

### 6.4 Model Portability

Models trained on Kaggle can be used locally if:
1. **Same observation dimension:** Verify `obs_dim` matches
2. **Same action space:** Both use `Box(-5, 20)` for leverage
3. **Same normalization:** Use training stats from the original run

**Loading a Kaggle-trained model locally:**
```python
from stable_baselines3 import PPO

# Load model trained on Kaggle
model = PPO.load("checkpoints/kaggle_model/best_model.zip")

# Verify observation space matches your local env
assert model.observation_space.shape == local_env.observation_space.shape
```

---

## 7. Memory Troubleshooting

If you encounter OOM (Out of Memory) errors:

| Symptom | Fix |
|---------|-----|
| CUDA OOM during training | Reduce `batch_size` to 32 |
| CUDA OOM at startup | Reduce `n_envs` to 1 |
| Slow training | Enable `use_amp: true` |
| Unstable gradients | Increase `gradient_accumulation` |
