# Trading Gym

A local trading research cockpit with three views: **Live monitor**, **Experience**, and **Settings**, in English and فارسی. It opens directly on loopback. Production screens use actual connector records; unavailable data stays unavailable.

## Run locally

Python 3.11 or 3.12 and Node 22.12+ are required.

```powershell
poetry install --with dev
cd frontend
npm ci
npm run build
cd ..
poetry run tgym serve
```

Open [the cockpit](http://127.0.0.1:8000/). From a checkout, `python -m trading_gym.cli serve` is equivalent. `--home PATH` selects a data-home parent; the cockpit uses its `cockpit-v2` subdirectory. `--port PORT` changes the port. The default home is `~/.trading-gym/cockpit-v2`. Build the frontend before serving from a checkout.

## Connect and inspect

In Settings, save the Abantether API key and refresh the connection. Keys are encrypted on the server; Windows protects the encryption key with DPAPI. The browser receives key labels and identifiers, never stored secret values. Environment fallbacks are `ABAN_API_KEY`, `LLM_API_KEY`, and `DECISION1_API_KEY`; `.env` is not loaded automatically.

The default mode is **observe**. The connector reads quotes, balances and order history. It builds five-minute bars from observations collected locally; it does not fabricate historical candles. A cold start needs at least 14 complete bars before signals are available. Experience separates confirmed broker fills from paper fills and lets you inspect recorded signals, head weights, model route, risk constraints and the resulting decision. Imported fills without a matching decision are explicitly unattributed.

No broker order submission is connected to this cockpit; live execution returns a conflict response. A configured read connection does not make funded autonomous trading available. The current environment's Abantether connection failed during TLS negotiation; authenticated balances, real fills and model inference remain unverified until credentials and connectivity are available.

## Paper decisions and limits

Paper mode runs the actual Decimal accounting engine against observed market data, with modeled fees and slippage. It is explicitly labeled and has its own virtual opening capital. Exposure, daily-loss, drawdown, stop-loss and restart-persistent cooldown limits apply. Pause blocks entries while protection remains active; Stop ends the feed. Emergency flatten closes paper positions at their last marks and latches the halt. Interrupted writes halt further decisions.

Settings supports separate multi-key rosters for the LLM planner and Decision-1, up to 16 keys each. Configured gateways use OpenAI-compatible chat JSON. Rotation, cooldown and shared endpoint admission reuse the credential pool. Authentication or hard-quota failure blocks exposure; transient planner failure tightens it. Model advice cannot raise financial caps. Native System-One adapters remain research components and are not the cockpit gateway protocol.

Shadow learning waits for 400 actual observed bars. A CPU PPO challenger trains on the first 70%; the final 30% is held out. Promotion requires better equity and per-bar Sharpe, no worse drawdown, and probabilistic and deflated Sharpe evidence of at least 0.95 through the paper engine. Trials appear in Experience. Promotion affects paper decisions only and does not establish future profitability.

## Develop and verify

Run `poetry run pytest -q -m "not integration"`; run `npm run test:ci` and `npm run build` inside `frontend`. For frontend development, `npm run dev -- --host 127.0.0.1` proxies `/api` to port 8000. `poetry run tgym doctor` checks installed dependencies. `poetry run tgym train --config training_local.yaml` starts the research trainer; verify its data paths first.

Python code lives under `trading_gym/{core,data,market,execution,rl,agent,api}`. The optional Rust crate is `kernel/`; feature fixtures are `data/parquet/`, distinct from market candles. Retained research/governance APIs under `core/legacy_api` are not mounted by `tgym serve`.

See the [documentation index](docs/README.md), [first-run guide](docs/FIRST_RUN.md), [rebuild status](docs/REBUILD.md), and [deletion review](docs/DELETION_REVIEW.md). The current API is [openapi.json](docs/contracts/openapi.json); retained research has a separate [contract](docs/contracts/research-openapi.json). Regenerate both with `python scripts/export_openapi.py`.

Containers retain the loopback boundary. Ordinary bridge port publishing has not been qualified. No remote publication or funded order execution is implied by a successful local build.

## License

AGPL-3.0-only. See [LICENSE](LICENSE).
