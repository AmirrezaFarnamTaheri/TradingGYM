# Trading Gym Frontend

React-based web GUI for Trading Gym.

## Quick Start

### 1. Install Dependencies

```bash
cd frontend
npm install
```

### 2. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Adjust public API/WS endpoints only if the proxy differs from defaults.
# Enter the API key in the running app, not in this file.
nano .env
```

Example `.env`:
```
VITE_API_URL=/api/v1
# VITE_WS_URL is optional; by default it follows the current page's origin.
# Do not put credentials in VITE_ variables: they are public build inputs.
```

### 3. Start Development Server

```bash
npm run dev
```

The app will be available at `http://localhost:3000`

### 4. Build for Production

```bash
npm run build
```

Output will be in `dist/`

## Features

### Operator workspace

The sidebar currently has 12 panels for production authority, institutional analysis,
research/intelligence, Forge and memory, playground, grants, system health,
configuration, data, training, models and trading.

Mutation requests never retry automatically: a lost response may represent an
executed operation. Check the authoritative backend state before resubmission.

### Real-Time Features

- ✅ Live log streaming via WebSocket
- ✅ Live metrics streaming (P&L, balance, positions)
- ✅ Real-time status updates
- ✅ Emergency kill switch with an independent credential
- ✅ Principal-bound critical proposal and approval workflow
- ✅ Governed model activation and live-start evidence
- ✅ Single-tenant deployment, audit, retention, rotation, and recovery readiness views

## Technology Stack

- **React 18** - UI framework
- **Vite** - Build tool & dev server
- **TanStack Query** - Server state management
- **Zustand** - Client state management
- **Axios** - HTTP client
- **WebSocket API** - Real-time streaming

## Project Structure

```
frontend/
├── src/
│   ├── components/
│   │   ├── ConfigPanel.jsx       - Configuration viewer
│   │   ├── DataPanel.jsx         - Data management
│   │   ├── TrainingPanel.jsx     - Training controls + logs
│   │   ├── ModelsPanel.jsx       - Model management
│   │   └── TradingPanel.jsx      - Trading controls + metrics
│   ├── api.js                    - API client
│   ├── store.js                  - Global state (Zustand)
│   ├── App.jsx                   - Main app component
│   ├── App.css                   - Styles
│   ├── main.jsx                  - Entry point
│   └── index.css                 - Global styles
├── index.html                    - HTML template
├── vite.config.js                - Vite configuration
├── package.json                  - Dependencies
└── README.md                     - This file
```

## Development

### Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build

### API Integration

The frontend communicates with the FastAPI backend via:

1. **REST API** - For control operations (start/stop training, etc.)
2. **WebSocket** - For real-time streaming (logs, metrics)

All API calls require authentication via `X-API-Key`. High/critical proposal creation, decisions, model activation, and live start also require transient actor/approval credentials. Those values stay in component-local memory, are cleared after use/cancel, and must not be persisted in Zustand, local storage, URLs, or logs.

The UI is non-authoritative: readiness cards and evidence reports cannot promote a model, trade, delete data, rotate a key, restore state, fail over traffic, or create multi-tenant isolation.

### Adding New Features

1. Create a new component in `src/components/`
2. Add API methods to `src/api.js` if needed
3. Import and use in `src/App.jsx`
4. Add styling to `src/App.css`

## Troubleshooting

### Can't Connect to API

1. Make sure API server is running: `poetry run python apps/api/main.py`
2. Check `VITE_API_URL` or the Vite proxy targets your API server.
3. Enter the backend `API_KEY` in the app's session login; never embed it in a Vite build.

### WebSocket Not Connecting

1. Check browser console for errors
2. Verify API server has WebSocket support enabled
3. Check CORS settings in API server

### Logs Not Streaming

1. Start training first: click "START TRAINING"
2. Check WebSocket connection in browser DevTools > Network > WS
3. Verify API server is using asyncio subprocess (not threading)

### Metrics Always Zero

1. Make sure trading/paper app is running
2. Verify `live_metrics.json` exists and is being updated
3. Check API server logs for errors

## Architecture

```
┌──────────────────────────────────┐
│      React Frontend              │
│  • 6 panels                      │
│  • WebSocket clients             │
│  • TanStack Query                │
└──────────┬───────────────────────┘
           │
           │ HTTP + WebSocket
           │
┌──────────▼───────────────────────┐
│      FastAPI Server              │
│  • REST endpoints                │
│  • WebSocket streaming           │
└──────────┬───────────────────────┘
           │
           │ subprocess
           │
┌──────────▼───────────────────────┐
│   Trading Gym Apps               │
│  • Trainer                       │
│  • Paper/Live trading            │
│  • Simulator                     │
└──────────────────────────────────┘
```

## Browser Support

- Chrome/Edge: Latest 2 versions
- Firefox: Latest 2 versions
- Safari: Latest 2 versions

## License

Same as parent project.

## Resources

- [React Documentation](https://react.dev/)
- [Vite Documentation](https://vitejs.dev/)
- [TanStack Query](https://tanstack.com/query/latest)
- [Zustand](https://github.com/pmndrs/zustand)
- [Trading Gym API Docs](../apps/api/README.md)
