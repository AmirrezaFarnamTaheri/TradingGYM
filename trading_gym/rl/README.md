# trading_gym/rl

This directory contains the core reinforcement learning environment for the trading gym.

## `MarginTradingEnv`

The `MarginTradingEnv` is a `gymnasium`-based environment that simulates a single-asset margin trading scenario. It is designed to be highly configurable and realistic, incorporating detailed logic for leverage, financing, transaction costs, and liquidation.

### Design

The environment is built on NumPy for performance and is configured via a set of dataclasses (`EnvConfig`, `LeverageConfig`, etc.). It encapsulates all the core trading logic, making it a self-contained simulation engine.

### Observation Space

The observation space is a one-dimensional array containing the current market data (mid price, spread, volatility) and the agent's account state (position, equity).

### Action Space

The action space is a continuous value representing the target position in the base asset (e.g., number of BTC to hold).

### Reward Function

The reward function is the log return of the portfolio's equity from one time step to the next, naturally accounting for all costs and PnL.
