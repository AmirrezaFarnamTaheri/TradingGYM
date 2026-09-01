# Paper Trading

Paper Trading (or "Forward Testing") is the bridge between Simulation and Live Trading. It allows you to test the entire system infrastructure without risking real capital.

## 🎭 What is Paper Trading?

In Trading Gym, Paper Trading is a hybrid mode:
*   **Live Data:** It connects to the **real** Abantether API to fetch live market data (Price, Spread, Order Book).
*   **Simulated Execution:** It executes orders on a local, simulated ledger (`ExecutionState`).

This is superior to "Backtesting" because it exposes the agent to real-world latency, WebSocket disconnects, and data anomalies, but superior to "Live Trading" because mistakes cost $0.

## ⚙️ Configuration

Enable Paper Mode in `config/app.yaml` or via CLI.

```yaml
paper_trader:
  trade_size_pct_equity: 1.0  # Use 100% of virtual equity
  loop_interval_seconds: 60   # Run inference every minute
```

## 🏃 How to Run

```bash
python -m apps.paper.main run --config config/app.yaml --live
```
*(Note: The `--live` flag here enables the network connection to Abantether, but the `apps.paper.main` entry point ensures execution remains virtual.)*

## 🆚 Simulator vs. Paper vs. Live

| Feature | Simulator | Paper Trader | Live Trader |
| :--- | :--- | :--- | :--- |
| **Data Source** | Historical Parquet | Real-time API | Real-time API |
| **Execution** | Simulated (Fast) | Simulated (Real-time) | Real Exchange |
| **Latency** | Zero (or Configured) | Real Network Latency | Real Network Latency |
| **Risk** | None | None | **Real Money** |
| **Speed** | 10,000 steps/sec | 1 step / X sec | 1 step / X sec |

## 📝 Best Practices

1.  **Burn-in Period:** Run the Paper Trader for at least 1 week before going Live.
2.  **Compare with Sim:** If your Paper Trading results diverge significantly from your Backtest results (on the same time period), you have a **Reality Gap** (likely due to slippage or latency).
3.  **Monitor Logs:** Use this phase to verify that error handling (API retries, disconnects) works as expected.
