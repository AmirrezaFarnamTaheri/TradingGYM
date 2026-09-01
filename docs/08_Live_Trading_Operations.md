# Live Trading Operations

Going live is the ultimate test. This section details the operational protocols for running Trading Gym with real money.

## 🚀 Deployment Checklist

Before running `apps/live`, verify:
1.  **API Keys:** `ABANTETHER_TOKEN` is set in `.env`.
2.  **Funds:** Wallet has sufficient `USDT` (or `IRT`).
3.  **Time Sync:** Server clock is synchronized (NTP).
4.  **Network:** Low-latency connection to Abantether servers.
5.  **Config:** `app.yaml` matches your intended risk parameters (Max Leverage, Stop Loss).

## 🖥 The Live Trader (`apps/live`)

The Live Trader is a long-running process loop:

1.  **Heartbeat:** Writes timestamp to `health.heartbeat` every 5s.
2.  **Safety Check:** Checks for `panic.signal`.
3.  **Fetch Data:** Gets latest candle from Adapter.
4.  **Inference:** Model predicts `Target Leverage`.
5.  **Reconcile:**
    *   Calculates `Target Qty`.
    *   Compares with `Current Qty` (from Broker API).
    *   Sends `Limit Order` (Post-Only if possible) or `Market Order` to close the gap.

## 🚨 The Panic Protocol

This is the "Nuclear Option". If the bot goes rogue or the market crashes unexpectedly.

**Triggering Panic:**
*   **Method 1 (CLI):** Run `touch panic.signal` in the project root.
*   **Method 2 (API):** POST `/api/v1/trading/panic`.
*   **Method 3 (GUI):** Click the big red "STOP" button.

**What happens?**
1.  The Live Trader detects the signal immediately.
2.  **Cancel All:** Sends API request to cancel all open orders.
3.  **Flatten:** Sends API request to Market Close all positions.
4.  **Shutdown:** The process exits.

## 🧟 Zombie Pruner

Python processes can sometimes "hang" or become detached.
*   **Mechanism:** On startup, the `apps/api` checks for any existing processes matching `apps.live`, `apps.trainer`, etc.
*   **Action:** It aggressively `kill -9` them to ensure a clean slate.
*   **Logging:** You will see `✂️ Pruned X zombie processes` in the logs.

## 📊 Monitoring

We support industry-standard monitoring stacks.

*   **Prometheus:** The API exposes metrics at `/api/v1/metrics`.
    *   `tg_equity`: Current account equity.
    *   `tg_position`: Current position size.
    *   `tg_api_latency`: Time for API roundtrip.
*   **Grafana:** Dashboards can visualize these metrics in real-time.
*   **Logs:** Structured logs are written to `logs/`.
    *   `live_trader.log`: Operational logs.
    *   `execution.log`: Detailed trade records.

## 🛡 Security Best Practices

1.  **No Root:** Run the bot as a dedicated user, not root.
2.  **Firewall:** Block all inbound ports except SSH (22) and API (8000 - allowlisted IPs only).
3.  **Secrets:** Never commit `.env` to Git. Use `secrets.compare_digest` for API Key checks.
