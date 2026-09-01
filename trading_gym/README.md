# Trading Gym

This directory contains the core logic for the trading system, encapsulated in a library-like structure. It is designed to be imported and used by the various applications in the `/apps` directory.

## Core Principles

- **Library, Not a Framework**: `trading_gym` is designed to be a collection of tools and components that can be imported and used by various applications. It does not impose a rigid structure on how the applications are built.
- **Realism is Key**: The core components, especially the `MarginTradingEnv`, are designed to simulate real-world trading conditions as closely as possible, including costs, fees, slippage, and liquidation.
- **Performance**: The environment is built on NumPy arrays for fast, vectorized operations, avoiding the overhead of pandas DataFrames in the critical `step` loop.

## Core Components

### `/trading_gym/rl`
- **Purpose**: This subdirectory contains the reinforcement learning environment.
- **Key Components**:
    - `environment.py`: This file defines the `MarginTradingEnv`, the heart of the simulation. It is a deterministic, array-based environment that simulates the mechanics of a margin trading account with a high degree of realism. It handles everything from financing and costs to leverage and liquidation.

### `/trading_gym/venue`
- **Purpose**: This subdirectory contains the logic for asset and symbol management.
- **Key Components**:
    - `asset_registry.py`: This file defines the universe of tradable assets, including which assets are shortable and which are available for credit trading.

### `/trading_gym/margin`
- **Purpose**: This subdirectory contains the logic for margin and borrowing.
- **Key Components**:
    - `borrow_caps.py`: This file enforces the borrow caps for short selling, ensuring that the agent cannot exceed the limits for BTC and ETH.

## Design Philosophy

The `trading_gym` library is the single source of truth for all trading logic. By centralizing the core components in this library, we ensure that all applications, from the simulator to the live trader, operate on the same set of rules and calculations. This is a critical aspect of the `sim == prod` design principle.
