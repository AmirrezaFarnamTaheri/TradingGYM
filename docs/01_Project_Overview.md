# Project Overview

## 🚀 Mission

The mission of **Trading Gym** is to democratize institutional-grade algorithmic trading for the Abantether ecosystem. We aim to build a system where:

1.  **Simulation is Reality:** Backtests are not just "directional indicators"; they are mathematically precise twins of production execution.
2.  **AI is the Driver:** We move beyond simple heuristics (Moving Averages, RSI thresholds) to deep Reinforcement Learning agents that adapt to changing market regimes.
3.  **Safety is Paramount:** Capital preservation is enforced by hard-coded circuit breakers, not just the AI's whims.

## 📚 Documentation Index

This Wiki is your definitive guide, organized logically from high-level concepts to deep technical implementation details.

| Section | Description |
| :--- | :--- |
| **[01 Project Overview](01_Project_Overview.md)** | Mission, Tech Stack, and Index. |
| **[02 System Architecture](02_System_Architecture.md)** | Diagrams, Ports & Adapters, Data Flow. |
| **[03 Core Financial Engine](03_Core_Financial_Engine.md)** | Decimal precision, slippage, costs, and liquidity logic. |
| **[04 Market Microstructure & Data](04_Market_Microstructure_Data.md)** | Data Lake, Feature Engineering, Lookahead Bias prevention. |
| **[05 Abantether Broker Rules](05_Abantether_Broker_Rules.md)** | **CRITICAL:** Borrow Caps, Shorting Whitelists, Asymmetric Leverage. |
| **[06 RL Agent Design](06_RL_Agent_Design.md)** | PPO Architecture, Observation/Action Spaces, Reward Function. |
| **[07 Training & Simulation](07_Training_Simulation.md)** | Curriculum Learning, Backtesting, Walk-Forward Validation. |
| **[08 Live Trading Operations](08_Live_Trading_Operations.md)** | Deployment, Panic Protocol, Monitoring. |
| **[09 Paper Trading](09_Paper_Trading.md)** | Forward testing in the sandbox. |
| **[10 Developer Guidelines](10_Developer_Guidelines.md)** | Coding standards, Type hints, Testing strategy. |
| **[11 Troubleshooting & FAQ](11_Troubleshooting_FAQ.md)** | Recovery procedures and common errors. |
| **[12 Future Roadmap](12_Future_Roadmap.md)** | Multi-Agent RL, Transformer Architectures. |
| **[13 GUI & Visualization](13_GUI_and_Visualization.md)** | Dashboard usage and deployment. |

## 💡 Core Philosophy: "Sim = Prod"

In most trading systems, the backtester is a simple loop that assumes instant fills and zero friction. This leads to **Overfitting** and **Live Trading Failure**.

Trading Gym takes a different approach:
*   **Shared Execution Engine:** The exact same code that processes backtests (`ExecutionEngine`) validates orders in Live mode.
*   **Unified Data Pipeline:** Feature engineering is identical for historical training and live streaming.
*   **Latency Simulation:** We explicitly model the time delay between "Decision" and "Execution".

## 🛠 Technology Stack

We stand on the shoulders of giants, leveraging a modern, high-performance Python stack:

*   **Core:** Python 3.12+, PyTorch, Stable Baselines 3, Gymnasium, Numba.
*   **Data:** Pandas, NumPy, Polars/PyArrow.
*   **Infra:** FastAPI, Docker, Psutil.
*   **Connectivity:** Abantether Python SDK.

## 🌟 Key Features

1.  **Zero Lookahead Bias:** Strict chronological splitting and rolling window normalization.
2.  **Institutional Risk Management:** Drawdown Circuit Breakers, Panic Protocol, Borrow Caps.
3.  **Production Readiness:** "One-Click" Scripts, Self-Healing, Observability.

## 🗺 Current Status

**Version:** 4.2.1
**Status:** Production-Ready

We have completed **Phase 6: Validation & Polish**. The system is **production-ready** for single-pair margin trading (BTC/USDT, ETH/USDT).

### Coverage & Quality
- Test coverage exceeds **90%** on critical components
- All financial calculations use **Decimal precision**
- Comprehensive risk metrics with theoretical correctness verified
- Walk-forward validation prevents overfitting

### Recent Improvements (v4.2.1)
- Fixed Sharpe ratio calculation (now uses equity returns, not rewards)
- Fixed financing rate calculation (100x bug resolved)
- Added comprehensive risk metrics module (`trading_gym/utils/metrics.py`)
- Documented market impact model with academic references
- Enhanced observation normalization for faster training
