# Feature Fixtures vs. Lake Partition Integrity

## Source boundary

The 28 small files matching `trading_gym/data/*_5m_features.parquet`
are checked-in feature/test fixtures. Their release content hashes are
already pinned in the top-level `SHA256SUMS`. They are **not** the same
filesystem layout as the versioned lake partitions verified by
`apps/lake/manifest.py`, which expects a symbol/timeframe directory
such as `BTC/5m/part_2020-09-13.parquet`.

The existing `BTC__5m.manifest.json` is a lake-partition manifest for
`BTC/5m`, not a manifest for `BTCUSDT_5m_features.parquet`.
Generating 27 additional similarly named manifests against these
fixtures would create misleading provenance and fail the lake verifier.

## Verification

`python scripts/verify_feature_fixtures.py` checks that every feature
fixture has a unique tracked SHA-256 entry, that every pinned fixture
still exists, and that the actual contents match the hash.
`tests/data/test_feature_fixture_inventory.py` covers valid, modified,
untracked and missing fixtures and checks the current repository set.

`apps/lake/manifest.py` continues to own runtime partition integrity,
including actual membership checks and daily Merkle roots; it must be
invoked against **real partition directories** and genuine provider
data. No fake partitions or Merkle roots are generated here.

## Future externalization

Move fixtures or historical ledgers into content-addressed storage only
after a versioned provenance mapping, tested download/restore path,
offline fixtures for deterministic CI, and release-consumer migration
are available. Existing checked-in fixtures occupy less than 1 MiB
each as a group at this checkpoint; claims of >85% source reduction
from deleting them were not established.
