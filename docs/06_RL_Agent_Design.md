# RL Agent Design

The AI brain of the system is a Proximal Policy Optimization (PPO) agent. This document details the neural architecture and the Markov Decision Process (MDP) formulation.

## 1. Observation Space (State)

The agent "sees" the market through a structured tensor of size `N_Features + 3`. The observation space is a `Box` (Continuous) containing normalized features.

### Components

| Component | Description | Range |
| :--- | :--- | :--- |
| **Market Features** | Fused features from the pipeline (e.g., Log Returns, Volatility, RSI). Normalized to approx `N(0,1)`. | $(-\infty, \infty)$ |
| **Equity State** | Log-normalized equity relative to initial capital: $\ln(\frac{\text{Equity}_t}{\text{InitialEquity}})$. | $(-\infty, \infty)$ |
| **Price State** | Log-normalized price relative to initial price: $\ln(\frac{\text{Price}_t}{\text{InitialPrice}})$. | $(-\infty, \infty)$ |
| **Position State** | Position size normalized by initial purchasing power: $\frac{\text{Position}}{\text{InitialEquity} / \text{InitialPrice}}$. | Approx `[-20, 20]` |

### Key Features
1.  **Log Returns:** `ln(p_t / p_{t-1})` (Z-Score)
2.  **Volatility:** Rolling Std Dev of Log Returns (Z-Score)
3.  **RSI:** Relative Strength Index (Scaled 0-1)
4.  **Spread:** Bid-Ask Spread (Log-Scaled)
5.  **Time Encoding:** Sine/Cosine encodings of Hour of Day (to learn intraday seasonality).

### Multi-Timeframe Stacking (Fusion)
To give the agent "peripheral vision", we use an attention-based mechanism to fuse features from multiple timeframes (e.g., 5m, 1h, 4h). This logic is handled by `feature_fusion.py`, which dynamically weights the importance of different timeframes based on market context.

## 2. Action Space

The action space is a single continuous value representing **Target Leverage**.

*   **Type:** `Box(low=-5.0, high=5.0, shape=(1,), dtype=float32)`
*   **Semantics:**
    *   `+5.0` = Max Long (5x Leverage) - Abantether maximum
    *   `+2.5` = 2.5x Long
    *   `0.0` = Flat (Cash)
    *   `-2.5` = 2.5x Short
    *   `-5.0` = Max Short (5x Leverage) - Abantether maximum

**Note:** Abantether limits both long and short leverage to 5x maximum.

### Why Target Leverage?
By controlling leverage rather than raw position size or units, the policy becomes **invariant to account size**. A policy trained on $10,000 works identically on $1,000,000 because it outputs "1x leverage", not "Buy 0.1 BTC".

## 3. Reward Function

The Reward Function is the compass that guides the agent. We use a **Risk-Adjusted Return** formulation based on log-returns.

### Step Reward
$$ R_t = \text{clip}\left( \text{Scaling} \times \ln\left(\frac{\text{Equity}_t}{\text{Equity}_{t-1}}\right), -100, 100 \right) $$

*   **Log Return:** Symmetrical treatment of gains and losses.
*   **Scaling:** Multiplied by 100 (configurable) to prevent gradient underflow (PPO struggles with tiny rewards like 0.0001).
*   **Clipping:** Clamped between -100 and +100 to prevent "exploding gradients" from extreme market events.

### Terminal Rewards (The "Death Penalty")
If `Equity <= 0` (Bankruptcy), the episode terminates immediately, and the agent receives a massive penalty (default: **-100.0**). This creates a strong incentive for survival and risk management.

## 4. Network Architecture (PPO)

We use `Stable-Baselines3` implementation of PPO.

*   **Policy Network (Actor):** Decides the Action.
    *   Structure: `MLP(256, 256, Tanh)` - Two hidden layers with 256 units each
*   **Value Network (Critic):** Estimates expected future reward.
    *   Structure: `MLP(256, 256, Tanh)` - Separate network, same architecture
*   **Activation:** Tanh (bounded outputs, good for continuous control)
*   **Optimizer:** Adam (`lr=3e-4`).
*   **Entropy Coefficient:** `0.01` (Encourages exploration).
*   **Clip Range:** `0.2` (Prevents drastic policy updates).
*   **GAE Lambda:** `0.95` (Generalized Advantage Estimation).

## 5. Simulation Mechanics & Fixes

The environment implements several critical fixes to ensure realistic training:

1.  **Random Resets:** Episodes start at a random point in history to prevent overfitting to a specific timeline ("Groundhog Day" effect).
2.  **Intra-Bar Liquidation:** The environment checks if the `High` or `Low` of a candle would have triggered liquidation, ensuring the agent respects volatility risk within a timeframe.
3.  **Action Clipping:** Invalid actions (e.g., requesting 50x leverage) are clipped to the maximum allowed, rather than crashing the simulation. This clipping logic is centralized in the environment and exposed via `clip_target_position` to ensure live trading executions respect the exact same limits.
