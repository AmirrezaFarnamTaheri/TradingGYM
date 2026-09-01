# Troubleshooting & FAQ

## 🚨 Emergency Procedures

### My bot is losing money rapidly!
1.  **PANIC:** Run `touch panic.signal` or hit the GUI Panic button.
2.  **Verify:** Check Abantether website manually to ensure positions are closed.
3.  **Logs:** Check `logs/live_trader.log` to see what happened.

### The process is stuck ("Zombie" process)
Sometimes a process dies but leaves a PID file or keeps a port open.
1.  Run the cleanup script:
    ```bash
    python -m apps.api.main --prune
    ```
2.  Or kill manually:
    ```bash
    pkill -f "python -m apps"
    ```

## 🔍 Common Issues

### `ImportError: No module named 'trading_gym'`
*   **Cause:** Python path not set correctly.
*   **Fix:** Run via `poetry run python -m ...` or set `export PYTHONPATH=$PYTHONPATH:.`

### `Decimal invalid operation`
*   **Cause:** Mixing `float` and `Decimal` directly, or dividing by zero.
*   **Fix:** Ensure all inputs are cast to `Decimal(str(float_val))` before math operations.

### `WebSocket Connection Closed`
*   **Cause:** Network instability or API maintenance (Abantether resets connections periodically).
*   **Fix:** The `AbanWebSocket` class has auto-reconnect logic. Check logs for "Reconnecting..." messages. If persistent, check your internet connection.

### CI Fails with `Aborted` (Segfault)
*   **Cause:** Often due to C-extension conflicts (numpy/pandas) or Numba cache corruption in a CI environment.
*   **Fix:** Clear cache `rm -rf __pycache__` and try running tests serially.

### `Permission Denied` on Scripts
*   **Cause:** Script not executable.
*   **Fix:** `chmod +x scripts/*.sh`

## 📜 Logging

Logs are stored in `logs/`.
*   `app.log`: General application logs.
*   `error.log`: Only errors/criticals.
*   `trade.log`: Record of every trade execution (Side, Price, Qty, Fee).
*   `api.log`: Raw API requests/responses (Sensitive info redacted).

## ❓ FAQ

**Q: Can I trade on Binance?**
A: Not out of the box. The `LiveExecutionAdapter` is hardcoded for Abantether's specific API. You would need to write a new Adapter.

**Q: How much RAM do I need?**
A:
*   **Training:** 32GB+ (Dataset is loaded into RAM).
*   **Live Trading:** 4GB is sufficient.

**Q: Does it support GPU?**
A: Yes. `stable_baselines3` and PyTorch will automatically use CUDA if available.
