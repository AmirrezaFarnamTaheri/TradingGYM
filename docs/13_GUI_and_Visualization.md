# GUI & Visualization

Trading Gym includes a professional-grade React-based GUI for monitoring and control. This document covers its architecture, deployment, and usage.

## 🖥 The Dashboard

The GUI provides a "Mission Control" view of the system with six main panels:

### 1. Config Panel
- View and edit system configuration
- Select trading symbol and timeframe
- Adjust leverage settings (with asymmetric limit indicators)

### 2. Training Panel
- Start/stop training sessions
- Monitor training progress
- View real-time logs via WebSocket

### 3. Models Panel
- List trained models
- Set active model for trading
- Run backtests on selected models

### 4. Trading Panel
- **Mode Selection:** Paper or Live trading
- **Model Selection:** Choose which model to deploy
- **Leverage Slider:** Visual representation of leverage limits (1.5x-5x for both long and short)
- **Production Warning:** Clear indicator when in live mode
- **Emergency Controls:** Panic button to flatten all positions

### 5. Data Panel
- Download market data
- Generate features from raw data
- View data statistics

### 6. System Panel
- Real-time system metrics (CPU, Memory, Disk)
- API latency monitoring
- Environment status (PROD/DEV indicator)

## 🛠 Technical Stack

*   **Frontend:** React, Vite, TailwindCSS, Recharts.
*   **Backend:** FastAPI (Python).
*   **Communication:**
    *   **REST:** Control commands (Start/Stop).
    *   **WebSocket:** Real-time streaming of logs and metrics (`/ws/metrics`, `/ws/logs`).

## 🚀 Deployment

The GUI is designed to run locally or on a remote server (accessed via SSH tunnel or VPN).

### Windows (One-Click)
```powershell
# Starts API (Port 8000) and Frontend (Port 3000)
powershell -ExecutionPolicy Bypass -File scripts/start-gui.ps1
```

### Linux / Mac
```bash
# Starts API and Frontend
./scripts/start-gui.sh
```

### Docker (Production)
For headless servers, use Docker Compose.
```bash
docker-compose up -d
```
*   **API:** `http://localhost:8000`
*   **Frontend:** `http://localhost:3000`

## 📊 Visualization Details

### Backtest Reports
When the Simulator completes a run, it generates a PDF report and a set of PNG charts in `results/<run_id>/`.

*   **`equity.png`:** The growth of $1.
*   **`drawdown.png`:** Underwater plot.
*   **`heatmap.png`:** Monthly returns heatmap.

### TensorBoard
For training progress, we use TensorBoard.
```bash
tensorboard --logdir logs/tensorboard
```
Access at `http://localhost:6006`. Key metrics to watch:
*   `rollout/ep_rew_mean`: Mean reward per episode. Should trend up.
*   `train/value_loss`: Critic error. Should trend down.
*   `train/approx_kl`: Divergence. If too high (>0.03), training is unstable.

## 🛡 Security

*   **Authentication:** The GUI requires an `API Key` to connect to the backend.
*   **CORS:** The API is locked down to only allow requests from the frontend origin.
*   **No External Access:** By default, the GUI binds to `localhost`. To access remotely, use an SSH Tunnel:
    ```bash
    ssh -L 3000:localhost:3000 -L 8000:localhost:8000 user@server
    ```
