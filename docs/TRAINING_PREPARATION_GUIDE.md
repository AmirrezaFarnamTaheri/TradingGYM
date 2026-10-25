# Preparing research training

Install the locked Python dependencies and run `tgym doctor`. Inspect `training_local.yaml` and the selected profile under `config/`; resolve every data path before invoking `tgym train --config training_local.yaml`.

Training requires real, correctly timestamped data, a known feature schema, chronological train/validation/holdout splits and explicit execution-cost assumptions. The small files under `data/parquet/` are engineered regression features, not live candles or a production training corpus. Verify them with `python scripts/verify_feature_fixtures.py`.

If data is unavailable, stop and obtain a qualified dataset. Do not generate dummy market data to claim a trained product, market performance or venue qualification. Synthetic inputs belong only in isolated tests with explicit labels.

The cockpit's shadow trainer is a separate bounded path: it waits for at least 400 observed broker bars, trains a CPU PPO challenger on a chronological prefix and evaluates a held-out suffix through the paper engine. A successful smoke test establishes pipeline execution, not profitability. See [the rebuild status](REBUILD.md) and [current operation](FIRST_RUN.md).
