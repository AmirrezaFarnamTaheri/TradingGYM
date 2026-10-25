> Current follow-up: [four-view product and guarded funded execution](../docs/DEFERRED_COMPLETION.md) supersedes earlier three-view and unconnected-submission descriptions below. Funded execution remains disabled by default and no real orders were placed.

# Trading Gym package

One Python namespace owns the supported local product and retained research components:

| Domain | Responsibility |
| --- | --- |
| `core` | Configuration, persistence, encrypted local secrets and research compatibility |
| `data` | Ingestion, features and lineage |
| `market` | Abantether connectors, market rules and venue metadata |
| `execution` | Decimal accounts, orders, risk and retained execution engines |
| `rl` | Simulation, training and evaluation |
| `agent` | Signal heads, fusion, model gateway, decision journal and daemon |
| `api` | Current loopback cockpit API |

Start with `python -m trading_gym.cli serve`. The React build must exist in the source checkout. Default mode observes actual connector records; paper execution is separate. Funded spot execution requires explicit live configuration and remains disabled by default. Retained `core/legacy_api` services are not mounted by this entrypoint.

The optional native crate is `../kernel/`. Engineered regression fixtures live in `../data/parquet/`, outside the package's code. See [current documentation](../docs/README.md) and [verification](../tests/README.md).
