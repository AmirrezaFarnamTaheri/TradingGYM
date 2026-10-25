> Current follow-up: [four-view product and guarded funded execution](../docs/DEFERRED_COMPLETION.md) supersedes earlier three-view and unconnected-submission descriptions below. Funded execution remains disabled by default and no real orders were placed.

# Trading Gym cockpit frontend

React, TanStack Query and Vite provide four views: Live monitor, Experience, Strategy lab and Settings. English and Persian share the same real API; Persian sets document language and RTL direction. Browser persistence contains the language preference, not credentials.

```sh
npm ci
npm run dev -- --host 127.0.0.1
npm run test:ci
npm run build
```

Vite proxies `/api` to `127.0.0.1:8000`. The production server serves this checkout's `frontend/dist`. Use the [first-run guide](../docs/FIRST_RUN.md) and [settings/evidence guide](../docs/SETTINGS_AND_DECISIONS.md). There is no demo-data fallback. Tests may stub API responses in isolated fixtures; production imports do not use those fixtures.
