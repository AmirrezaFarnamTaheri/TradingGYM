# Future Roadmap

Trading Gym is an evolving ecosystem. This document outlines the strategic direction and upcoming features.

## 🛣 Phase 7: Multi-Agent Reinforcement Learning (MARL)

Current state: Single PPO agent managing one asset.
**Goal:** A swarm of specialized agents.

*   **The "Portfolio Manager" Agent:** Allocates capital dynamically between different strategies (e.g., Trend Following vs. Mean Reversion).
*   **The "Execution" Agent:** Focuses solely on minimizing slippage for large orders (TWAP/VWAP execution).
*   **Cooperative Learning:** Agents share a common reward pool to encourage global optimization.

## 🛣 Phase 8: Advanced Architectures

Current state: MLP (Multi-Layer Perceptron).
**Goal:** Sequence modeling.

*   **Transformer Encoder:** Replace the LSTM/Stacking with a Transformer (Attention Mechanism) to capture long-range dependencies in price action.
*   **Decision Transformer:** Treat trading as a sequence modeling problem, predicting the optimal action sequence conditional on desired return.

## 🛣 Phase 9: Multi-Asset Universe

Current state: Single-pair trading (BTC/USDT).
**Goal:** Portfolio of 10-50 assets.

*   **Graph Neural Networks (GNN):** Model the correlation structure between assets (e.g., ETH follows BTC).
*   **Global Normalization:** Learn a universal policy that generalizes across assets (train on 100 pairs, trade on new ones zero-shot).

## 🛣 Phase 10: HFT & L2 Data

Current state: 5m candles (Low/Mid Frequency).
**Goal:** Tick-level trading.

*   **Order Book Imbalance:** Feeding the full L2 Order Book (Bids/Asks) to the agent.
*   **Microsecond Execution:** Moving core logic from Python to Rust/C++ for sub-millisecond latency.

## 💡 Unexplored Ideas (RFC)

*   **Sentiment Analysis:** Ingesting Twitter/News/Telegram data as an observation channel.
*   **On-Chain Data:** Monitoring whale movements and exchange inflows/outflows.
*   **Macro Data:** Feeding Fed Rate, CPI, and SP500 correlation.

---
*Contributors are welcome to pick up any of these initiatives!*
