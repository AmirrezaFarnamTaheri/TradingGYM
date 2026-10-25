# Trading Gym cockpit frontend

React, TanStack Query and Vite provide three views: Live monitor, Experience and Settings. English and Persian share the same real API; Persian sets document language and RTL direction. Browser persistence contains the language preference, not credentials.

```sh
npm ci
npm run dev -- --host 127.0.0.1
npm run test:ci
npm run build
```

Vite proxies `/api` to `127.0.0.1:8000`. The production server serves this checkout's `frontend/dist`. Use the [first-run guide](../docs/FIRST_RUN.md) and [settings/evidence guide](../docs/SETTINGS_AND_DECISIONS.md). There is no demo-data fallback. Tests may stub API responses in isolated fixtures; production imports do not use those fixtures.
