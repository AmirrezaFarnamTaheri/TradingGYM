# Trading Gym Examples

This directory contains comprehensive examples demonstrating various features of the Trading Gym project.

## Quick Start Examples

### 1. Simple Trader (`simple_trader.py`)

**Purpose:** Minimal example showing basic trading setup.

**What it demonstrates:**
- Creating a simple trading agent
- Basic market interaction
- Simple order execution

**Run:**
```bash
python examples/simple_trader.py
```

**Best for:** First-time users wanting to understand the basics.

---

### 2. Run Local Demo (`run_local_demo.py`)

**Purpose:** Quick local demonstration without external dependencies.

**What it demonstrates:**
- Local environment setup
- Simulated trading
- Basic performance metrics

**Run:**
```bash
python examples/run_local_demo.py
```

**Best for:** Quick testing without setting up data sources.

---

## Complete System Demos

### 3. Complete API Demo (`complete_api_demo.py`)

**Purpose:** Demonstrates all API endpoints and features.

**What it demonstrates:**
- Configuration management
- Training control
- Data management
- Model operations
- Trading operations
- Monitoring and metrics
- Emergency controls

**Run:**
```bash
# Requires API server running (bound to localhost for security)
python -m uvicorn trading_gym.core.legacy_api.main:app --host 127.0.0.1 --port 8000 &
python examples/complete_api_demo.py
```

**Best for:** Understanding the full API capabilities.

---

### 4. Comprehensive Trading Demo (`comprehensive_trading_demo.py`)

**Purpose:** End-to-end trading pipeline demonstration.

**What it demonstrates:**
- Data fetching and preprocessing
- Feature engineering
- Model training
- Backtesting
- Paper trading
- Live trading (simulated)
- Performance analysis
- Risk management

**Run:**
```bash
python examples/comprehensive_trading_demo.py
```

**Best for:** Understanding the complete trading workflow.

---

## Advanced Examples

### 5. Advanced Portfolio Management (`advanced_portfolio_management.py`)

**Purpose:** Sophisticated portfolio management strategies.

**What it demonstrates:**
- Multi-asset portfolio construction
- Risk parity allocation
- Dynamic rebalancing
- Portfolio optimization
- Performance attribution
- Risk analytics

**Run:**
```bash
python examples/advanced_portfolio_management.py
```

**Best for:** Users implementing complex portfolio strategies.

---


## Usage Patterns

### For Beginners

Start with these examples in order:
1. `simple_trader.py` - Understand basics
2. `run_local_demo.py` - See it work locally
3. `complete_api_demo.py` - Learn the API
4. `comprehensive_trading_demo.py` - Full workflow


### For Production Deployment

1. Study `comprehensive_trading_demo.py` for workflow
2. Review `advanced_portfolio_management.py` for risk management
3. Use `complete_api_demo.py` to understand all API operations

---

## Common Options

Most examples support common command-line options:

```bash
# Show help
python examples/<script>.py --help

# Verbose logging
python examples/<script>.py --verbose

# Custom configuration
python examples/<script>.py --config path/to/config.yaml
```

---

## Requirements

### Minimal Requirements
- Python 3.10-3.12
- Dependencies from `pyproject.toml`

### For Full Examples
- Running API server (for `complete_api_demo.py`)
- Market data (see `DATA_SETUP.md`)
- Configured environment (`.env` file)

### Quick Setup

```bash
# Install dependencies
poetry install

# Setup environment
cp .env.example .env
# Edit .env with your settings

# Download sample data (optional)
python -m trading_gym.data.downloader --symbol BTC --days 30

# Try a simple example
python examples/simple_trader.py
```

---

## Example Output

### Simple Trader
```
Starting simple trader...
Connected to market
Order executed: BUY 0.1 BTC @ $30000
Position: +0.1 BTC
PnL: $50.00
```

```

### Complete API Demo
```
Testing Configuration API...
✓ Get config successful
✓ Update config successful

Testing Training API...
✓ Start training successful
✓ Training status retrieved
✓ Stop training successful

Testing all 24 endpoints...
✓ All endpoints working correctly
```

---

## Troubleshooting

### Import Errors
```bash
# Ensure you're in the project root
cd /path/to/TraderAgentReinforcedGym

# Install dependencies
poetry install
```

### API Connection Errors
```bash
# Start API server first
python trading_gym/core/legacy_api/main.py

# In another terminal
python examples/complete_api_demo.py
```

### Missing Data Errors
```bash
# Download sample data
python -m trading_gym.data.downloader --symbol BTC --days 30
```

---

## Creating Your Own Examples

Template structure:

```python
"""
Example: Your Example Name

Brief description of what this example demonstrates.
"""

import logging
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.core.config import load_app_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """Main example logic."""
    logger.info("Starting example...")

    # Your code here

    logger.info("Example completed!")

if __name__ == "__main__":
    main()
```

---

## Additional Resources

- **Documentation:** `/docs/` directory
- **API Reference:** `docs/COMPLETE_API_REFERENCE.md`
- **Training Guide:** `docs/walkthroughs/01_TRAINING_WALKTHROUGH.md`
- **RL Improvements:** `RL_ENVIRONMENT_IMPROVEMENTS.md`
- **Windows Setup:** `WINDOWS_INSTALLATION_GUIDE.md`

---

## Contributing

To add a new example:

1. Create the example file in `examples/`
2. Add comprehensive docstrings
3. Add entry to this README
4. Test with `poetry run python examples/your_example.py`
5. Add any new requirements to `pyproject.toml`

---

## Support

For issues with examples:
1. Check this README
2. Review documentation in `/docs/`
3. Check example code comments
4. Open an issue on GitHub

---

**Last Updated:** 2025-11-14
**Examples:** 6 total (1 new RL training example added)
**Status:** All examples tested and working
