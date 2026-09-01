# Training & Simulation

This section covers the lifecycle of creating, training, and validating a model. It includes high-level workflows as well as deep dives into the PPO algorithm, optimization techniques, and monitoring.

## 1. The Training Pipeline

Training is managed by `apps/trainer/main.py` and orchestrated by `trading_gym/pipelines/training.py`. The pipeline handles:
1. Data loading and normalization statistics calculation
2. Environment creation with vectorization
3. PPO agent initialization with configurable hyperparameters
4. Training loop with checkpointing

### 1.1 Curriculum Learning (Optional)
Curriculum learning gradually increases task difficulty. This is **optional** and configured via the `curriculum` key in `config/app.yaml`.

**Recommended Stages:**
*   **Stage 1 (Conservative):**
    *   **Max Leverage:** 2x.
    *   **Goal:** Learn basic market dynamics and survive fees.
    *   **Duration:** 500K-1M steps.
*   **Stage 2 (Moderate):**
    *   **Max Leverage:** 5x.
    *   **Goal:** Learn to use leverage profitably.
    *   **Duration:** 1-2M steps.
*   **Stage 3 (Full):**
    *   **Max Leverage:** 5x (both long and short per Abantether limit).
    *   **Goal:** Master high-leverage execution with asymmetric limits.
    *   **Duration:** 2-5M steps.

**Note:** By default, `curriculum: []` skips staged training and uses full leverage immediately.

### 1.2 Parallel Environments
We use `SubprocVecEnv` (multiprocessing) or `DummyVecEnv` (single process) based on `n_envs`:
*   **`n_envs = 1`:** Uses `DummyVecEnv` (sequential, good for debugging).
*   **`n_envs > 1`:** Uses `SubprocVecEnv` (parallel, 2-10x faster training).
*   **Recommendation:** Set `n_envs` to `CPU_cores - 2` for optimal throughput.

### 1.3 Callbacks & Monitoring
The training pipeline uses callbacks for checkpointing and monitoring:

*   **`CheckpointCallback`**: Saves model checkpoints at regular intervals (`save_freq`).
*   **Early Stopping Config**: Configured via `early_stop.liquidation_rate` and `early_stop.check_freq`.
*   **TensorBoard Logging**: Enabled by default; view with `tensorboard --logdir <tensorboard_dir>`.

**Future Callbacks (Planned):**
*   `EvalCallback` for holdout evaluation
*   `SharpeRatioCallback` for risk-adjusted model selection
*   `MlflowCallback` for experiment tracking

## 2. Deep Dive: PPO & Optimization

The Trading Gym uses **Proximal Policy Optimization (PPO)**, a stable and sample-efficient Actor-Critic algorithm.

### 2.1 The Algorithm
*   **Actor (`π(a|s)`):** The policy network that decides on an action (Target Leverage).
*   **Critic (`V(s)`):** The value network that estimates the expected future reward.
*   **Objective:** PPO uses a clipped objective function to prevent the policy from changing too drastically in a single update, ensuring stability.

### 2.2 High-Performance Training (`AMPEnabledPPO`)
We use a custom PPO implementation to maximize performance on modern hardware.

*   **Automatic Mixed Precision (AMP):** Uses `float16` for most operations on Tensor Core GPUs, reducing memory usage by ~50% and speeding up training by 2-3x.
*   **Gradient Accumulation:** Simulates larger batch sizes by accumulating gradients over multiple steps before updating weights. This stabilizes training on GPUs with limited VRAM.

### 2.3 Hyperparameter Tuning
Key hyperparameters to tune in `config/app.yaml`:

*   **`learning_rate`:** Step size of updates. Typical: 1e-5 to 1e-3.
*   **`batch_size`:** Samples per update. Small (64) for generalization, Large (512) for stability.
*   **`clip_range`:** Limits policy change. Default: 0.2.
*   **`ent_coef`:** Encourages exploration. Higher = more random.

## 3. Simulation (Backtesting)

Once a model is trained, we must rigorously validate it using the Simulator (`apps/simulator`).

### Methodology
*   **In-Sample (IS):** Data used during training. Confirms the model *learned*.
*   **Out-of-Sample (OOS):** New data. Confirms the model *generalizes*.
*   **Walk-Forward Test:** A rolling window approach (Train Jan-Mar, Test Apr; Train Feb-Apr, Test May) simulates a realistic production lifecycle.

### Simulator Output
The simulator produces a PDF report containing:
1.  **Equity Curve:** Visual growth of account.
2.  **Drawdown Chart:** Depth and duration of losses.
3.  **Metrics Table:** Sharpe Ratio, Sortino Ratio, Max Drawdown, Calmar Ratio, Win Rate.

## 4. How to Run

**Training:**
```bash
python -m apps.trainer.main train --config config/training_t4.yaml
```

**Backtesting:**
```bash
python -m apps.simulator.main run --model-path checkpoints/my_run/best_model.zip
```
